"""PromptAssembler adapter that appends per-run Binance context without forking PA prompts."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from pa_agent.ai.prompt_assembler import PromptAssembler


class CryptoPromptAssembler(PromptAssembler):
    """Adds one immutable market-context block to each user turn for this analysis."""

    def __init__(
        self,
        prompt_dir: Path,
        experience_reader: Any,
        *,
        prompt_settings: Any,
        crypto_context_text: str,
    ) -> None:
        super().__init__(
            prompt_dir=prompt_dir,
            experience_reader=experience_reader,
            prompt_settings=prompt_settings,
        )
        self._crypto_context_text = crypto_context_text

    def _append_crypto_context(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        copied = [dict(message) for message in messages]
        if any(
            self._crypto_context_text in str(message.get("content") or "")
            for message in copied
        ):
            return copied
        for message in reversed(copied):
            if message.get("role") == "user":
                original = str(message.get("content") or "")
                message["content"] = original + "\n\n" + self._crypto_context_text
                break
        return copied

    def build_stage1(self, frame: Any, *, analysis_mode: str = "original") -> list[dict]:
        return self._append_crypto_context(
            super().build_stage1(frame, analysis_mode=analysis_mode)
        )

    def build_incremental_stage1(self, *args: Any, **kwargs: Any) -> list[dict]:
        return self._append_crypto_context(
            super().build_incremental_stage1(*args, **kwargs)
        )

    def build_stage2_continuation(self, **kwargs: Any) -> list[dict]:
        return self._append_crypto_context(
            super().build_stage2_continuation(**kwargs)
        )

    def build_stage2(self, *args: Any, **kwargs: Any) -> list[dict]:
        return self._append_crypto_context(
            super().build_stage2(*args, **kwargs)
        )
