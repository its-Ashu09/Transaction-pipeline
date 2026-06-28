from datetime import date, datetime
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class JobStatus(str, Enum):
    pending = "pending"
    processing = "processing"
    completed = "completed"
    failed = "failed"


class UploadResponse(BaseModel):
    job_id: str
    status: JobStatus


class JobListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    filename: str
    status: JobStatus
    row_count_raw: int
    row_count_clean: int
    created_at: datetime
    completed_at: datetime | None = None


class HighLevelSummary(BaseModel):
    row_count_raw: int
    row_count_clean: int
    total_spend_by_currency: dict[str, float]
    anomaly_count: int
    risk_level: str
    llm_failed: bool


class JobStatusResponse(BaseModel):
    job_id: str
    status: JobStatus
    summary: HighLevelSummary | None = None
    error_message: str | None = None


class TransactionResult(BaseModel):
    id: str
    txn_id: str | None
    date: date
    merchant: str
    amount: Decimal
    currency: str
    status: str
    category: str
    effective_category: str
    account_id: str
    notes: str | None
    is_anomaly: bool
    anomaly_reason: str | None
    llm_category: str | None
    llm_failed: bool


class NarrativeSummary(BaseModel):
    total_spend_by_currency: dict[str, float]
    top_merchants: list[dict]
    anomaly_count: int
    narrative: str
    risk_level: str
    llm_failed: bool


class JobResultsResponse(BaseModel):
    job_id: str
    status: JobStatus
    cleaned_transactions: list[TransactionResult]
    flagged_anomalies: list[TransactionResult]
    category_spend_breakdown: dict[str, dict[str, float]]
    llm_summary: NarrativeSummary


class ErrorResponse(BaseModel):
    detail: str = Field(examples=["Job not found"])
