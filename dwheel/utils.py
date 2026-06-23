import sys, os, json, time, uuid, subprocess, re, sqlite3
from datetime import datetime
from pathlib import Path

# These are set by server.py before importing this module
BASE_DIR = None
DB_PATH = None
LOG_FILE = None
MAX_LOG_SIZE = 10 * 1024 * 1024
MAX_LOG_FILES = 5

def set_paths(base, db, log):
    global BASE_DIR, DB_PATH, LOG_FILE
    BASE_DIR = base
    DB_PATH = db
    LOG_FILE = log

def sizeof_fmt(num):
    for unit in ('B', 'KB', 'MB', 'GB'):
        if abs(num) < 1024:
            return '%.1f %s' % (num, unit)
        num /= 1024
    return '%.1f TB' % num

def cache_upload(file_storage):
    TEMP_DIR = BASE_DIR / 'temp_uploads'
    TEMP_DIR.mkdir(exist_ok=True)
    key = uuid.uuid4().hex
    path = TEMP_DIR / key
    file_storage.save(str(path))
    print(f'[preview] saved {key} to {path}', flush=True)
    return key

def load_cached_file(key):
    path = BASE_DIR / 'temp_uploads' / key
    exists = path.exists()
    print(f'[extract] key={key} path={path} exists={exists}', flush=True)
    return str(path) if exists else None

def cleanup_cache(key):
    path = BASE_DIR / 'temp_uploads' / key
    if path.exists():
        path.unlink()

def detect_column(header, samples):
    non_null = [s for s in samples if s is not None and str(s).strip()]
    if not non_null:
        return None
    header_lower = str(header or '').lower()
    tid_keywords = ['taskid', 'task_id', '任务id', '任务', 'id']
    hex_count = sum(1 for v in non_null if re.match(r'^[0-9a-f]{31,32}$', str(v).strip(), re.I))
    if hex_count > 0 and (hex_count / max(len(non_null), 1) > 0.4 or any(kw in header_lower for kw in tid_keywords)):
        return 'task_id'
    if hex_count >= 2:
        return 'task_id'
    dur_keywords = ['时长', '小时', 'duration', '小時', 'hour', 'h)', '(h', '交付', '采集']
    dur_count = 0
    for v in non_null:
        try:
            val = float(str(v).strip())
            if 0 < val < 100000:
                dur_count += 1
        except (ValueError, TypeError):
            pass
    if dur_count > 0 and (dur_count / max(len(non_null), 1) > 0.4 or any(kw in header_lower for kw in dur_keywords)):
        return 'duration'
    return None

def pg_get_episodes(tid):
    try:
        import psycopg2
    except ImportError:
        subprocess.run([sys.executable, '-m', 'pip', 'install', 'psycopg2-binary', '-q'], check=True)
        import psycopg2
    try:
        conn = psycopg2.connect(host='60.204.159.17', port=5432, dbname='data_collector',
                                user='psql_read', password='Psqlreader!123', connect_timeout=10)
        cur = conn.cursor()
        cur.execute("""
            SELECT e.episode_id,
                   (e.extra->>'alignedDuration')::bigint AS duration_ms,
                   (e.extra->>'alignedFileSize')::bigint AS file_size
            FROM episode e
            WHERE e.task_id = %s AND e.delete_flag = '0'
              AND e.extra ? 'alignedFileSize' AND e.data_status = '1'
              AND e.audit_status IN ('1','2')
            ORDER BY (e.extra->>'alignedDuration')::bigint DESC LIMIT 500
        """, (tid,))
        rows = [{'episode_id': r[0], 'duration_ms': int(r[1]) or 0, 'file_size': int(r[2]) or 0} for r in cur.fetchall()]
        cur.close(); conn.close()
        return rows
    except Exception as e:
        print(f'[pg] {e}', flush=True)
        return []

