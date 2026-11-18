"""Бизнес-логика: витрина, остатки, цены, промокоды, лояльность, заказы. Цены берутся только из базы."""
import re
import time

STATUSES = ('new', 'confirmed', 'shipped', 'done', 'cancelled')
STATUS_RU = {'new': 'Новый', 'confirmed': 'Подтверждён', 'shipped': 'Отправлен', 'done': 'Выполнен', 'cancelled': 'Отменён'}
TRANSITIONS = {'new': ('confirmed', 'cancelled'), 'confirmed': ('shipped', 'done', 'cancelled'),
               'shipped': ('done', 'cancelled'), 'done': (), 'cancelled': ()}
MAX_QTY = 99


class ShopError(Exception):
    def __init__(self, message, code='bad_request', status=400):
        super().__init__(message)
        self.message, self.code, self.status = message, code, status


def now():
    return int(time.time())


# ---------------------------------------------------------------- витрина
def _product_dict(p, flavors, admin=False):
    fl = [dict(id=f['id'], name=f['name'], stock=f['stock'], active=bool(f['active'])) for f in flavors]
    has_flavors = len(fl) > 0
    if admin:
        stock = sum(f['stock'] for f in fl if f['active']) if has_flavors else p['stock']
    else:
        fl = [f for f in fl if f['active'] and f['stock'] > 0]          # проданные вкусы пропадают сами
        stock = sum(f['stock'] for f in fl) if has_flavors else p['stock']
    return dict(id=p['id'], category_id=p['category_id'], name=p['name'], description=p['description'],
                price_minor=p['price_minor'], old_price_minor=p['old_price_minor'], badge=p['badge'], image=p['image'],
                has_flavors=has_flavors, flavors=fl, stock=stock, in_stock=stock > 0, active=bool(p['active']), sort=p['sort'])


def catalog(db, admin=False):
    c = db.conn()
    cats = [dict(r) for r in c.execute('SELECT id, name, sort, active FROM categories ORDER BY sort, id')]
    if not admin:
        cats = [x for x in cats if x['active']]
    fl_by = {}
    for f in c.execute('SELECT * FROM flavors ORDER BY sort, id'):
        fl_by.setdefault(f['product_id'], []).append(f)
    prods = []
    for p in c.execute('SELECT * FROM products ORDER BY sort, id DESC'):
        if not admin and not p['active']:
            continue
        d = _product_dict(p, fl_by.get(p['id'], []), admin)
        if not admin and not d['in_stock']:          # проданный товар исчезает с витрины
            continue
        prods.append(d)
    if not admin:
        ids = {x['id'] for x in cats}
        prods = [p for p in prods if p['category_id'] in ids or p['category_id'] is None]
    return dict(categories=cats, products=prods)


# ---------------------------------------------------------------- расчёт корзины
def _int(v, lo, hi, what):
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise ShopError('Некорректное значение: %s' % what)
    if n < lo or n > hi:
        raise ShopError('Некорректное значение: %s' % what)
    return n


def user_orders_count(c, user_id):
    return c.execute("SELECT COUNT(*) FROM orders WHERE user_id=? AND status!='cancelled'", (user_id,)).fetchone()[0]


def loyalty_state(settings, purchases):
    n = max(1, int(settings['loyalty_every_n']))
    enabled = bool(settings['loyalty_enabled'])
    filled = purchases % n
    return dict(enabled=enabled, every_n=n, purchases=purchases, filled=filled,
                remaining=(n - 1 - filled), next_is_discount=enabled and (purchases + 1) % n == 0,
                type=settings['loyalty_type'], value=settings['loyalty_value'])


def _promo_check(c, code, user_id, subtotal):
    row = c.execute('SELECT * FROM promo_codes WHERE code=?', (code.strip().upper(),)).fetchone()
    if not row or not row['active']:
        raise ShopError('Такого промокода нет', 'promo_invalid')
    if row['expires_at'] and row['expires_at'] < now():
        raise ShopError('Срок промокода истёк', 'promo_expired')
    if row['max_uses'] and row['used_count'] >= row['max_uses']:
        raise ShopError('Промокод уже использован максимальное число раз', 'promo_used_up')
    if row['per_user_limit']:
        used = c.execute('SELECT COUNT(*) FROM promo_uses WHERE promo_id=? AND user_id=?', (row['id'], user_id)).fetchone()[0]
        if used >= row['per_user_limit']:
            raise ShopError('Вы уже использовали этот промокод', 'promo_user_limit')
    if subtotal < row['min_order_minor']:
        raise ShopError('Промокод действует от суммы %s' % fmt_money(row['min_order_minor']), 'promo_min')
    disc = subtotal * row['value'] // 100 if row['type'] == 'percent' else row['value']
    return row, max(0, min(disc, subtotal))


