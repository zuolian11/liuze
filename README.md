# liuze
work and study
# dwheel — 分布式下载管理系统

通过 Web 管理面板控制多台 Linux Agent 执行华为云 OBS 数据下载，支持智能磁盘分配、实时进度监控、断点续传。

## 项目结构

```
dwheel/
├── server.py         # Server 入口：Flask 启动、CLI 参数
├── db.py             # SQLite 数据库（建表、连接、迁移）
├── auth.py           # 认证模块（Token 签发/校验、密码哈希）
├── api.py            # 全部 REST API 路由
├── utils.py          # 工具函数（OBS/PG 发现、磁盘分配、日志、缓存解析）
├── templates/
│   └── index.html    # Web 管理面板（Alpine.js + TailwindCSS）
├── agent.py          # Worker Agent（部署在下载节点）
├── requirements.txt
```

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

或在首次启动时自动安装（Server 内置 `ensure_deps()`）。

### 2. 初始化管理员账号

```bash
python server.py --init
# 输入用户名密码，回车确认
```

### 3. 启动 Server

```bash
python server.py --port 8080
```

浏览器打开 `http://localhost:8080`，用上一步创建的账号登录。

### 4. 在下载节点启动 Agent

```bash
python agent.py --port 8081 --api-key abc123
```

回到管理面板 →「机器管理」→ 添加机器（IP、端口 8081、密钥 abc123）→ 点「检测」确认在线。

## 工作流程

1. **导入** → 粘贴 task_id 或上传 xlsx/xls 表格，自动识别列映射、提取时长
2. **导入页面一键下载** → 选机器、设时长、自动分配磁盘、直接启动
3. **分配作业** → 查看任务池与磁盘容量，预览分配方案，逐盘下载
4. **下载作业** → 查看所有作业及实时进度
5. **任务队列** → 管理已完成/待处理 task_id，支持重试
6. **监控面板** → 磁盘使用、作业进度、操作日志实时刷新

## API 概览

| 分组 | 端点 | 说明 |
|------|------|------|
| 认证 | `/api/v1/auth/login` | 登录获取 Token |
| 仪表盘 | `/api/v1/dashboard` | 统计数据 + 磁盘告警 |
| Agent | `/api/v1/agents` | 机器增删查、检测、磁盘同步 |
| OBS | `/api/v1/obs-sources` | OBS 数据源配置 |
| 导入 | `/api/v1/import/preview` | 上传表格 → 列检测预览 |
| 导入 | `/api/v1/import/extract` | 确认列映射 → 提取数据 |
| 任务 | `/api/v1/tasks/prepare` | 批量发现 + 自动分配 + 入池 |
| 任务 | `/api/v1/tasks/allocate-preview` | 贪心分配预览 |
| 作业 | `/api/v1/jobs` | 创建/列表/进度/停止 |
| 监控 | `/api/v1/monitor` | 实时作业进度 + 磁盘使用 |

## 特性

- **智能表格解析** — 兼容任意结构的 xlsx/xls，自动试别 task_id 和时长列，支持手动确认映射
- **智能磁盘分配** — 容量感知贪心算法，填满优先策略，支持手动覆盖
- **任务池管理** — 导入后 task_id 持久存储，支持重试、状态追踪
- **断点续传** — 下载先写临时文件再 rename，重启不重复下载已完成文件
- **实时进度** — 任务级进度条、下载速度、失败文件详情（含 OBS 报错原因）
- **磁盘告警** — 容量 > 85% 提示、> 95% 红色告警
- **磁盘自动同步** — 每 5 分钟轮询在线 Agent 更新剩余容量
- **结构化日志** — 同时写入 SQLite 和文本文件，10MB 自动切割保留 5 个备份
