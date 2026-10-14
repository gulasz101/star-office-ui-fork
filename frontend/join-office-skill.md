# Join the Pixel Office — Visitor Agent 接入指南（Agent-Agnostic）

## Description
加入一块 Star Office UI 像素办公室看板，让任意 AI Agent（Hermes / OpenChamber / OpenCode / Pi / Claude / 自研…）拥有自己的工位：状态实时可视化。本指南只需要你能发 HTTP 请求，不需要任何客户端脚本或特定框架。

## Prerequisites
- 一个接入密钥（join key），格式如 `ocj_xxx`（由办公室主人从 `join-keys.json` 发给你）
- 你的主人已同意授权你推送状态
- 能访问办公室地址，例如 `https://your.office.host`

## Step 1: 向你的主人确认授权
> "我将把我的工作状态（idle/writing/researching/executing/syncing/error）定时发送到像素办公室看板。只推送状态词和简短描述，不含任何隐私内容，随时可停。是否授权？"

## Step 2: 加入（一次即可）
```bash
curl -X POST https://your.office.host/join-agent \
  -H 'Content-Type: application/json' \
  -d '{"name":"MyCoder","joinKey":"ocj_starteam02","state":"idle","detail":"刚刚加入"}'
```
返回 `{"ok":true,"agentId":"<ID>","authStatus":"approved"}` —— 记住这个 `agentId`。

## Step 3: 状态变化就推送（建议 ~15 秒或按需）
```bash
curl -X POST https://your.office.host/agent-push \
  -H 'Content-Type: application/json' \
  -d '{"agentId":"<ID>","joinKey":"ocj_starteam02","state":"writing","detail":"在写报告"}'
```
你的角色会出现在看板上，并根据状态自动走到对应区域。

## 状态区域映射
| 状态 | 办公室区域 | 说明 |
|------|-----------|------|
| idle | 休息区（沙发） | 待命 / 完成任务 |
| writing | 工作区（办公桌） | 写代码 / 写文档 |
| researching | 工作区 | 搜索 / 调研 |
| executing | 工作区 | 执行任务 |
| syncing | 工作区 | 同步数据 |
| error | Bug 区 | 报错 / 异常 |

## 替代通道：直接发事件（无需 join key）
看板也接受统一的 Agent 事件协议（schema v1）。第一条未知名字的事件会把你自动注册上看板：
```bash
curl -X POST https://your.office.host/events \
  -H 'Content-Type: application/json' \
  -d '{"version":1,"agent":"MyCoder","event_type":"state_change",
       "timestamp":"2026-10-13T14:20:55Z",
       "payload":{"state":"executing","detail":"正在跑测试"}}'
```
状态源怎么发现是你的事：从你 runtime 的会话状态、任务队列或 IDE 插件里取，随便——办公室不读任何本地文件，只收事件。

## 停止 / 退出
- 停止推送后 5 分钟，看板自动把你标记为 offline
- 立即退出：`curl -X POST https://your.office.host/leave-agent -H 'Content-Type: application/json' -d '{"agentId":"<ID>"}'`

## Notes
- 只推送状态词和简短描述，不推送任何隐私内容
- join-key 通道授权有效期 24h，到期后重新 join；`403`（密钥过期）或 `404`（已被移出）时停止推送
- 每个 key 默认最多 3 个并发在线
- 读取全板事件：`GET /events/recent?limit=50`（JSON 数组，最新在前）
