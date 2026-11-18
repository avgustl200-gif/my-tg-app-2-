"""Демо-данные: заменить своими через админку (раздел «Товары»)."""
from . import logic


def seed(db):
    if db.conn().execute('SELECT COUNT(*) FROM products').fetchone()[0]:
        return
    liq = logic.save_category(db, {'name': 'Жидкости', 'sort': 1})
    cart = logic.save_category(db, {'name': 'Картриджи', 'sort': 2})
    dev = logic.save_category(db, {'name': 'Устройства', 'sort': 3})
    logic.save_product(db, {'category_id': liq, 'name': 'Демо-жидкость Ягодный лёд (30 мл)', 'description': 'Пример товара со вкусами. Замените своим в админке.',
                            'price_minor': 2000, 'old_price_minor': 2500, 'badge': 'SALE', 'sort': 1,
                            'flavors': [{'name': 'Малина-лёд', 'stock': 8}, {'name': 'Киви-клубника', 'stock': 3}, {'name': 'Черника', 'stock': 1}]})
    logic.save_product(db, {'category_id': liq, 'name': 'Демо-жидкость Цитрус (30 мл)', 'price_minor': 2500, 'badge': 'NEW', 'sort': 2,
                            'flavors': [{'name': 'Лимон-лайм', 'stock': 5}, {'name': 'Грейпфрут', 'stock': 5}]})
    logic.save_product(db, {'category_id': cart, 'name': 'Демо-картридж (2 шт.)', 'price_minor': 1500, 'stock': 12, 'sort': 3})
    logic.save_product(db, {'category_id': dev, 'name': 'Демо-устройство Pod', 'description': 'Пример товара без вкусов: только общий остаток.',
                            'price_minor': 4500, 'stock': 4, 'sort': 4})
