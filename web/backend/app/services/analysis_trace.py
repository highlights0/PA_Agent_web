"""Safe, structured tracing for the original PA two-stage analysis pipeline.

The trace records orchestration events, prompt metadata, validation outcomes and
usage metadata. It deliberately does not capture provider credentials or model
reasoning_content / hidden chain-of-thought.
"""
from __future__ import annotations

import re
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import urlsplit


_SECRET_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key|authorization|bearer)\s*[:=]\s*[^\s,;]+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
)


def _safe_text(value: Any, limit: int = 800) -> str:
    text = " ".join(str(value or "").split())
    for pattern in _SECRET_PATTERNS:
        if pattern.pattern.startswith("(?i)"):
            text = pattern.sub(r"\1=***", text)
        else:
            text = pattern.sub("***", text)
    return text[:limit]


def _safe_output(value: Any, limit: int = 12000) -> str:
    text = str(value or "")
    for pattern in _SECRET_PATTERNS:
        if pattern.pattern.startswith("(?i)"):
            text = pattern.sub(r"\1=***", text)
        else:
            text = pattern.sub("***", text)
    return text[:limit]


def _safe_value(value: Any, depth: int = 0) -> Any:
    if depth > 8:
        return "[nested value omitted]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _safe_text(value, 12000)
    if isinstance(value, dict):
        return {
            _safe_text(key, 120): _safe_value(item, depth + 1)
            for key, item in list(value.items())[:200]
        }
    if isinstance(value, (list, tuple)):
        return [_safe_value(item, depth + 1) for item in value[:300]]
    return _safe_text(value, 300)


def _headings(value: str, limit: int = 10) -> list[str]:
    result: list[str] = []
    for line in value.splitlines():
        line = line.strip()
        if line.startswith("#"):
            heading = _safe_text(line.lstrip("#").strip(), 100)
            if heading and heading not in result:
                result.append(heading)
                if len(result) >= limit:
                    break
    return result


