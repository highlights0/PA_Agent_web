from __future__ import annotations

from app.services.analysis_jobs import AnalysisJobStore


def test_analysis_job_store_tracks_live_events_and_final_result():
    store = AnalysisJobStore()
    created = store.create(
        "127.0.0.1",
        {
            "market": "usdm",
            "exchange": "gate",
            "symbol": "BTCUSDT",
            "timeframe": "15m",
            "analysis_bars": 100,
        },
    )
    job_id = created["job_id"]
    assert created["status"] == "queued"

    store.set_running(job_id)
    store.append_event(
        job_id,
        {
            "id": 1,
            "type": "stage_started",
            "stage": "stage1",
            "title": "阶段一开始",
            "data": {},
        },
    )
    running = store.get(job_id, "127.0.0.1")
    assert running["status"] == "running"
    assert running["trace"][0]["type"] == "stage_started"

    store.complete(
        job_id,
        result={"status": "completed", "stage1_diagnosis": {"direction": "bullish"}},
        details={"validation": []},
        trace=[{"id": 1, "type": "stage_started"}],
    )
    completed = store.get(job_id, "127.0.0.1")
    assert completed["status"] == "completed"
    assert completed["result"]["stage1_diagnosis"]["direction"] == "bullish"
    assert completed["details"]["validation"] == []


def test_analysis_job_store_does_not_disclose_job_to_another_ip():
    store = AnalysisJobStore()
    created = store.create("127.0.0.1", {"symbol": "BTCUSDT"})
    assert store.get(created["job_id"], "192.0.2.10") is None


def test_analysis_job_store_failure_is_visible_in_snapshot():
    store = AnalysisJobStore()
    created = store.create("127.0.0.1", {"symbol": "BTCUSDT"})
    store.fail(created["job_id"], {"status_code": 502, "message": "upstream failed"})
    job = store.get(created["job_id"], "127.0.0.1")
    assert job["status"] == "failed"
    assert job["error"]["status_code"] == 502
