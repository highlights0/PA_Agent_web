from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from starlette.requests import Request

from app.api.v1.ai_settings import _authorize, _test_provider
from app.services.ai_provider_store import AIProviderStore, AIProviderStoreError


def _save(store: AIProviderStore, **overrides):
    values = {
        "provider_id": None,
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
        "api_key": "sk-test-secret-123456",
        "clear_api_key": False,
        "thinking": True,
        "reasoning_effort": "medium",
        "context_window": 200000,
        "backup_glm_api_key": "glm-backup-secret",
        "clear_backup_glm_api_key": False,
        "activate": True,
    }
    values.update(overrides)
    return store.save(**values)


def test_provider_keys_are_encrypted_and_never_returned_from_list(tmp_path):
    key = Fernet.generate_key()
    path = tmp_path / "ai_providers.json"
    store = AIProviderStore(path, master_key=key)

    result = _save(store)
    public = result["providers"][0]
    persisted = path.read_text(encoding="utf-8")

    assert public["has_api_key"] is True
    assert public["api_key_masked"].endswith("3456")
    assert public["has_backup_glm_api_key"] is True
    assert "sk-test-secret-123456" not in json.dumps(result)
    assert "glm-backup-secret" not in json.dumps(result)
    assert "sk-test-secret-123456" not in persisted
    assert "glm-backup-secret" not in persisted
    assert "api_key_encrypted" in persisted
    assert "api_key\":" not in persisted

    active = store.get_decrypted()
    assert active is not None
    assert active["api_key"] == "sk-test-secret-123456"
    assert active["backup_glm_api_key"] == "glm-backup-secret"


def test_saving_blank_key_preserves_existing_key_and_can_activate(tmp_path):
    store = AIProviderStore(tmp_path / "ai_providers.json", master_key=Fernet.generate_key())
    _save(store)
    provider_id = store.list_public()["providers"][0]["id"]

    _save(
        store,
        provider_id=provider_id,
        api_key="",
        backup_glm_api_key="",
        activate=True,
    )
    assert store.get_decrypted(provider_id)["api_key"] == "sk-test-secret-123456"


def test_key_clear_is_explicit_and_active_provider_requires_a_key(tmp_path):
    store = AIProviderStore(tmp_path / "ai_providers.json", master_key=Fernet.generate_key())
    _save(store)
    provider_id = store.list_public()["providers"][0]["id"]

    with pytest.raises(ValueError, match="API Key"):
        _save(
            store,
            provider_id=provider_id,
            api_key="",
            clear_api_key=True,
            activate=True,
        )

    _save(
        store,
        provider_id=provider_id,
        api_key="",
        clear_api_key=True,
        activate=False,
    )
    assert store.get_decrypted(provider_id)["api_key"] == ""


def test_wrong_master_key_cannot_decrypt_saved_provider(tmp_path):
    path = tmp_path / "ai_providers.json"
    store = AIProviderStore(path, master_key=Fernet.generate_key())
    _save(store)

    wrong = AIProviderStore(path, master_key=Fernet.generate_key())
    with pytest.raises(AIProviderStoreError, match="Unable to decrypt"):
        wrong.list_public()


def test_missing_key_file_is_not_regenerated_when_encrypted_store_exists(tmp_path):
    path = tmp_path / "ai_providers.json"
    store = AIProviderStore(path, master_key=Fernet.generate_key())
    _save(store)
    (tmp_path / "ai_settings.key").unlink(missing_ok=True)

    with pytest.raises(AIProviderStoreError, match="encryption key is missing"):
        AIProviderStore(path)


def test_base_url_validation_rejects_credentials_query_and_non_http():
    assert AIProviderStore.validate_base_url("https://api.deepseek.com/") == "https://api.deepseek.com"
    with pytest.raises(ValueError):
        AIProviderStore.validate_base_url("file:///etc/passwd")
    with pytest.raises(ValueError):
        AIProviderStore.validate_base_url("https://user:pass@example.com")
    with pytest.raises(ValueError):
        AIProviderStore.validate_base_url("https://example.com?token=secret")


def _request(client_host: str):
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [],
        "client": (client_host, 12345),
        "app": SimpleNamespace(
            state=SimpleNamespace(
                settings=SimpleNamespace(
                    ai_settings_admin_token="",
                    app_env="development",
                )
            )
        ),
    }
    return Request(scope)


def test_settings_routes_allow_only_loopback_in_local_development_without_token():
    _authorize(_request("127.0.0.1"), None)
    _authorize(_request("::1"), None)
    _authorize(_request("::ffff:127.0.0.1"), None)
    with pytest.raises(HTTPException) as error:
        _authorize(_request("192.168.1.22"), None)
    assert error.value.status_code == 503


def test_settings_routes_require_configured_admin_token_outside_local_dev():
    request = _request("127.0.0.1")
    request.app.state.settings.app_env = "production"
    request.app.state.settings.ai_settings_admin_token = "long-admin-token"

    with pytest.raises(HTTPException) as error:
        _authorize(request, "wrong-token")
    assert error.value.status_code == 401

    _authorize(request, "long-admin-token")


def test_provider_test_never_returns_or_logs_the_api_key(monkeypatch):
    class FakeMessage:
        content = "OK"

    class FakeChoice:
        message = FakeMessage()

    class FakeUsage:
        total_tokens = 7

    class FakeResponse:
        model = "fake-model"
        choices = [FakeChoice()]
        usage = FakeUsage()

    class FakeCompletions:
        @staticmethod
        def create(**kwargs):
            assert kwargs["model"] == "fake-model"
            return FakeResponse()

    class FakeClient:
        def __init__(self, **kwargs):
            assert kwargs["api_key"] == "sk-never-return-this"

            class Chat:
                completions = FakeCompletions()

            self.chat = Chat()

    monkeypatch.setattr("app.api.v1.ai_settings.OpenAI", FakeClient)
    result = _test_provider(
        "https://example.com/v1",
        "fake-model",
        "sk-never-return-this",
    )
    assert result["ok"] is True
    assert result["response_preview"] == "OK"
    assert "sk-never-return-this" not in json.dumps(result)


def test_generated_encryption_key_survives_store_recreation(tmp_path):
    path = tmp_path / "ai_providers.json"
    first = AIProviderStore(path)
    _save(first)
    key_file = tmp_path / "ai_settings.key"
    assert key_file.exists()

    second = AIProviderStore(path)
    active = second.get_decrypted()
    assert active is not None
    assert active["api_key"] == "sk-test-secret-123456"
