import base64
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import auth, logic
from app.config import Config
from app.db import DB
from app.logic import ShopError
from app.server import App, make_handler

TOKEN = '123456:TEST-TOKEN'
CONTACT = dict(phone='+375291234567', city='Минск', address='ул. Ленина 1, кв 2')


def new_db():
    return DB(os.path.join(tempfile.mkdtemp(), 't.db'))


def add_user(db, uid=10, age=True):
    db.conn().execute('INSERT OR IGNORE INTO users(tg_id, username, first_name, age_confirmed_at, created_at, last_seen) VALUES (?,?,?,?,0,0)', (uid, 'u%d' % uid, 'U', 1 if age else 0))
    return uid


def make_product(db, price=2000, stock=5, flavors=None, **kw):
    cat = db.conn().execute('SELECT id FROM categories').fetchone()
    cid = cat['id'] if cat else logic.save_category(db, {'name': 'Жидкости'})
    d = dict(category_id=cid, name='Товар', price_minor=price, stock=stock, flavors=flavors or [])
    d.update(kw)
    return logic.save_product(db, d)


def order(db, uid, items, promo='', **kw):
    p = dict(items=items, promo=promo, **CONTACT)
    p.update(kw)
    return logic.create_order(db, uid, p)


class TestAuth(unittest.TestCase):
    def test_valid(self):
        init = auth.make_init_data({'id': 42, 'first_name': 'Ян'}, TOKEN)
        self.assertEqual(auth.verify_init_data(init, TOKEN)['id'], 42)

    def test_tampered_user(self):
        init = auth.make_init_data({'id': 42}, TOKEN).replace('42', '43')
        with self.assertRaises(auth.AuthError):
            auth.verify_init_data(init, TOKEN)

    def test_wrong_token_expired_missing(self):
        init = auth.make_init_data({'id': 1}, TOKEN)
        with self.assertRaises(auth.AuthError):
            auth.verify_init_data(init, 'other:token')
        old = auth.make_init_data({'id': 1}, TOKEN, auth_date=time.time() - 3 * 86400)
        with self.assertRaises(auth.AuthError):
            auth.verify_init_data(old, TOKEN)
        for bad in ('', 'hash=abc', 'user=%7B%7D'):
            with self.assertRaises(auth.AuthError):
                auth.verify_init_data(bad, TOKEN)


