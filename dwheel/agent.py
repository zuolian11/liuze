#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# dwheel-agent - Linux Worker Agent（分布式下载工作节点）
# 作者: 刘泽 独立研究编写，盗版必究
"""
dwheel-agent: Linux 下载代理服务

功能:
  - 接收 Server 下发的下载任务并执行
  - 并行多文件下载（ThreadPoolExecutor）
  - 实时进度汇报
  - 下载进度持久化（SQLite）
  - 磁盘自动发现与扫描

用法:
  python3 dwheel-agent.py --port 8081 --api-key mykey
  python3 dwheel-agent.py --daemon              # 后台运行
  python3 dwheel-agent.py --parallel 5          # 设置并行数

API:
  POST /api/v1/start          启动下载任务
  POST /api/v1/stop/{job_id}  停止任务
  GET  /api/v1/status         查看状态
  GET  /api/v1/progress/{job_id}  查看进度
  GET  /api/v1/disks/scan     扫描磁盘
"""
import sys, os, json, time, subprocess, re, threading, signal, argparse, sqlite3
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse
from multiprocessing import Process, Manager
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

VERSION = '1.1.0'  # Agent 版本号
API_KEY = ''  # 认证密钥（Server 配置的 api_key）
RUNNING_JOBS = {}  # 当前运行中的作业 {job_id: {progress, workers, start_time, ...}}
LOCK = threading.Lock()  # 全局锁，保护 RUNNING_JOBS 并发访问
AGENT_DIR = Path(__file__).parent  # Agent 所在目录
PROGRESS_DB = AGENT_DIR / 'dwheel-progress.db'  # 进度持久化 SQLite 路径
PARALLEL_WORKERS = 3  # 每个 task 的并行下载数（可命令行调整）


def sizeof_fmt(num):
    """文件大小格式化（自动选择 B/KB/MB/GB/TB 单位）"""
    for unit in ('B', 'KB', 'MB', 'GB'):
        if abs(num) < 1024:
            return '%.1f %s' % (num, unit)
        num /= 1024
    return '%.1f TB' % num


def obs_cp(src, dest, timeout=600):
    """执行 obsutil cp 下载单个文件，返回 (成功, 错误信息)"""
    r = subprocess.run(['obsutil', 'cp', src, dest, '-f'],
                       capture_output=True, text=True, timeout=timeout)
    if r.returncode == 0:
        return True, ''
    err = r.stderr.strip() or r.stdout.strip()
    return False, err[:200]


def init_progress_db():
    """初始化进度持久化 SQLite 表"""
    conn = sqlite3.connect(PROGRESS_DB)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS job_progress (
            job_id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            completed INTEGER DEFAULT 0,
            failed INTEGER DEFAULT 0,
            total INTEGER DEFAULT 0,
            episode_ids TEXT DEFAULT '',
            disk_path TEXT DEFAULT '',
            obs_bucket TEXT DEFAULT '',
            start_time REAL,
            updated_at TEXT DEFAULT (datetime('now'))
        )
    ''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_job_progress_job ON job_progress(job_id)')
    conn.commit()
    conn.close()


def save_job_progress(job_id, task_id, completed, failed, total,
                      episode_ids, disk_path, obs_bucket, start_time):
    """保存当前下载进度到 SQLite（每 5 个文件写一次）"""
    try:
        conn = sqlite3.connect(PROGRESS_DB)
        conn.execute('''
            INSERT OR REPLACE INTO job_progress
            (job_id, task_id, completed, failed, total, episode_ids,
             disk_path, obs_bucket, start_time, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,datetime('now'))
        ''', (job_id, task_id, completed, failed, total,
              ','.join(episode_ids[:1000]), disk_path, obs_bucket, start_time))
        conn.commit()
        conn.close()
    except:
        pass


def remove_job_progress(job_id):
    """删除已完成的 job 的进度记录"""
    try:
        conn = sqlite3.connect(PROGRESS_DB)
        conn.execute('DELETE FROM job_progress WHERE job_id=?', (job_id,))
        conn.commit()
        conn.close()
    except:
        pass


