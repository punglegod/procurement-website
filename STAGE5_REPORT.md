# 第五阶段交付：账号、权限与个人推送

日期：2026-11-12。基线 HEAD：`2150d0b`。严格采用本目录 AGENTS.md 的新版账号与权限规定，未更换技术栈，未修改任何行情 adapter 或启用卓创。

## 变更摘要

- 注册、登录、退出、改密码完整落地。Argon2id 保存密码，服务端持久会话只存令牌 SHA-256 摘要；Cookie 为 HttpOnly、SameSite=Strict、7 天有效，可配置 Secure。改密码撤销全部旧会话，退出撤销当前会话。按账号和真实客户端地址持久记录登录失败，15 分钟内 5 次失败后返回 429；写请求另做同源校验。
- 管理员由根目录 `.env` 的 ADMIN_USERNAME / ADMIN_PASSWORD 播种，已存在的账号不覆盖。注册只创建 user，拒绝夹带 role/user_id。首次无管理员又缺播种配置时启动失败，事务回滚，不允许无主迁移。
- 未登录首页 303 跳 `/login`，全部受保护 API（含 health）401，文档也需登录。仅登录/注册入口与其静态脚本公开。普通账号调用数据源、抓取、Telegram 全局状态、用户管理、注册开关均 403；管理员正常访问。
- 新增用户管理：列表、普通账号禁用/启用/删除、注册开关。禁用与删除立即撤销会话；删除清理该账号规则、个人设置和日志。管理员账号受保护，不可从该页面禁用/删除。
- alert_rules、push_settings、push_logs 均按 user_id 隔离。规则修改/删除必须匹配当前账号，他人编号 404；列表、日志计数、个人立即检查及回放均按账号过滤。定时与采集后检查只遍历有效账号。
- 用户规则只发自己的邮箱；管理员继续使用 Telegram，填个人邮箱后双通道。Resend 请求发送至 `/emails`，只有确认返回有效消息编号才记 sent；未配 key/邮箱等记录 pending_configuration，绝不假装成功。超时记 unknown，失败保留当日额度，不自动重试。
- 每日额度唯一键为用户、规则、品种、北京时间自然日、通道。发送前事务领取；不同用户、不同通道独立。待配置记录不占发送额度，补配置后可原位转为发送记录，避免每 5 分钟重复追加待配置日志。
- 前端增加登录/注册、我的提醒、我的推送设置、账号改密码、全局设置、用户管理；按角色显示导航并设置路由守卫。普通账号行情刷新不会请求管理员来源接口。旧验收脚本增加交互式登录，密码隐藏输入。
- README、部署说明和环境模板已更新。Docker 健康检查相应验证受保护接口的 401；本机没有执行 Docker 构建/运行。

