# GPU experiment results (Phase 4a)

Real-embedding and real-LLM re-validation of Brain V2's memory-retrieval and ToM-appraisal
conclusions, per the pre-registered rules in `backend/experiments/gpu/README.md` and the audit
in `07-gpu-experiment-audit.md`. Run on home-gpu (RTX 2060 SUPER, 8GB), commit `d8de6ce`.

## H-R1: hybrid ≫ V1, on every real embedder tested

Rule: hybrid − V1 ≥ 0.30 hit@3 on the `summary` regime.

| Embedder | dim | v1@sqlite | cosine | hybrid (production) | Δ vs V1 | obsolete_win |
|---|---:|---:|---:|---:|---:|---:|
| nomic-embed-text (unprefixed, **production**) | 768 | 0.023 | 0.824 | 0.810 | **+0.787** | 0.778 |
| nomic-embed-text (prefixed) | 768 | 0.023 | 0.739 | 0.774 | +0.751 | 0.778 |
| embeddinggemma | 768 | 0.023 | 0.856 | 0.849 | +0.826 | 1.000 |
| mxbai-embed-large-v1 | 1024 | 0.023 | 0.814 | 0.814 | +0.791 | 0.778 |
| bge-base-en-v1.5 | 768 | 0.023 | 0.712 | 0.774 | +0.751 | 0.789 |
| all-MiniLM-L6-v2 | 384 | 0.024 | 0.778 | 0.776 | +0.752 | 0.989 |
| snowflake-arctic-embed-m-v1.5 | 768 | 0.023 | 0.496 | 0.524 | +0.501 | 0.733 |
| Qwen3-Embedding-0.6B | 1024 | 0.023 | 0.831 | 0.803 | +0.780 | 0.856 |

**8/8 PASS**, by a wide margin (smallest delta +0.50, on the weakest embedder tested). ADR-001's
core conclusion — hybrid beats V1 decisively — holds under every real embedding model tried, not
just the synthetic benchmark embedder. Confirms the Phase 3 baseline's headline number
(0.023 → hybrid) was not an artifact of the synthetic embedder's controlled cosine structure.

**Bonus finding, not what this table was built to show**: `obsolete_win` is high (0.73–1.00)
across every single real embedder, independent of which one. This independently confirms M-5
(stale facts win retrieval) using real embeddings — Phase 3's baseline showed the same thing on
the synthetic benchmark; this rules out "synthetic-embedder artifact" as an explanation. M-5 is
real and severe under real vectors too.

## H-R2: weight plateau holds under real embeddings

Rule: the best point on the `HYBRID_WEIGHT_GRID` (checked here on nomic-embed-text, `summary`
regime) is within 0.02 hit@3 of the shipped `hybrid:1.5:0.2`.

Shipped weights: 0.810 hit@3. Best on grid (`hybrid:0.0:0.0`): 0.813 hit@3. Gap: **0.004**.

**PASS.** No re-centering needed; the shipped weights are already essentially optimal under a
real embedder, matching the synthetic-benchmark finding.

## H-R3: nomic task prefixes — real negative result

Rule: prefixed − unprefixed ≥ +0.02 hit@3, paired per (seed, probe), CI excluding 0.

This needed the `--compare-prefix` fix built during the audit (07-gpu-experiment-audit.md item
7) — the two separate `--prefix none`/`--prefix nomic` runs above cannot answer this question on
their own; only the paired run can.

| Regime | Arm | Paired Δ (prefixed − unprefixed) | 95% CI | wins/losses |
|---|---|---:|---|---|
| verbatim | hybrid (production) | +0.007 | [-0.010, +0.024] | 62/54 |
| unique | hybrid (production) | -0.002 | [-0.013, +0.012] | 49/51 |
| **summary** | hybrid (production) | **-0.036** | **[-0.050, -0.024]** | 0/41 |
| verbatim | cosine | -0.054 | [-0.068, -0.039] | 34/95 |
| summary | cosine | -0.085 | [-0.097, -0.072] | 1/97 |

**FAIL — do not add nomic task prefixes.** Not just "no significant gain": on `summary` (the
regime closest to consolidated/paraphrased real conversation), prefixing is significantly
*worse* on both arms, with a CI that excludes zero. The cosine arm shows the same negative
pattern on every regime. **Decision: `MemoryStore.get_embedding` and `add_memory` keep embedding
raw text, unprefixed.** ADR-001 needs no change here — a real, well-evidenced "no" from a
pre-registered rule, which is exactly what these rules are for.

## ToM decision rule: fails decisively for every model tested

Rule: adopt `inferred_valence` as appraisal input only if Pearson r ≥ 0.8, sign agreement ≥ 0.9
on non-neutral messages, AND the simulated hostile-script trust ends ≤ 0.5.

