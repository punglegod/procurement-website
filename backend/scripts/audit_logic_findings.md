# 业务与部署审计分报告（供根目录 AUDIT_REPORT.md 整合）

检查对象：`e34b8ee` 工作树业务代码；仅新增审计脚本，未修改业务代码或正式数据。

## 已复现发现

### 中：提醒评估与领取额度之间存在规则更新竞态

- 位置：`backend/app/alerts.py:312-324`、`backend/app/alerts.py:242-245`。
- 原因：`run()` 在一个会话中读取并评估规则、生成文本；随后 `deliver()` 在新事务中只检查规则存在、归属与启用状态，没有核对阈值、品种、口径或版本仍与评估快照一致。
- 实际复现：使用两个真实线程和 Event 固定可达的交错顺序。线程一已以阈值 1、价格 100 评估命中，线程二把同一规则阈值成功改为 1000；线程一继续发送旧条件通知。
- 输出：`AUDIT_RULE_EDIT_RACE {"saved_threshold": "1000.0000", "current_price": 100, "current_condition_matches": false, "result": "sent", "isolated_sends": 1, "message_uses_old_threshold": true}`
- 影响：提醒在条件已不满足时仍发送，并消耗当天该规则通道额度，后续真正达到新条件时可能无法通知。不是跨账号越权。
- 建议：在领取额度的事务中重读规则及报价并重新评估，或给规则增加单调递增版本并检查快照版本；将评估结果、消息和额度领取绑定于同一版本。网络请求继续置于事务外。

### 中：一次检查跨午夜时沿用开始日期，发送旧报价并使实际发送日的去重失效

- 位置：`backend/app/alerts.py:306`、`backend/app/alerts.py:314-324`、`backend/app/alerts.py:257-261`。
- 原因：一次 `run()` 只读取一次当前北京时间，之后所有串行发送使用这一 `at`；`deliver()` 不再确认实时日期，日志与日额度也使用旧日期。
- 实际复现：检查于北京时间 11 月 12 日 23:59:59 开始，隔离通道在首条发送后把时钟推进至 11 月 13 日 00:00:01。第二条仍发送 11 月 12 日报价，并记入 11 月 12 日额度；随后写入隔离的 11 月 13 日报价并检查，第二规则当天再发一次。
- 输出：`AUDIT_MIDNIGHT {"actual_send_dates": ["2026-11-12", "2026-11-13", "2026-11-13", "2026-11-13"], "recorded_send_days": ["2026-11-12", "2026-11-12", "2026-11-13", "2026-11-13"], "second_rule_actual_sends_on_nov13": 2, "quote_date_in_second_message": true}`
- 影响：违反仅推送当天报价及实际北京时间自然日最多一次尝试的承诺。正常整日并发/重启去重仍有效；触发窗口要求检查跨午夜，可由午夜前手动检查或大量串行通道响应导致。
- 建议：真实发送前使用实时北京时间核对报价和领取日；检测日期变化则终止本轮或重新评估。历史回放需保留明确的模拟时钟模式，与线上实时检查分开。

### 低：分页 page 缺少上限，超大整数导致未处理 500

- 位置：`backend/app/main.py:151`、`backend/app/alerts.py:231`。
- 复现：已登录账号请求 `/api/alerts/logs?page=9223372036854775808&page_size=100`；page 只有 `ge=1`，计算出的 OFFSET 超出 SQLite 有符号 64 位范围。
- 输出：`AUDIT_PAGINATION {"ordinary_page": 200, "ordinary_items": [], "huge_page": 500, "huge_body": "Internal Server Error"}`；内部直接调用确认 `Python int too large to convert to SQLite INTEGER`。
- 影响：恶意/错误参数触发服务器异常与错误日志；响应未泄露堆栈或路径，未观察到数据库损坏。正常超出末页返回空数组。
- 建议：限制 page/计算后的 offset 上界，或超过总页数时直接返回空数组；无效范围返回 422/400。

## 实际执行命令与证据

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests/test_audit_logic.py -q -s --basetemp backend/tests/.audit-logic-tmp -p no:cacheprovider
.\.venv\Scripts\python.exe backend/scripts/audit_deployment.py
```

逻辑复现结果：`3 passed, 1 warning in 2.51s`。警告为已有 FastAPI/Starlette httpx TestClient 废弃提示。

完整输出：`backend/scripts/audit_logic_output.txt`、`backend/scripts/audit_deployment_output.txt`。脚本使用临时 `.env` 与 SQLite、隔离发送器；不访问真实行情源，不发送真实邮件/Telegram。`backend/tests/.audit-logic-tmp/` 是 pytest 临时产物，仅含测试账户与合成价格，验证后已按逐文件方式清理。

## 已查未发现与验证边界

- 调度时区：`main.py:42-45` 显式 `Asia/Shanghai`，每天 15:30 抓取，每 5 分钟检查；`max_instances=1`、`coalesce=True`。单 worker/单进程是 README 和部署明确约束。多进程免费源抓取仅靠进程内锁，不能保证多个不受支持副本间互斥；本报告不将违背部署前提的运行方式单独定为漏洞。
- SQLite：`db.py:13-19` 设置 15 秒 busy timeout、WAL、foreign_keys；提醒 `BEGIN IMMEDIATE` 加唯一约束 `models.py:77`；网络在额度提交后执行，不长期占用写事务。通常同日并发/重启额度由数据库保证；跨日发现见上。报价入库在同一个 session/事务内按唯一日线键 upsert，口径不一致抛错并回滚。
- 涨跌与报价口径：查询过滤品种、来源、口径、单位；显示百分比采用 Decimal/ROUND_HALF_UP，提醒使用未四舍五入值；低端口径明确而未擅自标成均价；解析器拒绝未来日期、混用规格、未公开价格。静态核对已查，未发现新增计算或口径问题。
- 空数据与分页：无规则返回空列表，普通超出末页返回 200 与空列表；超大整数问题见上。
- Dockerfile：仓库没有独立 Dockerfile，`docker-compose.yml:11-32` 使用内联多阶段构建。运行用户 `10001:10001`，`data` 目录显式 chown；主机端口绑定 `127.0.0.1:8000`，`.env` 只读挂载且根目录不在构建上下文；单 worker 与资源/日志限额符合预期。已查，未发现部署配置中的高权限运行或公网端口意外开放。
- 健康检查：从 YAML 读取原 Python 命令，仅替换 URL 为随机本地端口，实际执行隔离 HTTP 服务响应：401→exit 0，200→exit 1，500→exit 1。不是仅检查字符串，也未将此等同于容器运行通过。该检查只验证 HTTP 门禁存活，不能验证 DB、调度或上游源健康，部署文档已说明限制。
- Docker CLI 不存在（`shutil.which('docker') == None`），无法执行镜像构建、容器启动、Linux 实际卷权限或容器内进程身份验证。当前没有采购站点 Python/uvicorn 进程，不能宣称实际服务进程权限通过。`start.py` 的本地服务继承启动用户权限；文档指定默认回环访问。
- `audit_deployment.py` 用环境已有 PyYAML 解析 compose；未修改项目 requirements。

临时目录清理说明：递归删除被本机自动审批策略拒绝后，改为列明六个本次测试文件进行非递归删除，再移除空目录，已验证目录不存在；没有删除或移动用户已有文件。
