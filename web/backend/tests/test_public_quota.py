from app.core.public_quota import PublicAnalysisQuota


def test_quota_consumes_daily_credits_per_ip():
    quota = PublicAnalysisQuota(2)
    allowed, remaining = quota.consume("203.0.113.10")
    assert allowed is True
    assert remaining == 1
    allowed, remaining = quota.consume("203.0.113.10")
    assert allowed is True
    assert remaining == 0
    allowed, remaining = quota.consume("203.0.113.10")
    assert allowed is False
    assert remaining == 0


def test_quota_isolated_by_client_ip():
    quota = PublicAnalysisQuota(1)
    assert quota.consume("203.0.113.10")[0] is True
    assert quota.consume("203.0.113.11")[0] is True
