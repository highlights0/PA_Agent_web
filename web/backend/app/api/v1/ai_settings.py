"""Browser-managed AI provider settings with encrypted server-side secrets."""
from __future__ import annotations

import asyncio
import hmac
import ipaddress
import time
from typing import Literal

from fastapi import APIRouter, Header, HTTPException, Request
from openai import OpenAI
from pydantic import BaseModel, ConfigDict, Field

from app.services.ai_provider_store import AIProviderStoreError

router = APIRouter()


class ProviderInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str | None = None
    name: str = Field(min_length=1, max_length=80)
    base_url: str = Field(min_length=8, max_length=500)
    model: str = Field(min_length=1, max_length=160)
    api_key: str | None = Field(default=None, max_length=2000)
    clear_api_key: bool = False
    backup_glm_api_key: str | None = Field(default=None, max_length=2000)
    clear_backup_glm_api_key: bool = False
    thinking: bool = True
    reasoning_effort: Literal["low", "medium", "high", "max"] = "medium"
    context_window: int = Field(default=200000, ge=1024, le=2_000_000)
    activate: bool = False


class ProviderTestInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str | None = None
    base_url: str = Field(min_length=8, max_length=500)
    model: str = Field(min_length=1, max_length=160)
    api_key: str | None = Field(default=None, max_length=2000)


def _store(request: Request):
    return request.app.state.ai_provider_store


def _authorize(request: Request, supplied_token: str | None) -> None:
    configured_token = request.app.state.settings.ai_settings_admin_token
    if configured_token:
        if not supplied_token or not hmac.compare_digest(
            supplied_token, configured_token
        ):
            raise HTTPException(status_code=401, detail="AI 设置管理令牌不正确")
        return

    host = request.client.host if request.client else ""
    try:
        address = ipaddress.ip_address(host)
        is_loopback = address.is_loopback or bool(
            getattr(address, "ipv4_mapped", None)
            and address.ipv4_mapped.is_loopback
        )
    except ValueError:
        is_loopback = host.lower() == "localhost"

    if request.app.state.settings.app_env.lower() == "development" and is_loopback:
        return
    raise HTTPException(
        status_code=503,
        detail=(
            "AI 设置管理尚未配置访问保护。请在服务器环境变量中设置 "
            "AI_SETTINGS_ADMIN_TOKEN，然后使用该令牌打开设置页面。"
        ),
    )


def _safe_store_call(call):
    try:
        return call()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'")) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except AIProviderStoreError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/ai/providers")
async def list_ai_providers(
    request: Request,
    x_pa_admin_token: str | None = Header(default=None),
):
    _authorize(request, x_pa_admin_token)
    return _safe_store_call(_store(request).list_public)


@router.post("/ai/providers")
async def save_ai_provider(
    payload: ProviderInput,
    request: Request,
    x_pa_admin_token: str | None = Header(default=None),
):
    _authorize(request, x_pa_admin_token)
    return _safe_store_call(
        lambda: _store(request).save(
            provider_id=payload.id,
            name=payload.name,
            base_url=payload.base_url,
            model=payload.model,
            api_key=payload.api_key,
            clear_api_key=payload.clear_api_key,
            thinking=payload.thinking,
            reasoning_effort=payload.reasoning_effort,
            context_window=payload.context_window,
            backup_glm_api_key=payload.backup_glm_api_key,
            clear_backup_glm_api_key=payload.clear_backup_glm_api_key,
            activate=payload.activate,
        )
    )


@router.post("/ai/providers/{provider_id}/activate")
async def activate_ai_provider(
    provider_id: str,
    request: Request,
    x_pa_admin_token: str | None = Header(default=None),
):
    _authorize(request, x_pa_admin_token)
    return _safe_store_call(lambda: _store(request).activate(provider_id))


@router.delete("/ai/providers/{provider_id}")
async def delete_ai_provider(
    provider_id: str,
    request: Request,
    x_pa_admin_token: str | None = Header(default=None),
):
    _authorize(request, x_pa_admin_token)
    return _safe_store_call(lambda: _store(request).delete(provider_id))


def _test_provider(base_url: str, model: str, api_key: str) -> dict:
    started = time.monotonic()
    try:
        client = OpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=30.0,
            max_retries=0,
        )
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "Reply with OK only."},
                {"role": "user", "content": "Connection test."},
            ],
            max_tokens=12,
            stream=False,
        )
        content = (response.choices[0].message.content or "").strip()
        return {
            "ok": True,
            "model": response.model or model,
            "latency_ms": round((time.monotonic() - started) * 1000),
            "response_preview": content[:80],
            "total_tokens": getattr(response.usage, "total_tokens", None),
        }
    except Exception as exc:  # noqa: BLE001
        message = str(exc)
        if api_key:
            message = message.replace(api_key, "********")
        return {
            "ok": False,
            "model": model,
            "latency_ms": round((time.monotonic() - started) * 1000),
            "error": message[:600],
        }


@router.post("/ai/providers/test")
async def test_ai_provider(
    payload: ProviderTestInput,
    request: Request,
    x_pa_admin_token: str | None = Header(default=None),
):
    _authorize(request, x_pa_admin_token)
    try:
        base_url = _store(request).validate_base_url(payload.base_url)
        api_key = (payload.api_key or "").strip()
        if not api_key and payload.id:
            saved = _store(request).get_decrypted(payload.id)
            api_key = str(saved.get("api_key") or "") if saved else ""
        if not api_key:
            raise HTTPException(status_code=422, detail="请填写 API Key")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except AIProviderStoreError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return await asyncio.to_thread(_test_provider, base_url, payload.model, api_key)
