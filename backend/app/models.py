from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Commodity(Base):
    __tablename__ = "commodities"
    code: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    source: Mapped[str] = mapped_column(String(30))
    unit: Mapped[str] = mapped_column(String(20))
    quote_basis: Mapped[str] = mapped_column(String(200))
    enabled: Mapped[bool]


class PricePoint(Base):
    __tablename__ = "price_points"
    __table_args__ = (
        UniqueConstraint("code", "source", "quote_basis", "quote_date", name="uq_daily_quote"),
        CheckConstraint("price > 0", name="ck_positive_price"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(ForeignKey("commodities.code"), index=True)
    price: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    unit: Mapped[str] = mapped_column(String(20))
    quote_basis: Mapped[str] = mapped_column(String(200))
    quote_date: Mapped[date] = mapped_column(Date, index=True)
    source: Mapped[str] = mapped_column(String(30))
    source_name: Mapped[str] = mapped_column(String(100))
    source_url: Mapped[str] = mapped_column(String(1000))
    fetched_at: Mapped[datetime] = mapped_column(DateTime)


class SourceState(Base):
    __tablename__ = "source_states"
    source: Mapped[str] = mapped_column(String(30), primary_key=True)
    status: Mapped[str] = mapped_column(String(30), default="never_run")
    message: Mapped[str] = mapped_column(String(300), default="尚未抓取")
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime)


class SourceRun(Base):
    __tablename__ = "source_runs"
    __table_args__ = (UniqueConstraint("source", "run_date", name="uq_source_day"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(30))
    run_date: Mapped[date] = mapped_column(Date)
    started_at: Mapped[datetime] = mapped_column(DateTime)


class AlertRule(Base):
    __tablename__ = "alert_rules"
    # 删除后不复用规则编号，保留日志与日额度的对应关系。
    __table_args__ = (CheckConstraint("threshold > 0"), {"sqlite_autoincrement": True})
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    code: Mapped[str] = mapped_column(ForeignKey("commodities.code"), index=True)
    kind: Mapped[str] = mapped_column(String(30))
    threshold: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    enabled: Mapped[bool] = mapped_column(default=True)
    source: Mapped[str] = mapped_column(String(30))
    quote_basis: Mapped[str] = mapped_column(String(200))
    unit: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime)


class PushLog(Base):
    __tablename__ = "push_logs"
    __table_args__ = (UniqueConstraint("user_id", "rule_id", "code", "send_day", "channel", name="uq_user_rule_day_channel"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    channel: Mapped[str] = mapped_column(String(20), default="telegram")
    recipient: Mapped[str | None] = mapped_column(String(254))
    provider_message_id: Mapped[str | None] = mapped_column(String(200))
    # 不设外键：删除规则后仍保留原规则编号、内容和日额度。
    rule_id: Mapped[int | None]
    code: Mapped[str | None] = mapped_column(String(40))
    send_day: Mapped[date] = mapped_column(Date)
    kind: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(30))
    message: Mapped[str] = mapped_column(String(2000))
    detail: Mapped[str] = mapped_column(String(300))
    telegram_message_id: Mapped[int | None]
    created_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("role IN ('admin', 'user')"), {"sqlite_autoincrement": True})
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(500))
    role: Mapped[str] = mapped_column(String(10), default="user")
    enabled: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)


class UserSession(Base):
    __tablename__ = "user_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)


class PushSetting(Base):
    __tablename__ = "push_settings"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    email: Mapped[str] = mapped_column(String(254), default="")


class SiteSetting(Base):
    __tablename__ = "site_settings"
    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    value: Mapped[str] = mapped_column(String(200))


class LoginThrottle(Base):
    __tablename__ = "login_throttles"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    failures: Mapped[int]
    reset_at: Mapped[datetime] = mapped_column(DateTime)