实现参照 [Argon2 官方用法](https://argon2-cffi.readthedocs.io/en/stable/howto.html) 与 [Resend 官方发送接口](https://resend.com/docs/api-reference/emails/send-email)。

## 数据备份、迁移与回滚

本次自测先用 SQLite backup API 对正式库做只读一致性快照（含 WAL 已提交内容），保存为：

`data/backup/procurement-pre-stage5-20261112-150229-536876.sqlite3`

随后仅在下列副本上播种临时管理员、迁移、启动 HTTP 服务与验收：

`data/stage5-validation/20261112-150229-536876/procurement.sqlite3`

迁移代码在任何结构变更前另生成迁移备份，实际路径：

`data/stage5-validation/20261112-150229-536876/backup/procurement-pre-stage5-20261112-150231-728604.sqlite3`

正式 `.env` 字节摘要前后相同，正式库仍为第四阶段结构；没有写入临时账号、测试价格或推送日志。自测临时 `.env` 已删除，子服务已停止；未发送真实邮件或 Telegram，未请求真实行情源。副本中的真实铝锭点仅变换为测试当天日期以触发规则，此为测试处理，不作为真实新报价。

**当前正式库实际有 0 条提醒规则、5 条历史 Telegram 日志。** 副本升级后，5 条日志原有字段逐列完全一致，新增归属为管理员、channel=telegram。空规则比较不能证明非空迁移，因此另用精确旧表结构构造规则 #7、历史消息 #5 和已用最大规则编号 99，验证规则/日志保留、当日已发额度继续去重、新编号大于 99；也覆盖全部旧规则已删空时不复用编号。

正式启用时，站长需在正式 `.env` 中确认管理员播种配置，然后停掉旧服务，按 README 启动第五阶段；首次启动将自动在正式 `data/backup/` 再备份并事务迁移。不会把本次测试管理员带入正式库。

回滚已在独立恢复文件上执行：从迁移备份恢复，`PRAGMA integrity_check = ok`，旧规则与旧表结构恢复一致。正式回滚步骤见 README：停止全部服务、另存当前库、切回第四阶段代码 `2150d0b`，使用 backup API 将 pre-stage5 备份恢复到正式库，再启动第四阶段。备份之后创建的账号和修改不会保留。不要用第五阶段代码先启动已恢复的旧库，否则会再次升级。

## 自测命令与输出

以下均由本次实际执行。后端测试自动禁用真实网络，并给每项测试创建临时 `.env` 与 SQLite。

```text
python -m pytest backend/tests -q
133 passed, 2 warnings in 36.43s

npm test
Test Files  3 passed (3)
Tests       14 passed (14)
Duration    7.64s

npm run build
626 modules transformed
built in 4.25s

python backend/scripts/smoke_accounts.py
status: passed
checks_passed: 42
actual_messages_sent: 0

python backend/scripts/replay_alerts.py
status: passed
mode: isolated_replay_no_telegram
isolated_messages: 8
```

| 验收项 | 实测结果 |
| --- | --- |
| 匿名首页、价格、接口、文档 | 首页/文档 303；`/api/prices/latest` 等 401；登录/注册页 200 |
| 注册两个账号、禁止提升角色 | A/B 均 user，夹带 role/user_id 422，短密码 422，重复账号 409 |
| A/B 规则隔离 | B 列表只有 B 规则；B 修改/删除 A 规则均 404 |
| 设置、日志与评估隔离 | 邮箱各自保存；日志及总数按账号过滤；个人 check 仅返回自己的规则 |
| 用户去重互不影响 | B 先发送后 A 仍可发送；重复同日检查 deduplicated |
| 角色权限 | 普通账号管理接口 403；管理员状态/列表等 200；抓取入口管理员走隔离 mock，普通账号 403 |
| 会话与密码 | HttpOnly/SameSite/Secure、令牌摘要、过期、退出、改密撤销旧会话均通过 |
| 登录失败节流、跨站请求 | 连续 5 次失败后 429，跨站写 403，非 JSON 表单写 415 |
| 用户管理 | 关注册后 403；重开成功；禁用会话立即 401，启用需重登；删除不影响另一账号 |
| 缺少邮件 key | 命中规则后保留 pending_configuration 日志，不发送网络请求 |
| 邮件 mock 与双通道 | A/B 各自收到对应 mock 请求；管理员 Telegram+邮件独立发送；四路并发每邮箱仅一次 |
| 邮件错误、重启、跨日 | HTTP 401/429/500 均 failed；超时/非法响应 unknown；失败重启后仍去重，次日恢复额度 |
| 存量升级及回滚 | 真实副本 5 条日志逐列保留；非空旧规则/删空编号测试通过；恢复旧结构且 integrity_check=ok |
| 真实历史回放 | 复用 11/10～11/12 三日缓存，三种条件、跨日、重启、并发、第二账号独立触发及日志隔离通过 |
| 前端组件 | 登录/注册错误提示、角色导航、管理路由拦截、邮箱保存、用户管理、原行情与 ECharts SVG 共 14 项通过 |
| 代码及 Compose 静态检查 | 全部 Python 语法可解析，YAML 与 healthcheck 内外 Python 编译通过；mock 401 健康检查通过；git diff --check 无错误 |

详细证据（均已忽略提交）：`data/stage5-live.json`、`data/stage5-http-output.txt`、`data/stage5-backend-tests.txt`、`data/stage3-replay.json`。回放 8 条均为隔离通道事件，不代表 8 次真实推送。

已知验证边界：浏览器工具返回 `Browsers: Error: The browser access host is unavailable`，未完成真实浏览器点击、移动端视觉与截图；已完成组件、路由、ECharts SVG 与 HTTP 静态页面验收。Docker CLI/daemon 未用，容器构建/启动/卷恢复未实测。后端保留两项第三方弃用提示，前端保留既有 ECharts 约 503kB 的分包提示，均不导致失败。Resend 使用 mock，未核对真实收件箱；“sent”仅表示服务确认接受。

## Git 与补交文件清单

本会话 `.git` 为只读目录，无法创建 index.lock，故本阶段提交尚未生成；HEAD 保持 `2150d0b`。按第五阶段一个 commit 补交以下 38 个文件即可；建议消息 `feat: stage5 accounts permissions and personal notifications`。

本次确实执行了限定文件范围的 `git add -- .env.example README.md DEPLOYMENT.md STAGE5_REPORT.md docker-compose.yml backend frontend`，返回：

```text
fatal: Unable to create 'D:/Project/procurement-website/.git/index.lock': Permission denied
```

未反复尝试或修改 Git 元数据，没有暂存用户原有文件。

| 分组 | 文件 |
| --- | --- |
| 后端账号与业务（6） | `backend/app/auth.py`（新增）、`backend/app/alerts.py`、`backend/app/config.py`、`backend/app/db.py`、`backend/app/main.py`、`backend/app/models.py` |
| 依赖（1） | `backend/requirements.txt` |
| 验收与回放脚本（7） | `backend/scripts/smoke_accounts.py`（新增）、`backend/scripts/smoke_auth.py`（新增）、`backend/scripts/replay_alerts.py`、`backend/scripts/smoke.py`、`backend/scripts/smoke_alerts.py`、`backend/scripts/smoke_frontend.py`、`backend/scripts/smoke_stage4.py` |
| 后端测试（4） | `backend/tests/test_accounts.py`（新增）、`backend/tests/conftest.py`、`backend/tests/test_alerts.py`、`backend/tests/test_stage4.py` |
| 前端基础（7） | `frontend/src/auth.js`（新增）、`frontend/src/router.js`（新增）、`frontend/src/App.vue`、`frontend/src/api.js`、`frontend/src/main.js`、`frontend/src/store.js`、`frontend/src/style.css` |
| 前端页面（7） | `frontend/src/views/AccountView.vue`、`AuthView.vue`、`PushSettingsView.vue`、`SettingsView.vue`、`UsersView.vue`（前五项新增，同一目录）；`frontend/src/views/AlertsView.vue`、`frontend/src/views/PushLogsView.vue` |
| 前端测试（1） | `frontend/tests/accounts.test.js`（新增） |
| 配置与文档（5） | `.env.example`、`docker-compose.yml`、`README.md`、`DEPLOYMENT.md`、`STAGE5_REPORT.md`（新增） |

已有 AGENTS.md 的未提交修改由用户提供，本次保留原样，不擅自纳入提交。原有 PROMPTS、run_stage*.cmd、run_hidden*.vbs、stage*-prompt.txt 等未跟踪文件不在补交清单。正式 `.env`、data（含备份）、依赖缓存、前端 dist 均不提交，已核对 Git 忽略规则。

第五阶段工作到此停止，等待确认；没有进入后续阶段或部署正式服务。
