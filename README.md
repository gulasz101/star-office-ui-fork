# Star Office UI

🌐 语言: **中文** | [English](./README.en.md) | [日本語](./README.ja.md)

![Star Office UI 封面](docs/screenshots/readme-cover-2.jpg)

**像素风 AI 办公室看板** —— 实时可视化你的 AI 助手的工作状态：谁在干什么、昨天做了什么、是否在线，一眼看清。

支持多 Agent 协作、三语界面（中/英/日）、AI 房间设计、桌面宠物模式。

**Agent 无关（Agent-Agnostic）设计。** 办公室只讲一种开放 HTTP 协议——任何能发请求的 AI Agent 都能上板：Hermes、OpenChamber、OpenCode、Pi、Claude，或你自己的脚本。后端不依赖、也不读取任何 Agent 框架的工作区文件。

> 本项目由 **[Ring Hyacinth](https://x.com/ring_hyacinth)** 与 **[Simon Lee](https://x.com/simonxxoo)** 联合创作。本仓库是其维护分支（[gulasz101/star-office-ui-fork](https://github.com/gulasz101/star-office-ui-fork)）：事件入库、协议统一、并完成 OpenClaw 解耦。
> 欢迎提 Issue / PR —— 感谢每一位贡献者。

---

## ✨ 快速开始

### 方式一：让你的 AI Agent 帮你部署

任何能执行 shell 和 HTTP 请求的 Agent 都能完成部署。把这段话发给它：

```text
帮我部署 Star Office UI：
1. git clone https://github.com/gulasz101/star-office-ui-fork && cd star-office-ui-fork
2. python3 -m pip install -r backend/requirements.txt   （需要 Python 3.10+）
3. cd backend && python3 app.py    # 起服务：http://127.0.0.1:19000
4. 然后用 POST /events 推送你自己的状态，见 README「Agent 接入」一节。
```

Agent 会自动 clone、装依赖、起后端，并用第一条事件把自己注册上看板。

### 方式二：30 秒手动部署

> **需要 Python 3.10+**（代码使用了 `X | Y` 联合类型语法，3.9 及以下不支持）

```bash
# 1) 克隆仓库
git clone https://github.com/gulasz101/star-office-ui-fork.git
cd star-office-ui-fork

# 2) 安装依赖（Python 3.10+）
python3 -m pip install -r backend/requirements.txt

# 3) 启动后端 —— 事件数据库自动初始化
#    （默认 SQLite；如需 PostgreSQL 设置 DATABASE_URL=postgresql+psycopg2://...）
cd backend
python3 app.py
```

打开 **http://127.0.0.1:19000**，推一条状态试试：

```bash
curl -X POST http://127.0.0.1:19000/events \
  -H 'Content-Type: application/json' \
  -d '{"version":1,"agent":"hermes","event_type":"state_change",
       "timestamp":"2026-10-13T14:20:55Z","payload":{"state":"writing","detail":"正在整理文档"}}'
```

![Star Office UI 预览](docs/screenshots/readme-cover-1.jpg)

---

## 🤔 适合谁用？

### 有 AI Agent 的用户
这是**完整体验**。每个 Agent（Hermes、OpenChamber、OpenCode、Pi、Claude……）工作时自己 POST `state_change` 事件，像素小人实时走到对应区域——多个 Agent 共用一块看板，各用各的名字。

### 没有 Agent 运行时的用户
照样可以部署使用：
- 用 `curl` 一行命令 / cron / CI 步骤推送状态（任何 HTTP 客户端都行）
- 用页面内置的状态控制条（走 `POST /set_state`）
- 当像素风个人状态页或远程协作看板用

---

## 📋 功能特性

1. **状态可视化** —— 6 种状态（`idle` / `writing` / `researching` / `executing` / `syncing` / `error`）映射到不同办公区域，带动画精灵与对话气泡
2. **昨日小记** —— 自动读取 `memory/*.md` 最新日记，脱敏后以"昨日小记"卡片展示
3. **多 Agent 协作** —— 通过 join key 邀请其他 Agent 加入办公室，或直接让它们用 `POST /events` 自助注册，实时查看所有人状态
4. **三语界面** —— 中/英/日一键切换，全部 UI 文案、气泡、加载提示即时更新
5. **自定义美术资产** —— 侧边栏管理角色、场景、装饰；动态帧同步防闪烁
6. **AI 房间设计** —— 接入自己的 Gemini API 生成新办公室背景；不接 API 也能用核心功能
7. **移动端友好** —— 手机打开即可速览状态
8. **安全加固** —— 侧边栏密码保护、生产环境弱密码拦截、加固 Session Cookie
9. **灵活公网访问** —— Cloudflare Tunnel 一键公网分享，或自备域名/反代
10. **桌面宠物模式** —— 可选 Electron 桌面壳，把办公室变成透明桌面小组件（见下文）
11. **事件数据库** —— 每个事件都落库（SQLite/PostgreSQL），可通过 `GET /events/recent` 读取；看板是事件流的纯视图

---

## 🚀 详细部署指南

### 1) 安装依赖

```bash
cd star-office-ui-fork
python3 -m pip install -r backend/requirements.txt
```

### 2) 选择数据库（可选）

不需要再复制状态文件：主状态与事件都存在数据库里，默认自动落到本地 SQLite 文件；如需 PostgreSQL，导出 `DATABASE_URL=postgresql+psycopg2://user:pass@host/star_office` 后正常启动即可——迁移在启动时自动执行。

### 3) 启动后端

```bash
cd backend
python3 app.py
```

打开 `http://127.0.0.1:19000`

> ✅ 本地开发直接用默认配置即可；生产部署请把 `.env.example` 复制为 `.env`，为 `FLASK_SECRET_KEY` 和 `ASSET_DRAWER_PASS` 设置强随机值，避免弱密码与会话泄露。
>
> 🏷️ 想给办公室牌匾起名字？启动前设置 `OFFICE_NAME="Wojtek 的办公室"` 即可——后端不再读取任何 Agent 工作区的文件。

### 4) 切换状态

推送事件（面向 Agent 的入口，见下一节）：

```bash
curl -X POST http://127.0.0.1:19000/events -H 'Content-Type: application/json' -d '{
  "version": 1, "agent": "hermes", "event_type": "state_change",
  "timestamp": "2026-10-13T14:20:55Z",
  "payload": {"state": "executing", "detail": "正在跑测试"}
}'
```

或者直接用页面上的状态控制条（走 `POST /set_state`，只作用于主 Agent）。

### 5) 公网访问（可选）

```bash
cloudflared tunnel --url http://127.0.0.1:19000
```

把 `https://xxx.trycloudflare.com` 链接发给任何人。

### 6) 验证部署（可选）

```bash
python3 scripts/smoke_test.py --base-url http://127.0.0.1:19000
```

全部 `OK` 即部署完成。

---

## 🤖 Agent 接入（任意 Agent，一套协议）

办公室只有一个写入口：**`POST /events`**。没有需要安装的客户端脚本、没有共享的工作区文件、没有必须配置的框架——只要你的 Agent 会发 HTTP POST，它就能加入。

### 事件信封（Schema v1）

每个事件是一个小 JSON 信封（定义在 `backend/schemas/event_v1.json`）：

| 字段 | 类型 | 说明 |
|------|------|------|
| `version` | int | 固定为 `1` |
| `agent` | string | Agent 显示名，如 `hermes`、`openchamber`、`opencode`、`pi`、`claude`。**未知名字的第一条事件会自动注册**该 Agent |
| `event_type` | string | 如 `state_change`、`tool_call`、`file_edit`、`command_run`、`session_start`、`session_end`——非白名单，未知类型原样入库 |
| `timestamp` | string | ISO-8601 时刻（带时区则归一化为 UTC） |
| `payload` | object | 类型自定义数据；`state_change` 用 `{"state": "<6 状态之一>", "detail": "<一句话>"}` |

```bash
curl -X POST https://your.office.host/events \
  -H 'Content-Type: application/json' \
  -d '{"version":1,"agent":"claude","event_type":"state_change",
       "timestamp":"2026-10-13T14:20:55Z",
       "payload":{"state":"researching","detail":"正在读代码库"}}'
```

- `201 {"ok": true}` —— 已入库，一个轮询周期内即可在看板看到
- `400 {"ok": false, "error": "<字段>: <怎么改>"}` —— 错误信息点名出问题的字段，方便 hook 自纠错

**6 状态 → 3 区域：**

| 状态 | 办公室区域 | 何时使用 |
|------|-----------|----------|
| `idle` | 🛋 休息区（沙发） | 待命 / 完成任务 |
| `writing` | 💻 工作区（办公桌） | 写代码 / 写文档 |
| `researching` | 💻 工作区 | 搜索 / 调研 |
| `executing` | 💻 工作区 | 执行命令 / 跑任务 |
| `syncing` | 💻 工作区 | 同步数据 / 推送 |
| `error` | 🐛 Bug 区 | 报错 / 排查 |

### 常见 Agent 的挂接方式

套路永远只有一个——状态一变，发一条 `curl`（或任意 HTTP 调用）：

| Agent | 挂接点 |
|-------|--------|
| Hermes | 动作循环的任务开始/结束处：POST `state_change`（`executing` → `idle`） |
| OpenChamber | 会话 hook 或 Itsaplan bridge 运行时：`session_start` + `state_change` |
| OpenCode | `opencode.json` 自定义命令 / 插件事件：每次工具调用 `tool_call`，工作/待机 `state_change` |
| Pi | 驱动它的调度器的状态迁移处 |
| Claude (Code) | `Stop` / `PostToolUse` hook，或在 `CLAUDE.md` 写规则："开始任务前 POST executing，完成后 POST idle" |

在你 Agent 支持的任意指令文件里加一次规则即可：

```markdown
## Star Office 状态同步
- 开始任务前：POST /events 一条 state "executing" 事件，detail 写一句话
- 完成任务后：POST /events 一条 state "idle" 事件
只外发状态词和简短描述——绝不外发文件内容。
```

### 读取路径

`GET /events/recent` 按新→旧返回事件数组（`limit` ≤ 200，可用 `agent`、`event_type`、`since` 过滤，详见下方 API 表）。看板自身就在轮询它，外部组件也可以走同一个门。

### 邀请访客 Agent（join key 名册通道）

需要审批、在线 TTL、并发上限的受控访客，用经典加入流程：

**第 1 步：准备接入密钥**

首次启动后端时，若项目根目录没有 `join-keys.json`，服务会自动根据 `join-keys.sample.json` 生成一份（内含示例密钥 `ocj_example_team_01`）。之后编辑生成的 `join-keys.json` 增删密钥即可；默认每个密钥最多 3 个并发访客。

**第 2 步：访客注册并推送**

```bash
# 加入一次 —— 返回 {"ok":true,"agentId":"42","authStatus":"approved"}
curl -X POST https://your.office.host/join-agent -H 'Content-Type: application/json' \
  -d '{"name":"AliceBot","joinKey":"ocj_starteam02","state":"idle","detail":"刚刚加入"}'

# 之后在状态变化时推送（建议约 15 秒一次或按需）
curl -X POST https://your.office.host/agent-push -H 'Content-Type: application/json' \
  -d '{"agentId":"42","joinKey":"ocj_starteam02","state":"writing","detail":"在写报告"}'
```

访客会出现在看板上，随状态走向对应区域；5 分钟无推送自动标记离线，`POST /leave-agent` 可退出。

**第 3 步（可选）：把接入说明书发给访客**

`frontend/join-office-skill.md` 是一份可直接粘贴的、Agent 无关的接入说明——任何 Agent 用纯 `curl` 就能照着做。

---

## 📡 API 一览

| 端点 | 说明 |
|------|------|
| `POST /events` | **写入一条 Agent 事件（上文 v1 信封）；未知 Agent 自动注册** |
| `GET /events/recent` | 读取事件流：新→旧的 `{id, agent, event_type, payload, created_at}` 数组；参数 `limit`（≤200）、`agent`（大小写不敏感）、`event_type`、`since`（ISO） |
| `GET /health` | 健康检查 |
| `GET /status` | 主 Agent 状态（设置了 `OFFICE_NAME` 环境变量时附带 `officeName`） |
| `POST /set_state` | 设置主 Agent 状态（页面内置控制条；落一条 `state_change` 事件） |
| `GET /agents` | 全部 Agent 名册（含在线/授权状态） |
| `POST /join-agent` | 访客用接入密钥加入 |
| `POST /agent-push` | 访客推送状态（agentId + joinKey） |
| `POST /leave-agent` | 访客退出 |
| `GET /yesterday-memo` | 昨日小记 |
| `GET /config/gemini` | 读取 Gemini API 配置 |
| `POST /config/gemini` | 设置 Gemini API 配置 |
| `GET /assets/generate-rpg-background/poll` | 轮询生图进度 |

---

## 🖥 桌面宠物模式（可选）

`desktop-pet/` 目录是基于 **Electron** 的桌面壳，把像素办公室变成透明桌面小组件。

```bash
cd desktop-pet
npm install
npm run dev
```

- 启动时自动拉起 Python 后端
- 窗口默认指向 `http://127.0.0.1:19000/?desktop=1`
- 可通过环境变量自定义项目路径与 Python 路径

> ⚠️ 这是**可选的实验特性**，主要在 macOS 上开发测试，详见 [`desktop-pet/README.md`](./desktop-pet/README.md)。
>
> 🙏 桌面宠物模块由 [@Zhaohan-Wang](https://github.com/Zhaohan-Wang) 独立贡献——感谢！

---

## 🎨 美术资产与许可

### 素材来源

访客角色动画使用 **LimeZu** 免费素材：
- [Animated Mini Characters 2 (Platformer) [FREE]](https://limezu.itch.io/animated-mini-characters-2-platform-free)

再分发或演示时请保留出处，并遵守原始许可。

### 许可

- **代码 / 逻辑：MIT**（见 [`LICENSE`](./LICENSE)）
- **美术资产：仅限非商用**（学习 / 演示 / 分享）

> 商用请替换全部美术资产（本分支正在用 Apache-2.0 模型重制整套素材，见项目看板）。

---

## 📝 更新日志

| 日期 | 摘要 | 详情 |
|------|------|------|
| 2026-10 | 🌱 **分支计划** —— Phase 1–4：事件数据库（SQLite/Postgres）、`POST /events` 写入 + `GET /events/recent` 读取，并完成 OpenClaw 解耦（删除 `SKILL.md`、`set_state.py`、`office-agent-push.py`，接入方式以本文为准） | 看板 Issue SOU-18 … SOU-46 |
| 2026-03-06 | 🔌 默认端口调整 —— 默认后端端口从 18791 调整为 19000，以避开本地浏览器控制服务端口冲突；同步更新脚本、桌面壳与文档默认值 | [`docs/CHANGELOG_2026-03.md`](./docs/CHANGELOG_2026-03.md) |
| 2026-03-05 | 📱 稳定性修复 —— CDN 缓存修正、异步生图、移动端侧边栏 UX、接入密钥过期与并发上限 | [`docs/UPDATE_REPORT_2026-03-05.md`](./docs/UPDATE_REPORT_2026-03-05.md) |
| 2026-03-04 | 🔒 P0/P1 安全加固 —— 弱密码拦截、后端重构、状态超时自动回落、骨架屏加载 | [`docs/UPDATE_REPORT_2026-03-04_P0_P1.md`](./docs/UPDATE_REPORT_2026-03-04_P0_P1.md) |
| 2026-03-03 | 📋 开源发布清单完成 | [`docs/OPEN_SOURCE_RELEASE_CHECKLIST.md`](./docs/OPEN_SOURCE_RELEASE_CHECKLIST.md) |
| 2026-03-01 | 🎉 **v2 重构** —— 三语支持、资产管理系统、AI 房间设计、全套美术更新 | [`docs/FEATURES_NEW_2026-03-01.md`](./docs/FEATURES_NEW_2026-03-01.md) |

---

## 📁 项目结构

```text
star-office-ui-fork/
├── backend/            # Flask 后端
│   ├── app.py          # 路由：/events、/events/recent、/status、/agents ...
│   ├── db.py           # SQLite/Postgres 事件适配器（分支 Phase 1）
│   ├── schemas/event_v1.json  # POST /events 契约
│   ├── migrations/     # Alembic 数据库迁移
│   ├── requirements.txt
│   └── run.sh
├── frontend/           # 前端页面与素材
│   ├── index.html
│   ├── join.html
│   ├── invite.html
│   ├── join-office-skill.md   # 可直接粘贴的访客接入说明（Agent 无关）
│   └── layout.js
├── desktop-pet/        # Electron 桌面壳（可选）
├── docs/               # 文档与截图
│   └── screenshots/
├── scripts/            # 冒烟测试、安全检查、开发辅助
├── state.sample.json     # 旧状态模板（当前状态已入库）
├── join-keys.sample.json # 接入密钥模板（运行时生成 join-keys.json）
└── LICENSE               # MIT 许可证
```

---

## ⭐ Star History

[![Star History Chart](https://api.star-history.com/image?repos=ringhyacinth/Star-Office-UI&type=date&legend=top-left)](https://www.star-history.com/?repos=ringhyacinth%2FStar-Office-UI&type=date&legend=top-left)
