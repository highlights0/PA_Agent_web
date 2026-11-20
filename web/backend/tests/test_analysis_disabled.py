from fastapi.testclient import TestClient

from app.main import app


def test_public_analysis_is_disabled_by_default():
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/analysis",
            json={
                "market": "usdm",
                "symbol": "BTCUSDT",
                "timeframe": "15m",
                "analysis_bars": 100,
            },
        )
    assert response.status_code == 503
    assert "disabled" in response.json()["detail"].lower()
