# Linux 云服务器运行说明（第四阶段交付，未执行部署）

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

用编辑器填入已验收的 Telegram token/chat_id，`SCI99_USER`、`SCI99_PASS`、`SCI99_COOKIE` 留空；模板中的卓创 URL 不会启用休眠 adapter。服务按项目规则只从 `/app/.env` 文件读配置，不能使用 compose `env_file` 替代文件挂载。容器使用 UID/GID 10001，需能读取挂载的 `.env`；项目父目录应只允许管理员访问（例如 `chmod 700 /opt/procurement-website`），不要放在多人共享目录。配置是只读挂载，不写入镜像。若采用更严格的文件权限，请通过 ACL 授予 UID 10001 只读权限。

构建需能访问 Docker Hub、PyPI、npm registry；运行需访问各公开行情站和 api.telegram.org:443。不要照抄开发机 `127.0.0.1:7890` 代理：容器内回环是容器自身。如服务器需要代理，应通过服务器实际可达的代理配置另行设置 HTTPS_PROXY，不提交代理凭据。

## 构建、启动、访问

```bash
docker compose config --quiet
docker compose build --pull
docker compose up -d
docker compose ps
curl --fail http://127.0.0.1:8000/api/health
docker compose logs --tail=100 web
```

服务默认不在启动时抓价，北京时间每日 15:30 抓取，每 5 分钟检查提醒；主机时间须正常同步。首次采集可在服务器执行 `curl --fail -X POST http://127.0.0.1:8000/api/fetch/run`，这会在采集后检查真实提醒规则。失败不会删除历史，旧日期仍显示“过期”；健康检查只表示服务可用，不表示来源成功，应另查 `/api/sources/status`。

从自己的电脑建立 SSH 隧道：

```bash
ssh -N -L 8000:127.0.0.1:8000 your-user@your-server
```

浏览器打开 `http://127.0.0.1:8000/`。本网站没有登录系统，包含可写规则和推送接口，因此 compose 仅绑定服务器回环；服务器安全组不开放 8000。若以后要域名公网入口，需另行配置访问认证与 HTTPS，本阶段不部署该入口。

## 持久化、备份、迁移

SQLite 及 WAL/SHM 文件在 `procurement_procurement_data` 命名卷，重建容器和普通 `docker compose down` 均保留。**不要执行 `docker compose down -v`**。首次启动空卷自动建表；不自动迁移开发机历史。

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
docker tag procurement-website:stage4 procurement-website:previous
docker compose build --pull
docker compose up -d
curl --fail http://127.0.0.1:8000/api/health
```

回滚代码：`docker tag procurement-website:previous procurement-website:stage4` 后 `docker compose up -d --no-build`。若将来版本涉及数据库变更，还需按该版本迁移说明处理；不要直接把旧库覆盖到运行中的新服务。

手动停止用 `docker compose stop`，查看来源用 `curl --fail http://127.0.0.1:8000/api/sources/status`。不要开启自动高频重抓。免费页有更新延迟、统计日可能早于发布日，不能保证每日都有当日价格；源结构变化会明确失败，绝不填造价格。

## 本次验证边界

已对 compose 的 YAML、构建输入路径、单 worker、只读凭据挂载、数据卷、限额、健康检查和日志轮转做静态核对。本机没有 Docker CLI / daemon，因此 **未执行镜像构建、compose 启动、容器健康检查或卷恢复演练**。这些是将来服务器部署时待跑的步骤，不记为本阶段通过；本阶段未连接或部署任何服务器。
