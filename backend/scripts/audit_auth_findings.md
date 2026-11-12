# 认证与账号隔离审计分项证据（供总报告整合）

审计对象：当前工作树业务代码未修改；临时 `.env`、SQLite 使用 pytest 临时目录；发送器全部 mock；无真实推送、无上游请求。

复现文件：`backend/tests/test_audit_auth.py`。原始输出：`backend/scripts/audit_auth_output.txt`。

最终运行：`.\.venv\Scripts\python.exe -m pytest backend/tests/test_audit_auth.py -q -s --basetemp <TEMP下新建的procurement-audit-auth-随机目录> -p no:cacheprovider`。结果：`8 passed, 1 warning in 6.22s`。警告为 TestClient/httpx 弃用提示，不影响断言。第一次运行的 `backend/tests/.audit-auth-tmp/` 清理遭自动审批拒绝（仅报告 blocked by policy），目录留存测试库与临时配置，无真实凭据；未改换方法重试删除。

## 可纳入正式发现

### 高：普通注册账号可向未验证第三方邮箱无限发送测试邮件

- 位置：`backend/app/main.py:155-157`、`backend/app/main.py:190-195`；`backend/app/alerts.py:234-257`、`backend/app/alerts.py:299-302`。
- 路径：注册普通账号 → 保存任意语法合法的第三方邮箱 → 重复 POST `/api/alerts/test`。该接口无速率、日额度或验证邮箱要求；`rule_id=None` 明确绕过规则日额度查询。收件人无需证明所有权，变更后立即生效。
- 实测输出：`AUDIT UNVERIFIED_UNLIMITED_EMAIL {"http_200":20,"mock_sends":20,"real_external_messages":0,"recipient_verified":false,"requests":20}`。
- 影响：配置 Resend 后，任意普通账号可消耗全站共享邮件配额并骚扰任意地址，影响发件域信誉和正常告警送达。内容固定为测试消息，不宣称任意邮件正文注入；未配置 Resend 时不会发出真实邮件，但仍可无限写测试日志。
- 建议：收件邮箱先验证；收件地址变更撤销验证状态；测试发送按账号、IP、目的地址和全站设置短期速率与每日额度；返回 429 并记录审计信息。验证邮件本身也需防滥用。账号规则数/创建频率应有上限，避免通过不断新建规则绕开每规则额度。

### 中：匿名注册无节流，Argon2 在 SQLite 全局写锁内执行

- 位置：`backend/app/auth.py:116-128`；对比仅登录/改密调用节流 `backend/app/auth.py:99-112`、`:133-145`。
- 证据：同一匿名 IP 连续 12 个不同账号注册全部 201，耗时 0.741 秒；测试在每次调用真实 Argon2 前用第二个 SQLite 连接执行 `BEGIN IMMEDIATE`，12 次均得到 database locked，证明哈希期间持有写锁。
- 输出：`AUDIT REGISTRATION_NO_LIMIT {"argon2_inside_global_sqlite_write_lock":true,"http_statuses":[201,201,201,201,201,201,201,201,201,201,201,201],"requests_same_ip":12,"seconds":0.741}`。
- 影响：公开注册可被低成本批量创建账号，持续消耗 CPU/Argon2 内存、磁盘，并让登录、规则、推送、抓取入库等待同一个 SQLite 写锁。此复现证明缺少限制与锁范围，未对正式服务做压力攻击，也不将潜在全站中断表述为已发生。
- 建议：匿名注册加入 IP/全站速率与账号数量治理；哈希放到短写事务之前，在实际插入事务内再次检查注册开关和唯一性，保留现有原子保护；控制并发哈希数量。

### 中：全局账号硬锁可由匿名请求阻断其他 IP 的正确登录

- 位置：`backend/app/auth.py:99-106`、`backend/app/auth.py:133-143`。
- 证据：攻击端客户端地址 `198.51.100.10` 对已知管理员名错密 5 次，然后正常客户端 `203.0.113.20` 用正确密码登录，得到 429 和 `Retry-After: 900`。
- 输出：`AUDIT ACCOUNT_LOCKOUT_DISTINCT_IP {"attacker_failures":[401,401,401,401,401],"correct_password_other_ip":429,"retry_after":"900"}`。
- 影响：知道用户名的匿名人可每 15 分钟用 5 次请求持续阻断新登录，管理员也受影响；已存在的会话不被撤销。属于节流可用性/抗恶意锁定问题，不是登录限速不存在，也不代表绕过密码。
- 建议：保留防爆破能力，结合来源/账号组合限流、渐进延迟与风险验证，避免少量任意来源请求直接将全账号硬锁；提供可靠的管理员恢复路径并记安全日志。

