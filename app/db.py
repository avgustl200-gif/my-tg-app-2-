"""SQLite: схема, настройки магазина, подключение на поток. Деньги — целые «копейки» (100 = 1.00 BYN)."""
import json
import sqlite3
import threading

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS categories (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, sort INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS products (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL,
  name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
  price_minor INTEGER NOT NULL, old_price_minor INTEGER NOT NULL DEFAULT 0,
  badge TEXT NOT NULL DEFAULT '', image TEXT NOT NULL DEFAULT '',
  stock INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1, sort INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS flavors (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
  name TEXT NOT NULL, stock INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1, sort INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS users (
  tg_id INTEGER PRIMARY KEY, username TEXT NOT NULL DEFAULT '', first_name TEXT NOT NULL DEFAULT '',
  phone TEXT NOT NULL DEFAULT '', city TEXT NOT NULL DEFAULT '', address TEXT NOT NULL DEFAULT '',
  age_confirmed_at INTEGER NOT NULL DEFAULT 0, banned INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL, last_seen INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS promo_codes (
  id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT NOT NULL UNIQUE,
  type TEXT NOT NULL CHECK (type IN ('percent','fixed')), value INTEGER NOT NULL,
  min_order_minor INTEGER NOT NULL DEFAULT 0, max_uses INTEGER NOT NULL DEFAULT 0,
  per_user_limit INTEGER NOT NULL DEFAULT 1, used_count INTEGER NOT NULL DEFAULT 0,
  expires_at INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1, note TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS orders (
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(tg_id),
  status TEXT NOT NULL DEFAULT 'new', subtotal_minor INTEGER NOT NULL,
  promo_code TEXT NOT NULL DEFAULT '', promo_discount_minor INTEGER NOT NULL DEFAULT 0,
  loyalty_discount_minor INTEGER NOT NULL DEFAULT 0, delivery_minor INTEGER NOT NULL DEFAULT 0,
  total_minor INTEGER NOT NULL, phone TEXT NOT NULL, city TEXT NOT NULL, address TEXT NOT NULL,
  tg_username TEXT NOT NULL DEFAULT '', comment TEXT NOT NULL DEFAULT '',
  created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS order_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
  product_id INTEGER, flavor_id INTEGER, name TEXT NOT NULL, flavor_name TEXT NOT NULL DEFAULT '',
  price_minor INTEGER NOT NULL, qty INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS promo_uses (
  id INTEGER PRIMARY KEY AUTOINCREMENT, promo_id INTEGER NOT NULL REFERENCES promo_codes(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL, order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE);
CREATE INDEX IF NOT EXISTS idx_orders_user ON orders(user_id);
CREATE INDEX IF NOT EXISTS idx_flavors_product ON flavors(product_id);
CREATE INDEX IF NOT EXISTS idx_uses_promo ON promo_uses(promo_id, user_id);
"""

# Всё, что владелец меняет в админке (раздел «Настройки»)
DEFAULT_SETTINGS = {
    'shop_name': 'Vapeliquid',
    'currency': 'BYN',
    'welcome': 'Добро пожаловать! Выберите товар и оформите заказ — мы свяжемся с вами.',
    'age_gate': True,                 # окно «Мне есть 18 лет» при первом входе (рекомендуется)
    'shop_open': True,                # выключатель приёма заказов
    'closed_message': 'Сейчас заказы не принимаются. Загляните позже.',
    'loyalty_enabled': True,
    'loyalty_every_n': 5,             # каждая N-я покупка со скидкой
    'loyalty_type': 'percent',        # percent | fixed
    'loyalty_value': 10,              # % или копейки (для fixed)
    'loyalty_min_order_minor': 0,
    'stack_discounts': False,         # False: берём лучшую из скидок (промокод/лояльность), True: суммируем
    'min_order_minor': 0,
    'delivery_fee_minor': 0,
    'free_delivery_from_minor': 0,    # 0 = бесплатной доставки по сумме нет
    'low_stock_threshold': 3,         # «осталось N» в карточке
    'notify_chat_ids': [],            # дополнительные чаты для уведомлений о заказах
    'support_contact': '',            # например @username менеджера
}


class DB:
    def __init__(self, path):
        self.path = path
        self._local = threading.local()
        c = self.conn()
        c.executescript(SCHEMA)
        for k, v in DEFAULT_SETTINGS.items():
            c.execute('INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)', (k, json.dumps(v, ensure_ascii=False)))

    def conn(self):
        c = getattr(self._local, 'c', None)
        if c is None:
            c = sqlite3.connect(self.path, timeout=30, isolation_level=None, check_same_thread=False)
            c.row_factory = sqlite3.Row
            c.execute('PRAGMA foreign_keys=ON')
            c.execute('PRAGMA journal_mode=WAL')
            c.execute('PRAGMA busy_timeout=30000')
            self._local.c = c
        return c

    # --- настройки
    def settings(self):
        out = dict(DEFAULT_SETTINGS)
        for r in self.conn().execute('SELECT key, value FROM settings'):
            try:
                out[r['key']] = json.loads(r['value'])
            except ValueError:
                pass
        return out

    def set_settings(self, patch):
        c = self.conn()
        for k, v in patch.items():
            c.execute('INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                      (k, json.dumps(v, ensure_ascii=False)))

    # --- транзакция с блокировкой записи (чтобы два заказа не продали один остаток)
    def tx(self):
        return _Tx(self.conn())


class _Tx:
    def __init__(self, c):
        self.c = c

    def __enter__(self):
        self.c.execute('BEGIN IMMEDIATE')
        return self.c

    def __exit__(self, et, ev, tb):
        self.c.execute('ROLLBACK' if et else 'COMMIT')
        return False
