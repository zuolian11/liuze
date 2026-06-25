# dwheel — 分布式下载管理系统

通过 Web 管理面板控制多台 Linux Agent 执行华为云 OBS 数据下载。

## 项目结构

```
dwheel/
├── server.py         # Server 入口：Flask 启动、CLI 参数
├── db.py             # SQLite 数据库（建表、连接、迁移）
├── auth.py           # 认证模块（Token 签发/校验、密码哈希）
├── api.py            # 全部 REST API 路由
├── utils.py          # 工具函数（OBS/PG 发现、磁盘分配、日志、缓存解析）
├── templates/
│   └── index.html    # Web 管理面板
├── agent.py          # Worker Agent（部署在下载节点）
├── requirements.txt
```

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 初始化管理员

```bash
python server.py --init
```

### 3. 启动 Server + Agent

```bash
dwheel-restart   # 一键重启两个服务
# 或手动：
python server.py --port 8080 &
python agent.py --port 8081 --api-key abc123 &
```

浏览器打开 `http://<IP>:8080` 登录。

### 4. 注册 Agent

管理面板 →「机器管理」→ 添加机器（IP、端口 8081、密钥 abc123）→ 点「检测」。

---

## 工作流程

### 导入
1. 选机器 → 上传 xlsx/xls → 确认列映射 → 提取 task_id
2. 勾选需要的 task_id → 填批次名 →「保存到任务队列」
3. 选中的存为「待处理」，不选的存为「已跳过」，时长和大小自动计算

### 创建下载
4. 进入「任务队列」→ 展开批次 → 选磁盘 → 填作业名 →「创建下载」
5. 磁盘容量不足时自动拦截

### 启动下载
6. 「下载作业」→ 点对应作业的「启动」

### 监控
7. 「监控」页 → 实时速度、ETA、磁盘用量

---

## API 概览

| 分组 | 端点 | 说明 |
|------|------|------|
| 认证 | `/api/v1/auth/login` | 登录 |
| 仪表盘 | `/api/v1/dashboard` | 统计 + 磁盘告警 |
| Agent | `/api/v1/agents` | 机器增删查、检测 |
| OBS | `/api/v1/obs-sources` | 数据源配置 |
| 导入 | `/api/v1/import/preview` | 表格解析 + 列检测 |
| 任务 | `/api/v1/tasks/prepare` | 批量发现 + 入池 |
| 任务 | `/api/v1/tasks/pending` | 队列查询 + 分组 |
| 任务 | `/api/v1/tasks/pending/skip` | 跳过任务 |
| 任务 | `/api/v1/tasks/pending/batch/<name>` | 批量删除 |
| 作业 | `/api/v1/jobs` | 创建/列表/启动/停止/删除 |
| 监控 | `/api/v1/monitor` | 实时进度 + 磁盘 |

---

## 特性

- **智能表格解析** — 任意结构 xlsx/xls，自动识别列映射，提取时长
- **批次管理** — 每次导入自动分组，批量创建下载作业
- **磁盘容量检查** — 分配时自动校验 95% 上限，超容量提示
- **断点续传** — `.downloading` 临时文件 + rename
- **实时进度** — 任务级进度、下载速度、ETA、失败详情
- **磁盘告警** — 容量 > 85%/95% 分级告警
- **磁盘自动同步** — 5 分钟轮询在线 Agent
- **结构化日志** — SQLite + 文本文件，10MB 切割
