"""Уведомления через Bot API и простой бот на long-polling (кнопка «Открыть магазин»). Без внешних библиотек."""
import html
import json
import threading
import time
import urllib.request

from . import logic


class Bot:
    def __init__(self, token, public_url='', log=print):
        self.token, self.public_url, self.log = token, public_url, log

    def call(self, method, payload, timeout=40):
        if not self.token:
            return None
        req = urllib.request.Request('https://api.telegram.org/bot%s/%s' % (self.token, method),
                                     data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())

    def send(self, chat_id, text, markup=None):
        """Отправка в фоне: сбой Telegram не должен ломать заказ."""
        if not self.token:
            return

        def run():
            try:
                p = {'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML', 'disable_web_page_preview': True}
                if markup:
                    p['reply_markup'] = markup
                self.call('sendMessage', p, timeout=15)
            except Exception as e:
                self.log('notify error: %s' % e)
        threading.Thread(target=run, daemon=True).start()

    def webapp_markup(self, text='Открыть магазин'):
        if not self.public_url.startswith('https://'):
            return None
        return {'inline_keyboard': [[{'text': text, 'web_app': {'url': self.public_url}}]]}

    # --- сообщения
    def order_text(self, o, cur):
        lines = ['%s × %d — %s' % (html.escape(i['name'] + (' (' + i['flavor_name'] + ')' if i['flavor_name'] else '')), i['qty'],
                                   logic.fmt_money(i['price_minor'] * i['qty'], cur)) for i in o['items']]
        disc = []
        if o['promo_discount_minor']:
            disc.append('Промокод %s: −%s' % (html.escape(o['promo_code']), logic.fmt_money(o['promo_discount_minor'], cur)))
        if o['loyalty_discount_minor']:
            disc.append('Скидка за лояльность: −%s' % logic.fmt_money(o['loyalty_discount_minor'], cur))
        return '\n'.join(lines + disc + ['Доставка: %s' % logic.fmt_money(o['delivery_minor'], cur), '<b>Итого: %s</b>' % logic.fmt_money(o['total_minor'], cur)])

    def notify_new_order(self, o, admin_ids, extra_chats, cur):
        tg = ('@' + o['tg_username']) if o['tg_username'] else '—'
        text = ('🆕 <b>Заказ №%d</b>\n%s\n\n📞 %s\n🏙 %s\n📍 %s\n✈️ %s\n💬 %s\nID клиента: %d' %
                (o['id'], self.order_text(o, cur), html.escape(o['phone']), html.escape(o['city']), html.escape(o['address']),
                 html.escape(tg), html.escape(o['comment'] or '—'), o['user_id']))
        for cid in set(admin_ids) | set(extra_chats):
            self.send(cid, text)

    def notify_status(self, o):
        msg = {'confirmed': 'подтверждён ✅', 'shipped': 'отправлен 🚚', 'done': 'выполнен 🎉', 'cancelled': 'отменён ❌'}.get(o['status'])
        if msg:
            self.send(o['user_id'], 'Заказ №%d %s' % (o['id'], msg))

    # --- бот: на любое сообщение отвечает кнопкой открытия магазина
    def start_polling(self, shop_name_getter):
        if not self.token:
            return

        def loop():
            offset = 0
            try:
                mk = self.webapp_markup()
                if mk:
                    self.call('setChatMenuButton', {'menu_button': {'type': 'web_app', 'text': 'Магазин', 'web_app': {'url': self.public_url}}}, timeout=15)
            except Exception as e:
                self.log('menu button error: %s' % e)
            while True:
                try:
                    res = self.call('getUpdates', {'offset': offset, 'timeout': 25, 'allowed_updates': ['message']}, timeout=40)
                    for u in (res or {}).get('result', []):
                        offset = u['update_id'] + 1
                        m = u.get('message') or {}
                        if m.get('chat', {}).get('type') == 'private' and m.get('text'):
                            mk = self.webapp_markup()
                            text = 'Добро пожаловать в <b>%s</b>!' % html.escape(shop_name_getter())
                            self.send(m['chat']['id'], text + ('\nНажмите кнопку ниже, чтобы открыть магазин.' if mk else '\n(Укажите PUBLIC_URL с https в .env, чтобы здесь появилась кнопка магазина.)'), mk)
                except Exception as e:
                    self.log('polling error: %s' % e)
                    time.sleep(5)
        threading.Thread(target=loop, daemon=True).start()