def download_worker(job_id, task_id, episode_ids, disk_path, obs_bucket, progress, lock):
    """单个 task 的下载工作进程（使用 ThreadPoolExecutor 并行下载）"""
    task_dir = os.path.join(disk_path, task_id)
    os.makedirs(task_dir, exist_ok=True)
    start = time.time()

    def download_one(eid):
        dest = os.path.join(task_dir, f'{eid}.h5')
        tmp = dest + '.downloading'
        if os.path.isfile(dest) and os.path.getsize(dest) > 0:
            with lock:
                progress['completed'] += 1
            return True
        if os.path.exists(tmp):
            os.unlink(tmp)
        src = f'{obs_bucket}/{task_id}/{eid}/{eid}.h5'
        last_err = ''
        for attempt in range(1, 4):
            ok, err = obs_cp(src, tmp)
            if ok and os.path.isfile(tmp) and os.path.getsize(tmp) > 0:
                try:
                    os.rename(tmp, dest)
                except OSError:
                    time.sleep(1)
                    os.replace(tmp, dest)
                with lock:
                    progress['completed'] += 1
                return True
            last_err = err
            time.sleep(3)
        with lock:
            progress['failed'] += 1
            progress['errors'].append({
                'task_id': task_id, 'episode_id': eid,
                'src': f'{obs_bucket}/{task_id}/{eid}/{eid}.h5',
                'reason': last_err[:200],
            })
        return False

    with ThreadPoolExecutor(max_workers=PARALLEL_WORKERS) as pool:
        futures = {pool.submit(download_one, eid): eid for eid in episode_ids}
        done = 0
        total = len(episode_ids)
        for f in as_completed(futures):
            done += 1
            if done % 5 == 0 or done == total:
                with lock:
                    p = dict(progress)
                save_job_progress(job_id, task_id, p['completed'], p['failed'],
                                  p['total'], episode_ids, disk_path, obs_bucket, start)


def start_job(job_id, tasks, obs_source):
    """启动一个作业的所有下载进程"""
    mgr = Manager()
    progress = mgr.dict({'completed': 0, 'failed': 0, 'total': 0, 'errors': mgr.list()})
    lock = mgr.Lock()
    workers = []
    total = 0

    for t in tasks:
        tid = t['task_id']
        eps = t['episodes']
        disk = t['disk']
        total += len(eps)
        save_job_progress(f'{job_id}_{tid}', tid, 0, 0, len(eps),
                          eps[:1000], disk, obs_source, time.time())
        p = Process(target=download_worker, args=(job_id, tid, eps, disk, obs_source, progress, lock))
        p.start()
        workers.append(p)

    progress['total'] = total

    with LOCK:
        RUNNING_JOBS[job_id] = {
            'progress': progress,
            'workers': workers,
            'start_time': time.time(),
            'tasks': tasks,
            'obs_source': obs_source,
            'lock': lock,
        }
    return True


