# 第七阶段修复与验收报告

日期：2026-11-12（北京时间）。开始修复时 HEAD：`e34b8ee`（`docs: stage6 security audit prompt`）；第五阶段业务提交为 `b889774`。先读取项目 `AGENTS.md` 及完整 `AUDIT_REPORT.md`，以实际 Git 记录和工作树为准。

原报告共 13 项发现，本阶段 **12 项已修，M-08 按明确范围未修**。M-07 的本机及构建配置已修，但最终 Docker 镜像验收必须留待有 Docker 的部署环境执行。没有修改 Windows ACL，没有启用卓创，没有修改数据源 adapter、配置或行情口径，没有远端部署、真实抓价或发送真实邮件/Telegram。

本阶段新增业务修复、回归测试和维护文档；同时补交此前未跟踪的第六阶段审计报告、复现工具及证据。`AUDIT_REPORT.md` 和 `audit_*` 旧输出仍描述修复前状态，不能当作当前测试结论；`test_audit_*.py` 和 `audit_frontend.test.js` 已按要求改为断言修复后行为，当前结果看本报告及 `stage7_*` 输出。

## 1. 逐项处置（对应原 13 项）

| 编号 / 原级别 | 处置 | 实现位置与行为 | 验证 |
| --- | --- | --- | --- |
| H-01 / 高 | 已修 | `backend/app/email_verification.py:34`、`security.py:11`、`main.py:198`、`alerts.py:211`：保存邮箱不授予发送权；一次性验证码确认归属，换地址撤销验证；验证/测试邮件共享账号、IP、收件地址、全站配额；规则总数及创建速率有限额。管理员 Telegram 通道继续工作。 | 原攻击 20 次全部 403、零发送；验证后超限 429；四维轮换、并发、重建服务、旧验证码、同址另一账号均覆盖。 |
| M-01 / 中 | 已修 | `auth.py:196`：注册先领 IP/全站预算，Argon2 在写事务外执行；最终短事务复查注册开关，数据库唯一约束保证同名仅插入一次；哈希/校验共用 2 个并发槽。 | 同 IP 12 次为 5×201、7×429；全部哈希期间独立连接可取得写锁；并发同名为 201/409，哈希期间关闭注册为 403。 |
| M-02 / 中 | 已修 | `auth.py:153`：限流键改为操作+账号+实际来源 IP；保留来源/全站预算与渐进延迟，不再使用旧的全账号硬锁；安全事件脱敏入库；`backend/scripts/manage_admin.py:19` 提供停机、备份后针对账号/IP 的解锁与凭据轮换。 | 陌生 IP 五次 401 后本来源 429，另一来源正确密码 200；并发错密不能超五次；正确登录不清 IP 总预算；恢复脚本只清指定组合。 |
| M-03 / 中 | 已修 | `security.py:51`、`main.py:88`：最外层 ASGI 中间件在解析正文前按实际接收字节计数，最大 16 KiB，不依赖 Content-Length。 | 4 MiB JSON 为 413；真实 HTTP chunked 为 413；缺失/虚假长度均拦截；恰好 16 KiB 可完整传递。 |
| M-04 / 中 | 已修 | `alerts.py:250`：领取日额度的 `BEGIN IMMEDIATE` 短事务中重读规则归属、启用状态、当前报价，重新判断条件并生成文案；外部发送在事务外。 | 阈值从 1 改为 1000 后旧评估 `skipped`，发送 0、额度行 0；报价变更和暂停/删除边界有回归。 |
| M-05 / 中 | 已修 | `alerts.py:348` 与 `deliver`：每轮、取得写锁后、领取额度前、调用发送器前重读北京时间；跨日停止旧轮，由下一轮重新评估；保留每账号/规则/品种/通道/自然日唯一约束。 | 跨午夜旧轮第二条跳过；第二条在次日仅发送 1 次；没有将旧报价发到次日；锁等待、配置读取跨日及重启/并发去重通过。 |
| M-06 / 中（开发依赖） | 已修 | `frontend/package.json`、`package-lock.json`：限定上层依赖的 overrides 将 glob 更新到 14.0.5、minimatch 到 11.0.8；未执行强制全面升级。 | 全量及生产 npm audit 均 0；上层实际 glob/editorconfig/HTML/CLI 兼容探针、前端测试、构建通过，详见第 5 节。 |
| M-07 / 中（工具链） | 已修；镜像验收待部署 | 实际 `.venv` 与隔离审计工具环境升级为 pip 26.3；Compose 内联 Dockerfile 在安装 requirements 前升级 `pip>=26.2.0`；README 更新安装步骤；DEPLOYMENT 增加最终运行镜像 pip-audit 验收。 | requirements 解析 27 包、实际开发环境 35 包均 0 漏洞；本机无 Docker，未宣称镜像扫描已通过。 |
| M-08 / 中（本机环境） | 未修：本阶段明确排除 | 不执行任何 Windows 权限修改，保留原审计结论，由运维单独处理本机 `.env`、数据库及备份 ACL。 | 本阶段未调用权限修改命令；原 `audit_permissions*` 为第六阶段只读证据。 |
| L-01 / 低 | 已修 | `main.py:157`、`alerts.py:241`：page 1～100000，page_size 1～100，接口和服务层双重验证。 | page=9223372036854775808 为 422；范围内无数据页为 200/空数组，无 500。 |
| L-02 / 低 | 已修 | `frontend/src/views/PushSettingsView.vue:14`：初始读取前禁止保存，失败可重试；请求序号、编辑版本、dirty 状态及卸载保护；保存以提交快照同步本地状态。 | 慢 GET 期间保存被阻止，晚到读取不覆盖输入，保存后的显示与提交一致；验证状态错误路径也有覆盖。 |
| L-03 / 低 | 已修 | `frontend/src/views/PushLogsView.vue:7`：页码、记录、总数只在请求整组成功后同步；失败保留原页并提示，可重试。 | 第 2 页请求失败，页脚仍第 1 页，数据仍 PAGE_ONE_MESSAGE，重试按钮可用。 |
| L-04 / 低 | 已修 | `db.py:68`：只在完全没有 admin 时播种；已有管理员启动时明确记录全部管理员用户名，不因 `.env` 更名增建账号；README 说明停机维护流程。 | 改播种用户名再启动仅保留 `test_admin`；日志可见；更名保留 ID/资源、撤销旧会话、同名普通账号碰撞不提权。 |

