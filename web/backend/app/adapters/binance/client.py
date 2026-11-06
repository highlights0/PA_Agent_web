"""Small async REST client for Binance public market-data endpoints."""
from __future__ import annotations

from typing import Any

import httpx


class BinanceAPIError(Exception):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        error_code: int | None = None,
        retry_after: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code
        self.retry_after = retry_after


class BinanceRESTClient:
    """Uses a shared httpx client; intentionally implements GET-only access."""

    def __init__(self, http: httpx.AsyncClient, base_url: str) -> None:
        self._http = http
        self._base_url = base_url.rstrip("/")

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        if not path.startswith("/") or ".." in path:
            raise ValueError("Invalid Binance API path")
        try:
            response = await self._http.get(f"{self._base_url}{path}", params=params)
        except httpx.TimeoutException as exc:
            raise BinanceAPIError("Binance request timed out") from exc
        except httpx.HTTPError as exc:
            raise BinanceAPIError("Unable to reach Binance market-data API") from exc

        if response.status_code >= 400:
            retry_after = response.headers.get("Retry-After")
            message = "Binance market-data request failed"
            error_code = None
            try:
                payload = response.json()
                if isinstance(payload, dict):
                    if payload.get("msg"):
                        message = str(payload["msg"])[:240]
                    raw_code = payload.get("code")
                    if isinstance(raw_code, int):
                        error_code = raw_code
            except ValueError:
                # Do not expose arbitrary HTML/proxy response bodies to visitors.
                pass
            raise BinanceAPIError(
                message,
                status_code=response.status_code,
                error_code=error_code,
                retry_after=retry_after,
            )

        try:
            return response.json()
        except ValueError as exc:
            raise BinanceAPIError("Binance returned invalid JSON") from exc
