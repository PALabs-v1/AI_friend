"""Cognitive services, re-exported lazily.

The re-exports resolve on first attribute access (PEP 562) rather than at
package import. Importing any submodule runs this file first, and eager
imports here closed a cycle: app.state -> agent_state -> app.cognitive.goals
-> (this file) -> core -> app.state, half-initialised. Lazy re-exports keep
`from app.cognitive import CognitiveService` working without that edge.
"""

from importlib import import_module
from typing import Any

_EXPORTS = {
    "ActionService": ".action",
    "CognitiveService": ".core",
    "DecisionService": ".decision",
    "PerceptionService": ".perception",
    "ReflectionService": ".learning",
}

__all__ = [
    "ActionService",
    "CognitiveService",
    "DecisionService",
    "PerceptionService",
    "ReflectionService",
]


def __getattr__(name: str) -> Any:
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module, __name__), name)
    globals()[name] = value
    return value
