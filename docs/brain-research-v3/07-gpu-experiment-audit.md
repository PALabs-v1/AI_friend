# GPU experiment package audit (Phase 4a)

Independent audit of `backend/experiments/gpu/` before running it for real. Written and
committed before reading Codex C2's independent cold audit (see `codex-log.md`), so the two
are provably independent.

**Codex C2 caught three real things this audit missed** (agreed with everything else,
including the collision fix once applied): my arm count in item 5 was wrong (17, not the
actual 27); `embed_st()` reconstructs the sentence-transformers model from scratch on every
single (regime, seed) call — up to 60 reloads per model in the default sweep — which I never
checked, only that `PrecomputedEmbedder` itself didn't re-embed per arm; and H-R3's decision
rule needs a *paired* delta (prefixed − unprefixed on the same seed/probe), which running the
script twice with different `--prefix` values cannot produce — each run only pairs its arms
against `v1@sqlite` internally, never against the other run. All three are fixed below (items
5, 6 updated; new fixes: model caching in `embed_st`, a `--compare-prefix` flag). Also caught:
the ToM docstring's "already runs on every turn" overclaims past the deterministic/greeting
short-circuit in `decision.py:402` — fixed in `tom_valence_affect.py`'s docstring.

## 1. Does the synthetic scenario generator generalize to real embeddings?

Not fully answerable yet — `build_history` (evals/cognitive/scenarios.py) is a hand-authored
corpus (`corpus.py`) designed to stress a *lexical/cosine* ranker (verbatim/unique/summary
regimes, paraphrase vs keyword queries with a proven zero-content-word-overlap invariant for
paraphrase). It was never validated against `lifesim` (Phase 5, not built yet), so the honest
answer is: the retrieval *mechanism* conclusions (hybrid ≫ V1, weight plateau) generalize fine
because they're about ranking arithmetic, not about realism of the text distribution. Whether
the *absolute* hit@3 numbers match real long-horizon human-robot conversation is an open
question that Phase 5's lifesim is specifically built to answer. Not a blocker for running
this package now — it's the reason the plan runs GPU experiments before lifesim exists at all
(mechanism validation) and again after (realism validation via BrainBench).

## 2. `PrecomputedEmbedder` keying collision (`real_embedding_retrieval.py:125-144`)

**Real risk, currently not triggered.** `build_embedder()` builds one dict keyed by raw
(unprefixed) text, built from `dict(zip(docs + queries, vectors))` where `docs` and `queries`
are each deduplicated separately (`sorted({...})`) but never deduplicated *against each other*.
If a document's text and a query's text were ever identical, `raw` would embed that text twice
(once with `"search_document: "`, once with `"search_query: "` under `--prefix nomic`), and the
later dict insertion (query) would silently win — any later doc-side use of that key would read
the query-prefixed vector.

Verified empirically rather than reasoned abstractly:

```
$ python -c "... build_history(seed, regime) for every regime x seed in tune+heldout ..."
0/150 scenarios have doc==query exact text collisions
```

Checked all 3 regimes × 20 tune seeds × 30 held-out seeds = 150 scenarios. Zero collisions —
the hand-authored corpus (`kw`/`para` queries are always phrased differently from `text`, per
`corpus.py`'s own docstring and `tests/test_cognitive_bench.py`'s overlap tests) happens to
avoid this by construction. **Fix applied**: added a gate test asserting this invariant
(`tests/test_gpu_experiments.py::test_no_doc_query_text_collision`) so a future corpus edit
can't silently reintroduce it, plus a loud `assert` in `build_embedder()` itself so a real run
fails fast with a clear message instead of silently corrupting results.

## 3. ToM stage-ordering caveat (`tom_valence_affect.py` vs `app/cognitive/pipeline.py`)

**Confirmed real, and it matters for what the experiment result can license.** The real
pipeline computes stages in this order per turn (`pipeline.py`, `stage_times` keys):
stage 4 appraisal (`self.appraisal.appraise(...)`) → stage 5 state update
(`self.state.update_from_appraisal(...)`) → stage 6 decision, which is where
`DecisionService._classify_intent_and_goal` runs and populates `event.metadata["tom_inferences"]`
(`decision.py:702-707`).

So in production, `inferred_valence` for turn N is computed *after* turn N's appraisal and
mood/trust update already happened. Adopting `inferred_valence` as appraisal's goal-congruence
input, as ADR-002's follow-up proposes, is not a config flip — it needs either (a) reordering
stage 6's classification ahead of stage 4 (feasible, since classification doesn't depend on
appraisal output, only on the event's text), or (b) accepting a one-turn lag (this turn's
appraisal uses last turn's inferred valence).

This does **not** invalidate the experiment's accuracy measurement (Pearson r, MAE, sign
agreement) — `tom_valence_affect.py` calls the classifier directly against hand-labelled
messages, independent of pipeline order, so those numbers are valid evidence about
classifier quality on their own terms. It only means: if the ToM decision rule (r≥0.8, sign
agreement≥0.9, hostile-script trust≤0.5) passes, W2's implementation task is "reorder stage 6
before stage 4, or explicitly accept and document a one-turn lag" — not "wire an existing
field." Recorded here so W2 doesn't rediscover this the hard way after the experiment result
is already in hand.

## 4. VRAM (RTX 2060 SUPER, 8GB)

