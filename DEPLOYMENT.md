# Linux 云服务器运行说明（第七阶段更新，未执行部署）

目标为一台 3 核 / 20G Linux 主机。只运行一个 FastAPI 容器、一个 worker；Vue 构建产物由后端静态托管。SQLite 位于持久卷，APScheduler 位于同一进程。容器限额 2 CPU / 2G 内存，为宿主系统和构建留余量。不要增加副本或 worker，也不要同时运行 `start.py`。

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

用编辑器填入首次播种所需 `ADMIN_USERNAME` / `ADMIN_PASSWORD`（8～128 位），以及 Telegram token/chat_id。邮件需要 `RESEND_API_KEY`、`EMAIL_FROM`（默认 sourcing@bbbhl.com）；用户在个人设置填收件邮箱。`SCI99_USER`、`SCI99_PASS`、`SCI99_COOKIE` 留空；模板中的卓创 URL 不会启用休眠 adapter。服务只从 `/app/.env` 读取，不能使用 compose `env_file` 替代文件挂载。容器使用 UID/GID 10001，需能读取挂载的 `.env`；项目父目录只允许管理员访问，例如 `chmod 700 /opt/procurement-website`。配置只读挂载，不写入镜像；更严格权限可用 ACL 授予 UID 10001 只读。

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

服务不在启动时抓价，北京时间每日 15:30 抓取，每 5 分钟检查所有有效账号的提醒；主机时间须正常同步。首次采集请管理员登录后在“全局设置”点击“立即抓取行情”，会在采集后检查真实提醒。失败不删历史，旧日期仍显示“过期”；健康检查不表示来源成功，管理员应查看数据源状态。所有 API 均需会话，不再支持匿名 curl 写操作。

从自己的电脑建立 SSH 隧道：

```bash
ssh -N -L 8000:127.0.0.1:8000 your-user@your-server
```

浏览器打开 `http://127.0.0.1:8000/` 后登录。注册只创建普通账号，全局设置、抓取、用户管理仅管理员可用。默认 compose 绑定服务器回环，安全组不开放 8000，启动显式 `--no-proxy-headers`，伪造转发头不会改变限流来源。HTTP 本地隧道的 `SESSION_COOKIE_SECURE` 保持 false。本阶段未部署公网入口。

以后使用 HTTPS 域名入口时，在 `.env` 设置 `SESSION_COOKIE_SECURE=true`，反向代理保留正确 Host 并**覆盖**客户端提供的 X-Forwarded-For / X-Forwarded-Proto，后端仅允许该代理连接。通过 compose 的 `command` 覆盖默认 CMD，例如下列模板（先将示例地址替换为容器实际所见的受控代理 IP/CIDR；不能使用 `*`）：

```yaml
command: ["python", "-m", "uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips", "192.0.2.10"]
```

本机 `start.py` 的对应显式选项是 `--trusted-proxy-ips <实际代理IP或CIDR>`，默认不受 `FORWARDED_ALLOW_IPS` 环境变量影响。启用后验证 HTTPS Cookie、同源校验及日志中的来源归属；不要为消除 403 而全局信任转发头。SSH 隧道会将用户汇聚成同一来源 IP，共享该维度预算属于预期行为。

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
docker tag procurement-website:stage7 procurement-website:previous
docker compose build --pull
docker compose up -d
curl -i http://127.0.0.1:8000/api/health
```

同结构代码回滚：`docker tag procurement-website:previous procurement-website:stage7` 后 `docker compose up -d --no-build`。跨结构回滚须停服务并恢复对应升级前备份；第五阶段回第四阶段使用 pre-stage5 备份，第七阶段回第五阶段使用 pre-stage7 备份和对应旧镜像。不要将旧库覆盖到运行中的新服务。

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

已有第五阶段库首次运行本阶段会先创建 `pre-stage7` 一致性备份，事务新增验证/配额/安全事件结构，旧邮箱转为待验证；Telegram 与既有日额度保持。仅当没有任何管理员才播种；启动日志列出现有用户名。更名、密码轮换、特定来源解锁的停机维护步骤见 README；镜像内只携带 `backend/scripts/manage_admin.py` 这一维护脚本。未验证邮箱不发测试/提醒；验证与测试受四维配额约束。应用统一限制 JSON 正文为 16 KiB，反向代理可同步设置限制，勿配置为大于业务需要的上传服务。

本阶段没有执行远端部署或 Windows 权限修改。下列 Docker 实跑限制仍成立：

已对 compose 的 YAML、构建输入路径、单 worker、只读凭据挂载、数据卷、限额、健康检查和日志轮转做静态核对。本机没有 Docker CLI / daemon，因此 **未执行镜像构建、compose 启动、容器健康检查或卷恢复演练**。这些是将来服务器部署时待跑的步骤，不记为本阶段通过；本阶段未连接或部署任何服务器。
