#!/usr/bin/env python3
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app.server import run

if __name__ == '__main__':
    if '--seed' in sys.argv:
        from app.config import Config
        from app.db import DB
        from app.seed import seed
        cfg = Config()
        os.makedirs(os.path.dirname(cfg.db_path), exist_ok=True)
        seed(DB(cfg.db_path))
        print('Демо-товары добавлены.')
    run()
