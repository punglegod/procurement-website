from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Numeric, String, Text, UniqueConstraint
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
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime)
    verification_hash: Mapped[str | None] = mapped_column(String(64))
    verification_salt: Mapped[str | None] = mapped_column(String(64))
    verification_expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    verification_attempts: Mapped[int] = mapped_column(default=0, server_default="0")
    # NULL 使用角色默认值：管理员开、普通账号关，不影响既有邮箱验证。
    daily_digest_enabled: Mapped[bool | None]


class SiteSetting(Base):
    __tablename__ = "site_settings"
    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class LoginThrottle(Base):
    __tablename__ = "login_throttles"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    failures: Mapped[int]
    reset_at: Mapped[datetime] = mapped_column(DateTime)


class RateLimitBucket(Base):
    __tablename__ = "rate_limit_buckets"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    count: Mapped[int] = mapped_column(default=0)
    reset_at: Mapped[datetime] = mapped_column(DateTime, index=True)


class SecurityEvent(Base):
    __tablename__ = "security_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    kind: Mapped[str] = mapped_column(String(64))
    # 删除账号后保留安全记录，不保存口令、验证码、令牌或原始 IP。
    user_id: Mapped[int | None]
    account_hash: Mapped[str | None] = mapped_column(String(64))
    client_hash: Mapped[str | None] = mapped_column(String(64))
    detail: Mapped[str] = mapped_column(String(300), default="")


class FetchBatch(Base):
    __tablename__ = "fetch_batches"
    __table_args__ = (UniqueConstraint("run_day", "slot", name="uq_fetch_batch_slot"), {"sqlite_autoincrement": True})
    id: Mapped[int] = mapped_column(primary_key=True)
    run_day: Mapped[date] = mapped_column(Date, index=True)
    slot: Mapped[str | None] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(30))
    started_at: Mapped[datetime] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    summary: Mapped[str] = mapped_column(Text, default="")


class DailyDigest(Base):
    __tablename__ = "daily_digests"
    send_day: Mapped[date] = mapped_column(Date, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    title: Mapped[str] = mapped_column(String(100))
    message: Mapped[str] = mapped_column(Text)
    html: Mapped[str] = mapped_column(Text)


class NotificationClaim(Base):
    __tablename__ = "notification_claims"
    key: Mapped[str] = mapped_column(String(180), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    log_id: Mapped[int]


class QuoteEntry(Base):
    __tablename__ = "quote_entries"
    __table_args__ = (UniqueConstraint("code", "product_name", "specification", "market", "price_type", name="uq_quote_identity"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(ForeignKey("commodities.code"), index=True)
    product_name: Mapped[str] = mapped_column(String(200))
    specification: Mapped[str] = mapped_column(String(300), default="")
    market: Mapped[str] = mapped_column(String(200), default="")
    price_type: Mapped[str] = mapped_column(String(100))
    source: Mapped[str] = mapped_column(String(40), index=True)
    source_name: Mapped[str] = mapped_column(String(100))
    missing: Mapped[bool] = mapped_column(default=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime)
    last_quote_date: Mapped[date | None] = mapped_column(Date)
    last_price_text: Mapped[str] = mapped_column(String(100), default="")
    last_unit: Mapped[str] = mapped_column(String(30), default="元/吨")
    last_change_text: Mapped[str] = mapped_column(String(100), default="")


class QuoteObservation(Base):
    __tablename__ = "quote_observations"
    __table_args__ = (UniqueConstraint("entry_id", "quote_date", name="uq_quote_entry_day"),
                      CheckConstraint("price > 0"), CheckConstraint("low IS NULL OR (low > 0 AND high >= low)"))
    id: Mapped[int] = mapped_column(primary_key=True)
    entry_id: Mapped[int] = mapped_column(ForeignKey("quote_entries.id"), index=True)
    price: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    low: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    high: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    price_text: Mapped[str] = mapped_column(String(100))
    change_text: Mapped[str] = mapped_column(String(100), default="")
    quote_date: Mapped[date] = mapped_column(Date, index=True)
    unit: Mapped[str] = mapped_column(String(30))
    source_url: Mapped[str] = mapped_column(String(1000))
    fetched_at: Mapped[datetime] = mapped_column(DateTime)


class UserQuoteSelection(Base):
    __tablename__ = "user_quote_selections"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    code: Mapped[str] = mapped_column(ForeignKey("commodities.code"), primary_key=True)
    entry_id: Mapped[int] = mapped_column(ForeignKey("quote_entries.id"))
    selected_at: Mapped[datetime] = mapped_column(DateTime)


class QuoteImport(Base):
    __tablename__ = "quote_imports"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), index=True)
    source: Mapped[str] = mapped_column(String(40))
    count: Mapped[int]
    status: Mapped[str] = mapped_column(String(30))
    message: Mapped[str] = mapped_column(String(300))
    fetched_at: Mapped[datetime] = mapped_column(DateTime)


class UserDailyDigest(Base):
    __tablename__ = "user_daily_digests"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    send_day: Mapped[date] = mapped_column(Date, primary_key=True)
    title: Mapped[str] = mapped_column(String(100))
    message: Mapped[str] = mapped_column(Text)
    html: Mapped[str] = mapped_column(Text)
