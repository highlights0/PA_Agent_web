from __future__ import annotations

from types import SimpleNamespace

from app.services.analysis_service import AnalysisService


class FakeProviderStore:
    def __init__(self, provider):
        self.provider = provider

    def get_decrypted(self):
        return self.provider


def test_analysis_uses_active_browser_provider_instead_of_legacy_environment_key():
    selected = {
        "model": "custom-reasoner",
        "base_url": "https://gateway.example/v1",
        "api_key": "sk-browser-configured",
        "thinking": False,
        "reasoning_effort": "high",
        "context_window": 128000,
        "backup_glm_api_key": "glm-backup",
    }
    legacy = SimpleNamespace(
        ai_api_key="sk-old-env",
        ai_base_url="https://api.deepseek.com",
        ai_model="deepseek-chat",
        ai_thinking=True,
        ai_reasoning_effort="medium",
        max_klines=300,
    )
    service = AnalysisService(
        market_service=object(),
        web_settings=legacy,
        provider_store=FakeProviderStore(selected),
    )

    assert service._active_provider() == selected


def test_analysis_retains_legacy_environment_fallback_when_no_web_profile_is_active():
    legacy = SimpleNamespace(
        ai_api_key="sk-legacy-env",
        ai_base_url="https://api.deepseek.com",
        ai_model="deepseek-chat",
        ai_thinking=True,
        ai_reasoning_effort="medium",
        max_klines=300,
    )
    service = AnalysisService(
        market_service=object(),
        web_settings=legacy,
        provider_store=FakeProviderStore(None),
    )

    result = service._active_provider()
    assert result["api_key"] == "sk-legacy-env"
    assert result["base_url"] == "https://api.deepseek.com"
    assert result["model"] == "deepseek-chat"
