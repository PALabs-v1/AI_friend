# GPU experiment package audit (Phase 4a)

Independent audit of `backend/experiments/gpu/` before running it for real. Written and
committed before reading Codex C2's independent cold audit (see `codex-log.md`), so the two
are provably independent.

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

**No issue found, verified by reading `run_lab` (`evals/cognitive/lab.py:309-334`).**
`build_embedder()` computes every document and query vector exactly once per (regime, seed) —
before the arm loop — and stores them in the `PrecomputedEmbedder` dict. `run_lab` is then
called once per arm (17 arms) but only ever does dict lookups via `embedder.embed(...)`, never
a fresh model call. So the real cost is `3 regimes × 20 seeds = 60` embedding batch calls per
model (each batching a few hundred texts), not `60 × 17`. This is already optimal; no caching
fix needed. Rough wall-clock estimate from this: at Ollama's typical ~50-150ms/text on this
GPU for a 274MB model like nomic-embed-text, 60 batches of ~300 texts ≈ 60 × ~20s ≈ 20 minutes
per embedding model for the full tune-seed sweep; sentence-transformers models are faster
per-text on GPU (batched matrix ops) once loaded. Confirmed against a live timed smoke run
before committing to the full sweep — see `04-local-infrastructure.md`.

## 6. Output schema compatibility with `evals.cognitive.report`

Cell keys here are `f"{args.backend}:{args.model}"` (e.g. `ollama:nomic-embed-text`) paired
with a regime, e.g. `("ollama:nomic-embed-text", "verbatim")` — `mx.summarize` turns tuple keys
into `/`-joined strings (confirmed by reading `mx.summarize`, which the tune/heldout output
already exercises the same way with `nomic_like/verbatim`-style keys built from the same
tuple-join). So `python -m evals.cognitive.report memory <out> <experiment>` works unmodified
against this script's output — there is no `experiment` wrapper here (this script has one
implicit experiment per invocation, not `mx.experiment_definitions()`'s named set), so the
right call is `report.memory_worst_case(json.load(open(out)), None)`-style direct use of
`memory_categories`/`memory_worst_case` against `report["results"]` rather than
`report["experiments"][name]["results"]`. Minor, not a bug: the report renderer takes a
`report["experiments"][experiment]` shape, and this script's top-level `report` dict has no
`"experiments"` key — it *is* the single experiment. Fixed by wrapping the real-embedding
report's `results` under a synthetic top-level key before rendering, in the results-writeup
tooling, not by changing the experiment script's output shape (the shape matches
`evals.cognitive memory`'s per-experiment inner structure exactly, which is what matters for
reuse).

## 7. Are H-R1/H-R2/H-R3/ToM rules still well-specified?

Checked `HYBRID_WEIGHT_GRID` (`evals/cognitive/memory_experiments.py:175-179`):
`w_lex ∈ {0.0, 0.5, 1.0, 2.0}`, `w_act ∈ {0.0, 0.15, 0.3, 0.6, 1.0}`. H-R2 describes the
plateau as "w_lex 1-2, w_act 0.15-0.3" — both bounds are grid points in the actual grid, no
drift. No other staleness found: `ARMS` in `real_embedding_retrieval.py` references
`mx._policy`, `mx.GENERATORS`, `mx.HYBRID_WEIGHT_GRID` directly (not copy-pasted values), so it
cannot silently drift from the shipped grid. All four decision rules remain actionable as
written.

## Overall verdict

The package is sound and ready to run for real. One latent fragility fixed (item 2, now
gated by a test). One caveat documented for W2's future implementation scope, not the
experiment itself (item 3). One real environment gap closed before running (item 4: GPU-box
extras were never installed). Nothing else needed fixing.