All 7 planned embedding models are small (22M-600M params); the largest, Qwen3-Embedding-0.6B,
is well under 2GB even unquantized in fp32, let alone fp16. `--backend ollama` and
`--backend st` each run one model per process invocation and exit when done, so there's no
cross-run VRAM accumulation risk — each script run gets a clean CUDA context. No code change
needed. One real constraint found while checking this: **torch and sentence-transformers were
not installed on home-gpu** (Phase 0 only installed the base Python/Rust toolchain, not the
optional GPU-experiment extras) — fixed as part of this phase's setup, see `04-local-infrastructure.md`
update.

## 5. Expected runtime / redundant embedding calls

**Partially right, and Codex C2 found the real issue.** `ARMS` is actually **27 arms** (7
explicit + `HYBRID_WEIGHT_GRID`'s 4×5=20 combinations), not the 17 I first counted — corrected
here. `build_embedder()` does compute every document/query vector exactly once per (regime,
seed) — before the arm loop — and `run_lab` only does dict lookups via `embedder.embed(...)`
after that, never a fresh model call per arm; that part of my original finding holds. But
**`embed_st()` reconstructed the `SentenceTransformer` from scratch on every `build_embedder()`
call** — up to `3 regimes × 20 seeds = 60` model loads per sentence-transformers model in the
default sweep, something I didn't check because I was only looking at the embedder's per-arm
behavior, not the model's per-scenario behavior. **Fixed**: `embed_st()` now caches the loaded
model in a module-level dict keyed by model id, so it loads once per process and every
subsequent call reuses it. `--backend ollama` was never affected (Ollama keeps the model
resident server-side regardless of client behavior).

Wall-clock estimate, now that the fix is in: at Ollama's typical ~50-150ms/text on this GPU for
a 274MB model like nomic-embed-text, 60 batches of ~300 texts ≈ 60 × ~20s ≈ 20 minutes per
embedding model for the full tune-seed sweep; sentence-transformers models are now loaded once
and encode via batched GPU matrix ops, so they should be faster than that once warm. Confirmed
against a live timed smoke run before committing to the full sweep — see
`04-local-infrastructure.md`.

## 6. Output schema compatibility with `evals.cognitive.report`

Cell keys here are `f"{args.backend}:{args.model}"` paired with a regime (e.g.
`ollama:nomic-embed-text/verbatim`) via the same tuple-join `mx.summarize` uses everywhere.
I called this "not a bug, minor" because the inner shape matches one experiment's `results`
field exactly. **Codex C2 was right to push back harder than that**: run
`python -m evals.cognitive.report memory <out> <experiment>` against this script's raw output
literally as the README told you to, and it throws `KeyError: 'experiments'` — the top-level
`report` dict here has no `"experiments"` key at all, so the command in the original README
would fail for anyone who actually tried it, not just look "minor." **Fixed**: the README now
shows the correct one-line wrap (`{"experiments": {"x": report}}`) before calling the renderer,
and states plainly that the output is the inner shape, not the full command's shape.

## 7. Are H-R1/H-R2/H-R3/ToM rules still well-specified?

Checked `HYBRID_WEIGHT_GRID` (`evals/cognitive/memory_experiments.py:175-179`):
`w_lex ∈ {0.0, 0.5, 1.0, 2.0}`, `w_act ∈ {0.0, 0.15, 0.3, 0.6, 1.0}`. H-R2 describes the
plateau as "w_lex 1-2, w_act 0.15-0.3" — both bounds are grid points in the actual grid, no
drift.

**H-R3 was not actually computable as originally specified — Codex C2 caught this, I missed
it.** The decision rule needs "prefixed − unprefixed hit@3, paired, CI excluding 0." The README
suggested running the script twice with `--prefix none` and `--prefix nomic`, but each run's
`paired_vs_baseline` is only ever computed against `v1@sqlite` *within that run* — nothing
paired the two runs against each other, and the raw per-probe results needed for that pairing
are never persisted to JSON (only aggregated means survive `mx.summarize`). **Fixed**: added
`--compare-prefix`, which embeds every scenario both ways in one process (so the raw
`ProbeResult` lists exist simultaneously) and reports `metrics.paired_delta` directly under
`report["hr3_prefix_paired_delta"]`, keyed by regime → arm. Verified with a synthetic-backend
smoke test (delta is exactly 0.0, as expected — the synthetic embedder ignores prefixes
entirely, so this only proves the plumbing runs, not anything about H-R3 itself).

H-R1 and the ToM rule remain actionable as written; Codex noted H-R1's "hybrid − V1" is
underspecified about which V1 arm (`v1@sqlite` vs `v1@vec60`) — true, but the code has always
used `v1@sqlite` consistently as the baseline for every paired delta (`mx.summarize(grouped,
"v1@sqlite")`), so this is a documentation clarity nit, not an ambiguity that affects the
actual numbers produced.

## Overall verdict

Three real fixes landed as a direct result of Codex C2's independent audit catching what mine
missed: `embed_st()` no longer reloads the model 60 times per sweep, H-R3 now has a script path
that actually produces the paired comparison its decision rule requires, and the README no
longer tells the reader to run a report command that throws `KeyError`. Plus the collision
guard from my own pass (item 2) and the ToM stage-ordering caveat (item 3, confirmed
independently by both audits). The package is now sound and ready to run for real, and the
comparison itself is a data point for this cycle's methodology: a lone audit — mine, in this
case — found the correctness-critical issue (item 2) but missed two feasibility-critical ones
(items 5 and 7) that only a genuinely independent second pass caught.
