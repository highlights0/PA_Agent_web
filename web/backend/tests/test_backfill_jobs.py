from __future__ import annotations

from app.services.backfill_jobs import BackfillJobStore


def test_backfill_job_tracks_progress_and_hides_owner():
    store = BackfillJobStore()
    job = store.create(
        "127.0.0.1",
        [
            {"market": "usdm", "exchange": "gate", "symbol": "BTCUSDT", "timeframe": "15m", "bars": 3000},
            {"market": "usdm", "exchange": "gate", "symbol": "NEARUSDT", "timeframe": "1h", "bars": 3000},
        ],
    )
    job_id = job["job_id"]
    assert job["status"] == "queued"
    assert job["total"] == 2
    assert "owner" not in job

    store.set_running(job_id)
    store.set_current(job_id, {"symbol": "BTCUSDT", "timeframe": "15m"}, 1)
    store.complete_item(job_id, {"status": "completed", "symbol": "BTCUSDT"})
    snapshot = store.get(job_id, "127.0.0.1")
    assert snapshot is not None
    assert snapshot["status"] == "running"
    assert snapshot["completed"] == 1
    assert snapshot["current"]["symbol"] == "BTCUSDT"
    assert store.get(job_id, "192.0.2.1") is None

    store.complete_item(job_id, {"status": "failed", "symbol": "NEARUSDT", "error": "upstream"})
    store.finish(job_id)
    final = store.get(job_id, "127.0.0.1")
    assert final is not None
    assert final["status"] == "completed"
    assert final["completed"] == 2
    assert len(final["results"]) == 2
