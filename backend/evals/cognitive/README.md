# Cognitive benchmark (`python -m evals.cognitive`)

Measures cognitive *mechanisms* (memory ranking, affect dynamics, retrieval
latency) deterministically, with no model, network or database server. It is
the benchmark behind `docs/brain-research/`. The model-level behavioural
harness is the parent package `evals/`, which needs Ollama.

```bash
cd backend
PYTHONPATH=. python -m evals.cognitive memory --seeds tune --experiments E1_baseline_paths --out /tmp/e1.json
PYTHONPATH=. python -m evals.cognitive memory --seeds heldout --out /tmp/all_heldout.json   # every experiment
PYTHONPATH=. python -m evals.cognitive affect --turns 200 --out /tmp/affect.json
PYTHONPATH=. python -m evals.cognitive latency --sizes 1000 5000 --out /tmp/latency.json
PYTHONPATH=. python -m evals.cognitive.report memory /tmp/e1.json E1_baseline_paths          # markdown table
```

| Module | Role |
|---|---|
| `scenarios.py` | seeded multi-week user histories (`verbatim` / `unique` / `summary` regimes, optional distress share) with ground-truth probes |
| `corpus.py` | facts (each with a zero-overlap paraphrase question and a keyword question) and small talk |
| `embedder.py` | `LatentTopicEmbedder` (controlled cosine structure; profiles `nomic_like`, `minilm_like`, `hard`) and `PrecomputedEmbedder` (real vectors from elsewhere) |
| `harness.py` | drives the real `MemoryStore` (SQLite + Rust kernel) through a scenario |
| `lab.py` | the same ranking decomposed into candidate generator × scoring policy, for ablation; parity with production is a test |
| `memory_experiments.py` | named experiments E1–E7, tune/held-out seed sets, parallel runner, paired statistics |
| `metrics.py` | hit@k, MRR, obsolete-wins, trivia/trap intrusion, bootstrap CIs, paired deltas |
| `affect_sim.py` | long-run replay of the real appraisal → state update path under scripted conversations |
| `latency.py` | p50/p95/p99 of real `MemoryStore.search_memories` per policy and store size |
| `report.py` | JSON → markdown tables used in the docs |

Guarantees pinned by `tests/test_cognitive_bench.py`: paraphrase questions
share no content word with their fact; scenarios are deterministic per seed;
embedder profiles hit their target cosines; the lab equals production for
both ranking policies; headline regression thresholds hold.

Adding an experiment: register arms in `memory_experiments.experiment_definitions`
(policy names are parsed by `_policy`, generators come from `GENERATORS`), run
it on tuning seeds, choose, then re-run on held-out seeds before claiming anything.
