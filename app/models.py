import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_uuid() -> str:
    return str(uuid.uuid4())


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    filename: Mapped[str] = mapped_column(String(255))
    input_path: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    row_count_raw: Mapped[int] = mapped_column(Integer, default=0)
    row_count_clean: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, index=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    transactions: Mapped[list["Transaction"]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )
    summary: Mapped["JobSummary | None"] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
        uselist=False,
    )


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        Index("ix_transactions_job_account", "job_id", "account_id"),
        Index("ix_transactions_job_anomaly", "job_id", "is_anomaly"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), index=True
    )
    txn_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    date: Mapped[date] = mapped_column(Date)
    merchant: Mapped[str] = mapped_column(String(255))
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    currency: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(20))
    category: Mapped[str] = mapped_column(String(100))
    account_id: Mapped[str] = mapped_column(String(100), index=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_anomaly: Mapped[bool] = mapped_column(Boolean, default=False)
    anomaly_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    llm_category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    llm_raw_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    llm_failed: Mapped[bool] = mapped_column(Boolean, default=False)

    job: Mapped[Job] = relationship(back_populates="transactions")


class JobSummary(Base):
    __tablename__ = "job_summaries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), unique=True, index=True
    )
    total_spend_by_currency: Mapped[dict] = mapped_column(JSON)
    top_merchants: Mapped[list] = mapped_column(JSON)
    category_spend_breakdown: Mapped[dict] = mapped_column(JSON)
    anomaly_count: Mapped[int] = mapped_column(Integer)
    narrative: Mapped[str] = mapped_column(Text)
    risk_level: Mapped[str] = mapped_column(String(20))
    llm_failed: Mapped[bool] = mapped_column(Boolean, default=False)
    llm_raw_response: Mapped[str | None] = mapped_column(Text, nullable=True)

    job: Mapped[Job] = relationship(back_populates="summary")
