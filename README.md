# 采购行情网站

注册登录后使用的原材料现货行情网站，跟踪 ABS、PC、PMMA、碳酸锂、DMC、硅橡胶、PE、PP 粒、BOPET、瓦楞原纸和南海灵通 A00 铝锭，共 11 个品种。

## 核心功能

- 行情：最新现货价、涨跌额、涨跌幅、报价日期与过期提示。
- 走势：同一来源与固定报价口径的历史图表，不补造缺失报价。
- 提醒：日涨跌幅或到价触发，记录结果并按北京时间自然日去重。
- 日报：09:00轮采集后推送11品种价格与走势，个人可独立开关；每天四轮抓取，管理员可收进度通知。
- 账号隔离：每个账号独立管理提醒、邮箱与推送日志；全局管理仅管理员可用。

开放注册只创建普通账号；注册提交后请到登录页登录，无法登录可更换账号名或联系管理员。管理员仅在系统没有任何管理员时从配置播种。

普通账号通过已验证邮箱收信，修改邮箱后须重新验证；管理员使用 Telegram，也可同时接收已验证邮箱邮件。通道未配置时明确显示待配置。

卓创框架保留、默认关闭；管理员可在全局设置配置与测试，当前行情仍使用免费公开现货源。

## 快速开始

需要 Python 3.12、Node.js 20.19+ 或 22.12+（含 npm）。首次准备虚拟环境，升级 `pip>=26.2.0`，安装 `backend/requirements-dev.txt`；复制 `.env.example` 为 `.env` 并填首次管理员配置，已有配置不要覆盖。

本地 HTTP 开发须在 `.env` 显式设置 `SESSION_COOKIE_SECURE=false`；公开来源留空。激活虚拟环境后，从根目录启动：

```bash
python start.py
```

访问 <http://127.0.0.1:8000/>。启动脚本安装/构建前端并运行单服务；`--skip-build` 复用已有构建，`--port 8010` 改端口。停止用 Ctrl+C。

Docker Compose 2.17+ 有两种启动方式，均需先按部署文档准备配置：

```bash
# 从源码构建并启动
docker compose up -d --build
# 已构建镜像：复用镜像启动
docker compose up -d --no-build
```

默认只绑定回环端口；使用单 worker、持久数据卷和只读配置挂载。

## 配置

完整说明见 [.env.example](.env.example)，凭据只由服务端读取，不提交 `.env`。

| 关键项 | 用途 |
| --- | --- |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | 首次管理员播种，密码至少 8 位 |
| `PUBLIC_BASE_URL` | 公开站点来源；HTTPS 入口应显式配置 |
| `SESSION_COOKIE_SECURE` | 默认 true；仅本地 HTTP 开发显式关闭 |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | 管理员 Telegram 通道 |
| `RESEND_API_KEY` / `EMAIL_FROM` | 邮箱验证及个人邮件推送 |

## 技术栈

Python 3.12、FastAPI、APScheduler、SQLAlchemy + SQLite；httpx + selectolax 抓取。Vue 3、Vite、ECharts；前端构建后由后端静态托管。

## 目录

```text
backend/app/       API、认证、行情采集与提醒
backend/tests/     后端及审计回归
backend/scripts/   维护、隔离 smoke 与历史回放
frontend/src/      页面与组件
frontend/tests/    前端回归
data/             SQLite 与缓存（不提交）
start.py          本地单服务入口
```

部署、首次安装、代理与 HTTPS 配置、备份迁移、管理员恢复及自测命令统一见 [DEPLOYMENT.md](DEPLOYMENT.md)。
