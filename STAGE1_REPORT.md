# 第一阶段交付记录

验证日期：2026-11-12（Asia/Shanghai）。

## 变更摘要

- 初始化 Git、backend/ 与 frontend/ 空壳，忽略 `.env`、SQLite 数据目录、虚拟环境和日志。
- 新增 FastAPI 六个接口、SQLAlchemy SQLite 数据模型与 11 品种配置。
- 跑通世铝网南海灵通 A00 抓取、严格按送厂均价解析、去重入库及最新/历史接口。
- 卓创提供根目录 `.env` 读取、Cookie/普通表单登录框架、ABS 固定现货均价解析框架、验证码/风控停手及状态报告。
- 添加每天北京时间 15:30 调度、数据库持久化卓创日额度、跨源失败隔离和旧数据标记。
- README 记录安装启动、代理、配置、接口、调度与自测方式。

## 已执行的服务验收

```text
Python 3.12.14
Started server process [30232]
Application startup complete.
Uvicorn running on http://127.0.0.1:8000

GET /api/health -> HTTP 200 {"status":"ok"}
GET /api/commodities -> HTTP 200，共 11 个（free: 1，sci99: 10）
POST /api/fetch/run -> HTTP 200
  free: success / ok / stored=1
  sci99: skipped / pending_credentials / 待配置凭据

GET /api/prices/latest -> HTTP 200
  code: alu_a00
  price: 23800.0
  unit: 元/吨
  quote_basis: 南海灵通佛山国产A00批售（送厂）均价
  quote_date: 2026-11-12
  source: free
  source_name: 世铝网（南海灵通公开报价）
  source_url: https://market.cnal.com/nanhai/2026/11-12/1794454767672170.shtml
  fetched_at: 2026-11-12T12:58:28+08:00
  data_status: ok
  stale: false

GET /api/prices/history?code=alu_a00&days=30 -> HTTP 200，1 条真实记录
GET /api/sources/status -> free=ok；sci99=pending_credentials
GET /api/prices/history?code=missing&days=30 -> HTTP 404
GET /api/prices/history?code=alu_a00&days=0 -> HTTP 422
PASS: 第一阶段服务验收

pytest backend/tests -q -> 22 passed in 0.70s
pip check -> No broken requirements found.
scheduler -> cron[hour='15', minute='30'], timezone=Asia/Shanghai
next_run_time -> 2026-11-12T15:30:00+08:00
tables -> commodities, price_points, source_runs, source_states
price_points -> 1
sci99_run_count -> 0
```

来源：[11 月 12 日南海有色（灵通）铝锭价格](https://market.cnal.com/nanhai/2026/11-12/1794454767672170.shtml)。价格是当次真实抓取结果，不是演示数据。本机 `data/selftest.json` 保存服务验收结果；最终代码重启后再次通过只读服务验收。

## 边界

本次没有 `.env` 凭据，卓创未进行真实登录或 ABS 行情验收。当前官网 JS 登录及报价页需用有效 Cookie 和本人订阅页面进一步核定解析规则；无法解析时明确报错，不承诺配置凭据即可直接取价。其余 9 个卓创品种仅建目录，前端保持空壳。

## Git 提交限制

`git init` 已成功。当前运行环境将 `.git` 设为只读，暂存及提交因无法创建 `.git/index.lock`、`.git/HEAD.lock` 而被拒绝，因此本阶段**尚未创建 commit**。未绕过该限制。

可在项目根目录的普通本地 PowerShell 终端执行以下命令完成本阶段提交。本机 Git 不在 PATH，故使用已确认存在的路径。命令只包含本阶段文件，保留原有启动脚本和提示词文件。

```powershell
$stageGit = "$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\git\cmd\git.exe"
& $stageGit add -- .gitignore .env.example AGENTS.md README.md STAGE1_REPORT.md backend frontend
& $stageGit -c user.name=Codex -c user.email=codex@localhost commit -m 'feat: 完成第一阶段后端骨架与现货行情数据链路'
```

这里仅为该次提交指定自动化作者 Codex，不修改全局 Git 身份配置。

下一阶段：用 Vue 3 + Vite + ECharts 展示今日价、口径、涨跌、历史曲线和来源状态。