def fmt_money(minor, cur='BYN'):
    s = '%d.%02d' % divmod(int(minor), 100)
    if s.endswith('.00'):
        s = s[:-3]
    return '%s %s' % (s, cur)


def quote(db, user_id, items, promo_code='', strict=False):
    """Пересчёт корзины по данным из базы. strict=True — любое несоответствие остатку = ошибка (для заказа)."""
    c = db.conn()
    st = db.settings()
    if not isinstance(items, list) or len(items) > 60:
        raise ShopError('Корзина пуста или слишком большая')
    merged = {}
    for it in items:
        if not isinstance(it, dict):
            raise ShopError('Некорректная корзина')
        pid = _int(it.get('product_id'), 1, 2 ** 31, 'товар')
        fid = it.get('flavor_id')
        fid = _int(fid, 1, 2 ** 31, 'вкус') if fid not in (None, '', 0) else None
        qty = _int(it.get('qty', 1), 1, MAX_QTY, 'количество')
        k = (pid, fid)
        merged[k] = min(MAX_QTY, merged.get(k, 0) + qty)
    lines, problems = [], []
    for (pid, fid), qty in merged.items():
        p = c.execute('SELECT * FROM products WHERE id=?', (pid,)).fetchone()
        if not p or not p['active']:
            problems.append(dict(product_id=pid, flavor_id=fid, reason='Товар больше не продаётся'))
            continue
        has_fl = c.execute('SELECT COUNT(*) FROM flavors WHERE product_id=?', (pid,)).fetchone()[0] > 0
        fname, stock = '', p['stock']
        if has_fl:
            f = c.execute('SELECT * FROM flavors WHERE id=? AND product_id=?', (fid, pid)).fetchone() if fid else None
            if not f:
                problems.append(dict(product_id=pid, flavor_id=fid, reason='Выберите вкус'))
                continue
            if not f['active']:
                problems.append(dict(product_id=pid, flavor_id=fid, reason='Вкус «%s» закончился' % f['name']))
                continue
            fname, stock = f['name'], f['stock']
        elif fid:
            fid = None
        if stock <= 0:
            problems.append(dict(product_id=pid, flavor_id=fid, reason='«%s» закончился' % (p['name'] + (' — ' + fname if fname else ''))))
            continue
        if qty > stock:
            problems.append(dict(product_id=pid, flavor_id=fid, reason='В наличии только %d шт.: %s' % (stock, p['name'] + (' — ' + fname if fname else '')), max_qty=stock))
            qty = stock
        lines.append(dict(product_id=pid, flavor_id=fid, name=p['name'], flavor_name=fname, price_minor=p['price_minor'],
                          old_price_minor=p['old_price_minor'], image=p['image'], qty=qty, stock=stock, sum_minor=p['price_minor'] * qty))
    if strict and problems:
        raise ShopError(problems[0]['reason'], 'stock', 409)
    subtotal = sum(l['sum_minor'] for l in lines)

    # скидки
    promo_row, promo_disc, promo_error = None, 0, ''
    if promo_code and promo_code.strip():
        try:
            promo_row, promo_disc = _promo_check(c, promo_code, user_id, subtotal)
        except ShopError as e:
            if strict:
                raise
            promo_error = e.message
    loy = loyalty_state(st, user_orders_count(c, user_id))
    loy_disc = 0
    if loy['next_is_discount'] and subtotal >= st['loyalty_min_order_minor'] and subtotal > 0:
        v = int(st['loyalty_value'])
        loy_disc = subtotal * v // 100 if st['loyalty_type'] == 'percent' else v
        loy_disc = max(0, min(loy_disc, subtotal))
    applied_promo, applied_loy = promo_disc, loy_disc
    if not st['stack_discounts'] and promo_disc and loy_disc:
        if promo_disc >= loy_disc:
            applied_loy = 0
        else:
            applied_promo, promo_row = 0, None
    applied_loy = min(applied_loy, subtotal - applied_promo)
    after = subtotal - applied_promo - applied_loy
    delivery = int(st['delivery_fee_minor'])
    if st['free_delivery_from_minor'] and after >= st['free_delivery_from_minor']:
        delivery = 0
    if not lines:
        delivery = 0
    return dict(lines=lines, problems=problems, subtotal_minor=subtotal, promo_code=promo_row['code'] if promo_row else '',
                promo_discount_minor=applied_promo, promo_error=promo_error, loyalty_discount_minor=applied_loy,
                loyalty=loy, delivery_minor=delivery, total_minor=after + delivery, min_order_minor=int(st['min_order_minor']),
                _promo_id=promo_row['id'] if promo_row else None)


