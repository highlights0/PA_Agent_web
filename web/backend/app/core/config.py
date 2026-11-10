"""Environment-based configuration for the public crypto market API."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _csv_env(name: str, default: str) -> tuple[str, ...]:
    value = os.getenv(name, default)
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Settings:
    app_env: str = os.getenv("APP_ENV", "development")
    spot_rest_url: str = os.getenv("BINANCE_SPOT_REST_URL", "https://data-api.binance.vision")
    futures_rest_url: str = os.getenv("BINANCE_USDM_REST_URL", "https://fapi.binance.com")
    binance_us_spot_rest_url: str = os.getenv("BINANCE_US_SPOT_REST_URL", "https://api.binance.us")
    coinbase_spot_rest_url: str = os.getenv(
        "COINBASE_SPOT_REST_URL", "https://api.exchange.coinbase.com"
    )
    gate_futures_rest_url: str = os.getenv(
        "GATE_FUTURES_REST_URL", "https://api.gateio.ws/api/v4"
    )
    request_timeout_seconds: float = float(os.getenv("BINANCE_HTTP_TIMEOUT", "10"))
    max_klines: int = int(os.getenv("PUBLIC_MAX_BARS", "300"))
    cors_origins: tuple[str, ...] = _csv_env(
        "WEB_CORS_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173",
    )
    ai_api_key: str = os.getenv("AI_API_KEY", "")
    ai_base_url: str = os.getenv("AI_BASE_URL", "https://api.deepseek.com")
    ai_model: str = os.getenv("AI_MODEL", "deepseek-chat")
    ai_thinking: bool = _bool_env("AI_THINKING", True)
    ai_reasoning_effort: str = os.getenv("AI_REASONING_EFFORT", "medium")
    ai_provider_store_path: str = os.getenv(
        "AI_PROVIDER_STORE_PATH",
        str(Path(__file__).resolve().parents[2] / "data" / "ai_providers.json"),
    )
    ai_settings_master_key: str = os.getenv("AI_SETTINGS_MASTER_KEY", "")
    ai_settings_admin_token: str = os.getenv("AI_SETTINGS_ADMIN_TOKEN", "")
    public_analysis_enabled: bool = _bool_env("PUBLIC_ANALYSIS_ENABLED", False)
    public_analysis_per_ip_daily: int = int(os.getenv("PUBLIC_ANALYSIS_PER_IP_DAILY", "5"))
    public_max_concurrent_analyses: int = int(os.getenv("PUBLIC_MAX_CONCURRENT_ANALYSES", "1"))


settings = Settings()
