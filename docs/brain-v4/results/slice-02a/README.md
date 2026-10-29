# Slice 2a result: baseline harness and no-LLM defect baselines

Written for Aniket and for whoever fixes the defects below. Preregistration: `docs/brain-v4/prereg/slice-02.md` (written before the first run; file times in the working tree show it first, and the commit holds both). Raw data: `defect_baselines.json` in this folder.

## Instrument check (the measure that could fail)

Collected at revision `b525b2f6` (app code unchanged since). Two runs in separate processes, one from `backend/` and one from a different working directory with a stripped environment, produced byte-identical files: `result_sha256 = 3fd24f76ce940da78f51ec056fe4b116b804d67ef6331c87c3a60652d210855b`, `scenario_sha256 = b4cb324e8abc84885697d7206d75125c1d2dfc04acecbe04aa9cf7e965c9c739`, Python 3.13, appraisal backend `cognitive_rust`. The same collection with the Rust extension blocked (pure-Python mirror) gives the same digest. The noise band was zero by preregistration and the observed difference is zero.

What this check does not cover: the digest is not portable across checkouts that differ in source layout (it holds `file::function` names), and both runs share one machine. A first draft of this harness used a reduced rig; the critic round showed its numbers differed from a real `CognitiveService`, so the baselines now drive the real service through the BrainBench `architecture_only` adapter.

Reproduce, from `backend/`: `python -m evals.brainbench.baseline.defects --out DIR`. Gate tests: `pytest tests/test_bare_model_arm.py`.

## What the baselines show

Scope for the first two rows: System 1 (heuristic) appraisal, text-only input, null model. System 2 LLM appraisal (`apply_semantic_appraisal`, `pipeline.py:1025`), sensory perception (`apply_sensory_perception`, `core.py:399`) and voice cues (`appraisal.py:233-241`) are other writers of mood or trust that these baselines do not exercise.

| Defect (draft section 9) | Baseline | Reading |
|---|---|---|
| Mood is fed its own value (`pipeline.py:809`, state update at `:287`) | From start mood 0.0 the trajectory is 0.0 on every turn for every text. From 0.5 it falls by a constant factor of 0.944 per turn. Real and each of three shuffled input orders differ by 0.0 at every turn. Goal congruence returned has 1 distinct value per bias across 12 different texts | On this path mood is a function of its prior value alone: the appraisal returns the stored mood as goal congruence, so mood only decays and the user's words have no effect. Whether the model-backed and sensory paths change this is measured in 2b, not here |
| Hostile text may raise integrity (`agent_state.py:1815-1816`) | Hostile, neutral and supportive input give identical rows for all three trust dimensions: norm alignment 1.0 on every turn, integrity climbing 0.17 per turn from 0.38 to 1.0 in 4 turns | Integrity trust cannot tell abuse from kindness on this path. The two immutable boundaries in `IMMUTABLE_CORE` are about the agent's own conduct, so user hostility never lowers norm alignment |
| Constant behavioral signal (`pipeline.py:276-280`) | The only `evaluate_outcome` call passes the literal `0.5`; weight 0.2; outcome variance over the fixed grid is 0.14 and the behavioral term adds none | A fifth of the outcome score never varies. The zero share is true of any constant; the finding is the literal at the call site |
| Baseline PAD has no learning writer | Assignment sites: `_apply_redis_state`, `_apply_sqlite_state`, `_hydrate_locked`, `apply_external_state` and the property setter, all in `agent_state.py` (loading and restore). Constructor seed: `StateService.__init__`. Store-write strings: `_write_state_row` (persisting the current value) and `subconscious_agent.py::_on_state_broadcast` (a Cypher `SET` of the broadcast value) | Nothing in `backend/app` computes a new baseline during a run; the sites above move an existing value in or out. The test pins this exact list, so a new writer fails it |
| `state.subconscious` has no reader | 1 Python publisher (`subconscious_agent.py`), 0 Python subscribers, 0 Rust references, aliases and NATS wildcards considered | Matches claim #34. Scan scope is `backend/app` and `backend/crates`, not the frontend or scripts |
| `telemetry.reflection` has no reader | 1 publisher (`core.py`), 0 subscribers, same scope | Matches claim #35. This subject carries a timing metric (`duration_ms`, `episodes_count`), so it is not evidence about whether monologue or dream output is read |

## Bare-model arm (D-021)

`backend/evals/brainbench/bare_model.py`: persona text plus the whole history, one `generate` call with `ModelRole.REALIZATION`, nothing else. Gate tests prove, from source and from a subprocess `sys.modules` check, that it loads no `app.cognitive`, `app.state` or `app.agents` code; that history goes in order and untruncated; and that each turn is one call. `switchboard.resolve_arm("bare-model")` now says the arm runs through `BareModelArm` instead of reporting an unknown arm. Not run against a model in 2a.

## Not covered

- No model-backed arm ran. The noise band for those is an open owner decision, proposed in the prereg.
- Memory writes try an embedding call; the rig points it at a closed local port so the result does not depend on whether Ollama is running on the measuring machine.
- Draft section 9 rows not baselined here because they need a model or more design: persona review queue, stale-fact retrieval, AI disclosure probes, authored biography, persona reset (static facts are in the repo-intel ledger, #20), plan verifier and calibration call traces.
