from datetime import date
from decimal import Decimal

import pytest

from app.services.pipeline import (
    CSVValidationError,
    calculate_metrics,
    clean_transactions,
    detect_anomalies,
)


def row(**overrides: str) -> dict[str, str]:
    base = {
        "txn_id": "TXN-1",
        "date": "14-02-2024",
        "merchant": "Swiggy",
        "amount": "$100.00",
        "currency": "inr",
        "status": "success",
        "category": "",
        "account_id": "ACC-1",
        "notes": "",
    }
    return {**base, **overrides}


def test_cleaning_normalises_values_and_removes_exact_duplicates() -> None:
    source = row()
    cleaned = clean_transactions([source, source.copy()])

    assert len(cleaned) == 1
    assert cleaned[0]["date"] == date(2024, 2, 14)
    assert cleaned[0]["amount"] == Decimal("100.00")
    assert cleaned[0]["currency"] == "INR"
    assert cleaned[0]["status"] == "SUCCESS"
    assert cleaned[0]["category"] == "Uncategorised"
    assert cleaned[0]["needs_classification"] is True


def test_anomaly_rules_can_record_multiple_reasons() -> None:
    rows = [
        row(txn_id="1", amount="100"),
        row(txn_id="2", amount="100"),
        row(txn_id="3", amount="100"),
        row(txn_id="4", amount="500", currency="USD"),
    ]
    transactions = clean_transactions(rows)
    detect_anomalies(transactions)

    suspicious = transactions[-1]
    assert suspicious["is_anomaly"] is True
    assert "3x account median" in suspicious["anomaly_reason"]
    assert "domestic-only merchant" in suspicious["anomaly_reason"]


def test_metrics_exclude_failed_and_pending_spend() -> None:
    transactions = clean_transactions(
        [
            row(txn_id="1", amount="100", status="SUCCESS", category="Food"),
            row(txn_id="2", amount="900", status="FAILED", category="Food"),
        ]
    )
    detect_anomalies(transactions)
    metrics = calculate_metrics(transactions)

    assert metrics["total_spend_by_currency"] == {"INR": 100.0}


def test_invalid_date_is_rejected_with_row_number() -> None:
    with pytest.raises(CSVValidationError, match="Row 2"):
        clean_transactions([row(date="tomorrow")])
