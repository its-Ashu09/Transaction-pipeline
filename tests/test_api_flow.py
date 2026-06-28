from app.api import jobs as jobs_api
from app.tasks import process_job

CSV = b"""txn_id,date,merchant,amount,currency,status,category,account_id,notes
TXN-1,01-01-2024,Swiggy,100,USD,SUCCESS,,ACC-1,
TXN-2,2024/01/02,Amazon,$100,INR,success,Shopping,ACC-1,
TXN-3,03-01-2024,IRCTC,100,INR,FAILED,Travel,ACC-1,
TXN-4,04-01-2024,Swiggy,500,INR,SUCCESS,Food,ACC-1,SUSPICIOUS
TXN-4,04-01-2024,Swiggy,500,INR,SUCCESS,Food,ACC-1,SUSPICIOUS
"""


def test_upload_process_poll_and_results(client, monkeypatch) -> None:
    monkeypatch.setattr(jobs_api.process_job, "delay", lambda _: None)
    response = client.post(
        "/jobs/upload",
        files={"file": ("transactions.csv", CSV, "text/csv")},
    )
    assert response.status_code == 202
    job_id = response.json()["job_id"]

    pending = client.get(f"/jobs/{job_id}/status")
    assert pending.json()["status"] == "pending"
    assert client.get(f"/jobs/{job_id}/results").status_code == 409

    outcome = process_job.run(job_id)
    assert outcome["status"] == "completed"

    completed = client.get(f"/jobs/{job_id}/status")
    assert completed.status_code == 200
    assert completed.json()["summary"]["row_count_raw"] == 5
    assert completed.json()["summary"]["row_count_clean"] == 4

    results = client.get(f"/jobs/{job_id}/results")
    assert results.status_code == 200
    payload = results.json()
    assert len(payload["cleaned_transactions"]) == 4
    assert len(payload["flagged_anomalies"]) == 2
    assert payload["llm_summary"]["llm_failed"] is True
    assert any(
        transaction["effective_category"] == "Food"
        for transaction in payload["cleaned_transactions"]
    )

    jobs = client.get("/jobs", params={"status": "completed"})
    assert jobs.status_code == 200
    assert len(jobs.json()) == 1


def test_rejects_wrong_headers(client) -> None:
    response = client.post(
        "/jobs/upload",
        files={"file": ("bad.csv", b"a,b\n1,2\n", "text/csv")},
    )
    assert response.status_code == 400
    assert "missing required columns" in response.json()["detail"]
