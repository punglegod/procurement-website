# 第二阶段交付记录

验证日期：2026-11-12（Asia/Shanghai）。基线提交：`5acca1e`。

## 变更摘要

- 新建 Vue 3 + Vite 前端与哈希路由：首页行情表格、品种详情页。使用浅色工作台布局；窄屏下表格可横向滚动，详情与统计卡片调整排列。
- 首页展示全部 11 个品种、最新价 / 单位、涨跌额、涨跌幅、固定报价口径、报价日期、原始来源链接；支持品种搜索及已有 / 暂无报价筛选。真实旧报价整行标黄，无数据保留空值与待配置 / 待接入状态。
- 详情用 ECharts SVG 绘制 7 / 30 / 90 个自然日的真实价格点，支持单点、空历史、失败重试及窗口尺寸变化；快速切换时间范围时旧响应不会覆盖新结果。保留中文无障碍图表说明。
- 最新价格接口增加 `change_amount`、`change_percent`、`previous_quote_date`：仅对比同品种 / 来源 / 口径 / 单位的最近两个报价日；无上一笔时返回空值，持平返回零。历史接口也按单位过滤。
- FastAPI 在 API 路由之后挂载 `frontend/dist`。新增根目录 `start.py`：必要时按锁文件安装依赖、构建前端、启动单 worker 的 FastAPI。一条命令运行全站；也可复用已有构建。
- README 补充安装、日常启动、前端开发、构建、自测与当前免费源决定。刷新按钮只重读本地接口，不触发上游采集。

## 全站服务验收

从项目根目录执行：

```powershell
.\.venv\Scripts\python.exe start.py
```

实际完成 `npm ci`、Vite production build、单进程服务启动：

```text
Vite v7.3.8
617 modules transformed
build completed
Application startup complete.
Uvicorn running on http://127.0.0.1:8000

GET / -> HTTP 200，Vue 入口 HTML
GET /assets/*.js、*.css -> 4 个构建资源全部 HTTP 200（含详情与 ECharts 分包）
GET /api/health -> HTTP 200 {"status":"ok"}
GET /api/missing -> HTTP 404
GET /assets/missing.js -> HTTP 404
GET /api/prices/latest -> HTTP 200，共 11 个品种

alu_a00:
  price: 23800.0 元/吨
  quote_basis: 南海灵通佛山国产A00批售（送厂）均价
  quote_date: 2026-11-12
  source_name: 世铝网（南海灵通公开报价）
  fetched_at: 2026-11-12T12:58:28+08:00
  stale: false
  change_amount / change_percent / previous_quote_date: null

GET /api/prices/history?code=alu_a00&days=7  -> HTTP 200，1 条
GET /api/prices/history?code=alu_a00&days=30 -> HTTP 200，1 条
GET /api/prices/history?code=alu_a00&days=90 -> HTTP 200，1 条
```

来源原文：[11 月 12 日南海有色（灵通）铝锭价格](https://market.cnal.com/nanhai/2026/11-12/1794454767672170.shtml)。沿用第一阶段入库记录；本阶段没有重新抓取上游或写入演示价格。数据库仍只有 1 个真实价格点，没有更早对比报价，页面涨跌显示“—”，走势图展示 1 个真实点并说明尚未形成连续走势。

## 自动自测与图表检查

```text
python backend/scripts/smoke_frontend.py -> PASS
  首页和全部 4 个构建资源、API 路由、11 品种、铝锭真实报价、三个历史范围

npm test -> 1 test file passed, 5 tests passed
  首页真实数据字段 / 来源 URL / 缺失涨跌 / 搜索
  过期行黄标 class / 待配置与待接入 / 筛选
  真实响应驱动详情组件，实际 ECharts SVG 渲染 / 7、30、90 天切换
  空历史 / HTTP 503 / 重试
  快速切换时迟到旧响应不覆盖当前时间范围

python -m pytest backend/tests -q -> 27 passed
  新增涨跌 / 持平 / 四舍五入 / 其他口径隔离 / 缺少对比报价测试
python -m pip check -> No broken requirements found.
python backend/scripts/smoke.py -> PASS: 第一阶段服务验收（只读，未加 --fetch）
git diff --check -> PASS

price_points count -> 1
sci99 source_runs count -> 0
```

真实 HTTP 响应导出在忽略提交的 `data/stage2-live.json`。组件自测用 JSDOM 模拟页面 DOM 和尺寸，ECharts 使用真实 SVG 渲染器；输出 `data/stage2-qa/alu-a00-chart.svg`，另已转成 PNG 并检查单点、日期、价格坐标轴和单位均可见。隔离测试的状态和价格用例不会写入项目价格库。

**验收限制：** 浏览器工具返回 `Computer Use was not approved to use Google Chrome`，因此没有完成真实浏览器截图、悬浮提示或窄屏布局的目视验收。组件 / SVG 验收通过不能替代完整浏览器验收。Vite 构建有 ECharts 分包 503.18 kB（gzip 172.70 kB）略超默认 500 kB 的提示；构建成功，图表包随详情页加载。

## 本阶段边界

- 保留第一阶段抓取链路与目录配置；不填写 `.env`、不启用卓创、不接入新的免费 adapter、不推进 Telegram 提醒。
- 报价日期、来源、口径及抓取时间来自真实入库记录；不补造历史，不拿其他口径计算涨跌。
- 启动、自测与开发命令均已写入 README。自测完成后停止本次服务，避免留下重复调度进程。
- 第二阶段完成后停下，等待站长确认。

## Git 提交受限

本次按工程规矩尝试暂存本阶段文件，Git 返回：

```text
fatal: Unable to create 'D:/Project/procurement-website/.git/index.lock': Permission denied
```

当前沙箱允许编辑项目文件，但 `.git` 为只读，且无法申请权限。因此代码与自测报告已落盘，**本阶段 commit 尚未创建**；HEAD 仍为第一阶段 `5acca1e`。在项目根目录的普通本地 PowerShell 终端执行以下命令即可完成提交，文件清单不包含原有提示词或运行包装脚本：

```powershell
$stageGit = "$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\git\cmd\git.exe"
& $stageGit add -- README.md STAGE2_REPORT.md start.py backend/app/main.py backend/app/service.py backend/tests/test_pipeline.py backend/scripts/smoke_frontend.py frontend
& $stageGit -c user.name=Codex -c user.email=codex@localhost commit -m 'feat: stage2 spot price dashboard and history charts'
```

仅为本次提交指定自动化作者，不修改全局 Git 身份。
