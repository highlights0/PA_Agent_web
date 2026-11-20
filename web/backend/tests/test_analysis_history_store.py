from __future__ import annotations

from app.services.analysis_history_store import AnalysisHistoryStore


def test_analysis_and_search_history_persist_across_store_restart(tmp_path):
    path = tmp_path / "history.sqlite3"
    store = AnalysisHistoryStore(path)
    request_data = {
        "market": "usdm",
        "exchange": "gate",
        "symbol": "BTCUSDT",
        "timeframe": "15m",
        "analysis_bars": 100,
    }
    result = {
        "analysis_id": "analysis-1",
        "status": "completed",
        "stage1_diagnosis": {
            "cycle_position": "normal_channel",
            "direction": "bullish",
            "gate_result": "proceed",
            "support_levels": ["60000-60100"],
            "resistance_levels": ["62000"],
        },
        "stage2_decision": {
            "decision": {
                "order_type": "限价单",
                "order_direction": "做多",
                "entry_price": 60500,
                "stop_loss_price": 59800,
                "take_profit_price": 62000,
            }
        },
        "usage": {"total_tokens": 1234},
    }
    details = {
        "provider": {"model": "deepseek-chat"},
        "provenance": {"prompt_files": [{"path": "市场诊断框架.txt", "sha256": "abc"}]},
    }
    store.save_analysis(
        analysis_id="analysis-1",
        owner="127.0.0.1",
        request_data=request_data,
        status="completed",
        result=result,
        trace=[{"id": 1, "type": "analysis_started"}],
        details=details,
    )
    store.save_search(
        "127.0.0.1",
        query_text="BTCUSDT",
        market="usdm",
        exchange="gate",
        symbol="BTCUSDT",
        timeframe="15m",
    )

    reopened = AnalysisHistoryStore(path)
    history = reopened.list_analyses("127.0.0.1")
    assert len(history) == 1
    assert history[0]["symbol"] == "BTCUSDT"
    assert history[0]["cycle_position"] == "normal_channel"
    assert reopened.get_analysis("127.0.0.1", "analysis-1")["details"]["provenance"]["prompt_files"][0]["sha256"] == "abc"
    assert reopened.list_searches("127.0.0.1")[0]["symbol"] == "BTCUSDT"


def test_analysis_history_is_scoped_by_owner(tmp_path):
    store = AnalysisHistoryStore(tmp_path / "history.sqlite3")
    store.save_analysis(
        analysis_id="analysis-private",
        owner="127.0.0.1",
        request_data={"market": "spot", "exchange": "coinbase", "symbol": "BTC-USD", "timeframe": "15m"},
        status="completed",
        result={"stage1_diagnosis": {"direction": "bearish"}},
    )
    assert store.get_analysis("192.0.2.10", "analysis-private") is None
    assert store.list_analyses("192.0.2.10") == []


def test_structured_comparison_reports_only_changed_fields(tmp_path):
    store = AnalysisHistoryStore(tmp_path / "history.sqlite3")
    for analysis_id, direction, order_type in [
        ("a", "bullish", "限价单"),
        ("b", "bearish", "不下单"),
    ]:
        store.save_analysis(
            analysis_id=analysis_id,
            owner="local",
            request_data={"market": "usdm", "exchange": "gate", "symbol": "BTCUSDT", "timeframe": "15m"},
            status="completed",
            result={
                "stage1_diagnosis": {"cycle_position": "normal_channel", "direction": direction},
                "stage2_decision": {"decision": {"order_type": order_type}},
            },
        )
    comparison = store.compare_analyses("local", "a", "b")
    assert comparison["differences"]["direction"] == {"left": "bullish", "right": "bearish"}
    assert comparison["differences"]["order_type"] == {"left": "限价单", "right": "不下单"}
    assert "cycle_position" not in comparison["differences"]


def test_segment_samples_are_idempotent_and_keep_labels_separate(tmp_path):
    store = AnalysisHistoryStore(tmp_path / "history.sqlite3")
    sample = {
        "sample_id": "sample-1",
        "market": "usdm",
        "exchange": "gate",
        "symbol": "BTCUSDT",
        "timeframe": "15m",
        "regime": "bullish_trend",
        "decision_time_ms": 1000,
        "segment_start_ms": 0,
        "lookback_bars": 24,
        "horizon_bars": 12,
        "feature_schema_version": "pa-segment-features-v1",
        "label_schema_version": "forward-outcome-v1",
        "features": {"net_return_pct": 2.1},
        "labels": {"forward_direction": "up", "forward_return_pct": 1.2},
    }
    first = store.upsert_segments([sample])
    second = store.upsert_segments([sample])
    assert first == 1
    assert second == 0
    item = store.list_segments(symbol="BTCUSDT")[0]
    assert item["features"] == {"net_return_pct": 2.1}
    assert item["labels"]["forward_direction"] == "up"
    assert item["split"] == "unassigned"


def test_unfinished_analysis_is_marked_interrupted_after_backend_restart(tmp_path):
    path = tmp_path / "history.sqlite3"
    first = AnalysisHistoryStore(path)
    first.save_analysis(
        analysis_id="running-job",
        owner="127.0.0.1",
        request_data={"market": "spot", "exchange": "coinbase", "symbol": "BTC-USD", "timeframe": "15m"},
        status="running",
    )
    reopened = AnalysisHistoryStore(path)
    item = reopened.list_analyses("127.0.0.1")[0]
    assert item["status"] == "interrupted"
