import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)
CATEGORIES = (
    "Food",
    "Shopping",
    "Travel",
    "Transport",
    "Utilities",
    "Cash Withdrawal",
    "Entertainment",
    "Other",
)

CLASSIFICATION_SCHEMA = {
    "type": "object",
    "properties": {
        "classifications": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "source_index": {"type": "integer"},
                    "category": {"type": "string", "enum": list(CATEGORIES)},
                },
                "required": ["source_index", "category"],
            },
        }
    },
    "required": ["classifications"],
}

SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "total_spend_by_currency": {
            "type": "object",
            "properties": {
                "INR": {"type": "number"},
                "USD": {"type": "number"},
            },
        },
        "top_merchants": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "merchant": {"type": "string"},
                    "transaction_count": {"type": "integer"},
                    "spend_by_currency": {
                        "type": "object",
                        "properties": {
                            "INR": {"type": "number"},
                            "USD": {"type": "number"},
                        },
                    },
                },
                "required": [
                    "merchant",
                    "transaction_count",
                    "spend_by_currency",
                ],
            },
        },
        "anomaly_count": {"type": "integer"},
        "narrative": {"type": "string"},
        "risk_level": {
            "type": "string",
            "enum": ["low", "medium", "high"],
        },
    },
    "required": [
        "total_spend_by_currency",
        "top_merchants",
        "anomaly_count",
        "narrative",
        "risk_level",
    ],
}


class LLMUnavailableError(RuntimeError):
    pass


@dataclass
class LLMCallResult:
    data: dict[str, Any]
    raw_response: str


