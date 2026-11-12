# 采购行情网站

单人内部使用的原材料**现货**价格跟踪服务。第一阶段已完成 FastAPI、SQLite、11 个品种目录、免费源真实抓取及卓创 ABS 接入框架。`frontend/` 当前仅保留空目录，第二阶段使用 Vue 3 + Vite + ECharts；构建产物将由后端静态托管。

## 环境与安装

- Python **3.12**、Git。Windows / Linux 均可运行；Windows 的时区数据由 `tzdata` 提供。
- 可访问世铝网；卓创功能需要本人有权限的账号或登录态。
- 第一阶段无需 Node.js，未安装前端依赖。

以下命令均从项目根目录执行。PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements-dev.txt
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

Linux / macOS：

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements-dev.txt
.venv/bin/python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

仅运行服务时可只安装 `backend/requirements.txt`。停止服务使用 `Ctrl+C`。如需开发热重载，在上述 uvicorn 命令末尾加 `--reload`，保持单 worker。

本机当前终端 PATH 未配置 Python 和 Git。本次自测用已有 Python 3.12.14 创建了 `.venv`；如需重建，可使用：

```powershell
& "$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m venv .venv
```

本机网络通过已有代理访问。需要时在安装及启动命令前设置下列环境变量；正常联网的机器无需设置。账号凭据仍只从 `.env` 读取。

```powershell
$env:HTTPS_PROXY = 'http://127.0.0.1:7890'
$env:NO_PROXY = '127.0.0.1,localhost'
```

默认监听 `127.0.0.1:8000`。接口与文档仅用于本站内部，文档地址：<http://127.0.0.1:8000/docs>。本阶段没有多用户、注册或公网访问功能。

## 配置卓创

```powershell
Copy-Item .env.example .env
```

在根目录 `.env` 填写 `SCI99_USER` + `SCI99_PASS`，或填写本人账号的 `SCI99_COOKIE`。Cookie 优先；不要把账号、密码、Cookie 发到日志、代码或提交中。`.env`、`data/` 已被 Git 忽略。配置在每次手动/定时抓取时重新读取，无须重启。

没有凭据时，卓创完全跳过，状态为 `pending_credentials` / **待配置凭据**，不计为失败，也不消耗当日额度。

ABS 当前固定口径是 **华东市场 ABS 0215A 现货均价，元/吨**。`SCI99_ABS_URL` 可指定本人有订阅权限的同口径报价页（仅接受 sci99.com 域下的 HTTPS、无查询参数/片段 URL）。解析器严格校验表头“品种、市场、牌号、价格类型、均价、单位、报价日期”，并只接受 ABS / 华东 / 0215A / 现货这一行。无法确认口径、日期或均价时拒绝入库，不从新闻正文猜数值。

**卓创接入边界：** 本次未提供凭据，未验证真实账号登录或 ABS 真实价格。公开产品页采用 JS 加载，官方登录 SDK 也包含 JS 与验证流程。框架只提交官网明确给出的普通 POST 登录表单；遇 JS 登录、验证码、风控、登录异常或权限不足立即停止，不模拟加密/验证码。实际接入推荐人工登录后配置 Cookie；即便配置 Cookie，也需要在本人已订阅页面上核定真实表格结构，按实际情况完善 `adapters/sci99.py` 的解析规则。当前框架不保证默认产品页能直接返回 ABS 价格，解析失败会如实显示原因。

其余 9 个卓创品种本阶段只配置目录，标记 `pending_configuration`；具体市场、牌号、等级须后续核定后再启用，不能把 ABS 结果当作整个卓创源全部接通。包装用纸明确限定瓦楞原纸口径。

卓创每个北京时间自然日最多执行一轮（含失败）。网络请求之前，SQLite `source_runs` 唯一约束先占用当日额度；重启、手动触发与定时任务共享记录。失败后当天不会再次尝试，更换凭据也不能重置额度；请次日再运行。请求间隔至少 5 秒、超时 15 秒，仅 GET 网络/5xx 失败最多重试 1 次，登录 POST 不自动重试。验证码、401/403/429、访问异常不重试。数据仅供本站内部使用。

## 免费报价口径

只访问 [世铝网南海有色栏目](https://market.cnal.com/nanhai/)，定位最新的“南海有色（灵通）铝锭价格”日度文章，提取 **佛山地区（铝）/ 国产 A00 批售（送厂）/ 均价 / 元/吨**。同时校验区间与均价关系、文章标题和完整日期。不会切换到不含税、上海、长江或期货口径；上游结构变化时保留已有价格并报告失败。

每个价格点保存品种、价格、单位、报价口径、报价日期、来源标识与名称、来源 URL、北京时间抓取时间。同一品种/来源/口径/报价日使用唯一键更新，重复抓取不会制造多条日线。

## 接口与调度

| 接口 | 用途 |
| --- | --- |
| `GET /api/health` | 返回 `{"status":"ok"}` |
| `GET /api/commodities` | 11 个品种及来源、口径、启用状态 |
| `GET /api/prices/latest` | 全品种目录及可空的 `price_point`、`data_status`、`stale` |
| `GET /api/prices/history?code=alu_a00&days=30` | 包含今天的最近 30 个自然日，按报价日期升序 |
| `POST /api/fetch/run` | 同步执行一轮，逐源返回成功/跳过/失败；并发抓取返回 409 |
| `GET /api/sources/status` | 来源状态、原因、最近尝试/成功时间、卓创当日额度及待接入品种 |

未知 `code` 返回 404，`days` 须为 1～3650，非法参数返回 422。没有数据时返回 `null` 或空历史列表。`stale=true` 表示无数据或报价日期早于北京时间今天；周末/节假日也会标记旧日期，不伪造新报价。来源抓取失败与已有价格新旧程度分别呈现，抓取失败不会删除历史记录。

APScheduler 在**每天 15:30，Asia/Shanghai** 触发，服务启动不自动抓取。仅服务进程运行时调度有效，不补抓停机期间错过的行情；跨源隔离失败，一个源出错后仍继续处理其他源。进程内抓取锁防止手动和定时重叠，卓创额外使用数据库日额度约束。请使用单 worker，不要同时启动多个服务进程，以免免费源重复请求。

首次启动自动建表并同步 11 个品种。数据库为 `data/procurement.sqlite3`，包含 `commodities`、`price_points`，以及运行状态表 `source_states`、额度表 `source_runs`。不要删除额度表来重复触发卓创。

## 自测

保持 uvicorn 在一个终端运行，在另一个终端执行：

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests -q
.\.venv\Scripts\python.exe backend/scripts/smoke.py --fetch
```

Linux 对应使用 `.venv/bin/python`。测试使用临时 SQLite 和隔离响应，不访问真实卓创，也不会把测试价格写进项目数据库。`smoke.py --fetch` 才执行真实手动抓取，检查健康、11 品种目录、A00 最新价/历史/元数据、来源状态和参数错误；结果保存在忽略提交的 `data/selftest.json`。省略 `--fetch` 则只读验收，不重复请求上游。

也可逐个检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/health
Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/fetch/run
Invoke-RestMethod http://127.0.0.1:8000/api/prices/latest | ConvertTo-Json -Depth 8
Invoke-RestMethod 'http://127.0.0.1:8000/api/prices/history?code=alu_a00&days=30' | ConvertTo-Json -Depth 8
Invoke-RestMethod http://127.0.0.1:8000/api/sources/status | ConvertTo-Json -Depth 8
```

第一阶段变更与已执行的真实自测结果见 [STAGE1_REPORT.md](STAGE1_REPORT.md)。本阶段结束后等待确认，再开展前端。
