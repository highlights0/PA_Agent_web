"""Encrypted persistence for browser-configured AI providers.

Plaintext API keys are only returned to the analysis process after decryption.
They are never included in API responses or persisted to JSON.
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from cryptography.fernet import Fernet, InvalidToken


class AIProviderStoreError(RuntimeError):
    pass


class AIProviderStore:
    def __init__(
        self,
        path: str | Path,
        *,
        master_key: str | bytes | None = None,
    ) -> None:
        self.path = Path(path)
        self.key_path = self.path.with_name("ai_settings.key")
        self._lock = threading.RLock()
        self._fernet = self._load_fernet(master_key)

    def _load_fernet(self, master_key: str | bytes | None) -> Fernet:
        if master_key:
            key = master_key.encode("ascii") if isinstance(master_key, str) else master_key
            try:
                return Fernet(key)
            except (ValueError, TypeError) as exc:
                raise AIProviderStoreError(
                    "AI_SETTINGS_MASTER_KEY must be a valid Fernet URL-safe base64 key"
                ) from exc

        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.key_path.parent, 0o700)
        except OSError:
            pass
        if self.key_path.exists():
            key = self.key_path.read_bytes().strip()
            try:
                return Fernet(key)
            except (ValueError, TypeError) as exc:
                raise AIProviderStoreError(
                    f"Invalid encryption key file: {self.key_path}"
                ) from exc

        if self.path.exists():
            raise AIProviderStoreError(
                "AI provider data exists but its encryption key is missing. "
                "Restore ai_settings.key from backup; do not generate a replacement key."
            )

        key = Fernet.generate_key()
        try:
            fd = os.open(self.key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            key = self.key_path.read_bytes().strip()
        else:
            with os.fdopen(fd, "wb") as handle:
                handle.write(key)
            try:
                os.chmod(self.key_path, 0o600)
            except OSError:
                pass
        return Fernet(key)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def validate_base_url(base_url: str) -> str:
        value = base_url.strip().rstrip("/")
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Base URL 必须是有效的 HTTP/HTTPS API 根地址")
        return value

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": 1, "active_provider_id": None, "providers": []}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AIProviderStoreError("AI provider settings file is unreadable") from exc
        if not isinstance(data, dict) or not isinstance(data.get("providers"), list):
            raise AIProviderStoreError("AI provider settings file has an invalid format")
        data.setdefault("active_provider_id", None)
        return data

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        encoded = json.dumps(data, ensure_ascii=False, indent=2)
        try:
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, self.path)
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
        finally:
            if temp.exists():
                temp.unlink(missing_ok=True)

    def _encrypt(self, value: str | None) -> str:
        if not value:
            return ""
        return self._fernet.encrypt(value.encode("utf-8")).decode("ascii")

    def _decrypt(self, value: str | None) -> str:
        if not value:
            return ""
        try:
            return self._fernet.decrypt(value.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError, UnicodeDecodeError) as exc:
            raise AIProviderStoreError(
                "Unable to decrypt AI provider key; check AI_SETTINGS_MASTER_KEY/ai_settings.key"
            ) from exc

    @staticmethod
    def _mask(value: str) -> str:
        if not value:
            return ""
        if len(value) <= 8:
            return "*" * len(value)
        return "••••••" + value[-4:]

    def list_public(self) -> dict[str, Any]:
        with self._lock:
            data = self._read()
            providers = []
            for item in data["providers"]:
                key = self._decrypt(item.get("api_key_encrypted"))
                backup = self._decrypt(item.get("backup_glm_api_key_encrypted"))
                providers.append(
                    {
                        "id": item["id"],
                        "name": item["name"],
                        "base_url": item["base_url"],
                        "model": item["model"],
                        "thinking": bool(item.get("thinking", True)),
                        "reasoning_effort": item.get("reasoning_effort", "medium"),
                        "context_window": int(item.get("context_window", 200000)),
                        "has_api_key": bool(key),
                        "api_key_masked": self._mask(key),
                        "has_backup_glm_api_key": bool(backup),
                        "backup_glm_api_key_masked": self._mask(backup),
                        "active": item["id"] == data.get("active_provider_id"),
                        "created_at": item.get("created_at"),
                        "updated_at": item.get("updated_at"),
                    }
                )
            return {
                "providers": providers,
                "active_provider_id": data.get("active_provider_id"),
                "encryption": "fernet",
            }

    def save(
        self,
        *,
        provider_id: str | None,
        name: str,
        base_url: str,
        model: str,
        api_key: str | None,
        clear_api_key: bool,
        thinking: bool,
        reasoning_effort: str,
        context_window: int,
        backup_glm_api_key: str | None,
        clear_backup_glm_api_key: bool,
        activate: bool,
    ) -> dict[str, Any]:
        name = name.strip()
        model = model.strip()
        if not name:
            raise ValueError("请填写配置名称")
        if not model:
            raise ValueError("请填写模型名称")
        if reasoning_effort not in {"low", "medium", "high", "max"}:
            raise ValueError("Reasoning Effort 必须是 low/medium/high/max")
        if not 1024 <= context_window <= 2_000_000:
            raise ValueError("Context Window 必须在 1024 到 2000000 之间")
        base_url = self.validate_base_url(base_url)

        with self._lock:
            data = self._read()
            providers = data["providers"]
            existing = next((p for p in providers if p["id"] == provider_id), None)
            now = self._now()
            if existing is None:
                item = {
                    "id": provider_id or str(uuid.uuid4()),
                    "created_at": now,
                    "api_key_encrypted": "",
                    "backup_glm_api_key_encrypted": "",
                }
                providers.append(item)
            else:
                item = existing

            if clear_api_key:
                item["api_key_encrypted"] = ""
            elif api_key and api_key.strip():
                item["api_key_encrypted"] = self._encrypt(api_key.strip())

            if clear_backup_glm_api_key:
                item["backup_glm_api_key_encrypted"] = ""
            elif backup_glm_api_key and backup_glm_api_key.strip():
                item["backup_glm_api_key_encrypted"] = self._encrypt(
                    backup_glm_api_key.strip()
                )

            item.update(
                {
                    "name": name,
                    "base_url": base_url,
                    "model": model,
                    "thinking": bool(thinking),
                    "reasoning_effort": reasoning_effort,
                    "context_window": int(context_window),
                    "updated_at": now,
                }
            )
            if activate:
                if not item.get("api_key_encrypted"):
                    raise ValueError("请先配置 API Key，才能设为当前使用模型")
                data["active_provider_id"] = item["id"]
            elif (
                clear_api_key
                and data.get("active_provider_id") == item["id"]
            ):
                data["active_provider_id"] = None
            elif data.get("active_provider_id") is None and item.get("api_key_encrypted"):
                data["active_provider_id"] = item["id"]

            self._write(data)
            result = self.list_public()
            result["saved_provider_id"] = item["id"]
            return result

    def activate(self, provider_id: str) -> dict[str, Any]:
        with self._lock:
            data = self._read()
            item = next((p for p in data["providers"] if p["id"] == provider_id), None)
            if item is None:
                raise KeyError("AI 模型配置不存在")
            if not item.get("api_key_encrypted"):
                raise ValueError("该模型尚未配置 API Key")
            data["active_provider_id"] = provider_id
            self._write(data)
            return self.list_public()

    def delete(self, provider_id: str) -> dict[str, Any]:
        with self._lock:
            data = self._read()
            before = len(data["providers"])
            data["providers"] = [p for p in data["providers"] if p["id"] != provider_id]
            if len(data["providers"]) == before:
                raise KeyError("AI 模型配置不存在")
            if data.get("active_provider_id") == provider_id:
                data["active_provider_id"] = None
            self._write(data)
            return self.list_public()

    def get_decrypted(self, provider_id: str | None = None) -> dict[str, Any] | None:
        with self._lock:
            data = self._read()
            selected_id = provider_id or data.get("active_provider_id")
            if not selected_id:
                return None
            item = next((p for p in data["providers"] if p["id"] == selected_id), None)
            if item is None:
                return None
            result = dict(item)
            result["api_key"] = self._decrypt(item.get("api_key_encrypted"))
            result["backup_glm_api_key"] = self._decrypt(
                item.get("backup_glm_api_key_encrypted")
            )
            return result

    def has_active_key(self) -> bool:
        item = self.get_decrypted()
        return bool(item and item.get("api_key"))
