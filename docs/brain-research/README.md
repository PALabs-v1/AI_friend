# Brain research (Brain V2)

An evidence-driven audit and refinement of the AI_friend cognitive
architecture. Baseline V1 is `main` at `dac8d0a`. Everything here was either
measured with the executable benchmark in `backend/evals/cognitive`, pinned by
a test, or is labelled as unverified with the experiment that would settle it.

## Headline results

| | Baseline V1 | Brain V2 | Evidence |
|---|---|---|---|
| Right memory in top 3 (production-like cell, held-out seeds) | **0.02** | **0.69** (+0.67 [0.65, 0.69] by scenario-level bootstrap, 1,146 wins / 1 loss) | `03-memory-retrieval.md` |
| Worst case over 9 embedding × history cells | 0.02 | 0.69 | E3 |
| Keyword traps in the top 3 | 0.31 | 0.02 | E3 |
| Retrieval latency, SQLite, 5,000 memories (p50 / p95) | 56.9 / 68.3 ms | 3.9 / 6.6 ms | latency harness |
| Valence runaway from mood 0.6 (turns saturated at +1.0 of 200) | 143 | 0 | `04-affect-dynamics.md` |
| Backend tests | 2,388 passed | 2,523 passed, 0 failed | `pytest` |

Also fixed (each with a test that fails on the old code): graph relations
never loading into retrieval (M-4), dropped retrieval-outage markers (M-7),
SQLite timestamp parsing that could blank all retrieval (M-9), barge-in
"stop" addressed to the wrong turn (V-1), unsanitised memories in the
proactive prompt (S-1), and an interrupted reply overwriting the previous
turn's stored reply (R2-2b). Five independent adversarial reviews and their
findings are in `01-problems.md`.

Not solved, with measured stakes and a plan: stale facts after a preference
change (M-5, E7 upper bound), the user's words never reaching the agent's
valence (A-1, estimator evaluation packaged for GPU), trust rising under
hostility (A-3).

## Documents

| File | Contents |
|---|---|
| `00-current-architecture.md` | Baseline V1 as the code runs it: topology, turn flow, memory, affect |
| `01-problems.md` | Problem register with severity, evidence and status (IDs cited by tests) |
| `02-capability-map.md` | Capability → V1 mechanism → measured status → V2 |
| `03-memory-retrieval.md` | Hypotheses, method, E1–E7 results, ablations, latency, limits |
| `04-affect-dynamics.md` | Long-run affect simulation and results |
| `05-brain-v2-architecture.md` | What changed, config, restart, rollback, failure behaviour |
| `06-rejected-approaches.md` | What was tried and rejected, with numbers |
| `07-future-research.md` | Ordered research queue with hypotheses and decision rules |
| `adr/ADR-001…003` | Decision records: retrieval, affect learning, barge-in |

## Reproduce everything (no services, no model, ~8 minutes on 4 cores)

```bash
cd backend
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
(cd crates/cognitive-rust && maturin build --release) && pip install target/wheels/cognitive_rust-*.whl
CI=1 python -m pytest tests/test_cognitive_bench.py tests/test_memory_ranking.py tests/test_brain_v2_regressions.py -q
PYTHONPATH=. python -m evals.cognitive memory --seeds heldout \
    --experiments E3_alternatives E4b_supersession_tau E6_hybrid_ablation E7_validity_value \
    --out /tmp/memory_heldout.json
PYTHONPATH=. python -m evals.cognitive affect --turns 200 --out /tmp/affect.json
PYTHONPATH=. python -m evals.cognitive latency --queries 60 --out /tmp/latency.json
PYTHONPATH=. python -m evals.cognitive.report memory /tmp/memory_heldout.json E3_alternatives
```

`CI=1` matters: `tests/conftest.py` otherwise force-exits and hides pytest's
summary and tracebacks. Model-dependent follow-ups (real embeddings, LLM ToM
valence) are in `backend/experiments/gpu/README.md`.

## Environment this was produced in

Claude Code cloud container, 4 vCPU, 15 GB RAM, Python 3.12, Rust stable.
Not available: Docker daemon, pgvector, Qdrant, Neo4j, Ollama, GPUs,
HuggingFace downloads. The consequences are stated where they matter:
synthetic embeddings (03 §Limits), the Postgres path tested against a mocked
connection only, and the affect estimator evaluation deferred to the GPU package.
