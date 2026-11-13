from datetime import datetime
from pathlib import Path
import sqlite3
import logging

from sqlalchemy import create_engine, event, inspect, select
from sqlalchemy.orm import sessionmaker

from .config import COMMODITIES, DATA_DIR, SOURCES
from .models import Base, Commodity, SourceState, User, SiteSetting, AlertRule, PushLog

logger = logging.getLogger(__name__)


def make_engine(url: str):
    engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 15})

    @event.listens_for(engine, "connect")
    def set_pragmas(connection, _):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    return engine


DATA_DIR.mkdir(parents=True, exist_ok=True)
engine = make_engine(f"sqlite:///{(DATA_DIR / 'procurement.sqlite3').as_posix()}")
SessionLocal = sessionmaker(engine, expire_on_commit=False)


def initialize_database(db_engine=engine):
    from .auth import hash_password, validate_username
    from .config import read_account_settings

    tables = inspect(db_engine).get_table_names()
    legacy = "alert_rules" in tables and "user_id" not in {c["name"] for c in inspect(db_engine).get_columns("alert_rules")}
    first_upgrade = bool(tables) and "users" not in tables
    email_columns = {c["name"] for c in inspect(db_engine).get_columns("push_settings")} if "push_settings" in tables else set()
    stage7_upgrade = "users" in tables and ("email_verified_at" not in email_columns or "rate_limit_buckets" not in tables)
    stage9_upgrade = "users" in tables and ("daily_digest_enabled" not in email_columns or not {"fetch_batches", "daily_digests", "notification_claims"}.issubset(tables))
    backup = None
    # SQLite backup API 包含 WAL 中已提交的数据；备份完成后才允许任何结构变更。
    if first_upgrade or legacy or stage7_upgrade or stage9_upgrade:
        path = Path(db_engine.url.database)
        folder = path.parent / "backup"
        folder.mkdir(parents=True, exist_ok=True)
        previous_stage = "stage5" if first_upgrade or legacy else "stage7" if stage7_upgrade else "stage9"
        backup = folder / f"{path.stem}-pre-{previous_stage}-{datetime.now():%Y%m%d-%H%M%S-%f}.sqlite3"
        with sqlite3.connect(path) as source, sqlite3.connect(backup) as target:
            source.backup(target)

    settings = read_account_settings()
    with db_engine.begin() as connection:
        # 单个事务覆盖建表、播种、旧表重建及版本记录；异常时整体回滚。
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        Base.metadata.create_all(connection)
        if email_columns:
            # 旧邮箱不能视为已验证；仅增加可空列，旧账号/日志/行情及额度保持不变。
            for name, declaration in {
                "email_verified_at": "DATETIME", "verification_hash": "VARCHAR(64)",
                "verification_salt": "VARCHAR(64)", "verification_expires_at": "DATETIME",
                "verification_attempts": "INTEGER NOT NULL DEFAULT 0",
                "daily_digest_enabled": "BOOLEAN",
            }.items():
                if name not in email_columns:
                    connection.exec_driver_sql(f"ALTER TABLE push_settings ADD COLUMN {name} {declaration}")
        with sessionmaker(bind=connection, expire_on_commit=False)() as session:
            admin = session.scalar(select(User).where(User.role == "admin").order_by(User.id))
            username, password = settings["ADMIN_USERNAME"], settings["ADMIN_PASSWORD"]
            if admin is not None:
                names = list(session.scalars(select(User.username).where(User.role == "admin").order_by(User.id)))
                logger.warning("启动确认：已有管理员，跳过 .env 播种；现有管理员：%s", ", ".join(names))
            elif username and password and session.scalar(select(User).where(User.username == username)) is None:
                validate_username(username)
                if len(password) < 8 or len(password) > 128:
                    raise RuntimeError("ADMIN_PASSWORD 必须为 8～128 位")
                seeded = User(username=username, password_hash=hash_password(password), role="admin", enabled=True, created_at=datetime.now())
                session.add(seeded)
                session.flush()
                admin = seeded
            if admin is None:
                raise RuntimeError("首次启动请在项目根目录 .env 配置 ADMIN_USERNAME / ADMIN_PASSWORD；未进行数据迁移")
            if legacy:
                for table in (AlertRule.__table__, PushLog.__table__):
                    sequence = connection.exec_driver_sql("SELECT seq FROM sqlite_sequence WHERE name=?", (table.name,)).scalar() if table.name == "alert_rules" else None
                    rows = [dict(row) for row in connection.exec_driver_sql(f"SELECT * FROM {table.name}").mappings()]
                    # 旧索引随旧表删除，再由元数据重建。
                    connection.exec_driver_sql(f"DROP TABLE {table.name}")
                    table.create(connection)
                    for row in rows:
                        row["user_id"] = admin.id
                        if table.name == "push_logs":
                            row.update(channel="telegram", recipient=None, provider_message_id=None)
                        columns = ",".join(row)
                        placeholders = ",".join("?" for _ in row)
                        connection.exec_driver_sql(f"INSERT INTO {table.name} ({columns}) VALUES ({placeholders})", tuple(row.values()))
                    if sequence is not None:
                        connection.exec_driver_sql("INSERT INTO sqlite_sequence(name,seq) SELECT ?,? WHERE NOT EXISTS (SELECT 1 FROM sqlite_sequence WHERE name=?)", (table.name, sequence, table.name))
                        connection.exec_driver_sql("UPDATE sqlite_sequence SET seq=MAX(seq,?) WHERE name=?", (sequence, table.name))
            if session.get(SiteSetting, "registration_enabled") is None:
                session.add(SiteSetting(key="registration_enabled", value="true"))
            if backup:
                session.merge(SiteSetting(key="stage5_backup" if first_upgrade or legacy else "stage7_backup" if stage7_upgrade else "stage9_backup", value=str(backup)))
            session.merge(SiteSetting(key="schema_version", value="9"))
            for item in COMMODITIES:
                session.merge(Commodity(**vars(item)))
            for source in SOURCES:
                if session.get(SourceState, source) is None:
                    session.add(SourceState(source=source))
            session.commit()
    return str(backup) if backup else None
