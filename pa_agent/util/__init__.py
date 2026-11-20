"""PA Agent utility package with lazy exports.

Importing a submodule such as pa_agent.util.price_tick must not initialize the
desktop Qt event bus. Desktop-only utilities are imported only when requested.
"""
from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "CancelToken",
    "OrchestratorEvent",
    "EventBus",
    "configure_logging",
    "update_api_key",
]


def __getattr__(name: str) -> Any:
    if name in {"CancelToken", "OrchestratorEvent"}:
        module = import_module("pa_agent.util.threading")
        value = getattr(module, name)
    elif name == "EventBus":
        module = import_module("pa_agent.util.event_bus")
        value = module.EventBus
    elif name in {"configure_logging", "update_api_key"}:
        module = import_module("pa_agent.util.logging")
        value = getattr(module, name)
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    globals()[name] = value
    return value
