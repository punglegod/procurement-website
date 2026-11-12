# 采购行情网站

单人内部使用的原材料**现货**价格跟踪网站。包含 11 品种行情表格、搜索筛选、涨跌与过期提示、7 / 30 / 90 天历史走势，以及第三阶段的提醒规则、Telegram 推送和发送日志。FastAPI 同时提供 API 和前端构建产物，最终只运行一个服务进程。

**当前决定（2026-11-12）：只跑免费源；卓创暂缓启用，不填写凭据。** 第四阶段已逐个验证全部 11 个品种的免费公开现货源。每个品种固定一个报价口径，保留真实报价日；不补造历史。逐品种进度见 [STAGE4_PROGRESS.md](STAGE4_PROGRESS.md)，完整自测与提交限制见 [STAGE4_REPORT.md](STAGE4_REPORT.md)。

## 环境与安装

- Python **3.12**、Git、Node.js **20.19+ 或 22.12+**（含 npm）。Windows / Linux 均可运行；Windows 的时区数据由 `tzdata` 提供。
- 首次安装前端依赖需要访问 npm registry，采集免费报价需要访问下表列出的公开站点。
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

卓创在 `SOURCES["sci99"]["enabled"] = False` 明确休眠：无论是否误填凭据均跳过，状态为 `pending_credentials` / **待配置凭据**，不消耗当日额度。`adapters/sci99.py` 保留原框架未改动，全部品种已与其解耦；启用须另经站长明确指示与重新核定解析。

第一阶段卓创 ABS 框架曾限定 **华东市场 ABS 0215A 现货均价，元/吨**，只接受完整表头和该市场/牌号/现货行。第四阶段 ABS 使用下表中的独立免费来源；旧卓创框架不能向当前免费口径入库。历史旧来源记录保留，但不混入最新价、历史图或涨跌比较。

**卓创接入边界：** 本次未提供凭据，未验证真实账号登录或 ABS 真实价格。公开产品页采用 JS 加载，官方登录 SDK 也包含 JS 与验证流程。框架只提交官网明确给出的普通 POST 登录表单；遇 JS 登录、验证码、风控、登录异常或权限不足立即停止，不模拟加密/验证码。实际接入推荐人工登录后配置 Cookie；即便配置 Cookie，也需要在本人已订阅页面上核定真实表格结构，按实际情况完善 `adapters/sci99.py` 的解析规则。当前框架不保证默认产品页能直接返回 ABS 价格，解析失败会如实显示原因。

品种与来源全部维护在 `backend/app/config.py`。新源使用各自状态标识，失败只影响对应品种；同站点的不同 adapter 也遵守请求间隔。网络/5xx 最多重试一次，15 秒超时；安全检查、验证码、403/429 立即停手，不执行 JS 校验或绕过登录。

卓创每个北京时间自然日最多执行一轮（含失败）。网络请求之前，SQLite `source_runs` 唯一约束先占用当日额度；重启、手动触发与定时任务共享记录。失败后当天不会再次尝试，更换凭据也不能重置额度；请次日再运行。请求间隔至少 5 秒、超时 15 秒，仅 GET 网络/5xx 失败最多重试 1 次，登录 POST 不自动重试。验证码、401/403/429、访问异常不重试。数据仅供本站内部使用。

## 免费报价口径

