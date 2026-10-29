"""Trace record for the role a model call was made under (V4 slice 13).

Every call site passes a `ModelRole`; each client reports it here so a
`MEASURE_TRACE` run can join a call to the job it served. No prompt text is
recorded. Free when tracing is off.
"""

from __future__ import annotations

from app.llm.model_roles import ModelRole
from app.measure_trace import trace


def trace_role(client: str, role: ModelRole | None, model: str | None) -> None:
    trace(
        "llm",
        "role_call",
        client=client,
        role=role.value if role is not None else "UNROUTED",
        model=model,
    )
