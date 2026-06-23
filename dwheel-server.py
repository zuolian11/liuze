#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# dwheel-server — 分布式下载管理 Web 服务
# 作者: 刘泽 独立研究编写，盗版必究
"""
dwheel-server: 分布式下载管理 Web 服务

功能:
  - Web 管理面板 + REST API（内嵌 Alpine.js + TailwindCSS）
  - 任务发现（PostgreSQL 元数据 + OBS 对象存储 fallback）
  - 按交付时长贪婪选择 episode
  - 智能磁盘分配（容量感知）
  - Agent 任务调度与实时进度监控
  - HMAC 认证 Token + SQLite 持久化

用法:
  python3 dwheel-server.py              # 启动（端口 8080）
  python3 dwheel-server.py --port 8080  # 指定端口
  python3 dwheel-server.py --init       # 初始化管理员账号
"""
import sys, os, json, time, hashlib, hmac, sqlite3, uuid, subprocess, re, threading
from datetime import datetime, timedelta
from pathlib import Path
from functools import wraps

# ────── Flask / 依赖 ──────

def ensure_deps():
    """自动安装缺少的 Python 依赖包（Flask / Requests）"""
    deps = ['flask', 'requests', 'openpyxl', 'xlrd']
    for d in deps:
        try:
            __import__(d)
        except ImportError:
            subprocess.run([sys.executable, '-m', 'pip', 'install', d, '-q'], check=True)

ensure_deps()
from flask import Flask, request, jsonify, g
import requests as http_req

# ────── 配置 ──────

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / 'dwheel-server.db'
KEY_FILE = BASE_DIR / '.secret_key'
if KEY_FILE.exists():
    SECRET_KEY = KEY_FILE.read_text().strip()
else:
    SECRET_KEY = hashlib.sha256(str(uuid.uuid4()).encode()).hexdigest()
    KEY_FILE.write_text(SECRET_KEY)
TOKEN_EXPIRE_HOURS = 24

app = Flask(__name__)

