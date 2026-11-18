"""HTTP-сервер (стандартная библиотека): статика, загрузки, JSON API, авторизация Telegram."""
import base64
import collections
import json
import mimetypes
import os
import re
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from . import auth, logic
from .config import Config
from .db import DB
from .logic import ShopError
from .notify import Bot

MAX_BODY = 6 * 1024 * 1024
IMG_MAGIC = ((b'\x89PNG\r\n\x1a\n', 'png'), (b'\xff\xd8\xff', 'jpg'), (b'GIF8', 'gif'))
CSP = ("default-src 'self'; script-src 'self' https://telegram.org; style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; "
       "connect-src 'self'; frame-ancestors 'self' https://web.telegram.org https://webk.telegram.org https://webz.telegram.org")


def sniff_image(data):
    for magic, ext in IMG_MAGIC:
        if data.startswith(magic):
            return ext
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'webp'
    return None


class App:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        os.makedirs(os.path.dirname(cfg.db_path) or '.', exist_ok=True)
        os.makedirs(cfg.upload_dir, exist_ok=True)
        self.db = DB(cfg.db_path)
        self.bot = Bot(cfg.bot_token, cfg.public_url)
        self._hits = collections.defaultdict(collections.deque)
        self._lock = threading.Lock()

    def rate_ok(self, key, limit, window):
        t = time.time()
        with self._lock:
            q = self._hits[key]
            while q and q[0] < t - window:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(t)
            return True

    # ------------------------------------------------ авторизация
    def authenticate(self, headers):
        init = headers.get('X-Tg-Init-Data', '')
        if self.cfg.dev_mode and not init and headers.get('X-Dev-User'):
            try:
                uid = int(headers['X-Dev-User'])
            except ValueError:
                raise ShopError('Некорректный dev-пользователь', 'auth', 401)
            user = {'id': uid, 'first_name': 'Dev %d' % uid, 'username': 'dev%d' % uid}
        else:
            try:
                user = auth.verify_init_data(init, self.cfg.bot_token, self.cfg.initdata_max_age)
            except auth.AuthError as e:
                raise ShopError('Откройте магазин из Telegram (%s)' % e, 'auth', 401)
        c = self.db.conn()
        ts = logic.now()
        c.execute('INSERT INTO users(tg_id, username, first_name, created_at, last_seen) VALUES (?,?,?,?,?) '
                  'ON CONFLICT(tg_id) DO UPDATE SET username=excluded.username, first_name=excluded.first_name, last_seen=excluded.last_seen',
                  (user['id'], str(user.get('username') or '')[:64], str(user.get('first_name') or '')[:64], ts, ts))
        row = c.execute('SELECT * FROM users WHERE tg_id=?', (user['id'],)).fetchone()
        if row['banned']:
            raise ShopError('Доступ закрыт', 'forbidden', 403)
        return dict(row), user['id'] in self.cfg.admin_ids

    # ------------------------------------------------ API
    def public_config(self):
        s = self.db.settings()
        return dict(shop_name=s['shop_name'], currency=s['currency'], welcome=s['welcome'], age_gate=s['age_gate'], shop_open=s['shop_open'],
                    closed_message=s['closed_message'], support_contact=s['support_contact'], low_stock_threshold=s['low_stock_threshold'],
                    min_order_minor=s['min_order_minor'], delivery_fee_minor=s['delivery_fee_minor'], free_delivery_from_minor=s['free_delivery_from_minor'],
                    dev_mode=self.cfg.dev_mode)

    def me(self, user, is_admin):
        s = self.db.settings()
        c = self.db.conn()
        loy = logic.loyalty_state(s, logic.user_orders_count(c, user['tg_id']))
        return dict(user=dict(id=user['tg_id'], username=user['username'], first_name=user['first_name'], phone=user['phone'], city=user['city'],
                              address=user['address'], age_confirmed=bool(user['age_confirmed_at'])),
                    is_admin=is_admin, loyalty=loy, orders=logic.user_orders(self.db, user['tg_id']))

    def handle(self, method, path, query, body, headers):
        """Возвращает (status, payload_dict). Исключения ShopError превращаются в JSON-ошибку выше."""
        if path == '/api/config' and method == 'GET':
            return 200, self.public_config()
        user, is_admin = self.authenticate(headers)
        uid = user['tg_id']
        if not self.app_rate(uid):
            raise ShopError('Слишком много запросов, подождите минуту', 'rate', 429)
        if path.startswith('/api/admin/'):
            if not is_admin:
                raise ShopError('Нет доступа', 'forbidden', 403)
            return self.admin(method, path[len('/api/admin/'):], query, body)
        if path == '/api/me' and method == 'GET':
            return 200, self.me(user, is_admin)
        if path == '/api/me' and method == 'POST':
            part = {k: ' '.join(str(body.get(k, '')).split())[:200] for k in ('phone', 'city', 'address')}
            self.db.conn().execute('UPDATE users SET phone=?, city=?, address=? WHERE tg_id=?', (part['phone'], part['city'], part['address'], uid))
            return 200, {'ok': True}
        if path == '/api/age-confirm' and method == 'POST':
            self.db.conn().execute('UPDATE users SET age_confirmed_at=? WHERE tg_id=? AND age_confirmed_at=0', (logic.now(), uid))
            return 200, {'ok': True}
        if path == '/api/catalog' and method == 'GET':
            return 200, logic.catalog(self.db)
        if path == '/api/quote' and method == 'POST':
            q = logic.quote(self.db, uid, body.get('items'), str(body.get('promo', '')))
            q.pop('_promo_id', None)
            return 200, q
        if path == '/api/order' and method == 'POST':
            if not self.app_rate(('order', uid), 5, 600):
                raise ShopError('Слишком много заказов подряд, попробуйте позже', 'rate', 429)
            o = logic.create_order(self.db, uid, body)
            s = self.db.settings()
            self.bot.notify_new_order(o, self.cfg.admin_ids, s['notify_chat_ids'], s['currency'])
            return 200, {'order': o}
        raise ShopError('Не найдено', 'not_found', 404)

    def app_rate(self, key, limit=240, window=60):
        return self.rate_ok(key, limit, window)

    def admin(self, method, sub, query, body):
        db = self.db
        if sub == 'summary' and method == 'GET':
            return 200, dict(stats=logic.stats(db), settings=db.settings())
        if sub == 'catalog' and method == 'GET':
            return 200, logic.catalog(db, admin=True)
        if sub == 'product' and method == 'POST':
            pid = logic.save_product(db, body)
            return 200, {'id': pid}
        m = re.match(r'^product/(\d+)$', sub)
        if m and method == 'DELETE':
            logic.delete_product(db, int(m.group(1)))
            return 200, {'ok': True}
        if sub == 'category' and method == 'POST':
            return 200, {'id': logic.save_category(db, body)}
        m = re.match(r'^category/(\d+)$', sub)
        if m and method == 'DELETE':
            logic.delete_category(db, int(m.group(1)))
            return 200, {'ok': True}
        if sub == 'promos' and method == 'GET':
            return 200, {'promos': logic.list_promos(db)}
        if sub == 'promo' and method == 'POST':
            return 200, {'id': logic.save_promo(db, body)}
        m = re.match(r'^promo/(\d+)$', sub)
        if m and method == 'DELETE':
            logic.delete_promo(db, int(m.group(1)))
            return 200, {'ok': True}
        if sub == 'settings' and method == 'POST':
            return 200, {'settings': logic.save_settings(db, body)}
        if sub == 'orders' and method == 'GET':
            st = (query.get('status') or [''])[0]
            return 200, {'orders': logic.list_orders(db, st, 100, int((query.get('offset') or ['0'])[0] or 0))}
        m = re.match(r'^order/(\d+)/status$', sub)
        if m and method == 'POST':
            o = logic.set_status(db, int(m.group(1)), str(body.get('status', '')))
            self.bot.notify_status(o)
            return 200, {'order': o}
        if sub == 'upload' and method == 'POST':
            try:
                data = base64.b64decode(str(body.get('data', '')).split(',')[-1], validate=True)
            except Exception:
                raise ShopError('Не удалось прочитать файл')
            if len(data) > 3 * 1024 * 1024:
                raise ShopError('Файл больше 3 МБ')
            ext = sniff_image(data)
            if not ext:
                raise ShopError('Нужна картинка PNG, JPG, WEBP или GIF')
            name = '%s.%s' % (secrets.token_hex(12), ext)
            with open(os.path.join(self.cfg.upload_dir, name), 'wb') as f:
                f.write(data)
            return 200, {'path': '/uploads/' + name}
        raise ShopError('Не найдено', 'not_found', 404)


