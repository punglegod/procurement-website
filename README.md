# 采购行情网站

单人内部使用的原材料**现货**价格跟踪网站。第二阶段完成 Vue 3 + Vite + ECharts 前端：11 品种行情表格、搜索筛选、涨跌与过期提示、7 / 30 / 90 天历史走势。FastAPI 同时提供 API 和前端构建产物，最终只运行一个服务进程。

**当前决定（2026-11-12）：只跑免费源；卓创暂缓启用，不填写凭据。** 目前真实入库源为世铝网南海灵通 A00 铝锭，其余 10 个品种保留目录，等待后续免费源接入。本阶段不扩展抓取源或 Telegram 提醒。

## 环境与安装

- Python **3.12**、Git、Node.js **20.19+ 或 22.12+**（含 npm）。Windows / Linux 均可运行；Windows 的时区数据由 `tzdata` 提供。
- 首次安装前端依赖需要访问 npm registry，采集免费报价需要访问世铝网。
- 已有 `frontend/dist` 时可使用 `--skip-build` 启动，无需 Node.js。

以下命令均从项目根目录执行。PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements-dev.txt
.\.venv\Scripts\python.exe start.py
```

Linux / macOS：

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements-dev.txt
.venv/bin/python start.py
```

安装好 Python 依赖后，**一条命令 `python start.py` 即可启动全站**（建议使用上面虚拟环境中的 Python）。脚本根据锁文件安装前端依赖，再构建到 `frontend/dist` 并启动单 worker 的 FastAPI；锁文件未变时复用依赖。依赖以 `frontend/package-lock.json` 锁定。打开 <http://127.0.0.1:8000/>，点击品种进入详情；详情采用哈希路由，例如 <http://127.0.0.1:8000/#/commodity/alu_a00>，可以直接打开和刷新。

停止服务使用 `Ctrl+C`。可选参数：

```powershell
# 复用已构建前端（构建不存在时会明确报错）
.\.venv\Scripts\python.exe start.py --skip-build
# 自定义本地端口
.\.venv\Scripts\python.exe start.py --port 8010
```

仅运行服务时可只安装 `backend/requirements.txt`。启动脚本在本机 PATH 没有 Node 时，会识别已有 Codex runtime 的 Node；普通机器使用标准 Node.js + npm 即可。本机 npm 临时工具位于忽略提交的 `.tools/npm/`，不作为项目依赖分发。

本机当前终端 PATH 未配置 Python 和 Git。本次自测用已有 Python 3.12.14 创建了 `.venv`；如需重建，可使用：

```powershell
& "$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m venv .venv
```

本机网络通过已有代理访问。需要时在安装及启动命令前设置下列环境变量；正常联网的机器无需设置。账号凭据仍只从 `.env` 读取。

```powershell
$env:HTTPS_PROXY = 'http://127.0.0.1:7890'
$env:NO_PROXY = '127.0.0.1,localhost'
# npm 安装若也需要本机已有代理：
$env:NPM_CONFIG_HTTPS_PROXY = 'http://127.0.0.1:7890'
$env:NPM_CONFIG_PROXY = 'http://127.0.0.1:7890'
```

默认监听 `127.0.0.1:8000`。接口与文档仅用于本站内部，文档地址：<http://127.0.0.1:8000/docs>。本阶段没有多用户、注册或公网访问功能。

## 卓创框架（暂缓启用）

当前不创建或填写卓创凭据；adapter 只保留第一阶段框架。以后站长明确启用时，再按根目录 `.env.example` 配置根目录 `.env` 中的 `SCI99_USER` + `SCI99_PASS` 或 `SCI99_COOKIE`。不把账号、密码、Cookie 放入日志、代码或提交；`.env`、`data/` 已被 Git 忽略。以下是保留框架的技术边界，不代表当前已启用。

没有凭据时，卓创完全跳过，状态为 `pending_credentials` / **待配置凭据**，不计为失败，也不消耗当日额度。

ABS 当前固定口径是 **华东市场 ABS 0215A 现货均价，元/吨**。`SCI99_ABS_URL` 可指定本人有订阅权限的同口径报价页（仅接受 sci99.com 域下的 HTTPS、无查询参数/片段 URL）。解析器严格校验表头“品种、市场、牌号、价格类型、均价、单位、报价日期”，并只接受 ABS / 华东 / 0215A / 现货这一行。无法确认口径、日期或均价时拒绝入库，不从新闻正文猜数值。

**卓创接入边界：** 本次未提供凭据，未验证真实账号登录或 ABS 真实价格。公开产品页采用 JS 加载，官方登录 SDK 也包含 JS 与验证流程。框架只提交官网明确给出的普通 POST 登录表单；遇 JS 登录、验证码、风控、登录异常或权限不足立即停止，不模拟加密/验证码。实际接入推荐人工登录后配置 Cookie；即便配置 Cookie，也需要在本人已订阅页面上核定真实表格结构，按实际情况完善 `adapters/sci99.py` 的解析规则。当前框架不保证默认产品页能直接返回 ABS 价格，解析失败会如实显示原因。

