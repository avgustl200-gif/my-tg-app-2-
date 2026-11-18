"""Настройки из переменных окружения и файла .env (без внешних библиотек)."""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read_env_file(path):
    out = {}
    if not os.path.exists(path):
        return out
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, v = line.split('=', 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


class Config:
    def __init__(self, env=None, env_file=None):
        file_env = _read_env_file(env_file or os.path.join(ROOT, '.env'))
        src = dict(file_env)
        src.update(env if env is not None else os.environ)
        g = lambda k, d='': src.get(k, d)
        self.bot_token = g('BOT_TOKEN')
        self.admin_ids = {int(x) for x in g('ADMIN_IDS').replace(';', ',').split(',') if x.strip().lstrip('-').isdigit()}
        self.public_url = g('PUBLIC_URL').rstrip('/')
        self.host = g('HOST', '127.0.0.1')
        self.port = int(g('PORT', '8080'))
        self.db_path = g('DB_PATH', os.path.join(ROOT, 'data', 'shop.db'))
        self.upload_dir = g('UPLOAD_DIR', os.path.join(ROOT, 'data', 'uploads'))
        self.static_dir = os.path.join(ROOT, 'app', 'static')
        self.dev_mode = g('DEV_MODE', '0') == '1'
        self.initdata_max_age = int(g('INITDATA_MAX_AGE', '86400'))
        self.bot_polling = g('BOT_POLLING', '1') == '1'

    def check(self):
        if not self.bot_token and not self.dev_mode:
            raise SystemExit('Нужен BOT_TOKEN в файле .env (токен от @BotFather). Для локальной проверки без Telegram: DEV_MODE=1')
        if self.dev_mode:
            print('ВНИМАНИЕ: DEV_MODE=1 — подпись Telegram не проверяется. Не включайте на боевом сервере!')
