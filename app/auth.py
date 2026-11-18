"""Проверка подписи Telegram WebApp initData (https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app)."""
import hashlib
import hmac
import json
import time
import urllib.parse


class AuthError(Exception):
    pass


def verify_init_data(init_data, bot_token, max_age=86400, now=None):
    if not init_data or not bot_token:
        raise AuthError('нет данных авторизации')
    pairs = urllib.parse.parse_qsl(init_data, keep_blank_values=True)
    d = dict(pairs)
    got = d.pop('hash', None)
    if not got:
        raise AuthError('нет подписи')
    check = '\n'.join('%s=%s' % (k, v) for k, v in sorted(d.items()))
    secret = hmac.new(b'WebAppData', bot_token.encode(), hashlib.sha256).digest()
    calc = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc, got):
        raise AuthError('неверная подпись')
    try:
        auth_date = int(d.get('auth_date', '0'))
    except ValueError:
        raise AuthError('неверная дата')
    now = time.time() if now is None else now
    if auth_date <= 0 or now - auth_date > max_age:
        raise AuthError('данные устарели, откройте приложение заново')
    try:
        user = json.loads(d.get('user', ''))
        uid = int(user['id'])
    except (ValueError, KeyError, TypeError):
        raise AuthError('нет пользователя')
    user['id'] = uid
    return user


def make_init_data(user, bot_token, auth_date=None):
    """Для тестов: собрать корректно подписанные данные."""
    d = {'auth_date': str(int(auth_date or time.time())), 'query_id': 'AAH-test', 'user': json.dumps(user, ensure_ascii=False, separators=(',', ':'))}
    check = '\n'.join('%s=%s' % (k, v) for k, v in sorted(d.items()))
    secret = hmac.new(b'WebAppData', bot_token.encode(), hashlib.sha256).digest()
    d['hash'] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urllib.parse.urlencode(d)