## 2. 配额与实现取值

### 邮箱归属与发送控制

验证码为安全随机生成的 8 位数字，15 分钟有效，最多 5 次错误；绑定当前账号、完整地址、随机盐，数据库仅存 SHA-256 摘要。确认后一次性清除挑战；重发使旧码失效。响应、推送日志及安全事件均不含验证码，真正验证码只进入验证邮件。确认接口另限账号 10 次/15 分钟、IP 30 次/15 分钟。

以下预算由验证邮件和测试邮件混用，在同一短写事务内先检查所有维度，再原子领取。超限返回 429 与 Retry-After；失败/送达未知不退额度。收件地址按大小写归一，以避免相同地址仅改大小写轮换预算。

| 维度 | 10 分钟 | 24 小时 |
| --- | ---: | ---: |
| 账号 | 5 | 20 |
| 实际来源 IP | 10 | 100 |
| 收件地址 | 3 | 10 |
| 全站 | 50 | 500 |

窗口从首次领取开始，持久化在 SQLite，重启不会清零；不同于提醒的北京时间自然日去重。未验证邮箱不发送测试/提醒；未配置服务如实返回待配置；配置补齐、邮箱完成验证后，原待处理规则日志可以转为一次发送，不先消耗日额度。

账号最多保留 20 条规则（含暂停规则）；最多创建 10 条/10 分钟、50 条/24 小时，删除不退额度，编辑不重置日去重。旧库已有超过 20 条规则时保留其数据并禁止继续创建，用户可编辑/删除；不在迁移时擅删旧规则。规则提醒仍使用原自然日去重，测试/验证邮件使用上表预算。

### 注册、登录与正文限制