def make_handler(app: App):
    UP_RE = re.compile(r'^/uploads/([a-f0-9]{16,}\.(?:png|jpg|webp|gif))$')

    class H(BaseHTTPRequestHandler):
        server_version = 'Shop'
        protocol_version = 'HTTP/1.1'

        def log_message(self, fmt, *a):
            if os.environ.get('SHOP_VERBOSE'):
                super().log_message(fmt, *a)

        def _send(self, status, data, ctype, extra=None):
            self.send_response(status)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', CSP)
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(data)

        def _json(self, status, payload):
            self._send(status, json.dumps(payload, ensure_ascii=False).encode(), 'application/json; charset=utf-8', {'Cache-Control': 'no-store'})

        def _file(self, path, cache):
            try:
                with open(path, 'rb') as f:
                    data = f.read()
            except OSError:
                return self._json(404, {'error': {'code': 'not_found', 'message': 'Не найдено'}})
            ctype = mimetypes.guess_type(path)[0] or 'application/octet-stream'
            if ctype.startswith('text/') or ctype in ('application/javascript', 'application/json'):
                ctype += '; charset=utf-8'
            self._send(200, data, ctype, {'Cache-Control': cache})

        def _route(self, method):
            u = urlparse(self.path)
            path = u.path
            if path.startswith('/api/'):
                body = {}
                if method in ('POST', 'PUT'):
                    n = int(self.headers.get('Content-Length') or 0)
                    if n > MAX_BODY:
                        return self._json(413, {'error': {'code': 'too_big', 'message': 'Слишком большой запрос'}})
                    raw = self.rfile.read(n) if n else b''
                    try:
                        body = json.loads(raw.decode() or '{}')
                        if not isinstance(body, dict):
                            raise ValueError
                    except ValueError:
                        return self._json(400, {'error': {'code': 'bad_json', 'message': 'Некорректный запрос'}})
                try:
                    status, payload = app.handle(method, path, parse_qs(u.query), body, self.headers)
                    return self._json(status, payload)
                except ShopError as e:
                    return self._json(e.status, {'error': {'code': e.code, 'message': e.message}})
                except Exception as e:                       # не светим внутренности наружу
                    print('ERROR', method, path, repr(e))
                    return self._json(500, {'error': {'code': 'server', 'message': 'Внутренняя ошибка сервера'}})
            if method != 'GET':
                return self._json(405, {'error': {'code': 'method', 'message': 'Метод не поддерживается'}})
            m = UP_RE.match(path)
            if m:
                return self._file(os.path.join(app.cfg.upload_dir, m.group(1)), 'public, max-age=604800, immutable')
            if path in ('/', '/index.html'):
                return self._file(os.path.join(app.cfg.static_dir, 'index.html'), 'no-cache')
            rel = path.lstrip('/')
            if re.match(r'^[A-Za-z0-9_\-]+(\.[A-Za-z0-9_\-]+)?\.(js|css|png|jpg|svg|ico|webp|json)$', rel):
                return self._file(os.path.join(app.cfg.static_dir, rel), 'no-cache')
            return self._json(404, {'error': {'code': 'not_found', 'message': 'Не найдено'}})

        def do_GET(self): self._route('GET')
        def do_HEAD(self): self._route('GET')
        def do_POST(self): self._route('POST')
        def do_DELETE(self): self._route('DELETE')
    return H


def run(cfg=None):
    cfg = cfg or Config()
    cfg.check()
    app = App(cfg)
    if cfg.bot_token and cfg.bot_polling:
        app.bot.start_polling(lambda: app.db.settings()['shop_name'])
    srv = ThreadingHTTPServer((cfg.host, cfg.port), make_handler(app))
    srv.daemon_threads = True
    print('Магазин запущен: http://%s:%d  (админы: %s)' % (cfg.host, cfg.port, sorted(cfg.admin_ids) or 'не заданы!'))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