class GeminiClient:
    def __init__(
        self,
        settings: Settings | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.settings = settings or get_settings()
        self.sleep = sleep

    def classify_missing(
        self, transactions: list[dict[str, Any]]
    ) -> None:
        missing = [item for item in transactions if item["needs_classification"]]
        batch_size = max(1, self.settings.llm_batch_size)

        for offset in range(0, len(missing), batch_size):
            batch = missing[offset : offset + batch_size]
            prompt_rows = [
                {
                    "source_index": item["source_index"],
                    "merchant": item["merchant"],
                    "notes": item["notes"] or "",
                }
                for item in batch
            ]
            prompt = (
                "Classify every transaction into exactly one allowed category: "
                f"{', '.join(CATEGORIES)}. Return one result for every source_index. "
                f"Transactions: {json.dumps(prompt_rows)}"
            )

            try:
                expected_indices = {
                    int(item["source_index"]) for item in batch
                }
                result = self._with_retry(
                    lambda prompt=prompt, expected_indices=expected_indices: (
                        self._request_classifications(
                            prompt, expected_indices
                        )
                    )
                )
                category_by_index = result.data["category_by_index"]
                for item in batch:
                    item["llm_category"] = category_by_index[item["source_index"]]
                    item["llm_raw_response"] = result.raw_response
            except Exception as exc:
                logger.warning("Classification batch failed: %s", exc)
                for item in batch:
                    item["llm_category"] = heuristic_category(item["merchant"])
                    item["llm_failed"] = True
                    item["llm_raw_response"] = str(exc)

    def generate_summary(
        self, transactions: list[dict[str, Any]], metrics: dict[str, Any]
    ) -> dict[str, Any]:
        compact_transactions = [
            {
                "merchant": item["merchant"],
                "amount": float(item["amount"]),
                "currency": item["currency"],
                "category": item["llm_category"] or item["category"],
                "is_anomaly": item["is_anomaly"],
            }
            for item in transactions
        ]
        prompt = (
            "Produce the requested structured financial summary. Use the supplied "
            "deterministic metrics exactly and write a concise 2-3 sentence narrative. "
            "Choose risk_level low/medium/high based on anomaly frequency "
            "and severity. "
            f"Metrics: {json.dumps(metrics)}. Transactions: "
            f"{json.dumps(compact_transactions)}"
        )

        try:
            result = self._with_retry(
                lambda: self._request_summary(prompt)
            )
            return {
                **metrics,
                "narrative": result.data["narrative"],
                "risk_level": result.data["risk_level"],
                "llm_failed": False,
                "llm_raw_response": result.raw_response,
            }
        except Exception as exc:
            logger.warning("Narrative generation failed: %s", exc)
            return {
                **metrics,
                "narrative": fallback_narrative(metrics),
                "risk_level": fallback_risk_level(
                    metrics["anomaly_count"], len(transactions)
                ),
                "llm_failed": True,
                "llm_raw_response": str(exc),
            }

    def _with_retry(self, operation: Callable[[], LLMCallResult]) -> LLMCallResult:
        attempts = max(1, self.settings.llm_max_attempts)
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                return operation()
            except LLMUnavailableError:
                raise
            except Exception as exc:
                last_error = exc
                if attempt < attempts - 1:
                    self.sleep(2**attempt)
        assert last_error is not None
        raise last_error

    def _request_classifications(
        self, prompt: str, expected_indices: set[int]
    ) -> LLMCallResult:
        result = self._generate_json(prompt, CLASSIFICATION_SCHEMA)
        category_by_index = {
            int(item["source_index"]): item["category"]
            for item in result.data.get("classifications", [])
            if item.get("category") in CATEGORIES
        }
        if set(category_by_index) != expected_indices:
            raise ValueError("LLM omitted or invented classification indices")
        result.data["category_by_index"] = category_by_index
        return result

    def _request_summary(self, prompt: str) -> LLMCallResult:
        result = self._generate_json(prompt, SUMMARY_SCHEMA)
        narrative = str(result.data.get("narrative", "")).strip()
        risk_level = str(result.data.get("risk_level", "")).lower()
        if not narrative or risk_level not in {"low", "medium", "high"}:
            raise ValueError("LLM returned an invalid summary")
        result.data["narrative"] = narrative
        result.data["risk_level"] = risk_level
        return result

    def _generate_json(
        self, prompt: str, response_schema: dict[str, Any]
    ) -> LLMCallResult:
        if not self.settings.gemini_api_key:
            raise LLMUnavailableError(
                "GEMINI_API_KEY is not configured; deterministic fallback used"
            )

        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.settings.gemini_model}:generateContent"
        )
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.1,
                "responseMimeType": "application/json",
                "responseSchema": response_schema,
            },
        }
        with httpx.Client(timeout=self.settings.llm_request_timeout_seconds) as client:
            response = client.post(
                url,
                headers={
                    "x-goog-api-key": self.settings.gemini_api_key,
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            response.raise_for_status()
            body = response.json()

        try:
            raw = body["candidates"][0]["content"]["parts"][0]["text"]
            data = json.loads(raw)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError("Gemini returned an invalid structured response") from exc
        return LLMCallResult(data=data, raw_response=raw)


def heuristic_category(merchant: str) -> str:
    name = merchant.casefold()
    rules = (
        (("swiggy", "zomato", "restaurant", "cafe"), "Food"),
        (("amazon", "flipkart", "myntra"), "Shopping"),
        (("irctc", "airlines", "hotel"), "Travel"),
        (("ola", "uber", "metro", "fuel"), "Transport"),
        (("recharge", "electric", "broadband", "jio"), "Utilities"),
        (("atm",), "Cash Withdrawal"),
        (("netflix", "spotify", "cinema", "bookmyshow"), "Entertainment"),
    )
    for keywords, category in rules:
        if any(keyword in name for keyword in keywords):
            return category
    return "Other"


def fallback_risk_level(anomaly_count: int, total_count: int) -> str:
    ratio = anomaly_count / max(total_count, 1)
    if anomaly_count >= 5 or ratio >= 0.1:
        return "high"
    if anomaly_count > 0:
        return "medium"
    return "low"


def fallback_narrative(metrics: dict[str, Any]) -> str:
    totals = metrics["total_spend_by_currency"]
    total_text = ", ".join(
        f"{currency} {amount:,.2f}" for currency, amount in totals.items()
    ) or "no successful spend"
    merchants = ", ".join(
        item["merchant"] for item in metrics["top_merchants"]
    ) or "none"
    return (
        f"Successful transactions total {total_text}, led by {merchants}. "
        f"The pipeline identified {metrics['anomaly_count']} anomalous "
        "transaction(s) for review."
    )