class AgentHandler(BaseHTTPRequestHandler):

    def _auth(self):
        """校验 API Key（X-API-Key Header 或默认 key）"""
        key = self.headers.get('X-API-Key', '')
        return key == API_KEY

    def _json(self, data, status=200):
        """返回 JSON 响应"""
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode())

    def _error(self, msg, status=400):
        """返回错误 JSON 响应"""
        self._json({'ok': False, 'error': msg}, status)

    def _read_body(self):
        """读取并解析请求体 JSON"""
        length = int(self.headers.get('Content-Length', 0))
        return json.loads(self.rfile.read(length)) if length else {}

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, DELETE, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, X-API-Key')
        self.end_headers()

    def do_POST(self):
        if not self._auth():
            return self._error('Unauthorized', 401)
        path = urlparse(self.path).path

        # ── 启动下载任务
        if path == '/api/v1/start':
            body = self._read_body()
            job_id = body.get('job_id')
            tasks = body.get('tasks', [])
            obs_source = body.get('obs_source', 'obs://openloong-zhengzhou-apps-private/data-collector-svc/align')
            if not job_id or not tasks:
                return self._error('缺少 job_id 或 tasks')
            if job_id in RUNNING_JOBS:
                return self._error('job_id 已存在')
            start_job(job_id, tasks, obs_source)
            self._json({'ok': True, 'job_id': job_id})


        # ── 停止任务
        elif path.startswith('/api/v1/stop/'):
            job_id = path.split('/')[-1]
            with LOCK:
                info = RUNNING_JOBS.pop(job_id, None)
            if info:
                for w in info['workers']:
                    w.terminate()
                for w in info['workers']:
                    w.join(timeout=3)
                    if w.is_alive():
                        w.kill()
                        w.join(timeout=3)
                for t in info['tasks']:
                    remove_job_progress(f'{job_id}_{t["task_id"]}')
                self._json({'ok': True, 'message': f'Job {job_id} stopped'})
            else:
                self._error('任务不存在', 404)


        # ── 扫描本地磁盘（/media/* 物理挂载点或指定路径）
        elif path == '/api/v1/disks/scan':
            import shutil
            body = self._read_body()
            paths = body.get('paths', [])
            disks = []
            if paths:
                for d in paths:
                    parent = os.path.dirname(d)
                    if os.path.isdir(parent):
                        try:
                            usage = shutil.disk_usage(parent)
                            disks.append({
                                'path': d, 'label': os.path.basename(parent),
                                'total_bytes': usage.total, 'free_bytes': usage.free,
                            })
                        except:
                            pass
            else:
                seen = set()
                for line in open('/proc/mounts'):
                    parts = line.split()
                    if len(parts) < 2: continue
                    dev, mnt = parts[0], parts[1].replace('\\040', ' ').replace('\\011', '\t').replace('\\012', '\n')
                    if not (mnt.startswith('/media/') or mnt.startswith('/mnt/')): continue
                    if not dev.startswith('/dev/'): continue
                    if dev in seen: continue
                    seen.add(dev)
                    dwheel_path = os.path.join(mnt, 'dwheel')
                    try:
                        os.makedirs(dwheel_path, exist_ok=True)
                        usage = shutil.disk_usage(mnt)
                        disks.append({
                            'path': dwheel_path,
                            'label': os.path.basename(mnt) or mnt,
                            'total_bytes': usage.total,
                            'free_bytes': usage.free,
                        })
                    except:
                        pass
            self._json({'ok': True, 'disks': disks})

        else:
            self._error('Not Found', 404)

    def do_GET(self):
        if not self._auth():
            return self._error('Unauthorized', 401)
        path = urlparse(self.path).path

        # ── 健康检查 + 运行中作业列表
        if path == '/api/v1/status':
            jobs = []
            with LOCK:
                for jid, info in RUNNING_JOBS.items():
                    p = info['progress']
                    jobs.append({
                        'job_id': jid,
                        'completed': p['completed'],
                        'failed': p['failed'],
                        'total': p['total'],
                        'elapsed': time.time() - info['start_time'],
                    })
            self._json({'ok': True, 'version': VERSION, 'running_jobs': jobs})


        # ── 查询指定 job 的实时进度
        elif path.startswith('/api/v1/progress/'):
            job_id = path.split('/')[-1]
            with LOCK:
                info = RUNNING_JOBS.get(job_id)
            if not info:
                return self._error('任务不存在', 404)
            p = info['progress']
            elapsed = time.time() - info['start_time']
            speed = p['completed'] / elapsed if elapsed > 0 else 0
            # Per-task breakdown from SQLite
            tasks_detail = []
            for t in info['tasks']:
                key = f'{job_id}_{t["task_id"]}'
                try:
                    conn = sqlite3.connect(str(PROGRESS_DB))
                    row = conn.execute('SELECT completed, failed, total, disk_path FROM job_progress WHERE job_id=?', (key,)).fetchone()
                    conn.close()
                    tasks_detail.append({
                        'task_id': t['task_id'],
                        'completed': row[0] if row else 0,
                        'failed': row[1] if row else 0,
                        'total': row[2] if row else len(t['episodes']),
                        'disk': row[3] if row else t['disk'],
                    })
                except:
                    tasks_detail.append({
                        'task_id': t['task_id'],
                        'completed': 0, 'failed': 0,
                        'total': len(t['episodes']), 'disk': t['disk'],
                    })

            self._json({
                'ok': True, 'job_id': job_id,
                'completed': p['completed'], 'failed': p['failed'], 'total': p['total'],
                'elapsed': elapsed, 'speed': round(speed, 2),
                'tasks': tasks_detail,
                'errors': list(p['errors']),
            })

        else:
            self._error('Not Found', 404)

    def log_message(self, format, *args):
        sys.stderr.write(f'[{time.strftime("%H:%M:%S")}] {args[0]} {args[1]} {args[2]}\n')


def daemonize():
    """后台运行（Unix fork 两次，脱离终端）"""
    pid = os.fork()
    if pid > 0:
        print(f'后台启动，PID: {pid}')
        sys.exit(0)
    os.setsid()
    pid = os.fork()
    if pid > 0:
        sys.exit(0)
    sys.stdout.flush()
    sys.stderr.flush()
    with open('/dev/null', 'r') as f:
        os.dup2(f.fileno(), sys.stdin.fileno())
    with open('dwheel-agent.log', 'a') as f:
        os.dup2(f.fileno(), sys.stdout.fileno())
        os.dup2(f.fileno(), sys.stderr.fileno())


def main():
    """主入口：启动 HTTP 服务"""
    global API_KEY
    parser = argparse.ArgumentParser(description='dwheel-agent Linux Worker')
    parser.add_argument('--port', type=int, default=8081, help='监听端口')
    parser.add_argument('--api-key', default='', help='API Key')
    parser.add_argument('--daemon', action='store_true', help='后台运行')
    parser.add_argument('--parallel', type=int, default=3, help='并行下载数 (默认 3)')
    args = parser.parse_args()

    global PARALLEL_WORKERS
    PARALLEL_WORKERS = args.parallel
    API_KEY = args.api_key or os.environ.get('DWHEEL_API_KEY', 'dwheel-default-key')
    init_progress_db()

    server = HTTPServer(('0.0.0.0', args.port), AgentHandler)
    print(f'dwheel-agent v{VERSION} 启动在 :{args.port}')
    print(f'API Key: {API_KEY}')
    print(f'并行下载: {PARALLEL_WORKERS} workers')

    if args.daemon:
        daemonize()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n停止...')
        with LOCK:
            for jid, info in RUNNING_JOBS.items():
                for w in info['workers']:
                    w.terminate()
        server.shutdown()


if __name__ == '__main__':
    main()