# ---------------------------------------------------------------- заказ
PHONE_RE = re.compile(r'^\+?[0-9][0-9\s\-()]{6,19}$')
TG_RE = re.compile(r'^[A-Za-z0-9_]{5,32}$')


def clean_contact(d):
    phone = str(d.get('phone', '')).strip()
    city = ' '.join(str(d.get('city', '')).split())
    address = ' '.join(str(d.get('address', '')).split())
    tg = str(d.get('tg_username', '')).strip().lstrip('@')
    comment = str(d.get('comment', '')).strip()
    if not PHONE_RE.match(phone):
        raise ShopError('Укажите телефон, например +375291234567', 'validation')
    if not (2 <= len(city) <= 60):
        raise ShopError('Укажите город', 'validation')
    if not (5 <= len(address) <= 200):
        raise ShopError('Укажите адрес (улица, дом, квартира)', 'validation')
    if tg and not TG_RE.match(tg):
        raise ShopError('Telegram username: 5–32 символов, латиница, цифры, _', 'validation')
    if len(comment) > 500:
        raise ShopError('Комментарий слишком длинный (до 500 символов)', 'validation')
    return dict(phone=phone, city=city, address=address, tg_username=tg, comment=comment)


def create_order(db, user_id, payload):
    st = db.settings()
    if not st['shop_open']:
        raise ShopError(st['closed_message'] or 'Заказы временно не принимаются', 'closed', 403)
    contact = clean_contact(payload)
    with db.tx() as c:
        u = c.execute('SELECT * FROM users WHERE tg_id=?', (user_id,)).fetchone()
        if not u or u['banned']:
            raise ShopError('Доступ закрыт', 'forbidden', 403)
        if st['age_gate'] and not u['age_confirmed_at']:
            raise ShopError('Подтвердите, что вам есть 18 лет', 'age', 403)
        q = quote(db, user_id, payload.get('items'), payload.get('promo', ''), strict=True)
        if not q['lines']:
            raise ShopError('Корзина пуста')
        if q['subtotal_minor'] < st['min_order_minor']:
            raise ShopError('Минимальная сумма заказа — %s' % fmt_money(st['min_order_minor'], st['currency']), 'min_order')
        ts = now()
        cur = c.execute(
            'INSERT INTO orders(user_id, status, subtotal_minor, promo_code, promo_discount_minor, loyalty_discount_minor, delivery_minor,'
            ' total_minor, phone, city, address, tg_username, comment, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (user_id, 'new', q['subtotal_minor'], q['promo_code'], q['promo_discount_minor'], q['loyalty_discount_minor'], q['delivery_minor'],
             q['total_minor'], contact['phone'], contact['city'], contact['address'], contact['tg_username'], contact['comment'], ts, ts))
        oid = cur.lastrowid
        for l in q['lines']:
            c.execute('INSERT INTO order_items(order_id, product_id, flavor_id, name, flavor_name, price_minor, qty) VALUES (?,?,?,?,?,?,?)',
                      (oid, l['product_id'], l['flavor_id'], l['name'], l['flavor_name'], l['price_minor'], l['qty']))
            if l['flavor_id']:
                c.execute('UPDATE flavors SET stock = stock - ? WHERE id=? AND stock >= ?', (l['qty'], l['flavor_id'], l['qty']))
            else:
                c.execute('UPDATE products SET stock = stock - ? WHERE id=? AND stock >= ?', (l['qty'], l['product_id'], l['qty']))
            if c.execute('SELECT changes()').fetchone()[0] != 1:
                raise ShopError('Товар только что закончился', 'stock', 409)   # откат всей транзакции
        if q['_promo_id']:
            c.execute('UPDATE promo_codes SET used_count = used_count + 1 WHERE id=?', (q['_promo_id'],))
            c.execute('INSERT INTO promo_uses(promo_id, user_id, order_id) VALUES (?,?,?)', (q['_promo_id'], user_id, oid))
        c.execute('UPDATE users SET phone=?, city=?, address=? WHERE tg_id=?', (contact['phone'], contact['city'], contact['address'], user_id))
    return get_order(db, oid)