| 品种 | 公开来源 | 固定现货口径（均为元/吨） |
| --- | --- | --- |
| A00 铝锭 | [世铝网](https://market.cnal.com/nanhai/) | 南海灵通佛山国产 A00 批售送厂均价 |
| ABS | [普拉司日报](https://www.plasway.com/news/market-flash-news?id=198) | 华南吉化（揭阳）0215A 现货参考价 |
| PC | 普拉司日报 | 华南山东利华益维远 WY-111BR 现货参考价 |
| PMMA | [塑价365](https://www.pla365.com/pmma/) | 转载生意社橡塑大宗榜现货基准价，市场均价，非牌号成交价 |
| 碳酸锂 | [我的钢铁新能源](https://xny.mysteel.com/) | 电池级早盘 Li2CO3≥99.5% 现货中间价；不取升贴水、合约价或晚盘 |
| DMC | [有机硅商城](https://www.yjgmall.com/news/list.php?catid=73106) | 新料主流报价区间低端，非均价；税运未注明 |
| 硅橡胶 | 有机硅商城 | 普通生胶主流报价区间低端，非混炼胶；税运未注明 |
| PE | 普拉司日报 | 华南中石化海南 LLDPE DFDA 7042 现货参考价 |
| PP 粒 | 普拉司日报 | 华南福建中景石化 T30S 拉丝粒料现货参考价 |
| BOPET | [我的钢铁公开摘要](https://m.mysteel.com/hot/1647652.html) | 江浙 12μ 印刷基膜商谈区间低端 |
| 包装用纸 | [生意社交易平台](https://www.rawmex.cn/ppinews/) | 上海兆美 140g/m² 瓦楞原纸，上海交货报价 |

普拉司未明确税运条款的现货参考价不推断税运；塑价365页面明确说明是转载来源。区间低端是原文实际发布的数值，不将自行计算的中点称作上游均价。DMC/硅橡胶取“价格统计时间”，PMMA 取结构化 `spot` 的实际日期，均不使用页面更新日冒充报价日。BOPET 仅读取无需登录的公开摘要；不访问付费隐藏值。上游标题、市场、制造商、牌号、克重、单位或表头不符时拒绝入库。

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
| `GET /api/alerts/status` | Telegram 已配置 / 待配置，不返回凭据 |
| `GET /api/alerts/rules` | 规则列表、通道状态、当前报价检查原因 |
| `POST /api/alerts/rules` | 创建规则 |
| `PUT /api/alerts/rules/{id}` | 更新规则（含启用 / 暂停） |
| `DELETE /api/alerts/rules/{id}` | 删除规则，保留历史推送日志 |
| `POST /api/alerts/check` | 立即检查启用规则，可能发送真实 Telegram |
| `POST /api/alerts/test` | 发送一条通道测试消息并记录结果 |
| `GET /api/alerts/logs?page=1&page_size=30` | 按时间倒序的发送日志（每页 1～100 条） |

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

首次启动自动建表并同步 11 个品种。数据库为 `data/procurement.sqlite3`，包含 `commodities`、`price_points`，以及运行状态表 `source_states`、额度表 `source_runs`。第三阶段自动增建 `alert_rules` 和 `push_logs`，不改动已有价格和额度。不要删除额度表来重复触发卓创。

## Telegram 提醒

仍使用 `python start.py` 启动；打开 <http://127.0.0.1:8000/#/alerts> 管理规则，<http://127.0.0.1:8000/#/push-logs> 查看日志和发送测试。手机宽度也可通过导航进入这两个页面。

在根目录 `.env` 填写下列键，示例模板为 `.env.example`；不要提交真实值：

```dotenv
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
```

向 Telegram 的 `@BotFather` 创建 Bot 获取 token，先向自己的 Bot 发送 `/start`，再填写目标会话的 chat_id。配置只从根目录 `.env` 读取，修改后下次检查立即生效，无需重启；不从进程的同名变量读取，也不在页面、接口、日志中显示凭据。缺少任一项时显示 `pending_configuration` / **待配置**；测试按钮会记录待配置日志，不发网络请求，不占规则日额度。已配置只表示字段齐全，发送结果仍需查看日志。

规则可为全部 11 个品种预设，提交 JSON 为 `{"code":"alu_a00","kind":"daily_change","threshold":1,"enabled":true}`。`kind` 支持 `daily_change`（日涨跌幅绝对值 ≥ X%，包含涨和跌）、`price_gte`（价格 ≥ 阈值）、`price_lte`（价格 ≤ 阈值）；阈值必须大于 0，最多四位小数。价格阈值单位随品种展示。规则保存来源、报价口径和单位快照，配置变化后先跳过，需编辑保存重新确认。

日涨跌幅比较同品种 / 来源 / 口径 / 单位的前一个真实报价日，跨周末仍取上一报价日；以未四舍五入值比较绝对值，等于阈值也触发。没有上一报价日时只跳过涨跌规则，到价规则仍可判断。仅北京时间当天的报价参与发送；暂无报价、旧报价、未来报价均不发提醒。页面规则列表说明当前跳过原因，暂停规则不参与检查。

行情抓取完成后独立检查一次，另每 5 分钟检查一次；页面“立即检查规则”也可检查现有数据库，不请求上游。服务必须保持运行，不补发旧日提醒。推送通道异常不改变行情入库结果。

同规则同品种在**北京时间自然日最多尝试一次**：`push_logs` 的唯一约束在请求前提交，因此手动检查、定时任务、并发、重启和修改阈值共用额度。未配置不占额度；已发起请求的失败、超时和中断也占额度，不自动重试，以免送达未知时重复发送。删除规则后日志保留，新增规则使用新编号；不同规则独立计数。测试推送不占规则额度，每次点击会独立测试。

通过 [Telegram Bot API sendMessage](https://core.telegram.org/bots/api#sendmessage) 发送纯文本，超时 15 秒、不跟随跳转、不自动重试。收到 HTTP 200、`ok=true` 和有效 `message_id` 才标记 `sent` / 发送成功；此状态表示 Telegram 已接受，实际收信应在目标会话核对。`failed` 表示明确未确认成功，`unknown` 表示送达未知；`sending` 若在进程中断后残留，也按送达未知处理，不再次发送。日志保留时间、规则编号、内容、结果和 Telegram 消息编号，不保存 token 或 chat_id。需要代理时，按前述方法在**启动服务前**设置 HTTPS_PROXY 和 NO_PROXY，再重启服务。

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

第三阶段自测：

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests -q
# 服务启动后执行：创建/编辑/删除本脚本的暂停规则，发送一次真实通道测试
.\.venv\Scripts\python.exe backend/scripts/smoke_alerts.py
# 首次回放下载最近三个公开真实报价日；网络需要时先配置 HTTPS_PROXY
.\.venv\Scripts\python.exe backend/scripts/replay_alerts.py --download-history
# 后续复用历史 HTML 缓存，无网络请求
.\.venv\Scripts\python.exe backend/scripts/replay_alerts.py
cd frontend
npm test
```

`smoke_alerts.py` 可用 `--base-url http://127.0.0.1:8010` 指定服务，输出 `data/stage3-live.json`；缺配置时标注待配置，不作为失败。已配置时会真的发送测试消息。`replay_alerts.py` 将公开历史 HTML 缓存在 `data/stage3-history/`，通过原解析器验证口径，只写临时 SQLite；隔离通道明确记录 `replayed`，不会调用 Telegram，也不修改首页历史或规则。结果为 `data/stage3-replay.json`，验证三种条件、未触发、缺前价、跨日、重启、并发与修改后的日去重。单元测试和 Vue 组件测试验证错误及待配置路径，均不向真实通道发送消息。

第四阶段逐品种真实验证（会请求该品种公开源并入库，不调用提醒服务）：

```powershell
.\.venv\Scripts\python.exe backend/scripts/verify_stage4_source.py abs
# code 可替换为 pc、pmma、lithium_carbonate、dmc、silicone_rubber、pe、pp、bopet、paper
# 运行中的全站验收：只读行情，创建/编辑/删除本脚本的暂停规则，不发推送
.\.venv\Scripts\python.exe backend/scripts/smoke_stage4.py
```

`verify_stage4_source.py` 追加 `STAGE4_PROGRESS.md`、记录 `backend/tests/evidence/{code}.json` 并缓存公开响应；仅人工续跑/验收使用，不增加定时任务。完整测试仍为 `python -m pytest backend/tests -q`、`smoke_frontend.py`、前端 `npm test`；新解析器回归使用真实公开片段，测试期间禁止真实 HTTPTransport 网络。Linux 使用 `.venv/bin/python`，所有服务脚本支持 `--base-url` 指向自定义端口。

## Docker 与云服务器说明

部署交付仅包含 [docker-compose.yml](docker-compose.yml) 和 [DEPLOYMENT.md](DEPLOYMENT.md)，本阶段未连接或部署服务器。Compose 使用内联多阶段构建，Python 3.12 + Node 22 构建 Vue，最终单容器单 worker，数据持久卷、只读 `.env` 文件挂载、健康检查和日志轮转。将来在目标 Linux 主机按部署文档准备 `.env` 后运行：

```bash
docker compose config --quiet
docker compose build --pull
docker compose up -d
```

默认绑定服务器 `127.0.0.1:8000`，通过 SSH 隧道访问；备份、迁移、停止、回滚及网络要求见部署文档。需要 Docker Compose 2.17+。本机无 Docker，容器构建/运行尚未验证。

阶段交付记录见 [STAGE1_REPORT.md](STAGE1_REPORT.md)、[STAGE2_REPORT.md](STAGE2_REPORT.md)、[STAGE3_REPORT.md](STAGE3_REPORT.md)、[STAGE4_REPORT.md](STAGE4_REPORT.md)。本次停在第四阶段，不进入下一阶段。