- 注册：每 IP 5 次/小时、20 次/24 小时，全站 50 次/小时、200 次/24 小时；预算在哈希前领取。并发哈希/校验槽为 2，满时 429/Retry-After=1，避免 Argon2 的内存成本随并发无限增长。
- 登录及改密：各自每 IP 30 次/分钟、全站 120 次/分钟；同操作+账号+IP 组合 15 分钟最多 5 次失败或在途尝试，失败延迟依次 0.25、0.5、1、2、4 秒。等待发生在哈希槽和 SQLite 连接释放之后。安全日志保留事件、账号/IP 摘要和受控说明，对拒绝日志另设写入预算以避免放大。
- 正文上限选 **16 KiB**：本站没有文件上传，仅有账号、邮箱和规则等小型 JSON 表单，最长密码 128 字符、邮箱 254 字符。16 KiB 已明显高于合法表单所需，同时限制 JSON 解析与请求缓存开销。真实 chunked、虚假长度与边界值均已测。

额外完成两项直接关系本次安全约束的边界修正：

1. 实测 Uvicorn 默认回环信任可让 X-Forwarded-For 改写来源，影响 IP 限流。`start.py` 和 Compose 默认显式关闭代理头；`start.py` 仅在指定合法 `--trusted-proxy-ips` IP/CIDR 时开启，不继承宽泛 FORWARDED_ALLOW_IPS。Compose 反代通过文档中的 Uvicorn 命令覆盖显式启用。README/DEPLOYMENT 给出受控 HTTPS 反代方式。真实请求伪造 XFF、X-Real-IP、Forwarded 仍被限流，安全日志只记实际连接来源。
2. 异步推送返回期间删除账号可能让 SQLite 复用日志编号。更新发送结果前追加原 user_id/通道核对；验证邮件还核对 kind，避免覆盖或返回另一账号的新日志。提醒和验证邮件两条路径的编号复用对抗用例均通过。

## 3. 自测输出

### 全量测试与攻击回归

最终后端命令（`--basetemp` 复跑时换一个新子目录）：

```powershell
$env:PYTHONIOENCODING='utf-8'
.\.venv\Scripts\python.exe -m pytest backend/tests -q -s -p no:cacheprovider --basetemp=.tools/stage7-final-20261112b
```

完整输出：`backend/scripts/stage7_backend_full.txt`。

```text
207 passed, 2 warnings in 105.90s (0:01:45)
```

构成：**原有 133 项 + 改写后的审计 14 项 + 新增 60 项 = 207**。未删除原测试；原邮箱通道用例仅将隔离 fixture 标记为已验证，以继续验证其原有发送/去重职责。新增 60 项为认证 14、提醒 11、邮箱/迁移 10、流式正文 4、管理员维护 3、编号复用隔离 2、代理信任启动 16。

下列为输出中的证据摘录（只缩短字段，完整内容见原文件）：

```text
UNVERIFIED_UNLIMITED_EMAIL: requests=20, http_403=20, mock_sends_before_verification=0
verified_send_statuses=[200,200,200,429,429]
STAGE7_EMAIL_LIMIT account [200, 200, 429]
STAGE7_EMAIL_LIMIT ip [200, 200, 429]
STAGE7_EMAIL_LIMIT recipient [200, 200, 429]
STAGE7_EMAIL_LIMIT global [200, 200, 429]
REGISTRATION_LIMIT_FIXED: [201,201,201,201,201,429,429,429,429,429,429,429]
argon2_inside_global_sqlite_write_lock=false
ACCOUNT_LOCKOUT_FIXED: attacker_failures=[401,401,401,401,401]
correct_password_other_ip=200; attacker_source_still_limited=429
BODY_LIMIT: request_bytes=4194367, status=413
RULE_EDIT_RACE: current_condition_matches=false, result=skipped, isolated_sends=0, daily_quota_rows=0
MIDNIGHT: old_batch_second_rule=skipped, second_rule_actual_sends_on_nov13=1
old_quote_sent_after_midnight=false
PAGINATION: ordinary_page=200, ordinary_items=[], huge_page=422
BOOTSTRAP_ON_RESTART: admin_names=[test_admin]
STAGE7_LOG_OWNER path=alerts reused_id=True other_owner_unchanged=True other_data_returned=False
STAGE7_LOG_OWNER path=verification reused_id=True other_owner_unchanged=True other_data_returned=False
```

