"""Environment-based configuration for the public crypto market API."""
from __future__ import annotations

import os
from dataclasses import dataclass


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
    spot_rest_url: str = os.getenv("BINANCE_SPOT_REST_URL", "https://api.binance.com")
    futures_rest_url: str = os.getenv("BINANCE_USDM_REST_URL", "https://fapi.binance.com")
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


settings = Settings()