def _order_dict(c, o):
    d = dict(o)
    d['status_ru'] = STATUS_RU.get(o['status'], o['status'])
    d['items'] = [dict(r) for r in c.execute('SELECT product_id, flavor_id, name, flavor_name, price_minor, qty FROM order_items WHERE order_id=? ORDER BY id', (o['id'],))]
    return d


def get_order(db, oid):
    c = db.conn()
    o = c.execute('SELECT * FROM orders WHERE id=?', (oid,)).fetchone()
    return _order_dict(c, o) if o else None


def user_orders(db, user_id, limit=50):
    c = db.conn()
    return [_order_dict(c, o) for o in c.execute('SELECT * FROM orders WHERE user_id=? ORDER BY id DESC LIMIT ?', (user_id, limit))]


def list_orders(db, status='', limit=100, offset=0):
    c = db.conn()
    if status and status in STATUSES:
        rows = c.execute('SELECT o.*, u.username AS u_username, u.first_name AS u_first_name FROM orders o LEFT JOIN users u ON u.tg_id=o.user_id WHERE o.status=? ORDER BY o.id DESC LIMIT ? OFFSET ?', (status, limit, offset)).fetchall()
    else:
        rows = c.execute('SELECT o.*, u.username AS u_username, u.first_name AS u_first_name FROM orders o LEFT JOIN users u ON u.tg_id=o.user_id ORDER BY o.id DESC LIMIT ? OFFSET ?', (limit, offset)).fetchall()
    return [_order_dict(c, o) for o in rows]


def set_status(db, oid, new):
    if new not in STATUSES:
        raise ShopError('Неизвестный статус')
    with db.tx() as c:
        o = c.execute('SELECT * FROM orders WHERE id=?', (oid,)).fetchone()
        if not o:
            raise ShopError('Заказ не найден', 'not_found', 404)
        if new not in TRANSITIONS[o['status']]:
            raise ShopError('Нельзя перевести «%s» в «%s»' % (STATUS_RU[o['status']], STATUS_RU[new]), 'transition', 409)
        if new == 'cancelled':                                  # вернуть остатки и промокод
            for it in c.execute('SELECT * FROM order_items WHERE order_id=?', (oid,)).fetchall():
                if it['flavor_id']:
                    c.execute('UPDATE flavors SET stock = stock + ? WHERE id=?', (it['qty'], it['flavor_id']))
                elif it['product_id']:
                    c.execute('UPDATE products SET stock = stock + ? WHERE id=?', (it['qty'], it['product_id']))
            for pu in c.execute('SELECT promo_id FROM promo_uses WHERE order_id=?', (oid,)).fetchall():
                c.execute('UPDATE promo_codes SET used_count = MAX(0, used_count - 1) WHERE id=?', (pu['promo_id'],))
            c.execute('DELETE FROM promo_uses WHERE order_id=?', (oid,))
        c.execute('UPDATE orders SET status=?, updated_at=? WHERE id=?', (new, now(), oid))
    return get_order(db, oid)


# ---------------------------------------------------------------- админка: товары, категории, промокоды
def _s(v, lo, hi, what):
    v = ' '.join(str(v or '').split()) if what != 'описание' else str(v or '').strip()
    if not (lo <= len(v) <= hi):
        raise ShopError('Поле «%s»: от %d до %d символов' % (what, lo, hi), 'validation')
    return v