A/B 矩阵继续逐接口运行：B 携 A 的 id 查看/改/删规则和日志、伪造 user_id、个人推送设置、立即检查、测试通道及不存在的回放 API 均没有越权成功；A 资源保持不变。普通账号对七项全局管理操作全部 403。动态枚举目前 **26 个受保护 API**（包含新增两个验证端点），匿名全部 401。改密/禁用/删除/登出/会话轮换/过期后的旧会话全部 401；HTTPS 配置后的 Secure Cookie 仍通过。

原注入、XSS、SSRF、路径及错误信息对抗仍通过：10 个不允许的抓取地址拒绝、重定向不跟随到内网、5 个路径穿越尝试 404、SQL 输入不被执行、错误响应不含堆栈/内部路径。无需改动数据源或解析器。

### 前端

命令：frontend 目录下 `node ../.tools/npm/package/bin/npm-cli.js test -- --no-color`、`... run build`；根目录下 `node frontend/node_modules/vitest/vitest.mjs run --config backend/scripts/audit_frontend.config.mjs --reporter=verbose --no-color`。

```text
前端：Test Files 4 passed (4); Tests 23 passed (23)（原 14 + 新增 9）
审计回归：Test Files 1 passed (1); Tests 3 passed (3)
F1 FIXED: initial_save_blocked=true; delayed_GET_preserves_edit=true; saved_and_displayed=new@example.com
F2 FIXED: failed_page=2; footer=第 1 页; rows=PAGE_ONE_MESSAGE; retry_enabled=true
XSS: inserted_html_elements=0; rejected_protocols=javascript,data,file,vbscript
npm run build: success; 626 modules transformed
```

原有前端 14 项保留并全部通过。最终原始输出见 `stage7_frontend_tests.txt`、`stage7_frontend_audit_tests.txt`、`stage7_frontend_build.txt`；新增邮箱验证、加载失败重试、变更地址、错误响应、验证日志标签和保存快照等回归。ECharts 仍有单 chunk 超 500 kB 的构建提示，构建退出码为 0，未进行本阶段以外的图表重构。

### 真实 HTTP 与历史回放

执行 `.\.venv\Scripts\python.exe backend/scripts/smoke_stage7.py`：启动独立临时配置/SQLite 的真实 Uvicorn，加载刚构建的前端；仅使用隔离发送器，阻止外部 HTTP。通过实际不同回环源地址测试 IP 预算，不靠伪造测试客户端字段。

```text
status=passed
http.checks_passed=52
empty_catalog_count=11
isolated_email_count=3
real_email_or_telegram_sent=false
chunked_transfer_confirmed=true
socket_source_ips_confirmed_in_security_log=true
forged_proxy_ips_absent_from_security_log=true
isolated_server_stopped=true
replay.status=passed; isolated_messages=8
old_replay_evidence_unchanged=true
```

覆盖匿名门禁、静态页面/资源、管理员与普通账号权限、保存—验证—测试—换地址流程、验证码重放、跨账号同收件地址限额、chunked 413、巨大分页 422、登出、真实 IP 登录/注册限流及伪造代理头。证据：`stage7_live.json`、`stage7_live.txt`、`stage7_live_server.txt`（无服务错误输出时为空）、`stage7_replay.json`。

回放使用已有真实历史 HTML 缓存的隔离副本，原解析器不变；通过涨跌及两种到价条件、未触发、缺前价、跨日、重启、四路并发、改规则不重置额度、第二账号独立触发与隔离。回放设置仅对临时测试邮箱直接标记已验证，不放宽正式验证入口。

两项后端 warning 为 Starlette TestClient 的 httpx 弃用提示和原测试上传写法的弃用提示，无测试失败。无真实 Resend/Telegram 送达验收；本阶段通过模拟供应商响应验证发送流程和预算，实际凭据配置后仍应由站长核对真实收信。

## 4. 升级、恢复及兼容性

