#!/usr/bin/env python3
import sys, os, hashlib, uuid, subprocess, threading
from pathlib import Path
from flask import Flask, render_template

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / 'dwheel-server.db'
KEY_FILE = BASE_DIR / '.secret_key'
TEMP_DIR = BASE_DIR / 'temp_uploads'
TEMP_DIR.mkdir(exist_ok=True)
LOG_FILE = BASE_DIR / 'dwheel-server.log'
TOKEN_EXPIRE_HOURS = 24

if KEY_FILE.exists():
    SECRET_KEY = KEY_FILE.read_text().strip()
else:
    SECRET_KEY = hashlib.sha256(str(uuid.uuid4()).encode()).hexdigest()
    KEY_FILE.write_text(SECRET_KEY)

def ensure_deps():
    deps = ['flask', 'requests', 'openpyxl', 'xlrd']
    for d in deps:
        try:
            __import__(d)
        except ImportError:
            subprocess.run([sys.executable, '-m', 'pip', 'install', d, '-q'], check=True)

ensure_deps()

import db, auth, utils as utilmod, api

db.set_db_path(DB_PATH)
auth.set_secret_key(SECRET_KEY)
utilmod.set_paths(BASE_DIR, DB_PATH, LOG_FILE)

app = Flask(__name__)
app.teardown_appcontext(db.close_db)

api.register_routes(app, db, auth, utilmod)


@app.route('/')
def index():
    return render_template('index.html')


def main():
    import argparse
    parser = argparse.ArgumentParser(description='dwheel-server')
    parser.add_argument('--port', type=int, default=8080)
    parser.add_argument('--init', action='store_true')
    args = parser.parse_args()

    db.init_db()

    if args.init:
        import sqlite3
        username = input('管理员用户名 (默认 admin): ').strip() or 'admin'
        pw = input('密码 (默认 admin): ').strip() or 'admin'
        conn = sqlite3.connect(DB_PATH)
        conn.execute('INSERT OR IGNORE INTO users (username, password_hash, role) VALUES (?,?,?)',
                     (username, auth.hash_password(pw), 'admin'))
        conn.commit(); conn.close()
        print(f'管理员 {username} 创建完成')
        return

    threading.Thread(target=utilmod.disk_sync_worker, daemon=True).start()

    print(f'dwheel-server 启动在 :{args.port}')
    print(f'浏览器访问 http://localhost:{args.port}')
    app.run(host='0.0.0.0', port=args.port, debug=False)

if __name__ == '__main__':
    main()
