# 第三阶段：推送服务

日期：2026-11-12。基于第一阶段 `5acca1e`、第二阶段 `a899fe0` 开发；仅完成本阶段。

## 变更摘要

- 新增 SQLite `alert_rules`：按品种配置日涨跌幅绝对值 ≥ X%、价格 ≥ 阈值、价格 ≤ 阈值；支持新增、编辑、启用 / 暂停和删除。保存来源、单位和口径快照，不混用报价。
- 新增 `push_logs`：保存发送内容、时间、结果和 Telegram message_id；删除规则后保留日志。网络请求前通过唯一约束占用北京时间自然日额度；同规则同品种每天最多尝试一次，并发、重启、改阈值不会重置。失败、超时及中断保留额度；测试推送独立记录。
- Telegram 只从根目录 `.env` 读取 `TELEGRAM_BOT_TOKEN`、`TELEGRAM_CHAT_ID`；不继承同名进程变量。缺任一项时页面与接口显示待配置，不请求网络、不占规则额度。`.env.example` 已补占位说明；真实 `.env` 和数据库仍被 Git 忽略。
- 行情入库后独立检查提醒，另每 5 分钟检查一次；提供手动检查 API / 按钮。仅当天报价参与发送，涨跌只比较相同品种、来源、口径、单位的上一真实报价日。未接源可先设规则，明确显示等待数据。
- 新增 `#/alerts` 规则页、`#/push-logs` 日志页和发送测试按钮；导航同时适配窄屏。README 补充配置、全部接口、调度、去重语义、代理及自测命令。启动仍为 `python start.py`。

## 启动与服务验收

实际运行 `.venv/Scripts/python.exe start.py --port 8010`，Vue 构建成功，FastAPI 单进程在 `http://127.0.0.1:8010` 启动。网络测试使用本机已有代理 `HTTPS_PROXY=http://127.0.0.1:7890`，`NO_PROXY=127.0.0.1,localhost`。

`smoke_frontend.py --base-url http://127.0.0.1:8010`：首页和全部 6 个构建资源 HTTP 200；健康接口、缺失 API / 资源路由、11 品种列表、铝锭 23800 元/吨（2026-11-12）、7 / 30 / 90 天历史全部通过。项目价格库仍只含原有一条真实铝锭报价；回放未向首页插入历史。

`smoke_alerts.py --base-url http://127.0.0.1:8010`：真实服务规则创建、更新、查询、删除、非法阈值 422 和日志读取通过。验收创建的暂停规则均已删除，既有规则未更改。证据为忽略提交的 `data/stage3-live.json`。

## 真实 Telegram 发送日志

首次检查时根目录尚无 Telegram 配置。验收期间 `.env` 出现了 token 和 chat_id；没有在代码、报告或提交中记录它们。首次测试因服务未使用本机代理遇网络异常，日志 #1 保留 `unknown` / 送达未知，没有伪报成功。使用代理重启本次验收服务后，第二次测试返回：

```text
created_at:          2026-11-12T13:36:49+08:00
finished_at:         2026-11-12T13:36:51+08:00
log_id:              2
kind:                test
status:              sent
detail:              Telegram 已接受消息
telegram_message_id: 456
message:             采购行情 · Telegram 测试推送
                     时间：2026-11-12T13:36:49+08:00
                     这是一条通道测试消息，不代表行情规则触发。
```

这条测试通过按钮所用的同一 `POST /api/alerts/test` 接口真实发送，并校验了 Telegram `ok=true` 与有效 message_id。发送成功表示 Telegram 已接受；目标会话的实际收信已向用户发出确认请求，交付时以用户回复为准。缺配置路径由隔离单元测试和 Vue 按钮组件测试完整覆盖，不计为失败。

## 真实历史回放

执行 `backend/scripts/replay_alerts.py --download-history`，从世铝网公开栏目克制下载最近三个报价日，再由原免费 adapter 严格解析相同口径：南海灵通佛山国产 A00 批售（送厂）均价，元/吨。

| 报价日 | 真实均价 | 来源原文 |
| --- | ---: | --- |
| 2026-11-10 | 23800 | [世铝网](https://market.cnal.com/nanhai/2026/11-10/1794281786672033.shtml) |
| 2026-11-11 | 23230 | [世铝网](https://market.cnal.com/nanhai/2026/11-11/1794368242672095.shtml) |
| 2026-11-12 | 23800 | [世铝网](https://market.cnal.com/nanhai/2026/11-12/1794454767672170.shtml) |

11 月 11 日跌幅约 -2.3950%，12 日涨幅约 +2.4537%。回放设置日涨跌幅绝对值 ≥ 0.01%、价格 ≥ 23800、价格 ≤ 23800，三种规则在 12 日都触发；另三条未达到的条件均跳过。首日缺少前价时跳过涨跌规则。重建服务对象后同日检查、四路并发检查、修改已触发规则后检查均去重，跨日可重新触发。共 7 次**隔离回放事件**，明确记为 `replayed`，没有调用 Telegram，也没有伪装成真实发送。

结果：PASS。原始 HTML / URL 清单位于 `data/stage3-history/`，完整规则、逐日结果与日志在 `data/stage3-replay.json`。再次执行不带 `--download-history` 的命令可离线复验。所有回放价格只写临时 SQLite，结束后自动清理。

## 自动测试与限制

- 后端：`python -m pytest backend/tests -q` → **53 passed**，涵盖原抓取链路、涨跌及到价等号边界、未四舍五入比较、过期 / 缺数据 / 口径变更、暂停、跨日 / 并发 / 重启 / 修改后去重、缺配置恢复、真实发送响应判定、错误 / 超时不重试、敏感信息不进入日志、规则 CRUD 与校验。
- 前端：`npm test` → **2 files / 8 tests passed**，包含原真实行情与图表组件，以及规则新增 / 编辑 / 暂停 / 删除、测试按钮待配置反馈和日志刷新、接口错误反馈与按钮恢复。
- 前端生产构建通过。现有 ECharts chunk 仍有 Vite 500 kB 提示；后端测试有现有 FastAPI / Starlette httpx 兼容弃用提示，均不影响验收。
- 浏览器接入工具返回 `The browser access host is unavailable`，Chrome UI 访问又返回 `Computer Use was not approved to use Google Chrome`。因此未完成真实浏览器点击和截图验收；采用运行中的 HTTP 服务与 Vue 挂载组件验证，明确保留此限制。

## 提交状态

尝试仅暂存本阶段文件时，当前执行环境返回：

```text
fatal: Unable to create 'D:/Project/procurement-website/.git/index.lock': Permission denied
```

当前沙箱允许编辑项目源码，但 `.git` 目录只有读权限，且本会话不允许请求提权。因此没有创建第三阶段 commit，也没有声称已提交。已提供 `commit_stage3.ps1`，可在项目目录的普通 PowerShell 执行 `./commit_stage3.ps1`；它只暂存第三阶段文件并生成一个提交 `feat: stage3 Telegram alerts and delivery logs`，拒绝纳入已有暂存内容，不包含原有未跟踪提示词 / 批处理、真实 `.env` 或 `data/`。

本阶段代码与可执行自测已完成；真实浏览器点击、用户收信确认与 Git 提交限制如上。停在第三阶段，不开展下一阶段。