def save_product(db, d):
    name = _s(d.get('name'), 1, 120, 'название')
    desc = _s(d.get('description', ''), 0, 2000, 'описание')
    price = _int(d.get('price_minor'), 0, 10 ** 9, 'цена')
    old = _int(d.get('old_price_minor') or 0, 0, 10 ** 9, 'старая цена')
    badge = _s(d.get('badge', ''), 0, 12, 'ярлык')
    stock = _int(d.get('stock') or 0, 0, 10 ** 6, 'остаток')
    active = 1 if d.get('active', True) else 0
    cat = d.get('category_id')
    cat = _int(cat, 1, 2 ** 31, 'категория') if cat not in (None, '', 0) else None
    image = str(d.get('image', ''))
    if image and not re.match(r'^/uploads/[a-f0-9]{16,}\.(png|jpg|webp|gif)$', image):
        raise ShopError('Неверный путь картинки', 'validation')
    sort = _int(d.get('sort') or 0, -10 ** 6, 10 ** 6, 'порядок')
    flavors = d.get('flavors') or []
    if not isinstance(flavors, list) or len(flavors) > 200:
        raise ShopError('Слишком много вкусов')
    with db.tx() as c:
        if cat and not c.execute('SELECT 1 FROM categories WHERE id=?', (cat,)).fetchone():
            raise ShopError('Категория не найдена')
        pid = d.get('id')
        if pid:
            pid = _int(pid, 1, 2 ** 31, 'id')
            if not c.execute('SELECT 1 FROM products WHERE id=?', (pid,)).fetchone():
                raise ShopError('Товар не найден', 'not_found', 404)
            c.execute('UPDATE products SET category_id=?, name=?, description=?, price_minor=?, old_price_minor=?, badge=?, image=?, stock=?, active=?, sort=? WHERE id=?',
                      (cat, name, desc, price, old, badge, image, stock, active, sort, pid))
        else:
            pid = c.execute('INSERT INTO products(category_id, name, description, price_minor, old_price_minor, badge, image, stock, active, sort, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                            (cat, name, desc, price, old, badge, image, stock, active, sort, now())).lastrowid
        keep = []
        for i, f in enumerate(flavors):
            fname = _s(f.get('name'), 1, 80, 'название вкуса')
            fstock = _int(f.get('stock') or 0, 0, 10 ** 6, 'остаток вкуса')
            fact = 1 if f.get('active', True) else 0
            fid = f.get('id')
            if fid and c.execute('SELECT 1 FROM flavors WHERE id=? AND product_id=?', (int(fid), pid)).fetchone():
                c.execute('UPDATE flavors SET name=?, stock=?, active=?, sort=? WHERE id=?', (fname, fstock, fact, i, int(fid)))
                keep.append(int(fid))
            else:
                keep.append(c.execute('INSERT INTO flavors(product_id, name, stock, active, sort) VALUES (?,?,?,?,?)', (pid, fname, fstock, fact, i)).lastrowid)
        if keep:
            c.execute('DELETE FROM flavors WHERE product_id=? AND id NOT IN (%s)' % ','.join('?' * len(keep)), [pid] + keep)
        else:
            c.execute('DELETE FROM flavors WHERE product_id=?', (pid,))
    return pid


def delete_product(db, pid):
    with db.tx() as c:
        c.execute('DELETE FROM products WHERE id=?', (pid,))


def save_category(db, d):
    name = _s(d.get('name'), 1, 60, 'название')
    sort = _int(d.get('sort') or 0, -10 ** 6, 10 ** 6, 'порядок')
    active = 1 if d.get('active', True) else 0
    with db.tx() as c:
        if d.get('id'):
            c.execute('UPDATE categories SET name=?, sort=?, active=? WHERE id=?', (name, sort, active, _int(d['id'], 1, 2 ** 31, 'id')))
            return int(d['id'])
        return c.execute('INSERT INTO categories(name, sort, active) VALUES (?,?,?)', (name, sort, active)).lastrowid


def delete_category(db, cid):
    with db.tx() as c:
        c.execute('DELETE FROM categories WHERE id=?', (cid,))


def list_promos(db):
    return [dict(r) for r in db.conn().execute('SELECT * FROM promo_codes ORDER BY id DESC')]


def save_promo(db, d):
    code = re.sub(r'[^A-Z0-9_\-]', '', str(d.get('code', '')).upper())
    if not (3 <= len(code) <= 32):
        raise ShopError('Код: 3–32 символа, латиница, цифры, - и _', 'validation')
    typ = d.get('type')
    if typ not in ('percent', 'fixed'):
        raise ShopError('Тип скидки: percent или fixed')
    value = _int(d.get('value'), 1, 10 ** 9, 'размер скидки')
    if typ == 'percent' and value > 100:
        raise ShopError('Процент скидки не больше 100')
    vals = (code, typ, value, _int(d.get('min_order_minor') or 0, 0, 10 ** 9, 'минимальная сумма'), _int(d.get('max_uses') or 0, 0, 10 ** 7, 'лимит'),
            _int(d.get('per_user_limit') if d.get('per_user_limit') not in (None, '') else 1, 0, 10 ** 5, 'лимит на человека'),
            _int(d.get('expires_at') or 0, 0, 2 ** 40, 'срок'), 1 if d.get('active', True) else 0, _s(d.get('note', ''), 0, 200, 'заметка'))
    with db.tx() as c:
        try:
            if d.get('id'):
                c.execute('UPDATE promo_codes SET code=?, type=?, value=?, min_order_minor=?, max_uses=?, per_user_limit=?, expires_at=?, active=?, note=? WHERE id=?',
                          vals + (_int(d['id'], 1, 2 ** 31, 'id'),))
                return int(d['id'])
            return c.execute('INSERT INTO promo_codes(code, type, value, min_order_minor, max_uses, per_user_limit, expires_at, active, note) VALUES (?,?,?,?,?,?,?,?,?)', vals).lastrowid
        except Exception as e:
            if 'UNIQUE' in str(e):
                raise ShopError('Такой промокод уже есть', 'validation')
            raise


def delete_promo(db, pid):
    with db.tx() as c:
        c.execute('DELETE FROM promo_codes WHERE id=?', (pid,))


SETTING_VALIDATORS = {
    'shop_name': lambda v: _s(v, 1, 60, 'название магазина'),
    'currency': lambda v: _s(v, 1, 6, 'валюта'),
    'welcome': lambda v: _s(v, 0, 400, 'приветствие'),
    'closed_message': lambda v: _s(v, 0, 300, 'сообщение о закрытии'),
    'support_contact': lambda v: _s(v, 0, 80, 'контакт'),
    'age_gate': bool, 'shop_open': bool, 'loyalty_enabled': bool, 'stack_discounts': bool,
    'loyalty_every_n': lambda v: _int(v, 2, 100, 'каждая N-я покупка'),
    'loyalty_type': lambda v: v if v in ('percent', 'fixed') else (_ for _ in ()).throw(ShopError('Тип скидки: percent или fixed')),
    'loyalty_value': lambda v: _int(v, 1, 10 ** 9, 'размер скидки'),
    'loyalty_min_order_minor': lambda v: _int(v, 0, 10 ** 9, 'минимальная сумма'),
    'min_order_minor': lambda v: _int(v, 0, 10 ** 9, 'минимальная сумма заказа'),
    'delivery_fee_minor': lambda v: _int(v, 0, 10 ** 9, 'доставка'),
    'free_delivery_from_minor': lambda v: _int(v, 0, 10 ** 9, 'бесплатная доставка от'),
    'low_stock_threshold': lambda v: _int(v, 0, 1000, 'порог'),
    'notify_chat_ids': lambda v: [_int(x, -10 ** 15, 10 ** 15, 'chat id') for x in (v or [])][:10],
}


def save_settings(db, patch):
    clean = {}
    for k, v in patch.items():
        if k not in SETTING_VALIDATORS:
            continue
        clean[k] = SETTING_VALIDATORS[k](v)
    if clean.get('loyalty_type') == 'percent' or (db.settings()['loyalty_type'] == 'percent' and 'loyalty_type' not in clean):
        val = clean.get('loyalty_value', db.settings()['loyalty_value'])
        if val > 100:
            raise ShopError('Процент скидки не больше 100')
    db.set_settings(clean)
    return db.settings()


def stats(db):
    c = db.conn()
    one = lambda q, *a: c.execute(q, a).fetchone()[0]
    return dict(orders_new=one("SELECT COUNT(*) FROM orders WHERE status='new'"), orders_total=one('SELECT COUNT(*) FROM orders'),
                revenue_minor=one("SELECT COALESCE(SUM(total_minor),0) FROM orders WHERE status IN ('confirmed','shipped','done')"),
                users=one('SELECT COUNT(*) FROM users'),
                out_of_stock=one('SELECT COUNT(*) FROM products WHERE active=1') - len(catalog(db)['products']))
