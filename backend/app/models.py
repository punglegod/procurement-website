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