既有第五阶段数据库升级前先用 SQLite backup API（包含已提交 WAL）创建 `data/backup/*-pre-stage7-*.sqlite3`。迁移在一个事务内新增验证字段、配额/安全事件表，写入 schema_version=7 与 stage7_backup；旧邮箱全部待验证，行情、资源归属和既有推送日额度保留。重复启动不重复迁移。测试验证迁移前备份结构、升级后 integrity_check=ok、幂等；注入第二条 ALTER 失败时新增表/列和版本整体回滚，备份仍存在。

管理员已有账号时忽略 `.env` 播种变更，启动日志列出现有用户名。通常使用账号页改密；需更名/本机恢复时先停服务，根 `.env` 填新凭据，再执行：

```powershell
.\.venv\Scripts\python.exe backend/scripts/manage_admin.py replace --username 现有管理员名 --apply
# 仅恢复指定账号/来源失败锁；IP/全站分钟配额仍需等待
.\.venv\Scripts\python.exe backend/scripts/manage_admin.py unlock --username 现有管理员名 --ip 实际来源IP --apply
```

维护先备份、不从命令行接收密码；更名保留账号 ID/角色/资源并注销全部旧会话；与其他账号同名则拒绝，不提权、不新增管理员。相应备份、碰撞拒绝、会话撤销、针对来源解锁已在临时库验证。未在正式数据库执行维护。回滚第七阶段须停服务并恢复 pre-stage7 备份和旧代码/镜像，不能让旧代码直接使用新结构。

## 5. 依赖与部署单独验收

前端采用 glob **14.0.5**、minimatch **11.0.8**，均高于用户要求的修复下限。保留 js-beautify/editorconfig 的上层版本，避免无关 Node 支持范围变化；`stage7_dependency_compat.mjs` 实测 globSync、editorconfig 的匹配、HTML 格式化和 CLI 组合。实际测试运行 Node 24.22.1，未宣称每个受支持的 Node 20/22 主版本都已实跑。

```text
dependency compatibility: glob14.0.5/minimatch11.0.8/editorconfig3.0.2
glob_matching=passed; editorconfig_minimatch=passed
beautifier_html=passed; beautifier_cli_glob_editorconfig=passed
npm audit: info=0 low=0 moderate=0 high=0 critical=0 total=0
npm audit --omit=dev: total=0
pip version: 26.3
pip-audit requirements: 27 packages, 0 vulnerabilities
pip-audit installed development environment: 35 packages, 0 vulnerabilities
```

证据：`stage7_npm_audit.json`、`stage7_npm_production.json`、`stage7_npm_dependency_tree.json`、`stage7_dependency_compat_output.txt`、`stage7_pip_requirements.json`、`stage7_pip_environment.json`。requirements 无需更改；pip 本身属于工具链而非业务 requirements。扫描结果仅代表本次解析/安装版本与当次漏洞库，不承诺未来解析永久无漏洞。

复跑 pip 审计（本机审计工具独立于业务环境）：

```powershell
.\.tools\audit-tools\Scripts\python.exe -m pip_audit -r backend/requirements.txt --progress-spinner off --format json
.\.tools\audit-tools\Scripts\python.exe -m pip_audit --path .venv/Lib/site-packages --progress-spinner off --format json
```

Compose 仍为内联多阶段 Dockerfile、USER 10001、单 worker、回环端口、只读配置和持久数据卷。新增构建 pip 升级、离线维护脚本拷贝、默认关闭代理头。重新运行 `audit_deployment.py` 的静态核对及隔离健康命令验证，输出 `stage7_deployment_output.txt`：

```text
docker_cli_available=false
inline_user_nonroot=true; single_worker=true; env_read_only=true
data_directory_owned_by_runtime_user=true
health_command_exit_by_mock_status={401:0, 200:1, 500:1}
image_build_or_container_start=not_run_without_docker
```

本机无 Docker CLI/daemon，未执行镜像构建、容器启动、容器内 pip-audit、卷恢复演练或公网 HTTPS 反代验收。`DEPLOYMENT.md` 已要求部署后导出最终运行镜像实际 pip 清单，用独立工具跑 pip-audit，同时检查 UID、卷读写、匿名 401；扫描失败不能记为零漏洞。第六阶段只读部署脚本依赖 PyYAML，本次在隔离审计工具环境补齐后运行成功，未为此改生产 requirements。

