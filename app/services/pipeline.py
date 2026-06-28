import csv
from collections import defaultdict
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from statistics import median
from typing import Any

REQUIRED_COLUMNS = (
    "txn_id",
    "date",
    "merchant",
    "amount",
    "currency",
    "status",
    "category",
    "account_id",
    "notes",
)
ALLOWED_STATUSES = {"SUCCESS", "FAILED", "PENDING"}
ALLOWED_CURRENCIES = {"INR", "USD"}
DOMESTIC_ONLY_MERCHANTS = {"swiggy", "ola", "irctc"}
DATE_FORMATS = ("%d-%m-%Y", "%Y/%m/%d", "%Y-%m-%d")
MONEY_QUANTUM = Decimal("0.01")


class CSVValidationError(ValueError):
    pass


def inspect_csv(path: Path) -> int:
    """Validate CSV shape and return its raw data-row count."""
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                raise CSVValidationError("CSV is missing a header row")
            actual = tuple(name.strip() for name in reader.fieldnames)
            missing = sorted(set(REQUIRED_COLUMNS) - set(actual))
            if missing:
                raise CSVValidationError(
                    f"CSV is missing required columns: {', '.join(missing)}"
                )
            count = sum(
                1
                for row in reader
                if any((value or "").strip() for value in row.values())
            )
    except UnicodeDecodeError as exc:
        raise CSVValidationError("CSV must be UTF-8 encoded") from exc
    except csv.Error as exc:
        raise CSVValidationError(f"Malformed CSV: {exc}") from exc

    if count == 0:
        raise CSVValidationError("CSV contains no transaction rows")
    return count


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return [
            {column: row.get(column, "") or "" for column in REQUIRED_COLUMNS}
            for row in reader
            if any((value or "").strip() for value in row.values())
        ]


def clean_transactions(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    seen: set[tuple[str, ...]] = set()

    for source_index, row in enumerate(rows):
        signature = tuple(row.get(column, "") for column in REQUIRED_COLUMNS)
        if signature in seen:
            continue
        seen.add(signature)

        category = row.get("category", "").strip()
        status = row.get("status", "").strip().upper()
        currency = row.get("currency", "").strip().upper()

        if status not in ALLOWED_STATUSES:
            raise CSVValidationError(
                f"Row {source_index + 2}: unsupported status '{status}'"
            )
        if currency not in ALLOWED_CURRENCIES:
            raise CSVValidationError(
                f"Row {source_index + 2}: unsupported currency '{currency}'"
            )

        cleaned.append(
            {
                "source_index": source_index,
                "txn_id": row.get("txn_id", "").strip() or None,
                "date": _parse_date(row.get("date", ""), source_index),
                "merchant": _required_text(
                    row.get("merchant", ""), "merchant", source_index
                ),
                "amount": _parse_amount(row.get("amount", ""), source_index),
                "currency": currency,
                "status": status,
                "category": category or "Uncategorised",
                "needs_classification": not bool(category),
                "account_id": _required_text(
                    row.get("account_id", ""), "account_id", source_index
                ),
                "notes": row.get("notes", "").strip() or None,
                "is_anomaly": False,
                "anomaly_reason": None,
                "llm_category": None,
                "llm_raw_response": None,
                "llm_failed": False,
            }
        )

    return cleaned


def detect_anomalies(transactions: list[dict[str, Any]]) -> None:
    amounts_by_account: dict[str, list[Decimal]] = defaultdict(list)
    for transaction in transactions:
        amounts_by_account[transaction["account_id"]].append(transaction["amount"])

    medians = {
        account_id: median(amounts)
        for account_id, amounts in amounts_by_account.items()
    }

    for transaction in transactions:
        reasons: list[str] = []
        account_median = medians[transaction["account_id"]]
        if account_median > 0 and transaction["amount"] > account_median * 3:
            reasons.append(
                "Amount exceeds 3x account median "
                f"({transaction['amount']:.2f} > {(account_median * 3):.2f})"
            )

        merchant = transaction["merchant"].strip().casefold()
        if (
            transaction["currency"] == "USD"
            and merchant in DOMESTIC_ONLY_MERCHANTS
        ):
            reasons.append("USD used at a domestic-only merchant")

        transaction["is_anomaly"] = bool(reasons)
        transaction["anomaly_reason"] = "; ".join(reasons) or None


def calculate_metrics(transactions: list[dict[str, Any]]) -> dict[str, Any]:
    """Calculate financial aggregates over successful transactions only."""
    successful = [item for item in transactions if item["status"] == "SUCCESS"]
    totals: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    merchant_stats: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "transaction_count": 0,
            "spend_by_currency": defaultdict(lambda: Decimal("0")),
        }
    )
    category_totals: dict[str, dict[str, Decimal]] = defaultdict(
        lambda: defaultdict(lambda: Decimal("0"))
    )

    for item in successful:
        amount = item["amount"]
        currency = item["currency"]
        effective_category = item["llm_category"] or item["category"]
        totals[currency] += amount
        merchant_stats[item["merchant"]]["transaction_count"] += 1
        merchant_stats[item["merchant"]]["spend_by_currency"][currency] += amount
        category_totals[effective_category][currency] += amount

    top_merchants = sorted(
        (
            {
                "merchant": merchant,
                "transaction_count": stats["transaction_count"],
                "spend_by_currency": {
                    currency: float(amount.quantize(MONEY_QUANTUM))
                    for currency, amount in sorted(
                        stats["spend_by_currency"].items()
                    )
                },
            }
            for merchant, stats in merchant_stats.items()
        ),
        key=lambda item: (-item["transaction_count"], item["merchant"].casefold()),
    )[:3]

    return {
        "total_spend_by_currency": {
            currency: float(amount.quantize(MONEY_QUANTUM))
            for currency, amount in sorted(totals.items())
        },
        "top_merchants": top_merchants,
        "category_spend_breakdown": {
            category: {
                currency: float(amount.quantize(MONEY_QUANTUM))
                for currency, amount in sorted(currency_totals.items())
            }
            for category, currency_totals in sorted(category_totals.items())
        },
        "anomaly_count": sum(bool(item["is_anomaly"]) for item in transactions),
    }


def _parse_date(raw: str, source_index: int):
    value = raw.strip()
    for date_format in DATE_FORMATS:
        try:
            return datetime.strptime(value, date_format).date()
        except ValueError:
            continue
    raise CSVValidationError(
        f"Row {source_index + 2}: unsupported date format '{value}'"
    )


def _parse_amount(raw: str, source_index: int) -> Decimal:
    value = raw.strip().replace("$", "").replace(",", "")
    try:
        amount = Decimal(value).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError) as exc:
        raise CSVValidationError(
            f"Row {source_index + 2}: invalid amount '{raw}'"
        ) from exc
    if amount < 0:
        raise CSVValidationError(f"Row {source_index + 2}: amount cannot be negative")
    return amount


def _required_text(raw: str, field: str, source_index: int) -> str:
    value = raw.strip()
    if not value:
        raise CSVValidationError(f"Row {source_index + 2}: {field} is required")
    return value
