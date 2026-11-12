# 采购行情网站 — 分阶段提示词（按顺序喂给 Codex，一次一段）

## 第一段（先发这段）
在当前目录按 AGENTS.md 的约定初始化项目，并完成第一阶段。只做第一阶段，做完停下汇报。

第一阶段范围（骨架 + 数据打通）：
1. 初始化 git 仓库和目录结构（backend/、frontend/ 先建空壳）；.gitignore 必须包含 .env 和 data/
2. 后端 FastAPI 骨架，接口：GET /api/health、GET /api/commodities、GET /api/prices/latest、GET /api/prices/history?code=&days=、POST /api/fetch/run（手动触发抓取）、GET /api/sources/status（每个数据源状态：正常 / 失败原因 / 待配置凭据）
3. 数据模型与 SQLite 建表：commodities、price_points（字段按 AGENTS.md 的数据原则，含报价口径）
4. 品种配置内置全部 11 个，并标注数据源：alu_a00（南海灵通 A00 铝锭，source=free）；abs、pc、pmma、lithium_carbonate、dmc、silicone_rubber、pe、pp、bopet、paper（source=sci99，卓创资讯）
5. 写通免费源 adapter：南海灵通 A00 铝锭（世铝网等公开报价页），跑通「抓取 → 入库 → 接口返回」全链路
6. 写卓创 adapter 框架：从 .env 读 SCI99_USER / SCI99_PASS（或 SCI99_COOKIE）登录，以 ABS 为试点品种搭好「登录 → 取现货价 → 解析 → 入库」链路；.env 里没有凭据时，该 adapter 跳过并在 /api/sources/status 里标注「待配置凭据」——这不算失败。触发验证码 / 风控立即停手并如实报告。仓库里提供 .env.example 模板
7. APScheduler 定时抓取：每天 15:30（Asia/Shanghai）；卓创 adapter 每天最多跑 1 次
8. README 写清：环境要求、启动命令、自测方法、.env 配置方法

验收（你自己执行并贴出结果）：
- uvicorn 能启动，/api/health 返回 ok
- 手动触发一次抓取后，/api/prices/latest 返回 A00 铝锭的真实价格（带来源、口径和报价日期）
- /api/sources/status 正确显示 sci99 源状态（待配置凭据，或凭据已配置时返回 ABS 的真实价格）
- 最后给我：变更摘要 + 自测输出 + 下一阶段（前端）一句话预告，然后停下等我确认

## 第二段（第一段验收通过后发）
第二阶段：前端页面。
1. Vue 3 + Vite + ECharts，页面：行情首页表格（品种、最新价、涨跌额、涨跌幅、报价口径、报价日期、来源，数据过期标黄提示）；品种详情页走势图（7 / 30 / 90 天切换）
2. 前端构建产物由 FastAPI 静态托管，本地一条命令能起全站
3. 自测：启动后首页能看到第一阶段入库的铝锭真实数据，走势图能渲染；贴自测结果，写变更摘要，停下等确认

## 第三段（推送）
第三阶段：推送服务。
1. 提醒规则：按品种设「日涨跌幅 ≥ X%」或「价格 ≥ / ≤ 阈值」，规则存 SQLite，页面上可增删改
2. 推送通道：Telegram Bot API（token、chat_id 从 .env 读），同规则同品种每天最多推 1 次
3. 推送日志页 + 「发送测试推送」按钮
4. 自测：点测试推送能真收到一条 Telegram 消息（贴发送日志），规则触发逻辑用历史数据回放验证一次；写变更摘要，停下等确认

## 第四段（免费源补全 + 部署）— 2026-11-12 按站长决定改版，卓创暂停
第四阶段：
1. 其余 10 个品种（abs、pc、pmma、lithium_carbonate、dmc、silicone_rubber、pe、pp、bopet、paper）逐个配免费公开源 adapter（生意社等公开行情页，只取现货价），一个品种一个 commit，每个都验证抓到真实价格再继续；卓创 adapter 保持休眠，不碰
2. docker-compose.yml + 部署说明（目标：一台 3 核 20G 的 Linux 云服务器长期运行）
3. 全站自测清单逐项跑一遍，贴结果

## 第五段（账号权限改版 — 2026-11-12 站长新增，部署前先做这段）
第五阶段：账号、权限与个人推送。
1. 用户系统：注册 / 登录 / 退出 / 改密码；密码哈希存储，会话 HttpOnly Cookie；未登录访问页面和接口一律拒绝（页面跳登录、接口 401）
2. 角色与播种：admin / user 两角色；管理员账号按 .env 的 ADMIN_USERNAME / ADMIN_PASSWORD 首次启动播种（已存在则不覆盖）；注册新账号永远是 user
3. 权限落地：普通账号只能看价格与走势；全局设置类接口与页面（数据源、Telegram、抓取控制、用户管理）仅 admin 可见可用，普通账号调用返回 403；admin 增加用户管理页（账号列表、禁用 / 启用 / 删除、注册开关）
4. 规则按账号隔离：现有 alert_rules、推送设置、推送日志加 user_id；存量全局规则迁移归管理员账号；规则的增删改查、评估、每日去重、回放验证全部按用户隔离，A 账号看不到也影响不到 B 账号的规则与日志
5. 个人推送设置页：每个账号填自己的邮箱；命中自己规则 → 发邮件到自己邮箱。邮件通道用 Resend API（.env 的 RESEND_API_KEY、EMAIL_FROM，默认 sourcing@bbbhl.com）；没配 key 时标「待配置」并如实记录。管理员的规则继续走 Telegram；管理员填了邮箱则同时发邮箱
6. 前端：登录 / 注册页；导航按角色显隐（普通账号看不到设置入口）；「我的提醒」「我的推送设置」页面
7. 数据迁移要可回滚：迁移前备份 SQLite 文件到 data/backup/ 并在报告里写明
验收（你自己执行并贴出结果，自测用临时测试账号和临时 .env，不要动正式 .env）：
- 未登录访问首页和 /api/prices/latest 被拒
- 注册两个普通账号 A、B：A 建的规则 B 看不到、B 改不了（403/404）；B 的规则触发不影响 A 的去重计数
- 普通账号访问全局设置接口返回 403；管理员登录后一切正常，存量 Telegram 规则在管理员名下完好
- 邮箱通道：配测试 key 或 mock，验证命中规则后按账号分别生成待发 / 已发记录；未配 key 时状态如实为「待配置」
- 全量测试通过；写 STAGE5_REPORT.md（变更摘要 + 自测输出 + 补交文件清单），停下等确认