### 低（配置行为偏差，可作为观察项）：换播种用户名后重启会新增第二个管理员

- 位置：`backend/app/db.py:53-61`；`backend/app/auth.py:207-213`。
- 实测在隔离 `.env` 改成新的 ADMIN_USERNAME 后调用初始化，库里同时保留 `test_admin` 与 `replacement_admin` 两个 admin，旧账号仍不可通过用户管理删除。
- 输出：`AUDIT BOOTSTRAP_ON_RESTART {"admin_names":["test_admin","replacement_admin"],"old_admin_still_protected":true}`。
- 此操作需要可信运维修改 `.env`，不是远程提权漏洞。与“首次启动播种”理解不一致，运维若想换名/轮换账号可能保留旧高权限入口。
- 建议：仅无 admin 时自动播种；另提供明确、可审计的管理员迁移流程，或至少在文档明确改名会增建管理员而非替换。

## 已查，未发现（有运行证据）

- 密码哈希算法为 `argon2id`，参数 `m=65536,t=3,p=4`，数据库无密码明文；会话由 `secrets.token_urlsafe(32)` 产生 256 位随机量，实测 43 字符，数据库仅 SHA-256 摘要；有效期 7 天。
- Cookie 为 HttpOnly、SameSite=Strict、Path=/、Max-Age=604800；默认 HTTP 开发不设 Secure，启用 `.env` 设置时会添加 Secure（原有测试覆盖）。HTTPS 部署需要该配置属于已注明的部署约束。
- 改密、禁用、删除后持原 Cookie 请求均 401；重新启用账号不会复活旧会话。补充审计实际验证登录轮换后的旧 Cookie、退出后的 Cookie、数据库过期的 Cookie 均 401；配置 Secure 后属性存在。原有测试另覆盖禁止改写既有管理员密码。
- 注册多塞 `role=admin`、`user_id`、`enabled` 均 422，业务硬编码 role=user。旧版迁移归管理员和重复启动幂等由原有测试验证。
- 从当前 `/openapi.json` 枚举 24 个受保护 API，匿名 GET/POST/PUT/DELETE 全部 401。
- 普通用户对 7 个全局管理操作（来源状态、抓取、Telegram 状态、用户列表、禁用、删除、注册开关）全部 403。
- B 请求 A 的规则 id：GET 404（没有单项 GET 路由），PUT/DELETE 404；列表加入 user_id/id query 仅返回 B 自己规则；create body 注入 user_id 422。
- B 请求 A 的推送设置：query user_id 被忽略且只返回 B；body user_id 422；按 id GET/PUT/DELETE 无路由 404/405；A 的邮箱未改变。
- B 请求 A 的推送日志：query user_id/id 无法改变 owner 过滤；按 id GET/PUT/DELETE 无路由 404/405；A 的日志未暴露。
- B 立即检查注入 A 的 user_id/rule_id，仍只评估 B 的规则，A 未生成日志；B 测试推送注入 user_id 仍发给 B 的邮箱。
- 回放没有 HTTP 接口，POST `/api/alerts/replay` 返回 405；实际是本地隔离脚本 `backend/scripts/replay_alerts.py`，它使用临时 SQLite 和 ReplaySender，显式传入 user_id，管理员与个人账号的查询/去重分开。真实脚本运行由主审计记录。
- 来源写请求的跨站 Origin/Sec-Fetch-Site 校验和 JSON 类型拒绝由原有测试覆盖；并非依赖前端隐藏菜单实现授权。

核心归属检查代码：规则保存/删除 `backend/app/alerts.py:204-224`；日志查询 `:227-232`；发送前重查账号、角色、归属 `:234-253`；立即检查查询 `:304-314`；API 当前用户绑定 `backend/app/main.py:120-162`、`:182-195`。