其余 9 个品种在第一阶段目录中标记 `pending_configuration`，ABS 标记 `pending_credentials`。第二阶段未改动源分工代码或接入 adapter，页面说明这些空目录等待免费源接入；旧目录中的待核定口径不代表已验证的报价。后续逐个接免费源时，须在 `backend/app/config.py` 核定市场、牌号、等级与口径，包装用纸明确限定瓦楞原纸。

卓创每个北京时间自然日最多执行一轮（含失败）。网络请求之前，SQLite `source_runs` 唯一约束先占用当日额度；重启、手动触发与定时任务共享记录。失败后当天不会再次尝试，更换凭据也不能重置额度；请次日再运行。请求间隔至少 5 秒、超时 15 秒，仅 GET 网络/5xx 失败最多重试 1 次，登录 POST 不自动重试。验证码、401/403/429、访问异常不重试。数据仅供本站内部使用。

## 免费报价口径

只访问 [世铝网南海有色栏目](https://market.cnal.com/nanhai/)，定位最新的“南海有色（灵通）铝锭价格”日度文章，提取 **佛山地区（铝）/ 国产 A00 批售（送厂）/ 均价 / 元/吨**。同时校验区间与均价关系、文章标题和完整日期。不会切换到不含税、上海、长江或期货口径；上游结构变化时保留已有价格并报告失败。

每个价格点保存品种、价格、单位、报价口径、报价日期、来源标识与名称、来源 URL、北京时间抓取时间。同一品种/来源/口径/报价日使用唯一键更新，重复抓取不会制造多条日线。

## 接口与调度

| 接口 | 用途 |
| --- | --- |
| `GET /api/health` | 返回 `{"status":"ok"}` |
| `GET /api/commodities` | 11 个品种及来源、口径、启用状态 |
| `GET /api/prices/latest` | 全品种目录、可空 `price_point`、`data_status`、`stale`、`change_amount`、`change_percent`、`previous_quote_date` |
| `GET /api/prices/history?code=alu_a00&days=30` | 包含今天的最近 30 个自然日，按报价日期升序 |
| `POST /api/fetch/run` | 同步执行一轮，逐源返回成功/跳过/失败；并发抓取返回 409 |
| `GET /api/sources/status` | 来源状态、原因、最近尝试/成功时间、卓创当日额度及待接入品种 |

未知 `code` 返回 404，`days` 须为 1～3650，非法参数返回 422。没有数据时返回 `null` 或空历史列表。`stale=true` 表示无数据或报价日期早于北京时间今天；周末/节假日也会标记旧日期，不伪造新报价。来源抓取失败与已有价格新旧程度分别呈现，抓取失败不会删除历史记录。

涨跌只比较相同品种 / 来源 / 口径 / 单位的最近两个报价日（跨周末仍取上一真实报价日）。涨跌额 = 最新价 − 上一报价日价格；涨跌幅 = 涨跌额 ÷ 上一报价日价格 × 100%，四舍五入保留两位小数。缺少对比报价时返回 `null`，页面显示“—”；持平才显示 0。详情图只绘制区间内已有报价，单点也会渲染，不补造历史。表格有真实旧报价时整行标黄，无报价时明确显示待接入 / 待配置。

页面“刷新数据”只重新读取 API / SQLite，不触发上游采集。首页记录页面读取时间，详情保留报价日期、抓取时间和来源原文链接；请求失败时显示错误，可重试。

## 前端开发与单独构建

以下是开发用途，不是日常全站运行所必需。两个终端分别执行：

```powershell
# 终端一：后端，单 worker
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --workers 1 --reload
# 终端二：Vite 开发服务器，/api 代理到后端
cd frontend
npm ci
npm run dev
```

打开 Vite 输出的本地地址（默认 <http://127.0.0.1:5173>）。单独构建：`cd frontend` 后执行 `npm ci`、`npm run build`。使用构建产物前重启后端，使其挂载 `frontend/dist`。日常推荐根目录 `python start.py`，无需开发服务器。

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

第二阶段只读全站验收（先运行 `start.py`），随后执行页面与 SVG 组件自测：

```powershell
.\.venv\Scripts\python.exe backend/scripts/smoke_frontend.py
cd frontend
npm test
```

`smoke_frontend.py` 检查首页、构建资源、API 路由、11 品种、铝锭真实报价和 7 / 30 / 90 天历史，导出忽略提交的 `data/stage2-live.json`；`npm test` 读取此真实响应验收组件，包含 ECharts SVG 实际渲染、时间切换、搜索筛选、黄标、空历史、失败重试、并发响应防覆盖。自测通过后输出 `data/stage2-qa/alu-a00-chart.svg`。这些脚本不会写入价格库或触发抓取。服务非默认端口时给脚本加 `--base-url http://127.0.0.1:8010`。

第一阶段交付记录见 [STAGE1_REPORT.md](STAGE1_REPORT.md)，第二阶段变更与自测结果见 [STAGE2_REPORT.md](STAGE2_REPORT.md)。第二阶段完成后停下等待确认。
