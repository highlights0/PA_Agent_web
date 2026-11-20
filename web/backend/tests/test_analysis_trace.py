from __future__ import annotations

from types import SimpleNamespace

from app.services.analysis_trace import AnalysisTrace, TracingClient, TracingValidator


class Ok:
    def __init__(self, obj):
        self.obj = obj


class ValidationError:
    category = "b"
    missing_fields = ["direction"]
    invalid_fields = ["gate_result"]
    parse_position = "12:4"
    message = "missing required field"
    raw_text = "THIS RAW MODEL TEXT MUST NOT BE EXPOSED"


class FakeValidator:
    def __init__(self, result):
        self.result = result

    def validate(self, stage, content, **kwargs):
        return self.result

    def normalize_parsed(self, *args, **kwargs):
        return {}


class FakeClient:
    def __init__(self):
        self._settings = SimpleNamespace(model="test-model")

    def stream_chat(self, *args, **kwargs):
        return SimpleNamespace(
            content='{"direction":"bullish"}',
            reasoning_content="private reasoning must not be traced",
            latency_ms=20,
            usage=SimpleNamespace(
                prompt_tokens=100,
                cached_prompt_tokens=20,
                completion_tokens=30,
                total_tokens=130,
            ),
        )

    def update_provider(self, settings):
        self._settings = settings


def test_trace_records_real_orchestrator_order_and_prompt_metadata_only():
    trace = AnalysisTrace()
    trace.orchestrator_event(SimpleNamespace(name="Stage1Started"))
    trace.prompt_ready(
        "stage1",
        "# System Prompt\nsecret system body should not be exposed",
        "# User Prompt\nprivate user body should not be exposed",
    )
    trace.orchestrator_event(SimpleNamespace(name="Stage1Retry"))
    trace.orchestrator_event(SimpleNamespace(name="Stage1Done"))
    trace.strategy_files_ready(["上涨通道交易策略.txt"])
    trace.orchestrator_event(SimpleNamespace(name="Stage2Started"))
    trace.orchestrator_event(SimpleNamespace(name="Stage2Done"))

    events = trace.snapshot()
    types = [event["type"] for event in events]
    assert types.index("stage_started") < types.index("prompt_ready")
    assert types.index("validation_retry") < types.index("stage_done")
    assert "strategy_routed" in types
    prompt = next(event for event in events if event["type"] == "prompt_ready")
    assert prompt["data"]["system_chars"] > 0
    assert prompt["data"]["system_headings"] == ["System Prompt"]
    assert "private user body" not in str(events)
    assert "secret system body" not in str(events)


def test_tracing_validator_records_validation_failures_and_success_without_raw_text():
    trace = AnalysisTrace()
    failed = TracingValidator(FakeValidator(ValidationError()), trace)
    failed.validate("stage1", "untrusted raw text")
    passed = TracingValidator(FakeValidator(Ok({"direction": "bullish"})), trace)
    passed.validate("stage1", '{"direction":"bullish"}')

    validation = trace._validation_events
    assert len(validation) == 2
    assert validation[0]["status"] == "failed"
    assert validation[0]["missing_fields"] == ["direction"]
    assert validation[0]["attempt"] == 1
    assert validation[1]["status"] == "passed"
    assert validation[1]["attempt"] == 2
    assert "raw_text" not in str(trace.snapshot())
    assert "THIS RAW MODEL TEXT" not in str(trace.snapshot())


def test_tracing_client_records_usage_and_never_records_reasoning_content():
    trace = AnalysisTrace()
    trace.orchestrator_event(SimpleNamespace(name="Stage1Started"))
    client = TracingClient(FakeClient(), trace)
    reply = client.stream_chat([], on_reasoning_token=lambda _chunk: None)

    assert reply.content
    events = trace.snapshot()
    completed = next(event for event in events if event["type"] == "model_call_completed")
    assert completed["data"]["model"] == "test-model"
    assert completed["data"]["total_tokens"] == 130
    assert completed["data"]["output_text"] == '{"direction":"bullish"}'
    assert "private reasoning" not in str(events)
    assert "reasoning_content" not in str(events)


def test_trace_redacts_api_key_like_values_from_event_details():
    trace = AnalysisTrace()
    trace.emit(
        "provider_error",
        stage="stage1",
        title="Provider error",
        detail="Authentication failed api_key=sk-abcdefghijklmnopqrstuvwxyz",
    )
    rendered = str(trace.snapshot())
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in rendered
    assert "api_key=***" in rendered


def test_model_output_redacts_api_key_like_strings():
    trace = AnalysisTrace()
    call = trace.model_call_started("stage1", "test-model")
    reply = SimpleNamespace(
        content='{"error":"api_key=sk-abcdefghijklmnopqrstuvwxyz"}',
        usage=SimpleNamespace(
            prompt_tokens=1,
            cached_prompt_tokens=0,
            completion_tokens=1,
            total_tokens=2,
        ),
    )
    trace.model_call_finished(call, reply=reply)
    rendered = str(trace.snapshot())
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in rendered
    assert "api_key=***" in rendered