## 6. 文件交付与补交清单

### 本阶段业务和文档

- 修改：`backend/app/auth.py`、`alerts.py`、`db.py`、`main.py`、`models.py`；新增：`backend/app/security.py`、`email_verification.py`。
- 修改：`frontend/src/views/PushSettingsView.vue`、`PushLogsView.vue`、`frontend/src/api.js`、`frontend/package.json`、`package-lock.json`。
- 修改：`start.py`、`docker-compose.yml`、`README.md`、`DEPLOYMENT.md`；新增本报告。
- 新增维护/验收工具：`backend/scripts/manage_admin.py`、`smoke_stage7.py`、`stage7_dependency_compat.mjs`、`stage7_verify_submission.py`；修改隔离回放 `backend/scripts/replay_alerts.py`。
- 修改原测试 fixture：`backend/tests/test_accounts.py`；新增 `backend/tests/test_stage7_admin.py`、`test_stage7_alerts.py`、`test_stage7_auth.py`、`test_stage7_body.py`、`test_stage7_email.py`、`test_stage7_isolation.py`、`test_stage7_start.py`，以及 `frontend/tests/push-verification.test.js`。
- 本阶段证据：`backend/scripts/stage7_auth_output.txt`、`stage7_backend_full.txt`、`stage7_dependency_compat_output.txt`、`stage7_deployment_output.txt`、`stage7_frontend_audit_tests.txt`、`stage7_frontend_build.txt`、`stage7_frontend_install.txt`、`stage7_frontend_summary.md`、`stage7_frontend_tests.txt`、`stage7_isolation_output.txt`、`stage7_live.json`、`stage7_live.txt`、`stage7_live_server.txt`、`stage7_migration_admin_tests.txt`、`stage7_npm_audit.json`、`stage7_npm_production.json`、`stage7_npm_dependency_tree.json`、`stage7_pip_environment.json`、`stage7_pip_requirements.json`、`stage7_replay.json`、`stage7_submission_check.json`。分项测试输出只代表当次子集，以最终全量输出为准。

### 第六阶段补交（原先未跟踪）

- 根目录：`AUDIT_REPORT.md`。
- 修复断言已更新的审计测试：`backend/tests/test_audit_auth.py`、`test_audit_input.py`、`test_audit_logic.py`、`audit_frontend.test.js`。
- 原工具（位于 backend/scripts）：`audit_deployment.py`、`audit_frontend.config.mjs`、`audit_live.py`、`audit_permissions.ps1`、`audit_pip_probe.py`、`audit_secrets.py`、`audit_secrets_review.py`。
- 原证据/分项报告（位于 backend/scripts）：`audit_auth_findings.md`、`audit_auth_output.txt`、`audit_backend_all_output.txt`、`audit_baseline_backend.txt`、`audit_deployment_output.txt`、`audit_frontend_findings.md`、`audit_frontend_output.txt`、`audit_input_output.txt`、`audit_live_output.txt`、`audit_live_server_output.txt`、`audit_logic_findings.md`、`audit_logic_output.txt`、`audit_npm.json`、`audit_npm_production.json`、`audit_permissions_output.json`、`audit_pip_environment.json`、`audit_pip_findings.md`、`audit_pip_probe_output.txt`、`audit_pip_requirements.json`、`audit_replay_result.json`、`audit_secrets_result.json`、`audit_secrets_review.md`、`audit_secrets_review_shapes.json`。

提交前使用 `stage7_verify_submission.py` 对暂存文本与根 `.env` 中非空敏感值做内存精确匹配，输出仅含计数/键名；核对数据源、行情配置、正式 .env、data、用户提示词和个人启动脚本均未进入本阶段提交。结果文件为 `stage7_submission_check.json`。不把此精确匹配称为新一轮完整历史密钥审计。

按 AGENTS.md 在本阶段形成一个提交，不推送远端。用户已有 `PROMPTS.md` 修改及未跟踪的阶段提示词、个人启动脚本保留原样，不纳入提交。阶段完成后停下等待确认；M-08 运维权限整改、真实邮件验收、最终镜像/HTTPS 部署验收留待相应环境单独执行。
