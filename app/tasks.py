import logging
from pathlib import Path

from celery import Task
from sqlalchemy import delete, select

from app.celery_app import celery_app
from app.database import SessionLocal
from app.models import Job, JobSummary, Transaction, utc_now
from app.services.llm import GeminiClient
from app.services.pipeline import (
    calculate_metrics,
    clean_transactions,
    detect_anomalies,
    read_csv,
)

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    name="app.tasks.process_job",
    acks_late=True,
    soft_time_limit=540,
    time_limit=600,
)
def process_job(self: Task, job_id: str) -> dict[str, str]:
    input_path: Path | None = None
    try:
        with SessionLocal() as db:
            job = db.get(Job, job_id)
            if job is None:
                logger.error("Job %s no longer exists", job_id)
                return {"job_id": job_id, "status": "missing"}
            job.status = "processing"
            job.error_message = None
            db.commit()
            input_path = Path(job.input_path)

        rows = read_csv(input_path)
        transactions = clean_transactions(rows)
        detect_anomalies(transactions)

        llm = GeminiClient()
        llm.classify_missing(transactions)
        metrics = calculate_metrics(transactions)
        summary_data = llm.generate_summary(transactions, metrics)

        with SessionLocal() as db:
            job = db.get(Job, job_id)
            if job is None:
                return {"job_id": job_id, "status": "missing"}

            db.execute(delete(Transaction).where(Transaction.job_id == job_id))
            db.execute(delete(JobSummary).where(JobSummary.job_id == job_id))

            for item in transactions:
                db.add(
                    Transaction(
                        job_id=job_id,
                        txn_id=item["txn_id"],
                        date=item["date"],
                        merchant=item["merchant"],
                        amount=item["amount"],
                        currency=item["currency"],
                        status=item["status"],
                        category=item["category"],
                        account_id=item["account_id"],
                        notes=item["notes"],
                        is_anomaly=item["is_anomaly"],
                        anomaly_reason=item["anomaly_reason"],
                        llm_category=item["llm_category"],
                        llm_raw_response=item["llm_raw_response"],
                        llm_failed=item["llm_failed"],
                    )
                )

            db.add(
                JobSummary(
                    job_id=job_id,
                    total_spend_by_currency=summary_data[
                        "total_spend_by_currency"
                    ],
                    top_merchants=summary_data["top_merchants"],
                    category_spend_breakdown=summary_data[
                        "category_spend_breakdown"
                    ],
                    anomaly_count=summary_data["anomaly_count"],
                    narrative=summary_data["narrative"],
                    risk_level=summary_data["risk_level"],
                    llm_failed=summary_data["llm_failed"],
                    llm_raw_response=summary_data["llm_raw_response"],
                )
            )
            job.row_count_raw = len(rows)
            job.row_count_clean = len(transactions)
            job.status = "completed"
            job.completed_at = utc_now()
            db.commit()

        if input_path.exists():
            input_path.unlink()
        return {"job_id": job_id, "status": "completed"}
    except Exception as exc:
        logger.exception("Job %s failed", job_id)
        with SessionLocal() as db:
            job = db.scalar(select(Job).where(Job.id == job_id))
            if job is not None:
                job.status = "failed"
                job.error_message = str(exc)[:2000]
                job.completed_at = utc_now()
                db.commit()
        return {"job_id": job_id, "status": "failed"}
