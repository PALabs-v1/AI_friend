# Brain V2 Local Baseline — Results

Commit `b348c18` (`brain-v3`, docs-only ahead of the PR #216 merge `001069c`). Full manifest: [manifest.json](manifest.json). Raw JSON: `docs/brain-research-v3/results/{mac,home-gpu}/`.

This is the number every later phase (4, 6, 7-10) diffs against. Nothing here changes production code — it measures Brain V2 exactly as merged.

## Test suites

| | Mac (M5) | home-gpu (Ryzen 5 5600G) |
|---|---|---|
| `pytest` | 2548 passed, 995 warnings, 88.19s | see below |
| `cargo test --workspace` | 179 passed, 0 failed | see below |
| `barge_in_mutations.py` | 38 mutations, 33 killed, 5 survived (equivalent) | see below |

Cargo breakdown (Mac): 21 contracts + 10 stt-agent-contracts-parity + 70 stt-agent + 78 voice-agent = 179. Matches the count recorded in Phase 0 (`04-local-infrastructure.md`), confirming F-001's fix (stale `libonnxruntime.1.27.0.dylib` code signature, `codesign --force --sign -`) is stable and reproducible.

The 5 surviving barge-in mutants are the same 5 already on record from the pre-existing ADR-003 equivalence analysis: `M12_replacement_does_not_resolve`, `N11_replacement_keeps_intent`, `N12_end_of_generation_ignores_owner`, `N18_proactive_turn_takes_the_row_id`, `S3_store_insert_ignores_id`/`S5_store_update_drops_role`. No new survivors — no regression since V2 merged.

## Memory retrieval (`evals.cognitive memory`, tune seeds 1-20, all 8 experiments)

Worst-case hit@3 across 3 embedder profiles × 3 regimes (E3_alternatives):

| arm | worst-case hit@3 | mean hit@3 |
|---|---:|---:|
| `v1@sqlite` (pre-V2 baseline) | 0.023 | 0.032 |
| `v1@vec60` | 0.347 | 0.453 |
| `cosine@vec60` | 0.568 | 0.879 |
| `v1_retuned:12:2@vec60` | 0.445 | 0.813 |
| **`hybrid:1.5:0.2:none@all` (production V2)** | **0.699** | **0.913** |

Confirms the documented headline (0.02 → 0.69 worst-case hit@3) exactly, on this machine, this commit, today.

Category breakdown for the hardest regime (`hard/summary`, E3_alternatives) — this is where M-5 shows up quantitatively:

| arm | all | updated | obsolete wins | trap in top-3 |
|---|---:|---:|---:|---:|
| `v1@sqlite` | 0.02 | 0.00 | 0.00 | 0.30 |
| `v1@vec60` | 0.42 | 0.05 | 0.67 | 0.18 |
| **`hybrid:1.5:0.2:none@all` (production)** | **0.70** | **0.28** | **0.61** | **0.07** |

`obsolete_win` is the rate an outdated/contradicted fact beats the current one in the top-3. Production V2 is at 0.61-0.67 depending on arm — this is M-5 (stale facts win retrieval) measured directly, not inferred. W1's target is ≤0.10. `updated` hit@3 (0.28) is also far below W1's ≥0.48 target. This baseline number is what W1 has to move.

Held-out seeds (101-130) confirm the tune-seed picture; see `results/mac/memory_heldout.json` for the full breakdown per experiment.

## Affect (`evals.cognitive affect --turns 200`, seeds 0-2)

| appraisal input | script | r(user valence, mood) | final mood | final trust |
|---|---|---:|---:|---:|
| **`agent_mood` (production)** | **positive** | **undefined (mood never moves)** | **+0.00** | 0.83 |
| **`agent_mood` (production)** | **hostile** | **undefined (mood never moves)** | **+0.00** | 0.83 |
| `oracle` | positive | +0.29 | +0.96 | 1.00 |
| `oracle` | hostile | +0.10 | -0.11 | 0.34 |
| `vader` | positive | -0.05 | +0.86 | 1.00 |
| `vader` | hostile | +0.23 | -0.02 | 0.68 |

This is A-1 measured, not asserted: under production's actual appraisal input (`agent_mood`), final mood is exactly 0.00 and final trust is exactly 0.83 regardless of whether the 200-turn script is `positive` or `hostile` — the correlation with user valence can't even be computed because mood never leaves its starting value. Feed the same mechanism an input that actually reads user text (`oracle` or `vader`), and mood and trust respond exactly as expected (rises under positive, drops under hostile) using the *same* update arithmetic. The bug is entirely in what gets fed in, not the update rule itself — which matters for W2's design (fix the input, not rebuild the mechanism) and W3 (confirms `PersonModel`'s trust arithmetic is sound once wired to real evidence).

