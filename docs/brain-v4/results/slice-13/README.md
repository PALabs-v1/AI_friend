# Slice 13: ModelRole routing on every LLM call

Written for engineers adding or reviewing LLM calls. Outcome moved: no `generate`, `generate_stream` or `describe_image` call in `backend/app` can ship without a named `ModelRole`, and every call leaves its role in the trace.

## What changed

- `LLMClient` protocol and the Ollama, Anthropic and Claude Code clients take an optional `role: ModelRole | None = None`. Optional at the client so evals and test fakes keep working; required at every call site by the gate test.
- `backend/app/llm/role_trace.py::trace_role` emits a `MEASURE_TRACE` event `component=llm event=role_call client= role= model=` (role `UNROUTED` when none). Ollama also writes `role` into each `request_provenance` entry.
- 15 call sites in 9 files pass a role. The plan said 16; the sixteenth was a comment line.
- `backend/tests/test_model_role_routing.py`: AST walk over `backend/app` (outside `app/llm`) that fails on any call without `role=ModelRole.<member>`, plus parametrized cases proving the gate rejects unrouted, string and unknown-member roles, plus a trace test.

## Role assignment

| Call site | Role | Why |
|---|---|---|
| `cognitive/decision.py` intent and ToM extraction | INTERPRETATION | turns user input into structured intent |
| `cognitive/appraisal.py` appraisal JSON | INTERPRETATION | reads an event into appraisal dimensions; the kernel owns the appraisal result |
| `cognitive/learning.py` fact extraction | INTERPRETATION | structured facts from conversation |
| `cognitive/learning.py` identity update | EVALUATION | judges whether traits change |
| `cognitive/learning.py` consolidation | COMPRESSION | summarises history |
| `cognitive/subconscious.py`, `agents/subconscious_agent.py` (thought, dream) | CANDIDATE_GENERATION | proposals the kernel may discard |
| `cognitive/action.py` (4 streams) | REALIZATION | the reply text the user hears |
| `vision/appraisal.py` | INTERPRETATION | image to description |
| `persona/compiler.py` | INTERPRETATION | freeform text to structured profile |
| `api/persona.py` preview reply | REALIZATION | in-character reply |

These are judgment calls from each prompt's job. None changes behaviour: the role is not read by any client except to record it.

## Critic round 1 (cold, one round) and what was done

| Finding | Action |
|---|---|
| Strict test fakes reject `role=` (HIGH) | Added `role=None` to the fakes in `test_eval_harness.py`, `test_context_assembly.py`, `test_endocrine.py`, `test_pr66_review_fixes.py`; updated the `describe_image` assertion in `test_vision.py` |
| Trace gaps (MED) | `trace_role` now runs before the `MOCK_LLM_TEXT` guards and in both real `describe_image` paths; mock-mode runs show the same role events as live runs |
| Gate blind spots (MED) | Gate now also fails on a bound method used without a call, `getattr(x, "generate")`, and a file whose `ModelRole` is not imported from `app.llm.model_roles` |
| Thin trace tests (MED) | Per-client tests for Ollama, Anthropic (mock mode) and Claude Code; mutation check: deleting the trace call in any client, or the role at one call site, fails the suite |
| Diff hygiene (LOW) | Touched test files formatted; the extra `appraisal.py` re-wraps are from the repo's formatter and change no behaviour |

## Not covered

- Embeddings (`memory_store.py` raw HTTP to `/api/embed`) are model calls with no role; they are outside the `LLMClient` surface and out of scope here.
- Scripts, evals and `main.py` call the real clients without a role and log `UNROUTED`; the gate covers `backend/app` only.
- Aliasing and `getattr` access are now flagged by the gate; passing a client to code outside `backend/app` that calls it is not.
- Role capability negotiation (`ProviderCapabilityNegotiator`) is still not called at runtime; this slice only names the role. Enforcing that a model meets a role's requirements is a behaviour change and belongs to a later decision.
