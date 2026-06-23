import sqlite3
from flask import g

# DB_PATH is set by server.py before importing this module
DB_PATH = None

def set_db_path(path):
    global DB_PATH
    DB_PATH = path

def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute('PRAGMA journal_mode=WAL')
        g.db.execute('PRAGMA foreign_keys=ON')
    return g.db

def close_db(e):
    db = g.pop('db', None)
    if db:
        db.close()

def init_db():
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute('ALTER TABLE jobs ADD COLUMN agent_id INTEGER REFERENCES agents(id)')
    except:
        pass
    try:
        conn.execute("ALTER TABLE obs_sources ADD COLUMN audit_bucket TEXT DEFAULT ''")
    except:
        pass
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT DEFAULT 'user',
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS agents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            host TEXT NOT NULL,
            port INTEGER DEFAULT 8081,
            api_key TEXT NOT NULL,
            is_online INTEGER DEFAULT 0,
            last_seen TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS disks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id INTEGER NOT NULL REFERENCES agents(id),
            path TEXT NOT NULL,
            label TEXT DEFAULT '',
            total_bytes INTEGER DEFAULT 0,
            free_bytes INTEGER DEFAULT 0,
            UNIQUE(agent_id, path)
        );
        CREATE TABLE IF NOT EXISTS obs_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            bucket TEXT NOT NULL,
            audit_bucket TEXT DEFAULT '',
            binary_path TEXT DEFAULT 'obsutil',
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT DEFAULT '',
            status TEXT DEFAULT 'pending',
            total_episodes INTEGER DEFAULT 0,
            completed INTEGER DEFAULT 0,
            failed INTEGER DEFAULT 0,
            obs_source_id INTEGER,
            agent_id INTEGER REFERENCES agents(id),
            created_by INTEGER REFERENCES users(id),
            agent_job_id TEXT DEFAULT '',
            started_at TEXT,
            completed_at TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            level TEXT DEFAULT 'info',
            message TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS job_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL REFERENCES jobs(id),
            task_id TEXT NOT NULL,
            disk_path TEXT DEFAULT '',
            selected_episodes INTEGER DEFAULT 0,
            status TEXT DEFAULT 'pending'
        );
        CREATE TABLE IF NOT EXISTS pending_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL,
            agent_id INTEGER REFERENCES agents(id),
            disk_path TEXT DEFAULT '',
            episode_count INTEGER DEFAULT 0,
            estimated_bytes INTEGER DEFAULT 0,
            episodes TEXT DEFAULT '',
            status TEXT DEFAULT 'pending',
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(task_id, agent_id)
        );
    ''')
    conn.commit()
    conn.close()
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute('''CREATE TABLE IF NOT EXISTS pending_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL,
            agent_id INTEGER REFERENCES agents(id),
            disk_path TEXT DEFAULT '',
            episode_count INTEGER DEFAULT 0,
            estimated_bytes INTEGER DEFAULT 0,
            episodes TEXT DEFAULT '',
            status TEXT DEFAULT 'pending',
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(task_id, agent_id)
        )''')
        conn.commit()
        conn.close()
    except:
        pass
