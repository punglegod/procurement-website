# 部署与维护说明（第九阶段）

目标为一台 3 核 / 20G Linux 主机。只运行一个 FastAPI 容器、一个 worker；Vue 构建产物由后端静态托管。SQLite 位于持久卷，APScheduler 位于同一进程。容器限额 2 CPU / 2G 内存，为宿主系统和构建留余量。不要增加副本或 worker，也不要同时运行 `start.py`。


## 第九阶段上线动作：调度、日报与卓创配置

### 配置与安全入口

本阶段不操作生产。上线仍使用单实例、单 worker，保留现有 Cloudflare/受控代理的 override command 和已核实的代理地址、PUBLIC_BASE_URL 及 SESSION_COOKIE_SECURE=true。不要用新模板覆盖现有 .env，不要把后台源配置当成代理配置；第八阶段 HTTPS 登录事故修复继续有效。

1. 先备份数据库和服务器配置。运维在受控终端离线生成一份 Fernet 密钥，补到生产根 `.env` 的 **SETTINGS_ENCRYPTION_KEY**，随后与数据库备份分开保管。密钥不进入聊天、命令行参数、Git 或日志；不要每次部署重新生成。

```bash
# 仅由运维在受控终端执行；生成结果不要发聊天或放报告。
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Fernet 使用 URL-safe Base64 编码的32字节密钥和认证加密，不能拿账号密码替代；参考 [cryptography 官方 Fernet 文档](https://cryptography.io/en/latest/fernet/)。应用只读取密钥，**不会自动生成或写回 .env**；缺失/无效时启动警告且禁止保存后台凭据，免费行情等其他功能仍可运行。已有后台密文无法解密时也不静默回落，须恢复正确密钥或明确清除后台配置。

2. **确切入口：管理员登录 → 站点管理 → 全局设置 → 数据源配置 · 卓创资讯。** 账号、密码、Cookie 仅在提交时传给服务器，接口不返回明文，页面显示掩码、保存后清空输入。没有密钥时凭据输入禁用；无凭据测试明确“未配置”，不请求官网。普通账号没有入口，所有相关接口均403。
3. 账号密码和Cookie作为一包加密写入系统设置表。后台包存在时优先于 SCI99_USER / SCI99_PASS / SCI99_COOKIE；空输入表示保留字段，不把掩码提交。需要回落时点“清除后台凭据并回退服务器配置”，会删除后台包并关闭启用开关，随后读取根 .env。URL仍从受控服务器配置读取，不开放任意URL输入。启用开关独立持久化，默认关；仅配置凭据不会自行启用。
4. “测试连接”只验证一次登录可用性，不批量抓价；无凭据不发请求。账号登录需要普通官网表单，遇JS人工登录/验证码/风控立即停止。Cookie只查官方账户页的登录标志。测试限管理员1次/15分钟、来源IP2次/15分钟、全站4次/24小时，不消耗价格采集日额度。供应商页面未确认时如实失败，不能把“已保存”或“已启用”当成真实采集接入成功。
5. 本阶段不要求启用卓创，生产建议保持默认关。旧框架识别华东ABS0215A，与当前华南免费口径不同，明确拒绝混入现有行情；若启用，只有09:00自动轮允许框架采集，数据库日额度最多尝试一次，失败/重启/重跑也不重置。其他三轮和手动轮跳过。后续真正接入品种报价需要另行核定口径，不能仅开开关便替换当前免费数据。

### 自动迁移与回滚

停止旧实例后部署新代码；首次启动自动对已有第七/八阶段库创建 `data/backup/*-pre-stage9-*.sqlite3` 一致性备份，再以单事务新增：

- push_settings.daily_digest_enabled（可空；空值按角色默认，已有验证状态不变）。
- fetch_batches：轮次、开始/结束时间、逐品种结果；运行编号与进度推送对应。
- daily_digests：每天一份不可覆盖的价格/走势快照。
- notification_claims：每账号/类型/事件/通道的持久领取记录，跨重启与并发去重。

系统设置表存放密文、卓创启用及进度开关，不迁入或复制真实凭据；SQLite现有字符串列可兼容密文长度。schema_version更新为9，stage9_backup保存备份路径；迁移失败整体回滚，备份保留。价格、规则、会话、邮箱验证及既有规则额度保留。更老库仍先用对应 pre-stage5/pre-stage7 备份策略；本机测试已验证第八阶段副本升级和故障回滚。

回滚第九阶段须停止服务，保存新库与密钥，恢复 pre-stage9 备份和第八阶段镜像/代码，保留已有代理 override。回滚会丢弃之后的新设置/轮次/日报记录；不能对运行中的库直接覆盖。密钥丢失无法恢复后台凭据明文，不可用随机新密钥“修复”；恢复备份密钥或清除后重新录入。

### 调度切换与发送语义

固定四轮 **08:00 / 08:30 / 09:00 / 15:30，Asia/Shanghai**；替换旧15:30任务为四个新任务，保留原下午时段，不启动第二个进程。每轮11免费品种，独立失败；同口径同报价日同价格不再更新价格点时间或新增行，来源状态仍记录本轮尝试。手动入口同样生成运行记录与管理员开始/完成通知，始终不补发日报、不触发卓创。

09:00轮完成时生成日报，不设另外的固定发送时刻；15:30不重发。09:00异常则加“09:00 轮抓取异常，本报为最近可用数据”。每分钟的状态巡检给09:00轮2分钟启动宽限，正在执行且未超30分钟时等待完成；漏跑、停机重启后未跑或超过30分钟未结束才按最近可用数据补报。部分品种失败在完成消息点名，日报加异常说明，不另轰炸告警；整体异常/漏跑告警同事件同日去重。进程完全不运行时无法自行通知，仍需外部存活监测。

日报每用户有独立开关（“我的推送设置 → 每日行情日报”）：管理员默认开，普通账号默认关。管理员Telegram与已验证邮箱独立发送，普通账号只用已验证邮箱，价格规则设置不受影响。每日只生成一份快照，同账号/北京日期/通道最多尝试一份；失败/未知/发送中断保守占用额度，不自动重发。待配置/待验证记录可在当天补配置后原位发送；晚开启订阅也只发送当天已有快照，不用下午价格重生成第二份。

日报近7日按自然日窗口内现有报价首尾比较，不插值；同口径历史跨度不足7自然日或窗口少于2点时明确“历史数据不足”。近30日高低价只基于窗口内可用点并标条数；方向使用首尾涨跌和区间振幅，涨/跌/平同时有文字与符号。过期与站内都按最近周一至周五工作日，不接付费日历或臆测节假日。Telegram纯文本、过长按UTF-16预算分段；邮件HTML转义后与文本同内容，标题均为“采购行情日报 · YYYY-MM-DD”。

“全局设置 → 抓取调度与通知 → 抓取进度推送”默认开，仅管理员Telegram，覆盖开始、完成和调度异常告警。进度日志类型“抓取进度”，日报类型“日报”，普通账号不能查看管理员记录；“最近抓取记录”列运行编号、结果，与通知互相印证。多个管理员沿用原全局Telegram通道配置，各账号分别记账。

### 上线验收清单

- 核对 SETTINGS_ENCRYPTION_KEY 已由运维安全配置、被服务账户可读，启动无缺钥警告；后台保存/读取只见掩码，保持卓创默认关。
- 核对合并后的代理 command、公开来源、安全Cookie和第八阶段正常登录/外站Origin拒绝，保留端口回环绑定。
- 确认 schema_version=9、pre-stage9 备份、三张新表及可空开关，原价格/规则/验证状态保留。
- 确认四个北京时间任务均注册，旧进程停止；预期09:00完成触发日报，下午无第二份，来源失败在完成通知汇总。
- 注意升级当天若09:00已过去且新轮次表没有记录，巡检会在一分钟左右补一份明确标异常的日报及一条漏跑告警，遵循当前开关与验证状态。这不是新的固定发送时刻。
- 人工核对管理员/普通账号开关默认值、未验证邮箱不发、用户隔离、进度只发管理员。真实通道送达验收由站长另行执行；本阶段仅mock。
- 按本文既有流程对最终运行镜像再跑 pip-audit，并做持久卷/备份恢复验收。本阶段没有执行生产部署或容器构建。

## 环境准备

安装 Docker Engine、BuildKit 和 Docker Compose 插件 **2.17+**（需支持 `dockerfile_inline`、`additional_contexts`；建议使用发行版支持的近期稳定版）。依据 [Docker 官方 Compose 构建说明](https://docs.docker.com/reference/compose-file/build/) 与 [多阶段构建说明](https://docs.docker.com/build/building/multi-stage/)。本仓库将 Dockerfile 内联在 compose 中，因此部署部分只有本文件与 `docker-compose.yml`。

上传代码到 `/opt/procurement-website`，保留 `backend/`、`frontend/src/`、前端 package 文件、`index.html`、`vite.config.js` 和 compose 文件。不上传本地 `.env`、`.venv`、`frontend/node_modules`、日志或整份 `data/`。前端上下文无需本机 node_modules；构建自动执行 npm ci / npm run build。根目录 `.env` 和 `data/` 不属于两个构建上下文，均不进入镜像。

以下命令为将来在服务器执行的步骤，本阶段未运行：

```bash
cd /opt/procurement-website
docker version
docker compose version
cp .env.example .env
chmod 644 .env
```

用编辑器填入首次播种所需 `ADMIN_USERNAME` / `ADMIN_PASSWORD`（8～128 位），以及 Telegram token/chat_id。邮件需要 `RESEND_API_KEY`、`EMAIL_FROM`（默认 sourcing@bbbhl.com）；用户在个人设置填收件邮箱。`SCI99_USER`、`SCI99_PASS`、`SCI99_COOKIE` 留空；模板中的卓创 URL 不会自行启用 adapter，须由管理员在后台明确启用。服务只从 `/app/.env` 读取，不能使用 compose `env_file` 替代文件挂载。容器使用 UID/GID 10001，需能读取挂载的 `.env`；项目父目录只允许管理员访问，例如 `chmod 700 /opt/procurement-website`。配置只读挂载，不写入镜像；更严格权限可用 ACL 授予 UID 10001 只读。

构建需访问 Docker Hub、PyPI、npm registry；运行需访问各公开行情站、api.telegram.org:443、api.resend.com:443。不要照抄开发机 `127.0.0.1:7890` 代理：容器内回环是容器自身。如需代理，应配置服务器实际可达的 HTTPS_PROXY，不提交代理凭据。

## 构建、启动、访问

```bash
docker compose config --quiet
docker compose build --pull
docker compose up -d
docker compose ps
# 匿名请求应为 401；compose 健康检查也验证该认证门禁。
curl -i http://127.0.0.1:8000/api/health
docker compose logs --tail=100 web
```

服务不在启动时抓价，固定北京时间（Asia/Shanghai）每天08:00、08:30、09:00、15:30四轮抓取，每 5 分钟检查所有有效账号的提醒；主机时间须正常同步。首次采集请管理员登录后在“全局设置”点击“立即抓取行情”，会在采集后检查真实提醒。失败不删历史，旧日期仍显示“过期”；健康检查不表示来源成功，管理员应查看数据源状态。所有 API 均需会话，不再支持匿名 curl 写操作。

从自己的电脑建立 SSH 隧道：

```bash
ssh -N -L 8000:127.0.0.1:8000 your-user@your-server
```

浏览器打开 `http://127.0.0.1:8000/` 后登录。注册只创建普通账号，全局设置、抓取、用户管理仅管理员可用。默认 compose 绑定服务器回环，安全组不开放 8000，启动显式 `--no-proxy-headers`，伪造转发头不会改变限流来源。HTTP 本地隧道的 `SESSION_COOKIE_SECURE` 保持 false。本地默认不包含公网入口。

### HTTPS 入口与第八阶段生产迁移

公开来源和代理信任必须分别配置，两者解决不同问题，不能二选一：

1. 在服务器根 `.env` 设置 `PUBLIC_BASE_URL=https://www.bbbhl.com`（当前生产站点），保持 `SESSION_COOKIE_SECURE=true`。这是本次部署需要追加的配置。PUBLIC_BASE_URL 只允许协议、域名和可选端口，不含路径、凭据、查询或片段；尾 `/` 允许并会归一。它也可作为非敏感进程环境变量传入，进程值优先；凭据仍仅来自根 `.env`。
2. **保留生产现有 `docker-compose.override.yml` 的 `--proxy-headers` 和经核实的 `--forwarded-allow-ips` 列表，不要删掉或被主 compose 的默认命令覆盖。** 若网络拓扑未变，本次无需调整其信任地址。信任项必须覆盖容器实际看到的隧道/反向代理对端；经 Docker NAT 后可能是网络网关，而非主机回环。不要复制本地或他台机器的地址，也不要使用 `*`。
3. **入口必须覆盖客户端自带的转发头并透传真实访客 IP（X-Forwarded-For）及 HTTPS 协议（X-Forwarded-Proto），或者在可信入口按真实访客 IP 做限流。** 即使入口有限流，也应透传真实 IP，避免应用来源桶将所有访客合并。不可把客户端任意提供的 XFF、CF-Connecting-IP 或 X-Real-IP 直接当成应用身份；Uvicorn 仅从受信连接解析 XFF 后更新 ASGI client，应用统一使用该 client。
4. 主 compose 的 `--no-proxy-headers` 是无反代的安全默认。反代的 command 覆盖模板如下；将占位地址替换为容器**实际所见的受控代理 IP/CIDR**。隧道入口仍保持主机端口只绑定回环，禁止绕过入口直连。

```yaml
services:
  web:
    command: ["python", "-m", "uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips", "192.0.2.10"]
```

示例为文档保留地址，不是生产信任值。非容器入口 `start.py` 的对应选项为 `--trusted-proxy-ips <实际代理IP或CIDR>`。配置 HTTPS 公开来源而该入口未启用代理头时，启动会警告而不阻断；直接启动 Uvicorn 时需自行核对 command。固定公开来源可避免内部 scheme 错误导致合法 Origin 403，**不能修复真实 IP 透传**，也不会自动信任任何代理。

未配置 PUBLIC_BASE_URL 时继续用请求 scheme + Host 重构来源，兼容现有代理配置和本地开发；因此先保留已修复的 override，再追加公开来源配置。配置错误会在启动时明确失败，运行时不能静默退回 Host。HTTPS 的 Secure Cookie 默认已开启；本地 HTTP 和 HTTP SSH 隧道须显式设为 false，否则会表现为登录后没有会话。

部署由运维按下列顺序执行（本阶段未连接或修改生产）：

- 备份数据库与现有 override，追加公开来源配置，核对 secure=true，不在输出中展示配置全文或凭据。
- `docker compose config --quiet` 检查语法；核对合并后的 web.command 仍有代理头和正确受控地址，再构建重启。
- 在公开 HTTPS 地址用错误凭据发 JSON 登录：合法 Origin 应为正常鉴权 401，外站 Origin 必须 403；然后用浏览器正常登录，检查 Cookie 的 Secure/HttpOnly/SameSite=Strict。
- 从两个真实外部来源验证限流桶独立；在受控入口/脱敏安全事件中核对访客 IP 摘要，确保不是全体落到一个网关桶。不要向公网发送测试邮件来验证 IP。
- 按下文对最终运行镜像执行依赖审计与健康检查。第八阶段没有数据库结构变化，可保留配置回滚代码；回滚时仍保留已生效的代理 override，避免登录事故复发。

2026-11-12 登录事故的运行拓扑和修复来自站长提供的生产记录；仓库验证使用隔离代理链，不把生产专属网关地址写入默认配置或代码。

## 持久化、备份、迁移

SQLite 及 WAL/SHM 文件在 `procurement_procurement_data` 命名卷，重建容器和普通 `docker compose down` 均保留。**不要执行 `docker compose down -v`**。首次启动空卷自动建表；不自动迁移开发机历史。

第五阶段首次升级已有第四阶段数据库，先在卷内 `/app/data/backup/<库名>-pre-stage5-<时间戳>.sqlite3` 做一致性备份，再事务迁移规则和日志至管理员账号；首次启动缺管理员配置会失败。升级前停止旧实例，确保没有第二个服务同时使用旧结构。备份路径记录在 `site_settings.stage5_backup`，应复制到容器外。恢复第四阶段必须同时恢复该备份和第四阶段镜像，否则新代码启动会再次升级；回滚会丢弃备份之后创建的账号与配置。

在线一致性备份（SQLite backup API 会合并 WAL 中已提交记录；切勿只拷贝运行中的主库文件）：

```bash
mkdir -p backups
docker compose exec -T web python -c "import sqlite3; s=sqlite3.connect('/app/data/procurement.sqlite3'); d=sqlite3.connect('/app/data/backup.sqlite3'); s.backup(d); d.close(); s.close()"
docker compose cp web:/app/data/backup.sqlite3 ./backups/procurement.sqlite3
```

定期将备份复制到另一台机器；建议每日一次、保留 30 天，并单独保存 `.env`。每次备份结束将文件重命名为带日期版本，避免覆盖唯一副本。恢复前先停止服务、保存现有数据库及 WAL/SHM，再用临时容器把备份写回卷：

```bash
docker compose stop web
docker compose run --rm --no-deps --user 0 --volume "$PWD/backups:/restore:ro" web python -c "import shutil,pathlib,os; p=pathlib.Path('/app/data/procurement.sqlite3'); [f.unlink(missing_ok=True) for f in [pathlib.Path(str(p)+'-wal'),pathlib.Path(str(p)+'-shm')]]; shutil.copyfile('/restore/procurement.sqlite3',p); os.chown(p,10001,10001)"
docker compose up -d
```

以上恢复命令会覆盖卷中数据库，只在确认备份、停止服务后使用。迁移本地历史也走此流程：先用 SQLite backup API 导出本地库，然后恢复到服务器卷；同时带上提醒规则与推送日志，保留日去重记录。源/口径变化的旧提醒规则会跳过，需要用户在页面编辑保存后重新确认，不自动迁移阈值。

## 长期运维与更新

`restart: unless-stopped` 处理进程退出和 Docker 重启；健康检查变成 unhealthy 本身不会自动重启，应查看日志、来源状态并处理原因。日志轮转为 10MB × 3。定期检查磁盘、备份完整性、Telegram 日志和来源报价日期。验证码、安全检查、403/429 均停止本次来源请求，不绕限制。

更新前备份数据库，再保留当前镜像用于回滚：

```bash
docker tag procurement-website:stage9 procurement-website:previous
docker compose build --pull
docker compose up -d
curl -i http://127.0.0.1:8000/api/health
```

同结构代码回滚：`docker tag procurement-website:previous procurement-website:stage9` 后 `docker compose up -d --no-build`。跨结构回滚须停服务并恢复对应升级前备份；第五阶段回第四阶段使用 pre-stage5 备份，第七阶段回第五阶段使用 pre-stage7 备份和对应旧镜像。不要将旧库覆盖到运行中的新服务。

手动停止用 `docker compose stop`，管理员登录后查看来源状态。不要开启自动高频重抓。免费页有更新延迟，不能保证每日有当日价格；源结构变化明确失败，不填造价格。

## 本次验证边界

### 第七阶段新增验收

镜像构建先升级 `pip>=26.2.0` 再安装业务依赖；开发机本次已实际升级为 pip 26.3。**部署后必须对最终运行镜像的实际安装清单再跑一次 pip-audit**，不能只用开发机或 requirements 的结果代替。以下命令在目标 Linux 主机执行，审计工具在独立主机虚拟环境，不向运行容器安装工具：

```bash
docker compose exec -T web python -m pip --version
docker compose exec -T web python -m pip list --format=freeze > image-installed.txt
python3.12 -m venv .audit-tools
.audit-tools/bin/python -m pip install --upgrade 'pip>=26.2.0' pip-audit
.audit-tools/bin/python -m pip_audit -r image-installed.txt --progress-spinner off
# 同时核对身份、卷和匿名认证门禁
docker compose exec -T web id
docker compose exec -T web python -c "import os; assert os.geteuid()==10001; assert os.access('/app/data',os.W_OK); assert os.access('/app/.env',os.R_OK)"
curl -i http://127.0.0.1:8000/api/health
```

审计零已知漏洞、UID 10001、卷可写、凭据可读、匿名 health 为 401 后才记录验收通过。`image-installed.txt` 不含凭据；如存在无法解析/不适用项，须逐项核定，不能把扫描失败当成零漏洞。应另做一次卷备份恢复演练。

已有第五阶段库首次运行本阶段会先创建 `pre-stage7` 一致性备份，事务新增验证/配额/安全事件结构，旧邮箱转为待验证；Telegram 与既有日额度保持。仅当没有任何管理员才播种；启动日志列出现有用户名。更名、密码轮换、特定来源解锁见本文“管理员更换与登录恢复”；镜像内只携带 `backend/scripts/manage_admin.py` 这一维护脚本。未验证邮箱不发测试/提醒；验证与测试受四维配额约束。应用统一限制 JSON 正文为 16 KiB，反向代理可同步设置限制，勿配置为大于业务需要的上传服务。

本阶段没有执行远端部署或 Windows 权限修改。下列 Docker 实跑限制仍成立：

已对 compose 的 YAML、构建输入路径、单 worker、只读凭据挂载、数据卷、限额、健康检查和日志轮转做静态核对。本机没有 Docker CLI / daemon，因此 **未执行镜像构建、compose 启动、容器健康检查或卷恢复演练**。这些是将来服务器部署时待跑的步骤，不记为本阶段通过；本阶段未连接或部署任何服务器。

## 本地安装与开发

首次准备（已有 .env 不覆盖）：Python 3.12 建虚拟环境，升级 pip 并安装依赖；复制模板后填首次播种配置。本地 HTTP 必须显式 SESSION_COOKIE_SECURE=false，PUBLIC_BASE_URL 留空；HTTPS 使用安全默认。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade "pip>=26.2.0"
.\.venv\Scripts\python.exe -m pip install -r backend/requirements-dev.txt
.\.venv\Scripts\python.exe start.py
```

Linux/macOS 使用 `python3.12 -m venv .venv`、`.venv/bin/python`。启动默认单 worker、回环 8000；可用 --port、--skip-build，后者要求已有 frontend/dist。按 Ctrl+C 停止。需要代理时由操作者设置真实可达的 HTTPS_PROXY/NO_PROXY，不提交代理凭据。

### 前端单独开发

以下是开发用途，不是日常全站运行所必需。两个终端分别执行：

```powershell
# 终端一：后端，单 worker
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --workers 1 --reload --no-proxy-headers
# 终端二：Vite 开发服务器，/api 代理到后端
cd frontend
npm ci
npm run dev
```

打开 Vite 输出的本地地址（默认 <http://127.0.0.1:5173>）。单独构建：`cd frontend` 后执行 `npm ci`、`npm run build`。使用构建产物前重启后端，使其挂载 `frontend/dist`。日常推荐根目录 `python start.py`，无需开发服务器。

## 管理员更换与登录恢复

只有系统不存在任何 admin 时才读取 `.env` 播种。已有管理员时启动日志明确列出现有用户名；修改 `.env` 不会更名或增建管理员。通常密码轮换使用账号页改密码。确需本机更名/重设密码时，先停止服务，在根 `.env` 填入新的 ADMIN_USERNAME / ADMIN_PASSWORD，再执行维护脚本。它先备份，保留原管理员 ID、角色和资源归属，仅改用户名/密码并撤销所有旧会话；目标同名普通账号会被拒绝，不会提权。不要删除管理员行或数据库来重新播种。

```powershell
# 先停止服务；用户名不是密码，凭据不放命令行
.\.venv\Scripts\python.exe backend/scripts/manage_admin.py replace --username 现有管理员名 --apply
.\.venv\Scripts\python.exe start.py --skip-build
```

来源组合限流时，现有会话仍可用；等 Retry-After，或从其他可信来源登录。确需本机恢复时先停服务，只清指定管理员和来源组合，保留其他账号及全站/IP 配额（来源总量预算仍可能需等待一分钟）：

```powershell
.\.venv\Scripts\python.exe backend/scripts/manage_admin.py unlock --username 现有管理员名 --ip 127.0.0.1 --apply
```

Linux 换用 `.venv/bin/python`。容器先 `docker compose stop web`，再 `docker compose run --rm --no-deps web python backend/scripts/manage_admin.py replace --username 现有管理员名 --apply`（或 unlock），完成后 `docker compose up -d`。维护不清安全事件、不改系统权限，仅在可信本机操作。

## 安全策略与邮箱验证

保存邮箱后在“我的推送设置”点击发送验证码，输入邮件中的 **8 位数字**确认。验证码 15 分钟有效、最多 5 次错误尝试、一次性消费，库中只存带随机盐的摘要，不写响应或日志。换邮箱即撤销验证和旧验证码；保存同一地址不清除已验证状态。旧库邮箱升级后全部待验证。未验证时规则标 `pending_verification`、不发邮件、不占日额度；普通账号测试返回 403。管理员 Telegram 不受邮箱验证影响。缺 Resend 配置仍明确为 `pending_configuration`，不假发成功。

测试邮件与验证邮件共享持久配额，包括失败/未知尝试；换地址或重启不清额度，任一维度超限即 429/Retry-After：

| 维度 | 10 分钟窗口 | 24 小时窗口 |
| --- | --- | --- |
| 账号 | 5 | 20 |
| 来源 IP | 10 | 100 |
| 收件地址（大小写归一） | 3 | 10 |
| 全站 | 50 | 500 |

窗口从首次领取开始，区别于提醒的北京时间自然日额度。注册每 IP 5 次/小时、20 次/24 小时，全站 50 次/小时、200 次/24 小时；配额在哈希前领取，插入短事务再检查注册开关及唯一性。每账号最多 20 条规则，新建最多 10 条/10 分钟、50 条/24 小时；编辑不扣额度，删除不退额度。应用在解析前按实际流计数，正文上限 **16 KiB**（含 chunked），超限 413；本站没有上传接口，小型 JSON 表单无需大正文。日志分页 page 为 1～100000、page_size 为 1～100，越界 422。

登录和改密各有每 IP 30 次/分钟、全站 120 次/分钟预算；同操作、来源 IP、账号组合 15 分钟最多 5 次失败/在途尝试，失败等待依次 0.25、0.5、1、2、4 秒。陌生来源失败不会锁死整个账号。哈希和校验共用 2 个并发槽，忙时 429；哈希及等待不持 SQLite 写锁。安全事件记录账号/IP 摘要与受控说明，不存密码、验证码或令牌。反向代理只应信任受控代理地址，并剥离客户端伪造的转发头。

第八阶段起注册接口对已有/新用户名统一返回“注册请求已处理”，无账号编号、角色、会话或重定向差异，并统一计算密码哈希。注册页不自动尝试登录；用户自行到登录页登录。账号名已占用时原账号密码不变，无法登录应换用户名或联系管理员；管理员仍可在用户管理查看账号。请求预算和注册开关继续有效。

Origin 与 sec-fetch-site 同时缺失时，仍保留现有 JSON 客户端兼容行为；Cookie 为 SameSite=Strict，现代浏览器提供 Fetch Metadata。旧浏览器/WebView 的纵深不足为已评估接受风险，未声称有完整 CSRF token 防护。跨站 Origin 或明确 cross-site 仍拒绝。

## 固定报价口径与数据原则

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

除注册/登录外，下表均需登录；抓取与来源状态仅管理员可用。提醒、日志、通道状态均只返回当前账号的数据。

| 接口 | 用途 |
| --- | --- |
| `GET /api/health` | 返回 `{"status":"ok"}` |
| `GET /api/commodities` | 11 个品种及来源、口径、启用状态 |
| `GET /api/prices/latest` | 全品种目录、可空 `price_point`、`data_status`、`stale`、`change_amount`、`change_percent`、`previous_quote_date` |
| `GET /api/prices/history?code=alu_a00&days=30` | 包含今天的最近 30 个自然日，按报价日期升序 |
| `POST /api/fetch/run` | 同步执行一轮，逐源返回成功/跳过/失败；并发抓取返回 409 |
| `GET /api/sources/status` | 来源状态、原因、最近尝试/成功时间、卓创当日额度及待接入品种 |
| `GET /api/alerts/status` | 当前账号的邮件 / Telegram 通道状态，不返回凭据 |
| `GET /api/alerts/rules` | 规则列表、通道状态、当前报价检查原因 |
| `POST /api/alerts/rules` | 创建规则 |
| `PUT /api/alerts/rules/{id}` | 更新规则（含启用 / 暂停） |
| `DELETE /api/alerts/rules/{id}` | 删除规则，保留历史推送日志 |
| `POST /api/alerts/check` | 立即检查启用规则，可能发送真实 Telegram |
| `POST /api/alerts/test` | 发送一条通道测试消息并记录结果 |
| `GET /api/alerts/logs?page=1&page_size=30` | 按时间倒序的发送日志（每页 1～100 条） |
| `POST /api/auth/register`、`POST /api/auth/login` | 公开账号入口；注册永远为普通角色 |
| `GET /api/auth/me`、`POST /api/auth/logout`、`POST /api/auth/password` | 当前账号、退出、修改密码 |
| `GET /api/me/push-settings`、`PUT /api/me/push-settings` | 当前账号收件邮箱与通道状态 |
| `GET /api/settings/telegram` | 管理员查看 Telegram 配置状态 |
| `GET /api/admin/users`、`PUT /api/admin/users/{id}`、`DELETE /api/admin/users/{id}` | 管理员列出、禁用/启用、删除普通账号 |
| `PUT /api/admin/registration` | 管理员开关注册，JSON 为 `{"enabled":true}` |

未知 `code` 返回 404，`days` 须为 1～3650，非法参数返回 422。没有数据时返回 `null` 或空历史列表。`stale=true` 表示无数据或报价日期早于最近工作日：周一至周五取当天，周末取最近周五；不猜测法定节假日，不伪造新报价。来源抓取失败与已有价格新旧程度分别呈现，抓取失败不会删除历史记录。

涨跌只比较相同品种 / 来源 / 口径 / 单位的最近两个报价日（跨周末仍取上一真实报价日）。涨跌额 = 最新价 − 上一报价日价格；涨跌幅 = 涨跌额 ÷ 上一报价日价格 × 100%，四舍五入保留两位小数。缺少对比报价时返回 `null`，页面显示“—”；持平才显示 0。详情图只绘制区间内已有报价，单点也会渲染，不补造历史。表格有真实旧报价时整行标黄，无报价时明确显示待接入 / 待配置。

页面“刷新数据”只重新读取 API / SQLite，不触发上游采集。首页按钮位于“现货报价”工具栏搜索框旁；详情保留报价日期、抓取时间和来源原文链接，请求失败时显示错误，可重试。

## 调度、提醒与通道

APScheduler 在**每天08:00、08:30、09:00、15:30，Asia/Shanghai** 触发，服务启动不自动抓取。仅服务进程运行时调度有效，不补抓停机期间错过的行情；跨源隔离失败，一个源出错后仍继续处理其他源。进程内抓取锁防止手动和定时重叠，卓创额外使用数据库日额度约束。请使用单 worker，不要同时启动多个服务进程，以免免费源重复请求。

首次启动自动建表并同步 11 个品种。数据库为 `data/procurement.sqlite3`，包含 `commodities`、`price_points`，以及运行状态表 `source_states`、额度表 `source_runs`。第三阶段自动增建 `alert_rules` 和 `push_logs`，不改动已有价格和额度。不要删除额度表来重复触发卓创。

仍使用 `python start.py` 启动并登录；打开 <http://127.0.0.1:8000/#/alerts> 管理自己的规则，<http://127.0.0.1:8000/#/push-logs> 查看自己的日志和发送测试。管理员通道为 Telegram，普通账号改走邮箱；个人邮箱在 `/#/push-settings` 配置。手机宽度也可通过导航进入。

在根目录 `.env` 填写下列键，示例模板为 `.env.example`；不要提交真实值：

```dotenv
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
```

向 Telegram 的 `@BotFather` 创建 Bot 获取 token，先向自己的 Bot 发送 `/start`，再填写目标会话的 chat_id。配置只从根目录 `.env` 读取，修改后下次检查立即生效，无需重启；不从进程的同名变量读取，也不在页面、接口、日志中显示凭据。缺少任一项时显示 `pending_configuration` / **待配置**；测试按钮会记录待配置日志，不发网络请求，不占规则日额度。已配置只表示字段齐全，发送结果仍需查看日志。

规则可为全部 11 个品种预设，提交 JSON 为 `{"code":"alu_a00","kind":"daily_change","threshold":1,"enabled":true}`。`kind` 支持 `daily_change`（日涨跌幅绝对值 ≥ X%，包含涨和跌）、`price_gte`（价格 ≥ 阈值）、`price_lte`（价格 ≤ 阈值）；阈值必须大于 0，最多四位小数。价格阈值单位随品种展示。规则保存来源、报价口径和单位快照，配置变化后先跳过，需编辑保存重新确认。

日涨跌幅比较同品种 / 来源 / 口径 / 单位的前一个真实报价日，跨周末仍取上一报价日；以未四舍五入值比较绝对值，等于阈值也触发。没有上一报价日时只跳过涨跌规则，到价规则仍可判断。仅北京时间当天的报价参与发送；暂无报价、旧报价、未来报价均不发提醒。页面规则列表说明当前跳过原因，暂停规则不参与检查。

行情抓取完成后独立检查一次，另每 5 分钟检查一次；页面“立即检查规则”也可检查现有数据库，不请求上游。服务必须保持运行，不补发旧日提醒。推送通道异常不改变行情入库结果。

同用户同规则同品种同通道在**北京时间自然日最多尝试一次**，发送前在短事务中重读条件与报价，跨午夜停止旧轮；网络请求在事务外。删除规则后日志保留，新增规则使用新编号且受创建额度限制。测试推送不占规则额度；邮件须已验证并受第七阶段共享配额限制，管理员 Telegram 测试行为保持原样。

通过 [Telegram Bot API sendMessage](https://core.telegram.org/bots/api#sendmessage) 发送纯文本，超时 15 秒、不跟随跳转、不自动重试。收到 HTTP 200、`ok=true` 和有效 `message_id` 才标记 `sent` / 发送成功；此状态表示 Telegram 已接受，实际收信应在目标会话核对。`failed` 表示明确未确认成功，`unknown` 表示送达未知；`sending` 若在进程中断后残留，也按送达未知处理，不再次发送。日志保留时间、规则编号、内容、结果和 Telegram 消息编号，不保存 token 或 chat_id。需要代理时，按前述方法在**启动服务前**设置 HTTPS_PROXY 和 NO_PROXY，再重启服务。

## 本地旧库恢复补充

第五阶段升级备份与卷恢复见“持久化、备份、迁移”。本地需恢复旧结构时先停止全部服务、另存现库与 WAL/SHM，再切换对应旧代码，使用备份恢复，不能先启动新代码重迁移：

```powershell
.\.venv\Scripts\python.exe -c "import sqlite3; s=sqlite3.connect('data/backup/实际备份.sqlite3'); d=sqlite3.connect('data/procurement.sqlite3'); s.backup(d); d.close(); s.close()"
```

第四阶段目标代码为 2150d0b，使用 pre-stage5 备份；第五阶段目标使用 pre-stage7。恢复会丢弃备份之后的更改，须先确认目标与备份。第八阶段不新增 schema。

## 自测与历史验收工具

日常回归优先使用隔离测试和 smoke，临时配置使用独立目录，不读取正式 .env，不发送真实推送。为输出目录选择忽略提交的路径：

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests -q -p no:cacheprovider --basetemp=.tools/new-test-run
$env:PROCUREMENT_SMOKE_OUTPUT='.tools/smoke-output'
.\.venv\Scripts\python.exe backend/scripts/smoke_stage7.py
node frontend/node_modules/vitest/vitest.mjs run --config backend/scripts/audit_frontend.config.mjs
# frontend 目录内：npm test、npm run build、npm audit
```

smoke 包含独立服务的 52 项真实 HTTP 检查和已有真实缓存的 8 条隔离回放；首次缺缓存须先人工抓取公开历史，不能编造数据。旧阶段变更史见各 STAGE*_REPORT.md 和 STAGE4_PROGRESS.md。

以下为从 README 移入的历史验收步骤。需要正式账号的脚本只能由操作者手动运行；`--fetch`、`--download-history` 会请求上游，`smoke_alerts.py` 会向已配置通道真实发送测试，不能用于本阶段无外部副作用的自动验收。历史阶段号只标明证据用途，不覆盖现有安全策略。

保持 uvicorn 在一个终端运行，在另一个终端执行：

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests -q
.\.venv\Scripts\python.exe backend/scripts/smoke.py --fetch
```

Linux 对应使用 `.venv/bin/python`。测试使用临时 SQLite 和隔离响应，不访问真实卓创，也不会把测试价格写进项目数据库。`smoke.py --fetch` 才执行真实手动抓取，检查健康、11 品种目录、A00 最新价/历史/元数据、来源状态和参数错误；结果保存在忽略提交的 `data/selftest.json`。省略 `--fetch` 则只读验收，不重复请求上游。

也可逐个检查：

```powershell
$credential = Get-Credential -Message '采购行情管理员账号（密码不回显）'
$loginBody = @{ username=$credential.UserName; password=$credential.GetNetworkCredential().Password } | ConvertTo-Json
Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/auth/login -ContentType 'application/json' -Body $loginBody -SessionVariable procurementSession
$loginBody = $null
Invoke-RestMethod http://127.0.0.1:8000/api/health -WebSession $procurementSession
Invoke-RestMethod http://127.0.0.1:8000/api/prices/latest -WebSession $procurementSession | ConvertTo-Json -Depth 8
Invoke-RestMethod 'http://127.0.0.1:8000/api/prices/history?code=alu_a00&days=30' -WebSession $procurementSession | ConvertTo-Json -Depth 8
Invoke-RestMethod http://127.0.0.1:8000/api/sources/status -WebSession $procurementSession | ConvertTo-Json -Depth 8
# 需要实际抓取才执行，随后会检查提醒并可能推送：
# Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/fetch/run -WebSession $procurementSession
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