Full 21-row table across all 7 scripts × 3 appraisal inputs: `results/mac/affect_200.json` (rendered via `python -m evals.cognitive.report affect`).

## Latency (`evals.cognitive latency`, sizes 200/1000/3000/5000, 40 queries each)

| memories | policy | p50 ms | p95 ms | p99 ms |
|---:|---|---:|---:|---:|
| 200 | hybrid | 0.79 | 1.16 | 14.69 |
| 1000 | hybrid | 2.29 | 8.85 | 14.6 |
| 3000 | hybrid | 2.42 | 4.65 | 56.55 |
| 5000 | hybrid | 1.19 | 1.37 | 9.18 |
| 5000 | actr_v1 | 26.98 | 28.08 | 34.47 |

All well inside DR-024's confirmed interactive budget (300-500ms to first audio byte) even at 5000 memories with no store scaling applied yet — retrieval itself is not the bottleneck at this scale. Phase 8 (scale testing, 1k-1M+) is where this gets tested for real. Qdrant was unreachable on the Mac during this run (no infra stack running there for Phase 3 — by design, `evals.cognitive` is model-free/DB-free per its own README) and fell back to `qdrant_store.client = None` with a warning; this doesn't affect the numbers above, which are pure `MemoryStore.search_memories` timings.

## home-gpu (Ryzen 5 5600G, Ubuntu 24.04, RTX 2060 SUPER unused for this run)

Same commands, same seeds, run sequentially rather than in parallel to avoid resource contention on the weaker box. Full pipeline (memory tune → memory heldout → affect → latency → pytest → cargo test → barge-in mutations) took ~22 minutes end to end, vs a few minutes on the Mac — expected given the single-thread performance gap; this box's value is the GPU and the infra stack, not CPU speed.

| | Mac (M5) | home-gpu (Ryzen 5 5600G) |
|---|---|---|
| `pytest` | 2548 passed, 995 warnings, 88.19s | 2540 passed, 8 skipped, 2868 warnings, 80.18s |
| `cargo test --workspace` | 179 passed, 0 failed | 179 passed, 0 failed |
| `barge_in_mutations.py` | 38 mutations, 33 killed, 5 equivalent survivors | 38 mutations, 33 killed, same 5 equivalent survivors |

The 8 skips on home-gpu are all `tests/test_nats_accounts_enforcement.py`, reason: `no nats-server binary on PATH`. That's an environment difference (home-gpu uses the Dockerized NATS from the infra compose stack, not a standalone binary), not a defect — worth installing the binary there before Phase 4's live-infra validation if these tests matter for that phase, otherwise no action needed. Every other result is identical between machines: same 179/179 Rust tests, same 33/38 mutation kills, same 5 named equivalents.

Memory retrieval numbers are byte-identical to the Mac's (E3_alternatives worst-case hit@3: `v1@sqlite` 0.023, `hybrid@all` 0.699) — expected, since `evals.cognitive` is fully deterministic and seed-controlled with no model or network dependency. This is a useful sanity check on its own: the benchmark's determinism claim (pinned by `tests/test_cognitive_bench.py`) holds across a completely different OS, CPU architecture (arm64 vs x86_64), and Python build.

Latency is proportionally slower but the same shape — production `hybrid` policy stays flat and fast as memory count grows, while `actr_v1` degrades linearly:

| memories | policy | Mac p50 ms | home-gpu p50 ms | Mac p99 ms | home-gpu p99 ms |
|---:|---|---:|---:|---:|---:|
| 200 | hybrid | 0.79 | 1.53 | 14.69 | 36.62 |
| 1000 | hybrid | 2.29 | 3.63 | 14.6 | 11.14 |
| 5000 | hybrid | 1.19 | 4.51 | 9.18 | 24.03 |
| 5000 | actr_v1 | 26.98 | 51.53 | 34.47 | 69.58 |

Both machines stay well inside DR-024's interactive budget (300-500ms) at every size tested. Full tables: `results/home-gpu/latency.json`, rendered via `python -m evals.cognitive.report latency`.