| Model | Pearson r | sign agreement | parse failure rate | hostile-script final trust |
|---|---:|---:|---:|---|
| llama3.2:3b | 0.541 | 0.643 | **95%** | 0.667 (3 seeds) |
| qwen2.5:3b | 0.292 | 0.600 | 0% | 0.699 / 0.726 / 0.715 |
| qwen3:4b | — | — | **100%** | — (no signal reached the simulation) |

**0/3 models pass any of the three criteria.** Decision: **do not adopt LLM-inferred valence as
appraisal's goal-congruence input at this time.** The mechanism itself is proven sound (Phase 3's
baseline: feeding a hand-labelled or lexical estimate into the same update arithmetic produces
correct mood/trust responses) — what's missing is an accurate-enough real-time estimator, and
none of the three candidate local models clear the bar. This directly narrows W2: the fix is not
"wire up ToM," it's "find or build a better estimator," with the stage-ordering caveat
(`07-gpu-experiment-audit.md` item 3) still applying whenever one is found.

**Two things inside this result are themselves worth flagging, verified rather than glossed
over:**

- **qwen2.5:3b is the only one of the three that reliably produces parseable output** (0% parse
  failure) but its accuracy (r=0.29) is far below the bar regardless — a clean case of "reliable
  but not accurate enough," not a parsing problem.
- **llama3.2:3b — production's own configured fast model — failed to parse 95% of the time on
  this exact classification call** (`DecisionService._classify_intent_and_goal`), and even its
  rare successful parses were often wrong (e.g. "I got the job offer, I'm so happy!" →
  `inferred_valence: 0.0`). Verified against the raw per-message rows, not just the summary
  statistic, and confirmed it isn't a test-harness bug: the identical harness gets 0% failures on
  qwen2.5:3b with the same prompts. **This is a finding in its own right, independent of the ToM
  question** — if this classification call fails 95% of the time in this experiment, it's worth
  checking whether it also fails at a meaningful rate in production (a lower rate is plausible if
  production's fuller context/prompt differs from this experiment's isolated single-message
  calls, but that's a hypothesis, not something this experiment verified either way). Logged as a
  new finding — see `findings.md`.
- **qwen3:4b failed to parse 100% of the time**, with a median latency of 20.2 seconds per call
  (10-15x slower than the other two models). The most likely explanation, not confirmed with a
  captured raw transcript: Qwen3's thinking mode emits a `<think>...</think>` reasoning block
  before its answer, and `app/cognitive/json_extract.py` has no handling for stripping it before
  JSON extraction — grepped and confirmed no `think`-related logic exists there. Not chased
  further this session (root-causing a specific model integration is out of Phase 4's scope), but
  the hypothesis and the grep evidence are recorded so whoever touches qwen3 support next doesn't
  start from zero.

## An unplanned finding: home-gpu rebooted mid-experiment

At 22:26 IST, partway through the `qwen3:4b` ToM run, home-gpu's kernel log shows a clean boot
boundary (`journalctl -b -1` ends abruptly mid-Ollama-inference; `journalctl -b 0` / `last -x
reboot` confirm a fresh boot at 22:26) with **no** graceful-shutdown sequence, kernel panic trace,
OOM-killer message, or ACPI power event logged before it. The box was up only 1 minute when next
checked. I have no way to see a physical event (power blip, manual reset) from here — flagging
this plainly rather than guessing at a cause I can't verify.

**No data loss**: all 5 `aifv3` Docker services (Postgres, Qdrant, Neo4j, Redis, NATS) came back
healthy within 2 minutes via their restart policy — verified directly (`docker compose ps`), not
assumed. The only casualty was the in-flight `qwen3:4b` run, which was relaunched and completed
normally afterward (its own slowness, unrelated to the reboot, is what made the retry look stuck
before it clearly wasn't — see the ToM table above).

**Separately surfaced while investigating**: three old systemd services on this box
(`ai-friend-agents.service`, `ai-friend-stt.service`, `ai-friend-voice.service`) are crash-looping
with restart counters in the thousands (4777, 5424, 5424) and `status=203/EXEC` (the exec()
syscall itself fails — almost certainly a stale binary path from before this research cycle's
fresh clone at `/data/aif-v3`, since the box's original checkout is elsewhere). Pre-existing,
unrelated to this session's work, costing nothing but log noise and a few wasted systemd restart
attempts per second — not fixed here since it's outside Phase 4's scope, but worth Aniket knowing
about since it's silently running on the box. Not logged as a Brain V3 finding (it's an ops issue
on the machine, not a defect in the code being researched).

## Raw data

`docs/brain-research-v3/results/home-gpu/gpu-experiments/` — 11 files, ~5MB, all committed (each
is one model's full result, well under the "small text only" ceiling that made Phase 3's raw
memory-eval JSON too big to commit).
