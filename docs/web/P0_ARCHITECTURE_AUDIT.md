# P0 Architecture Audit — PA_Agent Web Crypto

**Audited baseline:** main at a9dade1e2c08ea92624231ed464bf91a452b451f  
**Target:** public web application for Binance Spot + USDⓈ-M Futures; monitoring and alerts only.

## Findings

### Reusable analysis core

The intended reuse boundary is:

- pa_agent/orchestrator/two_stage.py: TwoStageOrchestrator.submit() executes Stage 1, strategy routing, Stage 2, normalization, validation, and record creation.
- pa_agent/ai/prompt_assembler.py: builds the original Stage 1 and Stage 2 prompts.
- pa_agent/ai/router.py and pa_agent/ai/pattern_routing.py: strategy file routing.
- pa_agent/ai/market_features.py, pa_agent/ai/kline_features.py, pa_agent/indicators/: deterministic market and candle features.
- pa_agent/ai/decision_nodes.py, pa_agent/ai/decision_tree.py: deterministic decision nodes and gate logic.
- pa_agent/ai/stage1_normalizer.py, pa_agent/ai/stage2_normalizer.py, pa_agent/ai/json_validator.py: output normalization and validation.
- pa_agent/data/base.py and pa_agent/data/snapshot.py: canonical KlineBar, KlineFrame, and closed-candle snapshot logic.
- prompt_engineering/: strategy and decision-tree source material.

The Web implementation must adapt market data into these canonical types rather than fork the Price Action calculations.

### Integration constraints

1. TwoStageOrchestrator.submit() is synchronous and accepts a KlineFrame, a CancelToken, and event/stream callbacks. Run it outside the ASGI event loop (worker thread or dedicated analysis worker).
2. The orchestrator has mutable per-run state (provider fallback state and pending metadata). Create a fresh orchestrator and AI client per analysis job; never share one instance across concurrent jobs.
3. KlineFrame.bars uses newest-first ordering. K1 is the newest closed candle. Reuse build_analysis_frame() / take_snapshot_from_bars() so the forming candle is excluded from AI input.
4. pa_agent/main.py imports PyQt6 at module import and starts a desktop QApplication; it is not a Web entry point. Do not import it from FastAPI.
5. AppContext.bootstrap() initializes desktop-oriented resources and should not be used by the Web service. Construct Web dependencies explicitly.
6. PendingWriter writes local JSON files under the project tree. A multi-visitor Web service needs a database-backed record repository; do not use shared local pending files as tenant storage.
7. ExperienceReader reads the local experience directory. Mount the strategy and experience sources read-only initially; tenant-generated records must not be mixed into the global experience corpus.

### Security finding

The desktop settings implementation is not suitable for a public service:

- pa_agent/config/settings.py::save_settings() serializes Settings.model_dump(), including provider.api_key.
- The desktop settings UI assigns the entered key to provider.api_key.
- tests/unit/test_settings_round_trip.py::test_api_key_present_on_disk explicitly expects a plaintext key in the JSON file.
- The Web backend must not load config/settings.json, expose provider settings, or use desktop credential auto-discovery connectors.

For Web, read the AI provider secret only from server environment/secrets. Never return it to the browser, persist it in analysis records, or log it. Before production, add secret masking tests and remove reasoning/raw provider payloads from public API responses.

### Dependency finding

The root pyproject.toml includes PyQt6, pyqtgraph, MT5 and non-crypto data-source dependencies. Installing all root dependencies in a Linux Web container would pull in desktop and irrelevant data-source packages. The Web Docker image installs the project editable with --no-deps, then installs an explicit minimal Web requirements file. Keep desktop dependency behavior unchanged until a separate, tested packaging refactor is approved.

### Binance adapter boundary

The Web adapter normalizes Binance REST responses into DTOs and can convert closed candles into the existing KlineBar type. Spot and USDⓈ-M Futures are separate market types and separate REST clients. Public market-data endpoints do not require a Binance trading key.

### Current implementation scope

This first commit establishes P0 audit + P1 REST market-data foundation + a basic Vue market screen. It does not yet claim that the original AI analysis pipeline, persistent monitoring workers, PostgreSQL/Redis, guest quotas, or opportunity lifecycle are production-ready. Those are follow-on phases.


### Linux import boundary correction

The first Web CI run exposed one additional desktop side effect: importing any pa_agent.util submodule executes pa_agent/util/__init__.py, which eagerly imported EventBus and therefore PyQt6. The package initializer now uses lazy exports so pure analysis utilities can be imported without loading Qt. Desktop callers that explicitly request EventBus or logging helpers retain the same public import names. A subprocess regression test verifies that importing pa_agent.util.price_tick does not import PyQt6.