WEB_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>dwheel 下载管理</title>
<script defer src="https://cdn.jsdelivr.net/npm/alpinejs@3.14.9/dist/cdn.min.js"></script>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f5f5f5; color: #1a1a1a; min-height: 100vh; }
.login-page { display: flex; justify-content: center; align-items: center; min-height: 100vh; flex-direction: column; gap: 1rem; background: #f0f2f5; }
.login-box { background: #fff; padding: 2rem; border-radius: 8px; width: 360px; box-shadow: 0 1px 3px rgba(0,0,0,.1); }
.login-box h1 { margin-bottom: 1.5rem; font-size: 1.25rem; text-align: center; color: #1a1a1a; font-weight: 600; }
.login-box input { width: 100%; padding: 10px 12px; margin-bottom: 12px; border: 1px solid #d0d0d0; border-radius: 6px; font-size: 14px; outline: none; background: #fff; color: #1a1a1a; }
.login-box input:focus { border-color: #2563eb; }
.login-box button { width: 100%; padding: 10px; background: #2563eb; color: #fff; border: none; border-radius: 6px; font-size: 14px; cursor: pointer; font-weight: 500; }
.login-box button:hover { background: #1d4ed8; }
.login-box .error { color: #dc2626; font-size: 13px; margin-top: 8px; text-align: center; }
.layout { display: flex; min-height: 100vh; }
.sidebar { width: 220px; background: #fff; border-right: 1px solid #e0e0e0; padding: 1rem; display: flex; flex-direction: column; }
.sidebar h2 { font-size: 1rem; margin-bottom: 1.5rem; padding: 0 12px; color: #2563eb; font-weight: 700; }
.sidebar a { display: block; padding: 8px 12px; margin: 1px 0; border-radius: 6px; color: #555; text-decoration: none; font-size: 14px; cursor: pointer; }
.sidebar a:hover { background: #f0f0f0; color: #1a1a1a; }
.sidebar a.active { background: #eff6ff; color: #2563eb; font-weight: 500; }
.sidebar .logout { margin-top: auto; color: #dc2626; }
.main { flex: 1; padding: 1.5rem; max-width: 960px; }
.page-title { font-size: 1.25rem; font-weight: 600; margin-bottom: 1rem; color: #1a1a1a; }
.card { background: #fff; border-radius: 8px; padding: 1rem 1.25rem; margin-bottom: .75rem; border: 1px solid #e0e0e0; }
.card-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: .75rem; margin-bottom: 1rem; }
.stat-card { background: #fff; border-radius: 8px; padding: 1rem; text-align: center; border: 1px solid #e0e0e0; }
.stat-card .num { font-size: 1.75rem; font-weight: 700; color: #2563eb; }
.stat-card .label { font-size: 12px; color: #888; margin-top: 2px; }
table { width: 100%; border-collapse: collapse; font-size: 14px; }
th, td { text-align: left; padding: 8px 12px; border-bottom: 1px solid #eee; }
th { color: #888; font-weight: 500; font-size: 12px; }
tr:hover { background: #fafafa; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 12px; font-weight: 500; }
.badge-green { background: #dcfce7; color: #166534; }
.badge-yellow { background: #fef9c3; color: #854d0e; }
.badge-red { background: #fee2e2; color: #991b1b; }
.badge-blue { background: #dbeafe; color: #1e40af; }
.btn { display: inline-block; padding: 8px 16px; border-radius: 6px; border: none; font-size: 14px; cursor: pointer; font-weight: 500; }
.btn-primary { background: #2563eb; color: #fff; }
.btn-primary:hover { background: #1d4ed8; }
.btn-danger { background: #dc2626; color: #fff; }
.btn-danger:hover { background: #b91c1c; }
.btn-default { background: #e0e0e0; color: #333; }
.btn-default:hover { background: #d0d0d0; }
.btn-sm { padding: 4px 10px; font-size: 12px; }
.mr-2 { margin-right: 8px; }
.mt-2 { margin-top: 8px; }
.mb-2 { margin-bottom: 8px; }
.flex { display: flex; }
.gap-2 { gap: 8px; }
.items-center { align-items: center; }
.justify-between { justify-content: space-between; }
.text-muted { color: #888; font-size: 13px; }
.progress-bar { height: 20px; background: #eee; border-radius: 6px; overflow: hidden; position: relative; margin: 8px 0; }
.progress-bar .fill { height: 100%; background: #2563eb; border-radius: 6px; transition: width 1s; }
.progress-bar .text { position: absolute; top: 0; left: 0; right: 0; bottom: 0; display: flex; align-items: center; justify-content: center; font-size: 11px; color: #fff; font-weight: 500; }
.hidden { display: none !important; }
select, input[type="text"], input[type="number"] { border: 1px solid #d0d0d0; border-radius: 6px; background: #fff; color: #1a1a1a; padding: 8px 12px; font-size: 14px; outline: none; }
select:focus, input:focus { border-color: #2563eb; }
.floating { position: fixed; bottom: 24px; right: 24px; z-index: 100; }
.toast { background: #333; color: #fff; padding: 10px 16px; border-radius: 6px; margin-top: 8px; box-shadow: 0 2px 8px rgba(0,0,0,.2); animation: slideIn .2s; font-size: 13px; }
@keyframes slideIn { from { transform: translateX(100%); opacity: 0; } to { transform: translateX(0); opacity: 1; } }
textarea { border: 1px solid #d0d0d0; border-radius: 6px; background: #fff; color: #1a1a1a; padding: 12px; font-size: 13px; font-family: monospace; width: 100%; min-height: 100px; outline: none; }
textarea:focus { border-color: #2563eb; }
@media (max-width: 768px) { .layout { flex-direction: column; } .sidebar { width: 100%; flex-direction: row; overflow-x: auto; } }
</style>
</head>
<body>
<div x-data="app()">
  <!-- Login -->
  <div x-show="!token" class="login-page">
    <div class="login-box">
      <h1>dwheel</h1>
      <input type="text" x-model="username" placeholder="用户名" @keyup.enter="login()">
      <input type="password" x-model="password" placeholder="密码" @keyup.enter="login()">
      <button @click="login()">登录</button>
      <div class="error" x-show="error" x-text="error"></div>
    </div>
  </div>

  <!-- Main App -->
  <div x-show="token" class="layout">
    <div class="sidebar">
      <h2>dwheel</h2>
      <a :class="{active: page==='dashboard'}" @click="page='dashboard'">概览</a>
      <a :class="{active: page==='agents'}" @click="page='agents'">机器管理</a>
      <a :class="{active: page==='jobs'}" @click="page='jobs'">下载作业</a>
      <a :class="{active: page==='create'}" @click="page='create'">新建作业</a>
      <a :class="{active: page==='import'}" @click="page='import'">导入</a>
      <a :class="{active: page==='settings'}" @click="page='settings'">设置</a>
      <a class="logout" @click="logout()">退出</a>
    </div>
    <div class="main">
      <!-- Dashboard -->
      <div x-show="page==='dashboard'">
        <h2 class="page-title">概览</h2>
        <div class="card-grid">
          <div class="stat-card"><div class="num" x-text="dashboard.agents">0</div><div class="label">机器数</div></div>
          <div class="stat-card"><div class="num" x-text="dashboard.online">0</div><div class="label">在线</div></div>
          <div class="stat-card"><div class="num" x-text="dashboard.jobs_running">0</div><div class="label">运行中</div></div>
          <div class="stat-card"><div class="num" x-text="dashboard.jobs_completed">0</div><div class="label">已完成</div></div>
        </div>
      </div>

      <!-- Agent Management -->
      <div x-show="page==='agents'">
        <div class="flex justify-between items-center mb-2">
          <h2 class="page-title" style="margin-bottom:0">机器管理</h2>
          <button class="btn btn-primary" @click="showAddAgent=true">+ 添加机器</button>
        </div>

        <div class="card" x-show="showAddAgent">
          <h3 class="mb-2">添加机器</h3>
          <div class="flex gap-2 items-center" style="flex-wrap:wrap">
            <input type="text" x-model="newAgent.name" placeholder="名称" style="width:150px">
            <input type="text" x-model="newAgent.host" placeholder="IP 地址" style="width:150px">
            <input type="number" x-model="newAgent.port" placeholder="端口" style="width:80px">
            <input type="text" x-model="newAgent.api_key" placeholder="API Key" style="width:180px">
            <button class="btn btn-primary btn-sm" @click="addAgent()">保存</button>
            <button class="btn btn-sm" style="background:#475569;color:#e2e8f0" @click="showAddAgent=false">取消</button>
          </div>
        </div>

        <div class="card" x-show="agents.length===0">
          <div class="text-muted">暂无机器</div>
        </div>
        <template x-for="a in agents" :key="a.id">
          <div class="card">
            <div class="flex justify-between items-center">
              <div>
                <strong x-text="a.name"></strong>
                <span class="text-muted" style="margin-left:8px" x-text="a.host+':'+a.port"></span>
                <span :class="'badge '+(a.is_online?'badge-green':'badge-red')" x-text="a.is_online?'在线':'离线'" style="margin-left:8px"></span>
              </div>
              <div class="flex gap-2">
                <button class="btn btn-sm" style="background:#475569;color:#e2e8f0" @click="pingAgent(a.id)">检测</button>
                <button class="btn btn-sm btn-danger" @click="deleteAgent(a.id)">删除</button>
              </div>
            </div>
            <div x-show="a.disks && a.disks.length" class="mt-2">
              <div class="text-muted" style="font-size:12px;margin-bottom:4px">磁盘:</div>
              <template x-for="d in a.disks" :key="d.id">
                <div style="font-size:13px;padding:2px 0">
                  <span x-text="d.label || d.path"></span>
                  <span class="text-muted" style="margin-left:8px" x-text="(d.free_bytes/1024/1024/1024).toFixed(1)+' GB / '+(d.total_bytes/1024/1024/1024).toFixed(1)+' GB'"></span>
                </div>
              </template>
            </div>
          </div>
        </template>
      </div>

      <!-- Jobs -->
      <div x-show="page==='jobs'">
        <h2 class="page-title">下载作业</h2>
        <div x-show="!selectedJob">
          <div class="card" x-show="jobs.length===0"><div class="text-muted">暂无作业</div></div>
          <template x-for="j in jobs" :key="j.id">
            <div class="card" style="cursor:pointer" @click="viewJob(j.id)">
              <div class="flex justify-between items-center">
                <div>
                  <strong x-text="j.name || 'job_'+j.id"></strong>
                  <span :class="'badge '+(j.status==='running'?'badge-yellow':j.status==='completed'?'badge-green':'badge-blue')" style="margin-left:8px" x-text="j.status"></span>
                </div>
                <div class="text-muted" x-text="new Date(j.created_at).toLocaleString()"></div>
              </div>
              <div class="flex gap-2 mt-2 text-muted" style="font-size:13px">
                <span x-text="'共 '+j.total_episodes+' 个文件'"></span>
                <span x-show="j.completed>0" x-text="'已完成 '+j.completed"></span>
                <span x-show="j.failed>0" style="color:#ef4444" x-text="'失败 '+j.failed"></span>
              </div>
            </div>
          </template>
        </div>
        <!-- Job Detail -->
        <template x-if="selectedJob">
        <div>
          <button class="btn btn-sm" style="background:#475569;color:#e2e8f0;margin-bottom:12px" @click="selectedJob=null">← 返回</button>
          <div class="card">
            <div class="flex justify-between items-center">
              <h3 x-text="selectedJob.name"></h3>
              <div>
                <button class="btn btn-sm btn-danger" @click="stopJob(selectedJob.id)" x-show="selectedJob.status==='running'">停止</button>
              </div>
            </div>
            <div class="mt-2">
              <div class="progress-bar">
                <div class="fill" :style="'width:'+(selectedJob.total_episodes?((selectedJob.completed+selectedJob.failed)/selectedJob.total_episodes*100)+'%':'0%')"></div>
                <div class="text" x-text="selectedJob.completed+' / '+selectedJob.total_episodes"></div>
              </div>
            </div>
            <div class="flex gap-2 mt-2 text-muted" style="font-size:13px">
              <span x-text="'已完成: '+selectedJob.completed"></span>
              <span x-show="selectedJob.failed>0" style="color:#ef4444" x-text="'失败: '+selectedJob.failed"></span>
              <span x-text="'状态: '+selectedJob.status"></span>
            </div>
          </div>
          <div class="card" x-show="selectedJob.tasks && selectedJob.tasks.length">
            <h4 class="mb-2">任务列表</h4>
            <table>
              <tr><th>Task ID</th><th>磁盘</th><th>文件数</th><th>状态</th></tr>
              <template x-for="t in selectedJob.tasks" :key="t.id">
                <tr>
                  <td x-text="t.task_id?.slice(0,16)+'...'"></td>
                  <td style="font-size:12px" x-text="t.disk_path?.split('/').slice(-2).join('/')"></td>
                  <td x-text="t.selected_episodes"></td>
                  <td><span :class="'badge '+ (t.status==='completed'?'badge-green':'badge-yellow')" x-text="t.status"></span></td>
                </tr>
              </template>
            </table>
          </div>
        </div>
        </template>
      </div>

      <!-- Create Job -->
      <div x-show="page==='create'">
        <h2 class="page-title">新建下载作业</h2>
        <div class="card">
          <h3 class="mb-2">步骤1: 选择机器</h3>
          <select x-model="create.agent_id">
            <option value="">-- 选择机器 --</option>
            <template x-for="a in agents" :key="a.id">
              <option :value="a.id" x-text="a.name+' ('+a.host+')'"></option>
            </template>
          </select>
        </div>
        <div class="card" x-show="create.agent_id">
          <h3 class="mb-2">步骤2: 输入 Task ID</h3>
          <p class="text-muted mb-2">粘贴任意包含 task_id 的内容（聊天记录、列表等），每行一个或逗号分隔</p>
          <textarea x-model="create.task_text" placeholder="粘贴 task_id..."></textarea>
          <div class="text-muted mt-2" x-show="create_parsed.length" x-text="'识别到 '+create_parsed.length+' 个 task_id'"></div>
          <div class="text-muted mt-2" x-show="create_parsed.length>0" x-text="'识别到 '+create_parsed.length+' 个 task_id'"></div>
          <div x-show="create_parsed.length>0" class="mt-2" style="font-size:13px">
            <div class="flex gap-2 items-center mb-2" style="font-weight:500;color:#888;font-size:12px">
              <span style="min-width:120px">Task ID</span>
              <span style="min-width:80px">交付时长(h)</span>
              <span style="min-width:60px">文件数</span>
              <span style="min-width:80px">估计大小</span>
            </div>
            <template x-for="(tid, i) in create_parsed" :key="tid">
              <div class="flex gap-2 items-center mb-1" style="font-size:13px">
                <span style="min-width:120px" x-text="tid.slice(0,16)+'...'"></span>
                <input type="number" x-model="create.durations[tid]" placeholder="0" style="width:80px;padding:4px 8px" min="0" step="0.5">
                <span class="text-muted" style="min-width:60px;font-size:11px" x-text="(create.task_infos?.find(x=>x.task_id===tid)?.episode_count||'')"></span>
                <span class="text-muted" style="min-width:80px;font-size:11px" x-text="(create.task_infos?.find(x=>x.task_id===tid)?.estimated_str||'')"></span>
              </div>
            </template>
            <button class="btn btn-sm btn-primary mt-2" @click="discoverTasks()">查询 OBS 信息</button>
            <span class="text-muted ml-2" x-show="create.tasks_loading">查询中...</span>
          </div>
        </div>
        <div class="card" x-show="create_parsed.length">
          <div class="flex justify-between items-center">
            <h3>步骤3: 分配磁盘</h3>
            <button class="btn btn-sm btn-primary" @click="autoAssign()">智能分配</button>
          </div>
          <p class="text-muted mb-2" style="font-size:13px">每个 task 选一个磁盘（按交付时长降序、容量感知）</p>
          <template x-for="(tid, i) in create_parsed" :key="tid">
            <div class="flex items-center gap-2 mb-2" style="font-size:13px">
              <span style="min-width:120px" x-text="tid.slice(0,16)+'...'"></span>
              <span class="text-muted" style="min-width:80px;font-size:11px" x-text="(create.task_infos?.find(x=>x.task_id===tid)?.duration_h||'')+'h'"></span>
              <span class="text-muted" style="min-width:60px;font-size:11px" x-text="(create.task_infos?.find(x=>x.task_id===tid)?.episode_count||'')+'eps'"></span>
              <select x-model="create.assignments[tid]" style="width:auto;flex:1">
                <option value="">-- 选择磁盘 --</option>
                <template x-for="d in selectedAgentDisks" :key="d.path">
                  <option :value="d.path" x-text="d.label+' ('+(d.free_bytes/1024/1024/1024).toFixed(1)+' GB)'"></option>
                </template>
              </select>
            </div>
          </template>
        </div>
        <div class="card" x-show="canCreate">
          <h3 class="mb-2">步骤4: 确认并启动</h3>
          <div class="text-muted mb-2" x-text="'共 '+create_parsed.length+' 个 task'"></div>
          <div class="text-muted mb-2" x-show="diskWarning" style="color:#dc2626;font-size:13px" x-text="'⚠️ '+diskWarning"></div>
          <button class="btn btn-primary" @click="createJob()">开始下载</button>
        </div>
      </div>

      <!-- Settings -->
      <div x-show="page==='monitor'">
        <h2 class="page-title">监控面板</h2>
        
        <div class="card-grid">
          <div class="stat-card"><div class="num" x-text="mon.running" :style="mon.running>0?'color:#2563eb':''">0</div><div class="label">运行中</div></div>
          <div class="stat-card"><div class="num" x-text="mon.completed">0</div><div class="label">已完成</div></div>
          <div class="stat-card"><div class="num" x-text="mon.failed" :style="mon.failed>0?'color:#dc2626':''">0</div><div class="label">失败</div></div>
          <div class="stat-card"><div class="num" x-text="mon.disks.length">0</div><div class="label">磁盘数</div></div>
        </div>

        <div class="card" x-show="mon.disks.length">
          <h3 class="mb-2">磁盘使用</h3>
          <template x-for="d in mon.disks" :key="d.id">
            <div class="mb-2" style="font-size:13px">
              <div class="flex justify-between">
                <span x-text="d.agent_name+': '+d.label"></span>
                <span class="text-muted" x-text="((d.total_bytes-d.free_bytes)/1024/1024/1024).toFixed(1)+'G / '+(d.total_bytes/1024/1024/1024).toFixed(1)+'G'"></span>
              </div>
              <div class="progress-bar" style="height:16px;background:#eee">
                <div class="fill" :style="'width:'+((d.total_bytes-d.free_bytes)/d.total_bytes*100)+'%;background:#3b82f6;border-radius:6px'"></div>
              </div>
            </div>
          </template>
        </div>

        <div class="card" x-show="mon.jobs.length">
          <h3 class="mb-2">下载进度</h3>
          <template x-for="j in mon.jobs" :key="j.id">
            <div class="mb-2" style="font-size:13px;border-bottom:1px solid #eee;padding-bottom:8px">
              <div class="flex justify-between">
                <span x-text="j.name || 'job_'+j.id"></span>
                <span :class="'badge '+(j.status==='running'?'badge-yellow':j.status==='completed'?'badge-green':j.status==='failed'?'badge-red':'badge-blue')" x-text="j.status"></span>
              </div>
              <div class="progress-bar" style="height:16px;background:#eee">
                <div class="fill" :style="'width:'+(j.total_episodes?((j.agent_completed||0+j.agent_failed||0)/j.total_episodes*100)+'%':'0%')+';background:#2563eb;border-radius:6px'"></div>
                <div class="text" x-text="(j.agent_completed||0)+' / '+(j.total_episodes||0)"></div>
              </div>
              <div class="flex gap-2 text-muted mt-1" style="font-size:11px">
                <span x-show="j.agent_failed>0" style="color:#dc2626" x-text="'失败: '+j.agent_failed"></span>
                <span x-show="j.agent_elapsed" x-text="'耗时: '+Math.floor(j.agent_elapsed/60)+'m'"></span>
              </div>
            </div>
          </template>
        </div>

        <div class="card">
          <h3 class="mb-2">操作日志</h3>
          <div style="max-height:400px;overflow-y:auto;font-size:12px;font-family:monospace">
            <div x-show="!mon.logs.length" class="text-muted">暂无日志</div>
            <template x-for="l in mon.logs" :key="l.id">
              <div style="padding:3px 0;border-bottom:1px solid #f0f0f0">
                <span class="text-muted" x-text="l.created_at?.slice(5,19)"></span>
                <span x-text="' ['+l.level+'] '" :style="l.level==='error'?'color:#dc2626':'color:#888'"></span>
                <span x-text="l.message"></span>
              </div>
            </template>
          </div>
        </div>
      </div>

      <!-- Import (简化版：选机器 → 粘贴/上传 → 设时长 → 自动分配 → 一键下载) -->
      <div x-show="page==='import'">
        <h2 class="page-title">导入并下载</h2>

        <div class="card">
          <h3 class="mb-2">选择机器</h3>
          <select x-model="qi.agent_id">
            <option value="">-- 选择机器 --</option>
            <template x-for="a in agents" :key="a.id">
              <option :value="a.id" x-text="a.name+' ('+a.host+')'"></option>
            </template>
          </select>
          <span x-show="qi.agent_id && selectedAgentDisks.length" class="text-muted" style="margin-left:12px;font-size:13px">
            <template x-for="d in selectedAgentDisks" :key="d.path">
              <span style="margin-right:12px" x-text="d.label+': '+(d.free_bytes/1024/1024/1024).toFixed(1)+'GB'"></span>
            </template>
          </span>
        </div>

        <div class="card" x-show="qi.agent_id">
          <h3 class="mb-2">输入 Task ID</h3>
          <p class="text-muted mb-2" style="font-size:13px">粘贴任意包含 task_id 的内容，或上传 xlsx/txt/csv 文件</p>
          <textarea x-model="qi.task_text" placeholder="粘贴 task_id..."></textarea>
          <div class="flex gap-2 mt-2" style="flex-wrap:wrap;align-items:center">
            <input type="file" id="importFile" accept=".xlsx,.xls,.txt,.csv" style="font-size:13px">
            <button class="btn btn-sm" style="background:#475569;color:#e2e8f0" @click="qiParseFile()">解析文件</button>
            <span class="text-muted" style="font-size:13px" x-show="qi_parsed.length" x-text="'识别到 '+qi_parsed.length+' 个 task_id'"></span>
          </div>
        </div>

        <div class="card" x-show="qi_parsed.length && qi.agent_id">
          <h3 class="mb-2">交付时长设置（小时）</h3>
          <p class="text-muted mb-2" style="font-size:13px">设置每个 task 的交付时长（0 或留空 = 全部下载）</p>
          <div class="flex gap-2 items-center mb-2" style="font-weight:500;color:#888;font-size:12px">
            <span style="min-width:200px">Task ID</span>
            <span style="min-width:80px">时长(h)</span>
          </div>
          <template x-for="tid in qi_parsed" :key="tid">
            <div class="flex gap-2 items-center mb-1" style="font-size:13px">
              <span style="min-width:200px;font-family:monospace;font-size:12px" x-text="tid.slice(0,16)+'...'"></span>
              <input type="number" x-model="qi.durations[tid]" placeholder="0" style="width:80px;padding:4px 8px" min="0" step="0.5">
            </div>
          </template>
          <button class="btn btn-sm btn-primary mt-2" @click="qiPrepare()" x-text="qi.loading ? '查询中...' : '查询并自动分配'"></button>
        </div>

        <div class="card" x-show="qi.tasks.length">
          <div class="flex justify-between items-center mb-2">
            <h3 class="mb-0">分配结果</h3>
            <div>
              <span class="text-muted" style="margin-right:12px" x-text="'共 '+qi.tasks.length+' 个 task, '+qi.total_eps+' episodes'"></span>
              <span class="text-muted" x-text="'总计 '+(qi.total_bytes/1024/1024/1024).toFixed(2)+' GB'"></span>
            </div>
          </div>
          <div style="max-height:300px;overflow-y:auto;font-size:13px">
            <table>
              <tr><th style="min-width:200px">Task ID</th><th>磁盘</th><th style="text-align:right">文件数</th><th style="text-align:right">大小</th></tr>
              <template x-for="t in qi.tasks" :key="t.task_id">
                <tr>
                  <td style="font-family:monospace;font-size:12px" x-text="t.task_id.slice(0,16)+'...'"></td>
                  <td x-text="t.disk_path.split('/').slice(-2).join('/')"></td>
                  <td style="text-align:right" x-text="t.episode_count"></td>
                  <td style="text-align:right" x-text="(t.total_bytes/1024/1024/1024).toFixed(2)+' GB'"></td>
                </tr>
              </template>
            </table>
          </div>
          <button class="btn btn-primary mt-2" @click="qiCreateJob()" x-text="qi.creating ? '正在启动...' : '一键下载'"></button>
        </div>
      </div>

      <div x-show="page==='settings'">
        <h2 class="page-title">设置</h2>
        <div class="card">
          <h3 class="mb-2">OBS 数据源</h3>
          <div x-show="!showAddObs">
            <button class="btn btn-sm btn-primary" @click="showAddObs=true">+ 添加源</button>
          </div>
            <div x-show="showAddObs" class="flex gap-2 items-center mt-2" style="flex-wrap:wrap">
              <input type="text" x-model="newObs.name" placeholder="名称" style="width:120px">
              <input type="text" x-model="newObs.bucket" placeholder="obs://bucket/prefix" style="width:240px">
              <input type="text" x-model="newObs.audit_bucket" placeholder="审核路径(可选)" style="width:180px">
              <button class="btn btn-sm btn-primary" @click="addObs()">保存</button>
              <button class="btn btn-sm" style="background:#475569;color:#e2e8f0" @click="showAddObs=false">取消</button>
            </div>
            <template x-for="s in obsSources" :key="s.id">
              <div class="mt-2" style="font-size:13px;padding:4px 0;border-bottom:1px solid #eee">
                <strong x-text="s.name"></strong>
                <span class="text-muted" style="margin-left:8px" x-text="s.bucket"></span>
                <span x-show="s.audit_bucket" class="badge badge-blue" style="margin-left:8px" x-text="'审核: '+s.audit_bucket"></span>
              </div>
            </template>
        </div>
      </div>
    </div>
  </div>

  <!-- Toast -->
  <div class="floating">
    <template x-for="(msg,i) in toasts" :key="i">
      <div class="toast" @click="toasts.splice(i,1)" x-text="msg"></div>
    </template>
  </div>
</div>

<script>
function app() {
  return {
    token: localStorage.getItem('dwheel_token') || '',
    username: '', password: '', error: '',
    page: 'dashboard',
    toasts: [],

    // Dashboard
    dashboard: { agents:0, online:0, jobs_running:0, jobs_completed:0 },
    mon: { logs:[], jobs:[], disks:[], running:0, completed:0, failed:0 },

    // Agents
    agents: [],
    showAddAgent: false,
    newAgent: { name:'', host:'', port:8081, api_key:'' },

    // Jobs
    jobs: [],
    selectedJob: null,

    // Create
    create: { agent_id:'', task_text:'', parsed_ids:[], assignments:{}, task_infos:[], tasks_loading:false, durations:{}, },

    // Settings
    obsSources: [],
    showAddObs: false,
    newObs: { name:'', bucket:'', audit_bucket:'' },

    // Import
    importResult: { ids:[], estimates:[], estimating:false, total_episodes:0, total_size:0, total_size_text:'' },

    // Quick Import (新版一键流程)
    qi: { agent_id:'', task_text:'', tasks:[], total_eps:0, total_bytes:0, loading:false, creating:false, durations:{} },

    init() {
      if (this.token) this.loadDashboard();
      setInterval(() => { this.autoRefreshJob(); }, 1000);
      this.$watch('create.agent_id', v => {
        if (v && this.create_parsed.length && !this.create.task_infos)
          this.discoverTasks();
      });
      this.$watch('create.task_text', () => {
        if (this.create.agent_id && this.create_parsed.length && !this.create.task_infos)
          this.discoverTasks();
      });
    },

    toast(msg) { this.toasts.push(msg); setTimeout(() => this.toasts.shift(), 3000); },

    api(path, opts={}) {
      opts.headers = opts.headers || {};
      if (this.token) opts.headers['Authorization'] = 'Bearer '+this.token;
      if (opts.body && typeof opts.body === 'object' && !(opts.body instanceof FormData)) {
        opts.body = JSON.stringify(opts.body);
        opts.headers['Content-Type'] = 'application/json';
      }
      return fetch(path, opts).then(r => {
        if (r.status === 401) { this.token=''; localStorage.removeItem('dwheel_token'); }
        return r.json();
      });
    },

    login() {
      // 用户登录：验证凭证后存储 Token 到 localStorage
      this.api('/api/v1/auth/login', {method:'POST', body:{username:this.username, password:this.password}})
        .then(r => { if (r.ok) { this.token = r.token; localStorage.setItem('dwheel_token', r.token); this.loadDashboard(); this.error=''; } else { this.error = r.error || '登录失败'; }});
    },
    logout() {
      this.token = ''; localStorage.removeItem('dwheel_token'); this.page = 'dashboard';
    },

    loadDashboard() {
      // 加载仪表盘数据（统计/Agent/任务/OBS源/监控）
      this.api('/api/v1/dashboard').then(r => { if (r.ok) this.dashboard = r; });
      this.loadAgents();
      this.loadJobs();
      this.api('/api/v1/obs-sources').then(r => { if (r.ok) this.obsSources = r.sources; });
      this.loadMonitor();
    },

    // Agents
    loadAgents() { this.api('/api/v1/agents').then(r => { if (r.ok) this.agents = r.agents; }); },
    addAgent() {
      this.api('/api/v1/agents', {method:'POST', body:this.newAgent}).then(r => {
        if (r.ok) { this.toast('机器已添加'); this.showAddAgent = false; this.newAgent = {name:'', host:'', port:8081, api_key:''}; this.loadAgents(); }
        else this.toast('添加失败: '+r.error);
      });
    },
    deleteAgent(id) {
      if (!confirm('确定删除此机器?')) return;
      this.api('/api/v1/agents/'+id, {method:'DELETE'}).then(r => { if (r.ok) { this.toast('已删除'); this.loadAgents(); }});
    },
    pingAgent(id) {
      this.api('/api/v1/agents/'+id+'/ping', {method:'POST'}).then(r => {
        if (r.ok) { this.toast('检测成功: '+(r.agent_status?.ok ? '在线' : '离线')); this.loadAgents(); }
        else this.toast('检测失败');
      });
    },

    // Jobs
    loadJobs() {
      this.api('/api/v1/jobs').then(r => { if (r.ok) this.jobs = r.jobs; });
    },
    viewJob(id) {
      // 查看作业详情（触发自动刷新）
      this.api('/api/v1/jobs/'+id).then(r => { if (r.ok) this.selectedJob = r.job; });
    },
    stopJob(id) {
      // 停止运行中的作业
      this.api('/api/v1/jobs/'+id+'/stop', {method:'POST'}).then(r => { if (r.ok) { this.toast('已停止'); this.loadJobs(); this.selectedJob=null; }});
    },
    autoRefreshJob() {
      // 每 1 秒轮询当前选中作业的实时进度
      if (this.selectedJob && this.selectedJob.status === 'running') {
        this.api('/api/v1/jobs/'+this.selectedJob.id+'/progress').then(r => {
          if (r.ok && this.selectedJob) {
            this.selectedJob.completed = r.completed;
            this.selectedJob.failed = r.failed;
            if (r.total) this.selectedJob.total_episodes = r.total;
          }
        });
      }
    },

    // Settings
    addObs() {
      // 添加 OBS 数据源配置
      this.api('/api/v1/obs-sources', {method:'POST', body:this.newObs}).then(r => {
        if (r.ok) { this.toast('已添加'); this.showAddObs = false; this.newObs = {name:'', bucket:''}; this.loadDashboard(); }
      });
    },

    // Quick Import (一键导入 + 自动分配 + 下载)
    get qi_parsed() {
      const ids = this.qi.task_text.match(/[0-9a-f]{32}/gi) || [];
      return [...new Set(ids.map(x => x.toLowerCase()))];
    },
    qiParseFile() {
      const input = document.getElementById('importFile');
      if (!input.files || !input.files[0]) { this.toast('请先选择文件'); return; }
      const file = input.files[0];
      const formData = new FormData();
      formData.append('file', file);
      this.api('/api/v1/import/parse', {method:'POST', body:formData}).then(r => {
        if (r.ok && r.ids.length) {
          this.qi.task_text = r.ids.join('\n');
          this.toast('识别到 ' + r.ids.length + ' 个 task_id');
        } else {
          this.toast(r.error || '未找到 task_id');
        }
      });
    },
    qiPrepare() {
      const ids = this.qi_parsed;
      if (!ids.length) { this.toast('未识别到 task_id'); return; }
      if (!this.qi.agent_id) { this.toast('请先选择机器'); return; }
      this.qi.loading = true; this.qi.tasks = [];
      this.api('/api/v1/tasks/prepare', {method:'POST', body:{
        task_ids: ids, agent_id: parseInt(this.qi.agent_id),
        durations: this.qi.durations, obs_source_id: this.obsSources[0]?.id || null,
      }}).then(r => {
        this.qi.loading = false;
        if (r.ok) {
          this.qi.tasks = r.tasks;
          this.qi.total_eps = r.tasks.reduce((s,t) => s+t.episode_count, 0);
          this.qi.total_bytes = r.tasks.reduce((s,t) => s+t.total_bytes, 0);
          this.toast('已分配 ' + r.count + ' 个 task');
        } else {
          this.toast('分配失败: ' + r.error);
        }
      });
    },
    qiCreateJob() {
      if (!this.qi.tasks.length) return;
      this.qi.creating = true;
      this.api('/api/v1/jobs', {method:'POST', body:{
        name: 'job_'+Date.now(),
        agent_id: parseInt(this.qi.agent_id),
        tasks: this.qi.tasks.map(t => ({task_id: t.task_id, disk_path: t.disk_path, episodes: t.episodes?.split(',') || []})),
        obs_source_id: this.obsSources[0]?.id || null,
      }}).then(r => {
        this.qi.creating = false;
        if (r.ok) {
          this.toast('作业已创建, ID: '+r.job_id);
          this.qi = { agent_id: this.qi.agent_id, task_text:'', tasks:[], total_eps:0, total_bytes:0, loading:false, creating:false, durations:{} };
          document.getElementById('importFile').value = '';
          this.page = 'jobs'; this.loadJobs();
        } else {
          this.toast('创建失败: '+r.error);
        }
      });
    },

    // Create Job
    get selectedAgentDisks() {
      const aid = this.create.agent_id || this.qi.agent_id;
      const a = this.agents.find(x => x.id == aid);
      return a ? a.disks : [];
    },
    get create_parsed() {
      // 从文本框中提取所有 32 位十六进制 task_id（去重）
      const ids = this.create.task_text.match(/[0-9a-f]{32}/gi) || [];
      return [...new Set(ids.map(x => x.toLowerCase()))];
    },
    get canCreate() {
      const assigned = Object.values(this.create.assignments).filter(x => x).length;
      return this.create_parsed.length > 0 && this.create.agent_id &&
             assigned >= this.create_parsed.length;
    },
    get diskWarning() {
      // 检查磁盘容量是否足够，返回警告字符串
      if (!this.create.assignments || !this.create.task_infos) return '';
      const obj = {};
      for (const info of this.create.task_infos) {
        const disk = this.create.assignments[info.task_id];
        if (disk) obj[disk] = (obj[disk] || 0) + (info.estimated_bytes || 0);
      }
      const warns = [];
      for (const d of this.selectedAgentDisks) {
        const need = obj[d.path] || 0;
        const free = d.free_bytes;
        if (need > free) {
          warns.push(d.label + ' 需要 ' + (need/1024/1024/1024).toFixed(1) + 'GB 剩余 ' + (free/1024/1024/1024).toFixed(1) + 'GB');
        }
      }
      return warns.join('; ');
    },

    autoAssign() {
      // 调用后端智能分配算法：按时长降序 + 容量感知
      const disks = this.selectedAgentDisks;
      if (!disks.length) { this.toast('无可用磁盘'); return; }
      this.api('/api/v1/tasks/assign', {method:'POST', body:{
        task_ids: this.create_parsed,
        agent_id: parseInt(this.create.agent_id),
        durations: this.create.durations,
        obs_source_id: this.obsSources[0]?.id || null,
      }}).then(r => {
        if (r.ok && r.assignments.length > 0) {
          this.create.assignments = {};
          this.create.task_infos = r.assignments;
          r.assignments.forEach(a => {
            this.create.assignments[a.task_id] = a.disk_path;
          });
          this.toast('已按交付时长+容量智能分配');
        } else {
          this.toast('智能分配失败，使用轮询');
          this.create.assignments = {};
          this.create_parsed.forEach((tid, i) => {
            this.create.assignments[tid] = disks[i % disks.length].path;
          });
        }
      });
    },

    loadMonitor() {
      // 加载监控面板数据
      this.api('/api/v1/monitor').then(r => {
        if (r.ok) {
          this.mon.logs = r.logs;
          this.mon.jobs = r.jobs;
          this.mon.disks = r.disks;
          this.mon.running = r.jobs.filter(j => j.status==='running').length;
          this.mon.completed = r.jobs.filter(j => j.status==='completed').length;
          this.mon.failed = r.jobs.filter(j => j.status==='failed').length;
        }
      });
    },
    
    discoverTasks() {
      // 查询 OBS 信息：获取每个 task 的 episode 列表和大小
      const ids = this.create_parsed;
      if (!ids.length) return;
      this.create.tasks_loading = true;
      this.api('/api/v1/tasks/info', {method:'POST', body:{
        task_ids: ids,
        durations: this.create.durations,
        obs_source_id: this.obsSources[0]?.id || null,
      }}).then(r => {
        this.create.tasks_loading = false;
        if (r.ok) {
          r.tasks.forEach(t => {
            let b = t.estimated_bytes;
            const units = ['B','KB','MB','GB','TB'];
            let i = 0;
            while (b >= 1024 && i < units.length-1) { b /= 1024; i++; }
            t.estimated_str = b.toFixed(1)+units[i];
          });
          this.create.task_infos = r.tasks;
        }
      });
    },

    createJob() {
      // 创建下载作业：组装任务列表并发送到后端
      const tasks = this.create_parsed.map(tid => {
        const info = this.create.task_infos?.find(x => x.task_id === tid);
        return {
          task_id: tid,
          disk_path: this.create.assignments[tid] || '',
          episodes: info?.episodes || [],
        };
      });
      this.api('/api/v1/jobs', {method:'POST', body:{
        name: 'job_'+Date.now(),
        agent_id: parseInt(this.create.agent_id),
        tasks: tasks,
        obs_source_id: this.obsSources[0]?.id || null,
      }}).then(r => {
        if (r.ok) {
          this.toast('作业已创建，ID: '+r.job_id);
          this.create = { agent_id:'', task_text:'', parsed_ids:[], assignments:{}, durations:{}, task_infos:null, tasks_loading:false };
          this.page = 'jobs';
          this.loadJobs();
        } else {
          this.toast('创建失败: '+r.error);
        }
      });
    },

    // Watch parsed_ids reactively
    _watch: null,
  };
}
</script>
</body>
</html>

"""

# ════════════════════════════════════════════════
# 数据库初始化与连接管理
# ════════════════════════════════════════════════

def get_db():
    """获取当前请求的 SQLite 连接（Flask 上下文单例）"""
    if 'db' not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute('PRAGMA journal_mode=WAL')
        g.db.execute('PRAGMA foreign_keys=ON')
    return g.db

@app.teardown_appcontext
def close_db(e):
    db = g.pop('db', None)
    if db:
        db.close()

def init_db():
    """初始化数据库：建表 + 字段迁移"""
    conn = sqlite3.connect(DB_PATH)
    # Migration: add agent_id to jobs if missing
    try:
        conn.execute('ALTER TABLE jobs ADD COLUMN agent_id INTEGER REFERENCES agents(id)')
    except:
        pass
    # Migration: add audit_bucket to obs_sources if missing
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
    # Migration: add pending_tasks if missing on older DBs
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

# ════════════════════════════════════════════════
# 工具函数（密码/Token/Episode选择/日志/Agent通信）
# ════════════════════════════════════════════════

def hash_password(pw):
    """SHA256 加盐哈希（SECRET_KEY 作为盐）"""
    return hashlib.sha256((pw + SECRET_KEY).encode()).hexdigest()

def make_token(user_id, username, role):
    """生成 HMAC-SHA256 签名 Token（无状态 JWT 风格）"""
    payload = {
        'id': user_id, 'username': username, 'role': role,
        'exp': (datetime.utcnow() + timedelta(hours=TOKEN_EXPIRE_HOURS)).isoformat(),
    }
    data = json.dumps(payload, sort_keys=True, separators=(',', ':'))
    sig = hmac.new(SECRET_KEY.encode(), data.encode(), hashlib.sha256).hexdigest()
    return f'{data}.{sig}'

def verify_token(token):
    """校验 Token 签名和过期时间，返回 payload 或 None"""
    try:
        data, sig = token.rsplit('.', 1)
        expected = hmac.new(SECRET_KEY.encode(), data.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        payload = json.loads(data)
        if payload['exp'] < datetime.utcnow().isoformat():
            return None
        return payload
    except:
        return None

def require_auth(f):
    """装饰器：要求请求携带有效的 Bearer Token"""
    @wraps(f)
    def decorated(*a, **kw):
        auth = request.headers.get('Authorization', '')
        token = auth.replace('Bearer ', '') if auth.startswith('Bearer ') else ''
        payload = verify_token(token)
        if not payload:
            return jsonify({'ok': False, 'error': 'Unauthorized'}), 401
        g.user = payload
        return f(*a, **kw)
    return decorated

def sizeof_fmt(num):
    for unit in ('B', 'KB', 'MB', 'GB'):
        if abs(num) < 1024:
            return '%.1f %s' % (num, unit)
        num /= 1024
    return '%.1f TB' % num


def pg_get_episodes(tid):
    """从 PostgreSQL 查询 task 的 episode 列表（仅审核通过的数据）"""
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
    """通过 obsutil ls 列出 OBS 路径下的 episode_id（仅发现，不含大小，大小从 PG 获取）"""
    # Try with -d first (fast directory listing), fallback to plain ls
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
        # Extract episode_id: parts[-2] if last part is empty/dir; otherwise parts[-1] (filename stem)
        stem = parts[-1].rsplit('.', 1)[0] if '.' in parts[-1] else parts[-1]
        eid = parts[-2] if len(parts) >= 2 and re.match(r'^[0-9a-z]{32}$', parts[-2]) else (stem if re.match(r'^[0-9a-z]{32}$', stem) else None)
        if eid and eid not in eps:
            eps[eid] = 0
    if not eps: return []
    return sorted([{'episode_id': e, 'duration_ms': 0, 'file_size': 0} for e in eps], key=lambda x: x['episode_id'])

def discover_task(tid, audit_bucket=''):
    """发现 task 的所有 episode：优先 PG，fallback 到 OBS（审核路径优先）"""
    eps = pg_get_episodes(tid)
    if not eps:
        if audit_bucket:
            eps = obs_ls(f'{audit_bucket}/{tid}/')
        if not eps:
            eps = obs_ls(f'obs://openloong-zhengzhou-apps-private/data-collector-svc/align/{tid}/')
    return eps

def estimate_size(eps):
    """估算 episode 列表的总文件大小"""
    return sum(e['file_size'] for e in eps)

def greedy_duration_select(eps, duration_hours):
    """按交付时长限制选取 episode：贪心策略（按时长降序选取，累计不超限）"""
    if duration_hours <= 0: return eps
    dur_ms = duration_hours * 3600 * 1000
    selected = []
    acc = 0
    for e in eps:
        if acc + e['duration_ms'] <= dur_ms:
            selected.append(e)
            acc += e['duration_ms']
    return selected



def add_log(level, message, db=None):
    """写入操作日志"""
    if not db:
        db = get_db()
    try:
        db.execute('INSERT INTO logs (level, message) VALUES (?,?)', (level, message))
        db.commit()
    except:
        pass

def get_recent_logs(limit=50):
    db = get_db()
    rows = db.execute('SELECT * FROM logs ORDER BY id DESC LIMIT ?', (limit,)).fetchall()
    return [dict(r) for r in rows]

def agent_req(agent, method, path, body=None):
    """向 Agent 发送 HTTP 请求（携带 API Key）"""
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

# ════════════════════════════════════════════════
# 认证 API
# ════════════════════════════════════════════════

@app.route('/api/v1/auth/init', methods=['POST'])
def auth_init():
    """初始化管理员账号（仅首次可用）"""
    db = get_db()
    count = db.execute('SELECT COUNT(*) FROM users').fetchone()[0]
    if count > 0:
        return jsonify({'ok': False, 'error': '已有用户，请登录'}), 400
    body = request.json or {}
    username = body.get('username', 'admin')
    pw = body.get('password', 'admin')
    db.execute('INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)',
               (username, hash_password(pw), 'admin'))
    db.commit()
    add_log('info', f'管理员 {username} 登录'); return jsonify({'ok': True, 'message': f'管理员 {username} 创建成功'})

@app.route('/api/v1/auth/login', methods=['POST'])
def auth_login():
    """用户登录：验证密码并签发 Token"""
    body = request.json or {}
    db = get_db()
    user = db.execute('SELECT * FROM users WHERE username=?', (body.get('username', ''),)).fetchone()
    if not user or user['password_hash'] != hash_password(body.get('password', '')):
        return jsonify({'ok': False, 'error': '用户名或密码错误'}), 401
    token = make_token(user['id'], user['username'], user['role'])
    add_log('info', f'用户 {user["username"]} 登录'); return jsonify({'ok': True, 'token': token, 'username': user['username'], 'role': user['role']})

@app.route('/api/v1/auth/me')
@require_auth
def auth_me():
    """获取当前登录用户信息"""
    return jsonify({'ok': True, 'user': g.user})

# ════════════════════════════════════════════════
# 仪表盘 API
# ════════════════════════════════════════════════

@app.route('/api/v1/dashboard')
@require_auth
def dashboard():
    """仪表盘统计数据（Agent数/在线数/任务数/磁盘总量）"""
    db = get_db()
    agents = db.execute('SELECT id, name, is_online FROM agents').fetchall()
    agent_count = len(agents)
    online_count = sum(1 for a in agents if a['is_online'])
    jobs = db.execute('SELECT COUNT(*) as total, '
                      "SUM(CASE WHEN status='running' THEN 1 ELSE 0 END) as running, "
                      "SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) as done "
                      'FROM jobs').fetchone()
    total_bytes = db.execute("SELECT COALESCE(SUM(total_bytes),0) FROM disks").fetchone()[0]
    return jsonify({
        'ok': True,
        'agents': agent_count,
        'online': online_count,
        'jobs_total': jobs['total'] or 0,
        'jobs_running': jobs['running'] or 0,
        'jobs_completed': jobs['done'] or 0,
        'total_disk_bytes': total_bytes,
    })

# ════════════════════════════════════════════════
# Agent / 工作节点管理 API
# ════════════════════════════════════════════════

@app.route('/api/v1/agents', methods=['GET'])
@require_auth
def list_agents():
    """Agent 列表（含磁盘信息）"""
    db = get_db()
    agents = db.execute('SELECT * FROM agents ORDER BY id').fetchall()
    result = []
    for a in agents:
        disks = db.execute('SELECT * FROM disks WHERE agent_id=?', (a['id'],)).fetchall()
        result.append({
            'id': a['id'], 'name': a['name'], 'host': a['host'], 'port': a['port'],
            'is_online': bool(a['is_online']), 'last_seen': a['last_seen'],
            'disks': [dict(d) for d in disks],
        })
    return jsonify({'ok': True, 'agents': result})

@app.route('/api/v1/agents', methods=['POST'])
@require_auth
def add_agent():
    """注册新的 Agent 节点"""
    body = request.json or {}
    name = body.get('name', '')
    host = body.get('host', '')
    port = body.get('port', 8081)
    api_key = body.get('api_key', '')
    if not name or not host:
        return jsonify({'ok': False, 'error': '缺少 name 或 host'}), 400
    db = get_db()
    cur = db.execute('INSERT INTO agents (name, host, port, api_key) VALUES (?,?,?,?)',
                     (name, host, port, api_key))
    db.commit()
    return jsonify({'ok': True, 'id': cur.lastrowid})

@app.route('/api/v1/agents/<int:aid>', methods=['DELETE'])
@require_auth
def delete_agent(aid):
    """删除 Agent 及其磁盘记录"""
    db = get_db()
    db.execute('DELETE FROM disks WHERE agent_id=?', (aid,))
    db.execute('DELETE FROM agents WHERE id=?', (aid,))
    db.commit()
    add_log('info', f'作业 #{jid} 已停止')
    return jsonify({'ok': True})

@app.route('/api/v1/agents/<int:aid>/ping', methods=['POST'])
@require_auth
def ping_agent(aid):
    """Ping Agent 并刷新在线状态和磁盘信息"""
    db = get_db()
    agent = db.execute('SELECT * FROM agents WHERE id=?', (aid,)).fetchone()
    if not agent:
        return jsonify({'ok': False, 'error': 'Agent not found'}), 404
    resp = agent_req(dict(agent), 'GET', '/api/v1/status')
    is_online = resp.get('ok', False)
    db.execute('UPDATE agents SET is_online=?, last_seen=? WHERE id=?',
               (int(is_online), datetime.now().isoformat(), aid))

    # ── Agent 在线则刷新磁盘信息
    if is_online:
        disk_resp = agent_req(dict(agent), 'POST', '/api/v1/disks/scan', {'paths': []})
        if disk_resp.get('ok'):
            db.execute('DELETE FROM disks WHERE agent_id=?', (aid,))
            for d in disk_resp.get('disks', []):
                db.execute(
                    'INSERT INTO disks (agent_id, path, label, total_bytes, free_bytes) VALUES (?,?,?,?,?)',
                    (aid, d['path'], d['label'], d['total_bytes'], d['free_bytes'])
                )
    db.commit()
    return jsonify({'ok': is_online, 'agent_status': resp})

# ════════════════════════════════════════════════
# OBS 数据源配置 API
# ════════════════════════════════════════════════

@app.route('/api/v1/obs-sources', methods=['GET'])
@require_auth
def list_obs():
    """OBS 数据源列表"""
    db = get_db()
    return jsonify({'ok': True, 'sources': [dict(r) for r in db.execute('SELECT * FROM obs_sources').fetchall()]})

@app.route('/api/v1/obs-sources', methods=['POST'])
@require_auth
def add_obs():
    """新增 OBS 数据源"""
    body = request.json or {}
    db = get_db()
    cur = db.execute('INSERT INTO obs_sources (name, bucket, audit_bucket, binary_path) VALUES (?,?,?,?)',
                     (body['name'], body['bucket'], body.get('audit_bucket', ''), body.get('binary_path', 'obsutil')))
    db.commit()
    return jsonify({'ok': True, 'id': cur.lastrowid})


# ════════════════════════════════════════════════
# Task 发现与磁盘分配 API
# ════════════════════════════════════════════════

@app.route('/api/v1/tasks/info', methods=['POST'])
@require_auth
def task_info():
    """查询 task 的 episode 信息（文件数/大小/时长）"""
    body = request.json or {}
    task_ids = body.get('task_ids', [])
    durations = body.get('durations', {})  # {task_id: duration_h, ...}
    obs_source_id = body.get('obs_source_id')
    db = get_db()
    audit_bucket = ''
    if obs_source_id:
        src = db.execute('SELECT * FROM obs_sources WHERE id=?', (obs_source_id,)).fetchone()
        if src:
            audit_bucket = src['audit_bucket'] or ''

    # 取第一项（向前兼容单个 task_id 字段）
    if not task_ids and 'task_id' in body:
        task_ids = [body['task_id']]

    results = []
    for tid in task_ids:
        eps = discover_task(tid, audit_bucket)
        selected = greedy_duration_select(eps, durations.get(tid, 0))
        size = estimate_size(selected)
        results.append({
            'task_id': tid,
            'duration_h': durations.get(tid, 0),
            'episodes': [e['episode_id'] for e in selected],
            'episode_count': len(selected),
            'estimated_bytes': size,
            'total_bytes': size,
            'total_size_gb': round(size / (1024**3), 2),
        })
    return jsonify({'ok': True, 'tasks': results, 'episodes': results[0]['episodes'] if results else []})

@app.route('/api/v1/import/parse', methods=['POST'])
@require_auth
def import_parse():
    """上传文件解析 task_id（支持 xlsx/txt/csv）"""
    if 'file' not in request.files:
        return jsonify({'ok': False, 'error': '请上传文件'}), 400
    f = request.files['file']
    if not f.filename:
        return jsonify({'ok': False, 'error': '空文件名'}), 400

    ids = set()
    ext = f.filename.rsplit('.', 1)[-1].lower() if '.' in f.filename else ''

    if ext in ('xlsx',):
        try:
            import openpyxl
            wb = openpyxl.load_workbook(f, read_only=True)
            for sheet in wb.worksheets:
                for row in sheet.iter_rows(values_only=True):
                    for cell in row:
                        if cell and isinstance(cell, str):
                            for m in re.finditer(r'[0-9a-f]{32}', cell, re.I):
                                ids.add(m.group().lower())
        except ImportError:
            return jsonify({'ok': False, 'error': '缺少 openpyxl（处理 xlsx 需要）'}), 500
    elif ext in ('xls',):
        try:
            import xlrd
            wb = xlrd.open_workbook(file_contents=f.read())
            for sheet in wb.sheets():
                for row in range(sheet.nrows):
                    for cell in sheet.row_values(row):
                        if cell and isinstance(cell, str):
                            for m in re.finditer(r'[0-9a-f]{32}', cell, re.I):
                                ids.add(m.group().lower())
        except ImportError:
            return jsonify({'ok': False, 'error': '缺少 xlrd（处理 xls 需要）'}), 500
    else:
        content = f.read().decode('utf-8', errors='ignore')
        for m in re.finditer(r'[0-9a-f]{32}', content, re.I):
            ids.add(m.group().lower())

    ids = sorted(ids)
    if not ids:
        return jsonify({'ok': False, 'error': '未找到 32 位十六进制 task_id'}), 400
    return jsonify({'ok': True, 'ids': ids, 'count': len(ids)})

@app.route('/api/v1/tasks/assign', methods=['POST'])
@require_auth
def task_assign():
    """智能分配磁盘：按交付时长降序 + 磁盘剩余空间"""
    body = request.json or {}
    task_ids = body.get('task_ids', [])
    agent_id = body.get('agent_id')
    durations = body.get('durations', {})
    obs_source_id = body.get('obs_source_id')
    if not task_ids or not agent_id:
        return jsonify({'ok': False, 'error': '缺少 task_ids 或 agent_id'}), 400

    db = get_db()
    audit_bucket = ''
    if obs_source_id:
        src = db.execute('SELECT * FROM obs_sources WHERE id=?', (obs_source_id,)).fetchone()
        if src:
            audit_bucket = src['audit_bucket'] or ''

    # Discover all tasks
    task_data = []
    for tid in task_ids:
        eps = discover_task(tid, audit_bucket)
        selected = greedy_duration_select(eps, durations.get(tid, 0))
        task_data.append({
            'task_id': tid,
            'duration_h': durations.get(tid, 0),
            'episodes': [e['episode_id'] for e in selected],
            'total_bytes': estimate_size(selected),
        })

    # ── 按交付时长降序排列，时长最短的优先分配
    task_data.sort(key=lambda t: t['duration_h'], reverse=True)

    # ── 获取 Agent 的磁盘列表（从数据库）
    db = get_db()
    disks = db.execute('SELECT * FROM disks WHERE agent_id=?', (agent_id,)).fetchall()
    if not disks:
        return jsonify({'ok': False, 'error': '该机器无磁盘信息，请先检测'}), 400

    disk_infos = [{'path': d['path'], 'label': d['label'],
                    'total': d['total_bytes'], 'free': d['free_bytes']} for d in disks]
    disk_infos.sort(key=lambda d: d['free'], reverse=True)

    # ── 贪心分配：每个 task 选剩余空间最大的磁盘
    assignments = []
    disk_free = {d['path']: d['free'] for d in disk_infos}
    for t in task_data:
        size = t['total_bytes']
        # 找剩余空间最大的可用盘
        best = None
        for d in sorted(disk_infos, key=lambda x: disk_free[x['path']], reverse=True):
            if size <= disk_free[d['path']]:
                best = d['path']
                break
        if best is None:
            best = max(disk_infos, key=lambda x: disk_free[x['path']])['path']
        disk_free[best] -= size
        assignments.append({
            'task_id': t['task_id'],
            'duration_h': t['duration_h'],
            'disk_path': best,
            'episodes': t['episodes'],
            'estimated_bytes': size,
        })

    return jsonify({'ok': True, 'assignments': assignments})


# ── 自动分配辅助函数

def auto_assign_disks(agent_id, task_data):
    """根据 agent 的磁盘容量，贪心分配每个 task 的磁盘"""
    db = get_db()
    disks = db.execute('SELECT * FROM disks WHERE agent_id=?', (agent_id,)).fetchall()
    if not disks:
        return None
    disk_infos = [{'path': d['path'], 'label': d['label'],
                    'total': d['total_bytes'], 'free': d['free_bytes']} for d in disks]
    disk_infos.sort(key=lambda d: d['free'], reverse=True)
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


# ── 批量导入 + 自动发现 + 自动分配

@app.route('/api/v1/tasks/prepare', methods=['POST'])
@require_auth
def tasks_prepare():
    """批量 task_id：自动发现 episode、自动分配磁盘、存入待处理队列"""
    body = request.json or {}
    task_ids = body.get('task_ids', [])
    agent_id = body.get('agent_id')
    durations = body.get('durations', {})
    obs_source_id = body.get('obs_source_id')
    if not task_ids or not agent_id:
        return jsonify({'ok': False, 'error': '缺少 task_ids 或 agent_id'}), 400

    db = get_db()
    audit_bucket = ''
    if obs_source_id:
        src = db.execute('SELECT * FROM obs_sources WHERE id=?', (obs_source_id,)).fetchone()
        if src:
            audit_bucket = src['audit_bucket'] or ''

    task_data = []
    for tid in task_ids:
        eps = discover_task(tid, audit_bucket)
        selected = greedy_duration_select(eps, durations.get(tid, 0))
        task_data.append({
            'task_id': tid,
            'duration_h': durations.get(tid, 0),
            'episodes': [e['episode_id'] for e in selected],
            'episode_count': len(selected),
            'total_bytes': estimate_size(selected),
        })

    task_data.sort(key=lambda t: t['duration_h'], reverse=True)

    assigned = auto_assign_disks(agent_id, task_data)
    if not assigned:
        return jsonify({'ok': False, 'error': '该机器无磁盘信息，请先检测'}), 400

    for t in assigned:
        db.execute(
            'INSERT OR REPLACE INTO pending_tasks (task_id, agent_id, disk_path, episode_count, estimated_bytes, episodes, status) VALUES (?,?,?,?,?,?,?)',
            (t['task_id'], agent_id, t['disk_path'], t['episode_count'], t['total_bytes'],
             ','.join(t['episodes'][:500]), 'pending')
        )

    db.commit()
    return jsonify({'ok': True, 'tasks': assigned, 'count': len(assigned)})


# ── 查询待处理任务列表

@app.route('/api/v1/tasks/pending', methods=['GET'])
@require_auth
def tasks_pending():
    """列出所有待处理的任务（可选按 agent_id 过滤）"""
    db = get_db()
    agent_id = request.args.get('agent_id', '')
    if agent_id:
        rows = db.execute('SELECT * FROM pending_tasks WHERE agent_id=? AND status=? ORDER BY created_at DESC',
                          (agent_id, 'pending')).fetchall()
    else:
        rows = db.execute("SELECT * FROM pending_tasks WHERE status='pending' ORDER BY created_at DESC").fetchall()
    return jsonify({'ok': True, 'tasks': [dict(r) for r in rows]})


# ── 删除单个待处理任务

@app.route('/api/v1/tasks/pending/<int:pid>', methods=['DELETE'])
@require_auth
def tasks_pending_delete(pid):
    """删除一个待处理任务"""
    db = get_db()
    db.execute('DELETE FROM pending_tasks WHERE id=?', (pid,))
    db.commit()
    return jsonify({'ok': True})


# ════════════════════════════════════════════════
# 下载作业 API（创建/列表/进度/停止）
# ════════════════════════════════════════════════

@app.route('/api/v1/jobs', methods=['GET'])
@require_auth
def list_jobs():
    """作业列表（最近 50 条）"""
    db = get_db()
    jobs = db.execute('SELECT * FROM jobs ORDER BY id DESC LIMIT 50').fetchall()
    return jsonify({'ok': True, 'jobs': [dict(j) for j in jobs]})

@app.route('/api/v1/jobs/<int:jid>')
@require_auth
def get_job(jid):
    db = get_db()
    job = db.execute('SELECT * FROM jobs WHERE id=?', (jid,)).fetchone()
    if not job:
        return jsonify({'ok': False, 'error': 'Not found'}), 404
    tasks = db.execute('SELECT * FROM job_tasks WHERE job_id=?', (jid,)).fetchall()
    return jsonify({'ok': True, 'job': dict(job), 'tasks': [dict(t) for t in tasks]})

@app.route('/api/v1/jobs/<int:jid>/progress')
@require_auth
def job_progress(jid):
    """实时进度（代理到 Agent 查询）"""
    db = get_db()
    job = db.execute('SELECT * FROM jobs WHERE id=?', (jid,)).fetchone()
    if not job:
        return jsonify({'ok': False, 'error': 'Not found'}), 404
    if not job['agent_job_id']:
        return jsonify({'ok': True, 'job_id': jid, 'completed': job['completed'],
                        'failed': job['failed'], 'total': job['total_episodes'],
                        'status': job['status']})
    # ── 从 Agent 拉取实时进度
    if job['agent_id']:
        agent = db.execute('SELECT * FROM agents WHERE id=?', (job['agent_id'],)).fetchone()
        if agent:
            resp = agent_req(dict(agent), 'GET', f'/api/v1/progress/{job["agent_job_id"]}')
            if resp.get('ok'):
                return jsonify(resp)
    return jsonify({'ok': True, 'completed': 0, 'failed': 0, 'total': 0})

@app.route('/api/v1/jobs', methods=['POST'])
@require_auth
def create_job():
    """创建并启动下载作业"""
    body = request.json or {}
    name = body.get('name', f'job_{int(time.time())}')
    agent_id = body.get('agent_id')
    tasks_config = body.get('tasks', [])
    obs_source_id = body.get('obs_source_id')
    use_pending = body.get('use_pending', False)

    if not agent_id:
        return jsonify({'ok': False, 'error': '需要 agent_id'}), 400

    db = get_db()
    agent = db.execute('SELECT * FROM agents WHERE id=?', (agent_id,)).fetchone()
    if not agent:
        return jsonify({'ok': False, 'error': 'Agent not found'}), 404

    obs = db.execute('SELECT * FROM obs_sources WHERE id=?', (obs_source_id,)).fetchone()
    bucket = obs['bucket'] if obs else 'obs://openloong-zhengzhou-apps-private/data-collector-svc/align'
    audit_bucket = obs['audit_bucket'] if obs and obs['audit_bucket'] else ''

    # ── 如果 use_pending=True，从 pending_tasks 读取
    if use_pending and not tasks_config:
        pending = db.execute("SELECT * FROM pending_tasks WHERE agent_id=? AND status='pending'", (agent_id,)).fetchall()
        if not pending:
            return jsonify({'ok': False, 'error': '无待处理任务'}), 400
        tasks_config = [{
            'task_id': p['task_id'],
            'disk_path': p['disk_path'],
            'episodes': p['episodes'].split(',') if p['episodes'] else [],
        } for p in pending]

    if not tasks_config:
        return jsonify({'ok': False, 'error': '需要 tasks 或 use_pending=true'}), 400

    # ── 补填 episodes 和磁盘（未传时自动分配）
    need_assign = any(not t.get('disk_path') for t in tasks_config)
    need_eps = any(not t.get('episodes') or len(t['episodes']) == 0 for t in tasks_config)

    if need_eps or need_assign:
        tmp_data = []
        for t in tasks_config:
            eps = t.get('episodes', [])
            if not eps or len(eps) == 0:
                eps = discover_task(t['task_id'], audit_bucket)
                selected = greedy_duration_select(eps, 0)
                eps = [e['episode_id'] for e in selected]
            tmp_data.append({
                'task_id': t['task_id'],
                'total_bytes': estimate_size([{'file_size': 0} for _ in eps]),
                'disk_path': t.get('disk_path', ''),
            })
            t['episodes'] = eps

        if need_assign:
            assigned = auto_assign_disks(agent_id, tmp_data)
            if assigned:
                for i, t in enumerate(tasks_config):
                    t['disk_path'] = assigned[i]['disk_path']

    # ── 写入作业记录
    total_eps = sum(len(t.get('episodes', [])) for t in tasks_config)
    cur = db.execute(
        'INSERT INTO jobs (name, status, total_episodes, obs_source_id, agent_id, created_by) VALUES (?,?,?,?,?,?)',
        (name, 'running', total_eps, obs_source_id, agent_id, g.user['id'])
    )
    job_id = cur.lastrowid

    for t in tasks_config:
        db.execute(
            'INSERT INTO job_tasks (job_id, task_id, disk_path, selected_episodes) VALUES (?,?,?,?)',
            (job_id, t['task_id'], t.get('disk_path', ''), len(t.get('episodes', [])))
        )
    db.commit()

    # ── 发送给 Agent 执行下载
    agent_job_id = f'srv_job_{job_id}'
    payload = {
        'job_id': agent_job_id,
        'tasks': [{'task_id': t['task_id'], 'episodes': t['episodes'], 'disk': t['disk_path']}
                  for t in tasks_config],
        'obs_source': bucket,
    }
    resp = agent_req(dict(agent), 'POST', '/api/v1/start', payload)
    if resp.get('ok'):
        db.execute('UPDATE jobs SET agent_job_id=? WHERE id=?', (agent_job_id, job_id))
        # 标记 pending_tasks 为 running
        db.execute("UPDATE pending_tasks SET status='running', disk_path='' WHERE agent_id=? AND status='pending'", (agent_id,))
        db.commit()
    else:
        db.execute('UPDATE jobs SET status=? WHERE id=?', ('failed', job_id))
        db.commit()
        return jsonify({'ok': False, 'error': f'Agent 启动失败: {resp.get("error", "unknown")}'}), 500

    add_log('info', f'作业 #{job_id} 已创建 ({len(tasks_config)} task)'); return jsonify({'ok': True, 'job_id': job_id, 'agent_job_id': agent_job_id})

@app.route('/api/v1/jobs/<int:jid>/stop', methods=['POST'])
@require_auth
def stop_job(jid):
    """停止运行中的作业"""
    db = get_db()
    job = db.execute('SELECT * FROM jobs WHERE id=?', (jid,)).fetchone()
    if not job or not job['agent_job_id']:
        return jsonify({'ok': False, 'error': 'Not found'}), 404
    agent = db.execute('SELECT * FROM agents WHERE id=?', (job['agent_id'],)).fetchone()
    if agent:
        agent_req(dict(agent), 'POST', f'/api/v1/stop/{job["agent_job_id"]}')
    db.execute("UPDATE jobs SET status='stopped' WHERE id=?", (jid,))
    db.commit()
    add_log('info', f'作业 #{jid} 已停止')
    return jsonify({'ok': True})


# ════════════════════════════════════════════════
# 监控 API（日志/实时进度/磁盘使用）
# ════════════════════════════════════════════════

@app.route('/api/v1/monitor')
@require_auth
def monitor():
    """监控面板：日志 + 作业实时进度 + 磁盘使用"""
    db = get_db()
    # Recent logs
    logs = get_recent_logs(30)
    # Running jobs with progress from agents
    jobs = db.execute('SELECT * FROM jobs ORDER BY id DESC LIMIT 20').fetchall()
    job_list = []
    for j in jobs:
        jd = dict(j)
        if jd['agent_job_id']:
            if jd['agent_id']:
                agent = db.execute('SELECT * FROM agents WHERE id=?', (jd['agent_id'],)).fetchone()
                if agent:
                    resp = agent_req(dict(agent), 'GET', f'/api/v1/progress/{jd["agent_job_id"]}')
                    if resp.get('ok'):
                        jd['agent_completed'] = resp.get('completed', 0)
                        jd['agent_failed'] = resp.get('failed', 0)
                        jd['agent_total'] = resp.get('total', 0)
                        jd['agent_elapsed'] = resp.get('elapsed', 0)
        job_list.append(jd)
    # Disk usage
    disks = db.execute(
        'SELECT d.*, a.name as agent_name FROM disks d '
        'JOIN agents a ON a.id = d.agent_id '
        'ORDER BY d.agent_id'
    ).fetchall()
    return jsonify({
        'ok': True,
        'logs': logs,
        'jobs': [dict(j) for j in job_list[:10]],
        'disks': [dict(d) for d in disks],
    })

# ════════════════════════════════════════════════
# Web 前端入口
# ════════════════════════════════════════════════

@app.route('/')
def index():
    return WEB_HTML, 200, {'Content-Type': 'text/html; charset=utf-8'}

# ════════════════════════════════════════════════
# 启动入口
# ════════════════════════════════════════════════

def main():
    import argparse
    parser = argparse.ArgumentParser(description='dwheel-server')
    parser.add_argument('--port', type=int, default=8080)
    parser.add_argument('--init', action='store_true', help='初始化管理员账号')
    args = parser.parse_args()

    init_db()

    if args.init:
        username = input('管理员用户名 (默认 admin): ').strip() or 'admin'
        pw = input('密码 (默认 admin): ').strip() or 'admin'
        db = sqlite3.connect(DB_PATH)
        db.execute('INSERT OR IGNORE INTO users (username, password_hash, role) VALUES (?,?,?)',
                   (username, hash_password(pw), 'admin'))
        db.commit()
        db.close()
        print(f'管理员 {username} 创建完成')
        return

    print(f'dwheel-server 启动在 :{args.port}')
    print(f'浏览器访问 http://localhost:{args.port}')
    app.run(host='0.0.0.0', port=args.port, debug=False)

if __name__ == '__main__':
    main()