def obs_ls(prefix):
    r = subprocess.run(['obsutil', 'ls', '-d', prefix], capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        r = subprocess.run(['obsutil', 'ls', prefix], capture_output=True, text=True, timeout=300)
    if r.returncode != 0: return []
    eps = {}
    for line in r.stdout.strip().split('\n'):
        line = line.strip()
        if not line or line.startswith('Listing') or line.startswith('Start') or line.startswith('Folder') or line.startswith('Object') or line.startswith('key'):
            continue
        parts = line.rstrip('/').split('/')
        if len(parts) < 2: continue
        stem = parts[-1].rsplit('.', 1)[0] if '.' in parts[-1] else parts[-1]
        eid = parts[-2] if len(parts) >= 2 and re.match(r'^[0-9a-z]{32}$', parts[-2]) else (stem if re.match(r'^[0-9a-z]{32}$', stem) else None)
        if eid and eid not in eps:
            eps[eid] = 0
    if not eps: return []
    return sorted([{'episode_id': e, 'duration_ms': 0, 'file_size': 0} for e in eps], key=lambda x: x['episode_id'])

def discover_task(tid, audit_bucket=''):
    eps = pg_get_episodes(tid)
    if not eps:
        if audit_bucket:
            eps = obs_ls(f'{audit_bucket}/{tid}/')
        if not eps:
            eps = obs_ls(f'obs://openloong-zhengzhou-apps-private/data-collector-svc/align/{tid}/')
    return eps

def estimate_size(eps):
    return sum(e['file_size'] for e in eps)

def greedy_duration_select(eps, duration_hours):
    if duration_hours <= 0: return eps
    dur_ms = duration_hours * 3600 * 1000
    selected = []
    acc = 0
    for e in eps:
        if acc + e['duration_ms'] <= dur_ms:
            selected.append(e)
            acc += e['duration_ms']
    return selected

def auto_assign_disks(db_conn, agent_id, task_data):
    disks = db_conn.execute('SELECT * FROM disks WHERE agent_id=?', (agent_id,)).fetchall()
    if not disks:
        return None
    disk_infos = [{'path': d['path'], 'label': d['label'],
                    'total': d['total_bytes'], 'free': d['free_bytes']} for d in disks]
    disk_infos.sort(key=lambda d: d['free'])
    disk_free = {d['path']: d['free'] for d in disk_infos}
    for t in task_data:
        size = t['total_bytes']
        best = None
        for d in sorted(disk_infos, key=lambda x: disk_free[x['path']], reverse=True):
            if size <= disk_free[d['path']]:
                best = d['path']
                break
        if best is None:
            best = max(disk_infos, key=lambda x: disk_free[x['path']])['path']
        disk_free[best] -= size
        t['disk_path'] = best
    return task_data

def _parse_sheet_data(rows, total_rows):
    header_row = list(rows[0]) if rows else []
    cols = []
    for ci in range(len(header_row)):
        samples = []
        for ri in range(1, min(len(rows), 50)):
            if ci < len(rows[ri]):
                samples.append(rows[ri][ci])
        samples = [s for s in samples if s is not None and str(s).strip()]
        detected = detect_column(header_row[ci] if ci < len(header_row) else '', samples)
        cols.append({
            'index': ci,
            'header': str(header_row[ci]) if ci < len(header_row) and header_row[ci] is not None else '',
            'sample': [str(s)[:40] for s in samples[:5]],
            'detected': detected,
        })
    suggested = {'task_id_col': None, 'duration_col': None}
    for c in cols:
        if c['detected'] == 'task_id' and suggested['task_id_col'] is None:
            suggested['task_id_col'] = c['index']
        if c['detected'] == 'duration' and suggested['duration_col'] is None:
            suggested['duration_col'] = c['index']
    preview_rows = []
    for i in range(1, min(len(rows), 501)):
        preview_rows.append([str(v)[:40] if v is not None else '' for v in rows[i]])
    return {
        'name': 'Sheet1',
        'total_rows': total_rows,
        'columns': cols,
        'suggested': suggested,
        'preview_rows': preview_rows,
    }

def add_log(level, message, db_conn=None):
    if db_conn:
        try:
            db_conn.execute('INSERT INTO logs (level, message) VALUES (?,?)', (level, message))
            db_conn.commit()
        except:
            pass
    try:
        ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        line = f'[{ts}] [{level.upper()}] {message}\n'
        with open(str(LOG_FILE), 'a', encoding='utf-8') as f:
            f.write(line)
        if LOG_FILE.stat().st_size > MAX_LOG_SIZE:
            _rotate_logs()
    except:
        pass

def _rotate_logs():
    for i in range(MAX_LOG_FILES - 1, 0, -1):
        old = BASE_DIR / f'dwheel-server.log.{i}'
        new = BASE_DIR / f'dwheel-server.log.{i+1}'
        if old.exists():
            if new.exists():
                new.unlink()
            old.rename(new)
    backup = BASE_DIR / 'dwheel-server.log.1'
    if backup.exists():
        backup.unlink()
    LOG_FILE.rename(backup)

def get_recent_logs(db_conn, limit=50):
    rows = db_conn.execute('SELECT * FROM logs ORDER BY id DESC LIMIT ?', (limit,)).fetchall()
    return [dict(r) for r in rows]

def agent_req(agent, method, path, body=None):
    import requests as http_req
    url = f'http://{agent["host"]}:{agent["port"]}{path}'
    headers = {'X-API-Key': agent['api_key']}
    try:
        if method == 'GET':
            r = http_req.get(url, headers=headers, timeout=10)
        else:
            r = http_req.post(url, headers=headers, json=body, timeout=10)
        return r.json() if r.status_code == 200 else {'ok': False, 'error': r.text}
    except Exception as e:
        return {'ok': False, 'error': str(e)}

def sync_all_disks():
    conn = sqlite3.connect(DB_PATH)
    agents = conn.execute('SELECT * FROM agents WHERE is_online=1').fetchall()
    synced = 0
    for a in agents:
        resp = agent_req(dict(a), 'POST', '/api/v1/disks/scan', {'paths': []})
        if resp.get('ok'):
            conn.execute('DELETE FROM disks WHERE agent_id=?', (a['id'],))
            for d in resp.get('disks', []):
                conn.execute(
                    'INSERT INTO disks (agent_id, path, label, total_bytes, free_bytes) VALUES (?,?,?,?,?)',
                    (a['id'], d['path'], d['label'], d['total_bytes'], d['free_bytes'])
                )
            synced += 1
            conn.execute('UPDATE agents SET last_seen=? WHERE id=?', (datetime.now().isoformat(), a['id']))
    conn.commit()
    conn.close()
    return synced

def disk_sync_worker():
    while True:
        time.sleep(300)
        try:
            sync_all_disks()
        except:
            pass
