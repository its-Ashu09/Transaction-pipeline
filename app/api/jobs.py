import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import Job, Transaction
from app.schemas import (
    ErrorResponse,
    HighLevelSummary,
    JobListItem,
    JobResultsResponse,
    JobStatus,
    JobStatusResponse,
    NarrativeSummary,
    TransactionResult,
    UploadResponse,
)
from app.services.pipeline import CSVValidationError, inspect_csv
from app.tasks import process_job

router = APIRouter(prefix="/jobs", tags=["jobs"])
settings = get_settings()


@router.post(
    "/upload",
    response_model=UploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={400: {"model": ErrorResponse}, 413: {"model": ErrorResponse}},
)
async def upload_job(
    file: Annotated[UploadFile, File()],
    db: Annotated[Session, Depends(get_db)],
) -> UploadResponse:
    filename = Path(file.filename or "").name
    if not filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Only .csv files are accepted")

    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    destination = settings.upload_dir / f"{uuid.uuid4()}.csv"
    bytes_written = 0

    try:
        with destination.open("wb") as output:
            while chunk := await file.read(1024 * 1024):
                bytes_written += len(chunk)
                if bytes_written > settings.max_upload_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=(
                            "File exceeds the "
                            f"{settings.max_upload_bytes // (1024 * 1024)} MB limit"
                        ),
                    )
                output.write(chunk)
        row_count = inspect_csv(destination)
    except CSVValidationError as exc:
        destination.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    finally:
        await file.close()

    job = Job(
        filename=filename,
        input_path=str(destination),
        status=JobStatus.pending.value,
        row_count_raw=row_count,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    try:
        process_job.delay(job.id)
    except Exception as exc:
        job.status = JobStatus.failed.value
        job.error_message = f"Could not enqueue job: {exc}"
        db.commit()
        raise HTTPException(
            status_code=503,
            detail=f"Job {job.id} was created but could not be enqueued",
        ) from exc

    return UploadResponse(job_id=job.id, status=JobStatus.pending)


@router.get("", response_model=list[JobListItem])
def list_jobs(
    db: Annotated[Session, Depends(get_db)],
    job_status: Annotated[JobStatus | None, Query(alias="status")] = None,
) -> list[Job]:
    statement = select(Job).order_by(Job.created_at.desc())
    if job_status is not None:
        statement = statement.where(Job.status == job_status.value)
    return list(db.scalars(statement))


@router.get(
    "/{job_id}/status",
    response_model=JobStatusResponse,
    responses={404: {"model": ErrorResponse}},
)
def get_job_status(
    job_id: str, db: Annotated[Session, Depends(get_db)]
) -> JobStatusResponse:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    summary = None
    if job.status == JobStatus.completed.value and job.summary is not None:
        summary = HighLevelSummary(
            row_count_raw=job.row_count_raw,
            row_count_clean=job.row_count_clean,
            total_spend_by_currency=job.summary.total_spend_by_currency,
            anomaly_count=job.summary.anomaly_count,
            risk_level=job.summary.risk_level,
            llm_failed=job.summary.llm_failed,
        )

    return JobStatusResponse(
        job_id=job.id,
        status=JobStatus(job.status),
        summary=summary,
        error_message=job.error_message if job.status == "failed" else None,
    )


@router.get(
    "/{job_id}/results",
    response_model=JobResultsResponse,
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
def get_job_results(
    job_id: str, db: Annotated[Session, Depends(get_db)]
) -> JobResultsResponse:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != JobStatus.completed.value or job.summary is None:
        raise HTTPException(
            status_code=409,
            detail=f"Results are not available while job status is '{job.status}'",
        )

    transactions = list(
        db.scalars(
            select(Transaction)
            .where(Transaction.job_id == job_id)
            .order_by(Transaction.date, Transaction.id)
        )
    )
    serialized = [_serialize_transaction(item) for item in transactions]

    return JobResultsResponse(
        job_id=job.id,
        status=JobStatus.completed,
        cleaned_transactions=serialized,
        flagged_anomalies=[item for item in serialized if item.is_anomaly],
        category_spend_breakdown=job.summary.category_spend_breakdown,
        llm_summary=NarrativeSummary(
            total_spend_by_currency=job.summary.total_spend_by_currency,
            top_merchants=job.summary.top_merchants,
            anomaly_count=job.summary.anomaly_count,
            narrative=job.summary.narrative,
            risk_level=job.summary.risk_level,
            llm_failed=job.summary.llm_failed,
        ),
    )


def _serialize_transaction(item: Transaction) -> TransactionResult:
    return TransactionResult(
        id=item.id,
        txn_id=item.txn_id,
        date=item.date,
        merchant=item.merchant,
        amount=item.amount,
        currency=item.currency,
        status=item.status,
        category=item.category,
        effective_category=item.llm_category or item.category,
        account_id=item.account_id,
        notes=item.notes,
        is_anomaly=item.is_anomaly,
        anomaly_reason=item.anomaly_reason,
        llm_category=item.llm_category,
        llm_failed=item.llm_failed,
    )
