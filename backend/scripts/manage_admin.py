"""停机后的本机管理员维护；凭据只读根 .env，不从命令行或进程变量接收。"""
import argparse
from datetime import datetime
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from sqlalchemy import delete, select
from sqlalchemy.orm import sessionmaker

from backend.app import auth, config
from backend.app.db import make_engine
from backend.app.models import LoginThrottle, SecurityEvent, User, UserSession


def maintain(db_engine, operation, username, source_ip=None):
    factory = sessionmaker(db_engine, expire_on_commit=False)
    settings = config.read_account_settings()
    replacement = None
    if operation == "replace":
        auth.validate_username(settings["ADMIN_USERNAME"])
        if not 8 <= len(settings["ADMIN_PASSWORD"]) <= 128:
            raise ValueError("根 .env 的 ADMIN_PASSWORD 必须为 8～128 位")
        replacement = auth.hash_password(settings["ADMIN_PASSWORD"])
    with factory() as session:
        target = session.scalar(select(User).where(User.username == username, User.role == "admin"))
        if target is None:
            raise ValueError("指定账号不是现有管理员")
    path = Path(db_engine.url.database)
    backup = path.parent / "backup" / f"{path.stem}-admin-maintenance-{datetime.now():%Y%m%d-%H%M%S-%f}.sqlite3"
    backup.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as source, sqlite3.connect(backup) as destination:
        source.backup(destination)
    with factory() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        target = session.scalar(select(User).where(User.username == username, User.role == "admin"))
        if target is None:
            raise ValueError("管理员状态已变更，维护已取消")
        if operation == "replace":
            other = session.scalar(select(User.id).where(User.username == settings["ADMIN_USERNAME"], User.id != target.id))
            if other is not None:
                raise ValueError("新的 ADMIN_USERNAME 已被其他账号使用，未作变更")
            target.username = settings["ADMIN_USERNAME"]
            target.password_hash = replacement
            session.execute(delete(UserSession).where(UserSession.user_id == target.id))
            detail = "本机管理员更名或密码轮换，撤销全部旧会话，账号编号和资源归属不变"
        elif operation == "unlock" and source_ip:
            keys = [auth.digest("pair:" + purpose + ":" + auth.digest("ip:" + source_ip) + ":" + username)
                    for purpose in ("login", "password")]
            session.execute(delete(LoginThrottle).where(LoginThrottle.key.in_(keys)))
            detail = "本机仅重置指定管理员和来源的登录/改密失败锁，保留全站及来源请求配额"
        else:
            raise ValueError("维护操作或来源地址无效")
        session.add(SecurityEvent(created_at=auth.utc_now(), kind="admin_local_" + operation,
                                  user_id=target.id, account_hash=auth.digest(username),
                                  client_hash=auth.digest("local-maintenance"), detail=detail))
        session.commit()
    return {"operation": operation, "backup": str(backup), "message": detail}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("replace", "unlock"))
    parser.add_argument("--username", required=True, help="当前管理员用户名")
    parser.add_argument("--ip", help="仅 unlock：要解锁的实际客户端来源地址")
    parser.add_argument("--apply", action="store_true", help="确认服务已停止，执行备份及维护")
    args = parser.parse_args()
    if not args.apply:
        parser.error("先停止服务；确认后加 --apply。replace 的新账号和密码只取根 .env。")
    if args.operation == "unlock" and not args.ip:
        parser.error("unlock 必须指定 --ip；不会清空全站限流")
    engine = make_engine(f"sqlite:///{(config.DATA_DIR / 'procurement.sqlite3').as_posix()}")
    try:
        result = maintain(engine, args.operation, args.username, args.ip)
        print(result["message"])
        print("维护前备份：", result["backup"])
    except (ValueError, sqlite3.Error) as error:
        raise SystemExit(str(error)) from None
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