class AnalysisTrace:
    """Thread-safe collector shared by async job polling and the PA worker thread."""

    def __init__(
        self,
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self._callback = on_event
        self._started = time.monotonic()
        self._lock = threading.RLock()
        self._events: list[dict[str, Any]] = []
        self._validation_events: list[dict[str, Any]] = []
        self._model_calls: list[dict[str, Any]] = []
        self._prompt_summaries: dict[str, dict[str, Any]] = {}
        self._retry_counts = {"stage1": 0, "stage2": 0}
        self.current_stage: str | None = None

    def emit(
        self,
        event_type: str,
        *,
        stage: str | None = None,
        title: str,
        detail: str = "",
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            event = {
                "id": len(self._events) + 1,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "elapsed_ms": round((time.monotonic() - self._started) * 1000),
                "type": event_type,
                "stage": stage,
                "title": _safe_text(title, 120),
                "detail": _safe_text(detail, 500),
                "data": _safe_value(data or {}),
            }
            self._events.append(event)
        if self._callback is not None:
            try:
                self._callback(event)
            except Exception:  # noqa: BLE001
                # Observability must never change the original PA analysis behavior.
                pass
        return event

    def orchestrator_event(self, event: Any) -> None:
        name = getattr(event, "name", str(event))
        mapping = {
            "Stage1Started": ("stage_started", "stage1", "阶段一开始"),
            "Stage1Retry": ("validation_retry", "stage1", "阶段一触发校验重试"),
            "Stage1Done": ("stage_done", "stage1", "阶段一诊断完成"),
            "Stage1Failed": ("stage_failed", "stage1", "阶段一失败"),
            "Stage2Started": ("stage_started", "stage2", "阶段二开始"),
            "Stage2Retry": ("validation_retry", "stage2", "阶段二触发校验重试"),
            "Stage2Done": ("stage_done", "stage2", "阶段二决策完成"),
            "Stage2Failed": ("stage_failed", "stage2", "阶段二失败"),
            "RecordSaved": ("record_saved", "system", "分析记录已生成"),
            "Cancelled": ("cancelled", "system", "分析已取消"),
            "InsufficientData": ("preflight_failed", "preflight", "数据预检未通过"),
        }
        event_type, stage, title = mapping.get(
            name, ("orchestrator_event", None, name)
        )
        if stage in {"stage1", "stage2"}:
            self.current_stage = stage
        if event_type == "validation_retry" and stage:
            with self._lock:
                self._retry_counts[stage] += 1
                attempt = self._retry_counts[stage]
            self.emit(
                event_type,
                stage=stage,
                title=title,
                detail=f"第 {attempt} 次重试；原版校验重试策略决定是否继续",
                data={"retry_number": attempt},
            )
            return
        self.emit(event_type, stage=stage, title=title)

    def prompt_ready(self, stage: str, system_prompt: str, user_prompt: str) -> None:
        summary = {
            "system_chars": len(system_prompt or ""),
            "user_chars": len(user_prompt or ""),
            "system_headings": _headings(system_prompt or ""),
            "user_headings": _headings(user_prompt or ""),
        }
        with self._lock:
            self._prompt_summaries[stage] = summary
        self.emit(
            "prompt_ready",
            stage=stage,
            title=f"{'阶段一' if stage == 'stage1' else '阶段二'} Prompt 已构建",
            detail=(
                f"System {summary['system_chars']:,} 字符，"
                f"User {summary['user_chars']:,} 字符"
            ),
            data=summary,
        )

    def strategy_files_ready(self, files: list[str]) -> None:
        self.emit(
            "strategy_routed",
            stage="routing",
            title="策略路由完成",
            detail=f"选中 {len(files)} 个策略文件",
            data={"strategy_files": [_safe_text(item, 160) for item in files]},
        )

    def validation_result(self, stage: str, result: Any) -> None:
        is_ok = result.__class__.__name__ == "Ok"
        if is_ok:
            fields = list((getattr(result, "obj", None) or {}).keys())
            item = {
                "stage": stage,
                "status": "passed",
                "category": None,
                "missing_fields": [],
                "invalid_fields": [],
                "message": "JSON 结构和业务字段校验通过",
                "result_fields": fields[:80],
                "validated_output": getattr(result, "obj", None),
            }
        else:
            item = {
                "stage": stage,
                "status": "failed",
                "category": getattr(result, "category", None),
                "missing_fields": list(getattr(result, "missing_fields", []) or []),
                "invalid_fields": list(getattr(result, "invalid_fields", []) or []),
                "parse_position": getattr(result, "parse_position", None),
                "message": _safe_text(getattr(result, "message", ""), 500),
            }
        with self._lock:
            item["attempt"] = sum(
                1 for old in self._validation_events if old["stage"] == stage
            ) + 1
            self._validation_events.append(item)
        self.emit(
            "validation_passed" if is_ok else "validation_failed",
            stage=stage,
            title=(
                f"{'阶段一' if stage == 'stage1' else '阶段二'}校验通过"
                if is_ok
                else f"{'阶段一' if stage == 'stage1' else '阶段二'}校验未通过"
            ),
            detail=(
                f"第 {item['attempt']} 次校验"
                if is_ok
                else f"第 {item['attempt']} 次校验；类别 {item.get('category') or '未知'}"
            ),
            data=item,
        )

    def model_call_started(self, stage: str | None, model: str | None) -> int:
        with self._lock:
            call_number = len(self._model_calls) + 1
            item = {
                "call": call_number,
                "stage": stage or self.current_stage or "unknown",
                "model": _safe_text(model or "", 120),
                "status": "running",
            }
            self._model_calls.append(item)
        self.emit(
            "model_call_started",
            stage=item["stage"],
            title=f"{'阶段一' if item['stage'] == 'stage1' else '阶段二'}模型请求已发送",
            detail=f"模型：{item['model'] or '配置模型'}",
            data={"call": call_number, "model": item["model"]},
        )
        return call_number

    def model_call_finished(
        self,
        call_number: int,
        reply: Any | None = None,
        error: Exception | None = None,
        latency_ms: float | None = None,
    ) -> None:
        with self._lock:
            item = next(
                (entry for entry in self._model_calls if entry["call"] == call_number),
                None,
            )
            if item is None:
                return
            if error is not None:
                item.update(
                    {
                        "status": "failed",
                        "error": _safe_text(error, 500),
                        "latency_ms": round(latency_ms or 0),
                    }
                )
            else:
                usage = getattr(reply, "usage", None)
                item.update(
                    {
                        "status": "completed",
                        "latency_ms": round(
                            latency_ms
                            if latency_ms is not None
                            else getattr(reply, "latency_ms", 0)
                        ),
                        "output_chars": len(getattr(reply, "content", None) or ""),
                        "output_text": _safe_output(getattr(reply, "content", None) or ""),
                        "prompt_tokens": getattr(usage, "prompt_tokens", 0),
                        "cached_prompt_tokens": getattr(usage, "cached_prompt_tokens", 0),
                        "completion_tokens": getattr(usage, "completion_tokens", 0),
                        "total_tokens": getattr(usage, "total_tokens", 0),
                    }
                )
            snapshot = dict(item)
        self.emit(
            "model_call_failed" if error is not None else "model_call_completed",
            stage=snapshot["stage"],
            title="模型请求失败" if error is not None else "模型响应已接收",
            detail=(
                snapshot.get("error", "")
                if error is not None
                else (
                    f"耗时 {snapshot.get('latency_ms', 0)} ms · "
                    f"{snapshot.get('total_tokens', 0)} tokens · "
                    f"正文 {snapshot.get('output_chars', 0)} 字符"
                )
            ),
            data=snapshot,
        )

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(item) for item in self._events]

    def details(
        self,
        *,
        frame: Any,
        context: Any,
        record: Any,
        provider: dict[str, Any],
    ) -> dict[str, Any]:
        bars = list(getattr(frame, "bars", []) or [])
        first = bars[-1] if bars else None
        latest = bars[0] if bars else None

        def bar_value(bar: Any, name: str) -> Any:
            return getattr(bar, name, None) if bar is not None else None

        experiences = []
        for entry in getattr(record, "experience_loaded", []) or []:
            if not isinstance(entry, dict):
                continue
            experiences.append(
                {
                    key: entry.get(key)
                    for key in ("filename", "case_type", "cycle_position", "timestamp_ms")
                    if entry.get(key) is not None
                }
            )

        exception = getattr(record, "exception", None)
        if isinstance(exception, dict):
            exception = {
                key: exception[key]
                for key in (
                    "type", "stage", "category", "message", "missing_fields",
                    "invalid_fields", "parse_position", "validation_attempts",
                    "failed_check",
                )
                if key in exception
            }
            if "message" in exception:
                exception["message"] = _safe_text(exception["message"], 700)

        with self._lock:
            validations = [dict(item) for item in self._validation_events]
            model_calls = [dict(item) for item in self._model_calls]
            prompts = {key: dict(value) for key, value in self._prompt_summaries.items()}

        base_url = str(provider.get("base_url") or "")
        parsed_base = urlsplit(base_url)
        safe_base_url = (
            f"{parsed_base.scheme}://{parsed_base.netloc}"
            if parsed_base.scheme and parsed_base.netloc
            else ""
        )
        return {
            "provider": {
                "name": _safe_text(provider.get("name") or "", 100),
                "model": _safe_text(provider.get("model") or "", 120),
                "base_url": _safe_text(safe_base_url, 160),
                "thinking": bool(provider.get("thinking", True)),
                "reasoning_effort": _safe_text(provider.get("reasoning_effort") or "medium", 20),
            },
            "frame": {
                "symbol": getattr(frame, "symbol", ""),
                "timeframe": getattr(frame, "timeframe", ""),
                "bar_count": len(bars),
                "first_open_time": bar_value(first, "ts_open"),
                "latest_open_time": bar_value(latest, "ts_open"),
                "latest_close": bar_value(latest, "close"),
                "latest_high": bar_value(latest, "high"),
                "latest_low": bar_value(latest, "low"),
            },
            "market_context": context.model_dump() if hasattr(context, "model_dump") else {},
            "prompts": prompts,
            "validation": validations,
            "model_calls": model_calls,
            "strategy_files_used": list(getattr(record, "strategy_files_used", []) or []),
            "experience_loaded": experiences,
            "experience_loaded_count": len(experiences),
            "stage2_short_circuited": bool(
                getattr(record, "stage1_diagnosis", None)
                and not getattr(record, "stage2_messages", [])
                and getattr(record, "stage2_response", None) is None
                and getattr(record, "stage2_decision", None)
            ),
            "stage1_diagnosis": getattr(record, "stage1_diagnosis", None),
            "stage2_decision": getattr(record, "stage2_decision", None),
            "usage": getattr(record, "usage_total", {}) or {},
            "exception": exception,
        }


class TracingValidator:
    """Transparent validator proxy that records each initial/retry validation result."""

    def __init__(self, validator: Any, trace: AnalysisTrace) -> None:
        self._validator = validator
        self._trace = trace

    def __getattr__(self, name: str) -> Any:
        return getattr(self._validator, name)

    def validate(self, stage: str, content: str, **kwargs: Any) -> Any:
        result = self._validator.validate(stage, content, **kwargs)
        self._trace.validation_result(stage, result)
        return result


class TracingClient:
    """Transparent PA client proxy; captures timing/usage, never reasoning tokens."""

    def __init__(self, client: Any, trace: AnalysisTrace) -> None:
        self._client = client
        self._trace = trace

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)

    def stream_chat(self, *args: Any, **kwargs: Any) -> Any:
        stage = self._trace.current_stage
        model = getattr(getattr(self._client, "_settings", None), "model", None)
        call_number = self._trace.model_call_started(stage, model)
        started = time.monotonic()
        try:
            reply = self._client.stream_chat(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            self._trace.model_call_finished(
                call_number,
                error=exc,
                latency_ms=(time.monotonic() - started) * 1000,
            )
            raise
        self._trace.model_call_finished(
            call_number,
            reply=reply,
            latency_ms=(time.monotonic() - started) * 1000,
        )
        return reply