class TestStock(unittest.TestCase):
    def setUp(self):
        self.db = new_db()
        self.u = add_user(self.db)

    def test_sold_out_flavor_and_product_disappear_then_return(self):
        pid = make_product(self.db, flavors=[{'name': 'A', 'stock': 1}, {'name': 'B', 'stock': 2}])
        prod = logic.catalog(self.db)['products'][0]
        fa = [f for f in prod['flavors'] if f['name'] == 'A'][0]['id']
        fb = [f for f in prod['flavors'] if f['name'] == 'B'][0]['id']
        order(self.db, self.u, [{'product_id': pid, 'flavor_id': fa, 'qty': 1}])
        names = [f['name'] for f in logic.catalog(self.db)['products'][0]['flavors']]
        self.assertEqual(names, ['B'])                                    # вкус A исчез сам
        order(self.db, self.u, [{'product_id': pid, 'flavor_id': fb, 'qty': 2}])
        self.assertEqual(logic.catalog(self.db)['products'], [])          # товар исчез целиком
        self.assertEqual(len(logic.catalog(self.db, admin=True)['products']), 1)   # админ его видит
        logic.set_status(self.db, 1, 'cancelled')                         # отмена вернула остаток
        self.assertEqual(len(logic.catalog(self.db)['products']), 1)

    def test_simple_product_sold_out(self):
        pid = make_product(self.db, stock=2)
        order(self.db, self.u, [{'product_id': pid, 'qty': 2}])
        self.assertEqual(logic.catalog(self.db)['products'], [])

    def test_oversell_blocked(self):
        pid = make_product(self.db, stock=2)
        with self.assertRaises(ShopError) as cm:
            order(self.db, self.u, [{'product_id': pid, 'qty': 3}])
        self.assertEqual(cm.exception.code, 'stock')
        self.assertEqual(self.db.conn().execute('SELECT stock FROM products').fetchone()[0], 2)      # ничего не списалось
        self.assertEqual(self.db.conn().execute('SELECT COUNT(*) FROM orders').fetchone()[0], 0)

    def test_failed_order_rolls_back_everything(self):
        a = make_product(self.db, stock=5)
        b = make_product(self.db, stock=1)
        with self.assertRaises(ShopError):
            order(self.db, self.u, [{'product_id': a, 'qty': 2}, {'product_id': b, 'qty': 4}])
        self.assertEqual([r[0] for r in self.db.conn().execute('SELECT stock FROM products ORDER BY id')], [5, 1])

    def test_two_buyers_one_item(self):
        pid = make_product(self.db, stock=1)
        add_user(self.db, 11)
        res = []

        def buy(uid):
            try:
                order(self.db, uid, [{'product_id': pid, 'qty': 1}])
                res.append('ok')
            except ShopError:
                res.append('fail')
        ts = [threading.Thread(target=buy, args=(u,)) for u in (10, 11)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(sorted(res), ['fail', 'ok'])
        self.assertEqual(self.db.conn().execute('SELECT stock FROM products').fetchone()[0], 0)

    def test_price_comes_from_db_and_flavor_must_belong_to_product(self):
        p1 = make_product(self.db, price=1500, flavors=[{'name': 'A', 'stock': 3}])
        p2 = make_product(self.db, price=1000, flavors=[{'name': 'X', 'stock': 3}])
        fa = self.db.conn().execute('SELECT id FROM flavors WHERE name="A"').fetchone()[0]
        o = order(self.db, self.u, [{'product_id': p1, 'flavor_id': fa, 'qty': 2, 'price_minor': 1}])
        self.assertEqual(o['total_minor'], 3000)
        q = logic.quote(self.db, self.u, [{'product_id': p2, 'flavor_id': fa, 'qty': 1}])    # чужой вкус
        self.assertEqual(q['lines'], [])
        self.assertTrue(q['problems'])
        q = logic.quote(self.db, self.u, [{'product_id': p1, 'qty': 1}])                      # не выбран вкус
        self.assertEqual(q['lines'], [])

    def test_hidden_product_not_orderable(self):
        pid = make_product(self.db, active=False)
        with self.assertRaises(ShopError):
            order(self.db, self.u, [{'product_id': pid, 'qty': 1}])

    def test_garbage_items(self):
        for bad in (None, 'x', [1], [{'product_id': 'a'}], [{'product_id': 1, 'qty': 0}], [{'product_id': 1, 'qty': 1000}], [{'product_id': 1, 'qty': -5}]):
            with self.assertRaises(ShopError):
                logic.quote(self.db, self.u, bad)


class TestOrderValidation(unittest.TestCase):
    def setUp(self):
        self.db = new_db()
        self.u = add_user(self.db)
        self.pid = make_product(self.db)

    def test_contact_validation(self):
        for bad in ({'phone': 'abc'}, {'phone': ''}, {'city': ''}, {'address': 'ул'}, {'tg_username': 'a b'}, {'comment': 'x' * 501}):
            with self.assertRaises(ShopError, msg=str(bad)):
                order(self.db, self.u, [{'product_id': self.pid, 'qty': 1}], **bad)

    def test_saved_contact_and_username_normalised(self):
        o = order(self.db, self.u, [{'product_id': self.pid, 'qty': 1}], tg_username='@client_1', comment='  привет ')
        self.assertEqual((o['tg_username'], o['comment']), ('client_1', 'привет'))
        row = self.db.conn().execute('SELECT phone, city FROM users WHERE tg_id=?', (self.u,)).fetchone()
        self.assertEqual((row['phone'], row['city']), (CONTACT['phone'], 'Минск'))

    def test_age_gate_and_closed_shop(self):
        young = add_user(self.db, 99, age=False)
        with self.assertRaises(ShopError) as cm:
            order(self.db, young, [{'product_id': self.pid, 'qty': 1}])
        self.assertEqual(cm.exception.code, 'age')
        logic.save_settings(self.db, {'age_gate': False})
        order(self.db, young, [{'product_id': self.pid, 'qty': 1}])         # с выключенным окном — можно
        logic.save_settings(self.db, {'shop_open': False})
        with self.assertRaises(ShopError) as cm:
            order(self.db, self.u, [{'product_id': self.pid, 'qty': 1}])
        self.assertEqual(cm.exception.code, 'closed')

    def test_min_order_and_delivery(self):
        logic.save_settings(self.db, {'min_order_minor': 5000, 'delivery_fee_minor': 300, 'free_delivery_from_minor': 4000})
        with self.assertRaises(ShopError):
            order(self.db, self.u, [{'product_id': self.pid, 'qty': 1}])    # 20 < 50
        self.assertEqual(logic.quote(self.db, self.u, [{'product_id': self.pid, 'qty': 1}])['delivery_minor'], 300)
        self.assertEqual(logic.quote(self.db, self.u, [{'product_id': self.pid, 'qty': 2}])['delivery_minor'], 0)


class TestLoyalty(unittest.TestCase):
    def setUp(self):
        self.db = new_db()
        self.u = add_user(self.db)
        self.pid = make_product(self.db, price=2000, stock=100)
        self.items = [{'product_id': self.pid, 'qty': 1}]

    def test_every_fifth_order_discounted_then_resets(self):
        totals = [order(self.db, self.u, self.items)['total_minor'] for _ in range(11)]
        self.assertEqual(totals, [2000] * 4 + [1800] + [2000] * 4 + [1800] + [2000])      # 5-й и 10-й со скидкой 10%

    def test_progress_reported(self):
        for _ in range(4):
            order(self.db, self.u, self.items)
        loy = logic.quote(self.db, self.u, self.items)['loyalty']
        self.assertEqual((loy['filled'], loy['remaining'], loy['next_is_discount']), (4, 0, True))

    def test_cancelled_order_does_not_count(self):
        for _ in range(4):
            order(self.db, self.u, self.items)
        logic.set_status(self.db, 4, 'cancelled')
        self.assertFalse(logic.quote(self.db, self.u, self.items)['loyalty']['next_is_discount'])

    def test_owner_can_change_rules(self):
        logic.save_settings(self.db, {'loyalty_every_n': 3, 'loyalty_type': 'fixed', 'loyalty_value': 500})
        totals = [order(self.db, self.u, self.items)['total_minor'] for _ in range(3)]
        self.assertEqual(totals, [2000, 2000, 1500])
        logic.save_settings(self.db, {'loyalty_enabled': False})
        self.assertEqual(order(self.db, self.u, self.items)['loyalty_discount_minor'], 0)

    def test_discount_never_exceeds_subtotal(self):
        logic.save_settings(self.db, {'loyalty_every_n': 2, 'loyalty_type': 'fixed', 'loyalty_value': 999999})
        order(self.db, self.u, self.items)
        self.assertEqual(order(self.db, self.u, self.items)['total_minor'], 0)

    def test_percent_over_100_rejected(self):
        with self.assertRaises(ShopError):
            logic.save_settings(self.db, {'loyalty_value': 150})


class TestPromo(unittest.TestCase):
    def setUp(self):
        self.db = new_db()
        self.u = add_user(self.db)
        self.pid = make_product(self.db, price=2000, stock=100)
        self.items = [{'product_id': self.pid, 'qty': 2}]       # 40.00

    def promo(self, **kw):
        d = dict(code='SALE10', type='percent', value=10, per_user_limit=1)
        d.update(kw)
        return logic.save_promo(self.db, d)

    def test_percent_and_fixed(self):
        self.promo()
        self.assertEqual(logic.quote(self.db, self.u, self.items, 'sale10')['total_minor'], 3600)       # регистр не важен
        self.promo(code='MINUS5', type='fixed', value=500)
        self.assertEqual(logic.quote(self.db, self.u, self.items, 'MINUS5')['total_minor'], 3500)

    def test_unknown_inactive_expired_min(self):
        q = logic.quote(self.db, self.u, self.items, 'NOPE')
        self.assertTrue(q['promo_error'])
        self.assertEqual(q['total_minor'], 4000)
        with self.assertRaises(ShopError):
            order(self.db, self.u, self.items, 'NOPE')                 # при оформлении неверный код = ошибка
        pid = self.promo(code='OFF', active=False)
        with self.assertRaises(ShopError):
            order(self.db, self.u, self.items, 'OFF')
        self.promo(code='OLD', expires_at=int(time.time()) - 10)
        with self.assertRaises(ShopError):
            order(self.db, self.u, self.items, 'OLD')
        self.promo(code='BIG', min_order_minor=10000)
        with self.assertRaises(ShopError):
            order(self.db, self.u, self.items, 'BIG')

    def test_limits_total_and_per_user(self):
        self.promo(max_uses=1)
        add_user(self.db, 11)
        order(self.db, self.u, self.items, 'SALE10')
        with self.assertRaises(ShopError):
            order(self.db, self.u, self.items, 'SALE10')              # лимит на человека
        with self.assertRaises(ShopError):
            order(self.db, 11, self.items, 'SALE10')                  # общий лимит исчерпан

    def test_cancel_gives_promo_back(self):
        self.promo(max_uses=1)
        order(self.db, self.u, self.items, 'SALE10')
        logic.set_status(self.db, 1, 'cancelled')
        self.assertEqual(order(self.db, self.u, self.items, 'SALE10')['promo_discount_minor'], 400)

    def test_best_of_or_stack(self):
        self.promo(code='BIGGER', value=20)              # 8.00 против лояльности 10% = 4.00
        logic.save_settings(self.db, {'loyalty_every_n': 2})
        order(self.db, self.u, self.items)
        q = logic.quote(self.db, self.u, self.items, 'BIGGER')
        self.assertEqual((q['promo_discount_minor'], q['loyalty_discount_minor']), (800, 0))             # лучшая из двух
        self.promo(code='SMALLER', value=5)              # 2.00 против 4.00
        q = logic.quote(self.db, self.u, self.items, 'SMALLER')
        self.assertEqual((q['promo_discount_minor'], q['loyalty_discount_minor'], q['promo_code']), (0, 400, ''))
        logic.save_settings(self.db, {'stack_discounts': True})
        q = logic.quote(self.db, self.u, self.items, 'SMALLER')
        self.assertEqual((q['promo_discount_minor'], q['loyalty_discount_minor'], q['total_minor']), (200, 400, 3400))

    def test_duplicate_code_and_validation(self):
        self.promo()
        with self.assertRaises(ShopError):
            self.promo()
        for bad in (dict(code='ab'), dict(type='x'), dict(value=0), dict(value=101)):
            with self.assertRaises(ShopError):
                self.promo(code=bad.pop('code', 'OK1234'), **bad)


class TestStatuses(unittest.TestCase):
    def test_transitions(self):
        db = new_db()
        u = add_user(db)
        pid = make_product(db)
        order(db, u, [{'product_id': pid, 'qty': 1}])
        with self.assertRaises(ShopError):
            logic.set_status(db, 1, 'done')                 # new -> done нельзя
        for st in ('confirmed', 'shipped', 'done'):
            self.assertEqual(logic.set_status(db, 1, st)['status'], st)
        with self.assertRaises(ShopError):
            logic.set_status(db, 1, 'cancelled')            # выполненный не отменить
        with self.assertRaises(ShopError):
            logic.set_status(db, 999, 'confirmed')

    def test_double_cancel_does_not_double_restock(self):
        db = new_db()
        u = add_user(db)
        pid = make_product(db, stock=3)
        order(db, u, [{'product_id': pid, 'qty': 2}])
        logic.set_status(db, 1, 'cancelled')
        with self.assertRaises(ShopError):
            logic.set_status(db, 1, 'cancelled')
        self.assertEqual(db.conn().execute('SELECT stock FROM products').fetchone()[0], 3)


class TestAdminData(unittest.TestCase):
    def test_flavor_sync_and_validation(self):
        db = new_db()
        pid = make_product(db, flavors=[{'name': 'A', 'stock': 1}, {'name': 'B', 'stock': 1}])
        fl = db.conn().execute('SELECT id, name FROM flavors ORDER BY id').fetchall()
        logic.save_product(db, dict(id=pid, name='Новый', price_minor=1000, flavors=[{'id': fl[0]['id'], 'name': 'A2', 'stock': 9}, {'name': 'C', 'stock': 2}]))
        names = [r['name'] for r in db.conn().execute('SELECT name FROM flavors ORDER BY sort')]
        self.assertEqual(names, ['A2', 'C'])                # B удалён, A переименован, C добавлен
        for bad in (dict(name=''), dict(price_minor=-1), dict(image='http://evil/x.png'), dict(category_id=999)):
            d = dict(name='X', price_minor=100)
            d.update(bad)
            with self.assertRaises(ShopError):
                logic.save_product(db, d)

    def test_delete_product_keeps_order_history(self):
        db = new_db()
        u = add_user(db)
        pid = make_product(db)
        order(db, u, [{'product_id': pid, 'qty': 1}])
        logic.delete_product(db, pid)
        self.assertEqual(logic.get_order(db, 1)['items'][0]['name'], 'Товар')


class TestHTTP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp = tempfile.mkdtemp()
        cls.cfg = Config(env={'BOT_TOKEN': TOKEN, 'ADMIN_IDS': '1', 'DB_PATH': os.path.join(tmp, 's.db'), 'UPLOAD_DIR': os.path.join(tmp, 'up'), 'BOT_POLLING': '0'}, env_file='/nonexistent')
        cls.app = App(cls.cfg)
        cls.srv = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(cls.app))
        cls.srv.daemon_threads = True
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.pid = make_product(cls.app.db, stock=5)

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def req(self, method, path, body=None, uid=None, headers=None, raw=None):
        h = {'Content-Type': 'application/json'}
        if uid:
            h['X-Tg-Init-Data'] = auth.make_init_data({'id': uid, 'first_name': 'T', 'username': 'user%d' % uid}, TOKEN)
        h.update(headers or {})
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        r = urllib.request.Request('http://127.0.0.1:%d%s' % (self.port, path), data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(r) as resp:
                return resp.status, resp.read(), resp.headers
        except urllib.error.HTTPError as e:
            return e.code, e.read(), e.headers

    def j(self, *a, **k):
        s, b, _ = self.req(*a, **k)
        return s, json.loads(b)

    def test_auth_required_and_dev_header_ignored(self):
        self.assertEqual(self.j('GET', '/api/catalog')[0], 401)
        self.assertEqual(self.j('GET', '/api/catalog', headers={'X-Dev-User': '1'})[0], 401)       # DEV_MODE выключен
        self.assertEqual(self.j('GET', '/api/catalog', headers={'X-Tg-Init-Data': 'hash=1&auth_date=1'})[0], 401)
        self.assertEqual(self.j('GET', '/api/config')[0], 200)

    def test_admin_only_for_admin_ids(self):
        self.assertEqual(self.j('GET', '/api/admin/summary', uid=2)[0], 403)
        self.assertEqual(self.j('POST', '/api/admin/settings', {'shop_name': 'Hack'}, uid=2)[0], 403)
        s, d = self.j('GET', '/api/admin/summary', uid=1)
        self.assertEqual(s, 200)
        self.assertIn('settings', d)

    def test_full_customer_flow(self):
        uid = 77
        self.assertEqual(self.j('POST', '/api/order', dict(items=[{'product_id': self.pid, 'qty': 1}], **CONTACT), uid=uid)[0], 403)     # сначала 18+
        self.assertEqual(self.j('POST', '/api/age-confirm', {}, uid=uid)[0], 200)
        s, d = self.j('POST', '/api/order', dict(items=[{'product_id': self.pid, 'qty': 2}], **CONTACT), uid=uid)
        self.assertEqual(s, 200, d)
        self.assertEqual(d['order']['total_minor'], 4000)
        s, me = self.j('GET', '/api/me', uid=uid)
        self.assertEqual((me['loyalty']['filled'], len(me['orders']), me['is_admin']), (1, 1, False))
        s, d = self.j('POST', '/api/order', dict(items=[{'product_id': self.pid, 'qty': 9}], **CONTACT), uid=uid)
        self.assertEqual((s, d['error']['code']), (409, 'stock'))

    def test_customer_cannot_see_others_orders(self):
        self.j('POST', '/api/age-confirm', {}, uid=81)
        self.j('POST', '/api/order', dict(items=[{'product_id': self.pid, 'qty': 1}], **CONTACT), uid=81)
        self.assertEqual(self.j('GET', '/api/me', uid=82)[1]['orders'], [])

    def test_upload_checks(self):
        png = base64.b64encode(b'\x89PNG\r\n\x1a\n' + b'0' * 100).decode()
        s, d = self.j('POST', '/api/admin/upload', {'data': png}, uid=1)
        self.assertEqual(s, 200)
        st, body, hd = self.req('GET', d['path'])
        self.assertEqual((st, hd['Content-Type']), (200, 'image/png'))
        self.assertEqual(self.j('POST', '/api/admin/upload', {'data': base64.b64encode(b'<script>alert(1)</script>').decode()}, uid=1)[0], 400)
        self.assertEqual(self.j('POST', '/api/admin/upload', {'data': base64.b64encode(b'\x89PNG\r\n\x1a\n' + b'0' * (3 * 1024 * 1024)).decode()}, uid=1)[0], 400)
        self.assertEqual(self.j('POST', '/api/admin/upload', {'data': png}, uid=2)[0], 403)

    def test_static_safety(self):
        self.assertEqual(self.req('GET', '/')[0], 200)
        self.assertEqual(self.req('GET', '/style.css')[0], 200)
        for p in ('/../app/auth.py', '/..%2fconfig.py', '/auth.py', '/uploads/../../etc/passwd', '/uploads/x.html', '/.env', '/app/static/index.html'):
            self.assertEqual(self.req('GET', p)[0], 404, p)
        _, _, hd = self.req('GET', '/')
        self.assertIn('frame-ancestors', hd['Content-Security-Policy'])
        self.assertEqual(hd['X-Content-Type-Options'], 'nosniff')

    def test_bad_json_and_errors_hide_internals(self):
        self.assertEqual(self.req('POST', '/api/quote', raw=b'{oops', uid=5)[0], 400)
        self.assertEqual(self.req('POST', '/api/quote', raw=b'[1,2]', uid=5)[0], 400)
        self.assertEqual(self.j('GET', '/api/nothing', uid=5)[0], 404)

    def test_xss_text_is_stored_raw_and_escaped_in_notifications(self):
        import html
        from app.notify import Bot
        o = {'id': 1, 'items': [{'name': '<b>x</b>', 'flavor_name': '', 'qty': 1, 'price_minor': 100}], 'promo_discount_minor': 0, 'promo_code': '',
             'loyalty_discount_minor': 0, 'delivery_minor': 0, 'total_minor': 100}
        t = Bot('').order_text(o, 'BYN')
        self.assertIn(html.escape('<b>x</b>'), t)
        self.assertNotIn('<b>x</b> ×', t)


if __name__ == '__main__':
    unittest.main(verbosity=1)
