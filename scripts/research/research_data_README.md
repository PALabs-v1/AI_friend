# AI_friend research data

Every result, report and raw run artifact produced for AI_friend's brain research,
from the Mac and from home-gpu, in one place. Use it to showcase results and as
the reference that future runs are compared against.

- **Collected by** `scripts/research/collect_research_data.sh` in the AI_friend repo.
  Rerun it after any new run; it copies only what is new or changed and never
  deletes or modifies a source.
- **This README** is maintained in the repo as `scripts/research/research_data_README.md`. The collector installs it here, so edit it there.
- **`INDEX.md`**: generated summary. Size per category, plus one row per BrainBench run with its code commit, mode, suites, dates and cells completed.
- **`MANIFEST.tsv`**: every file with its size and sha256. Verify a copy with `shasum -a 256`.
- **`COLLECT_LOG.tsv`**: every copy step, when it ran, where it came from, where it went.
- The small summaries are also committed in the repo under `docs/brain-research-v3/results/`.
  This archive keeps the raw data too (GBs) that git should not hold.

## Layout

| Directory | What it holds | Source |
|---|---|---|
| `brainbench/home-gpu/<run>/` | Full BrainBench runs from home-gpu. Each has `plan.json` (the grid), `manifest.json` (git sha, argv, dates), `cells.jsonl` (one row per cell), `outcomes.jsonl` (raw per-probe outcomes) and `outcome-cells/`, plus `report.md`/`report.json` where a report was generated | `home-gpu:/data/aif-v3/runs/` |
| `brainbench/mac/<run>/` | BrainBench dev and review runs made on the Mac (W9 proactive panels and their Codex logs, C13 and attention/metacognition probes) | Mac `/private/tmp/` |
| `lifesim/` | Synthetic life-simulator corpora and the stress/quality sweeps from Phase 5 | Mac `/private/tmp/lifesim`, session scratchpads |
| `gpu-experiments/` | Phase 4a real-embedding retrieval (8 models) and ToM valence (3 LLMs) results | `home-gpu:/data/aif-v3/gpu-results` (+ the Mac's earlier pull) |
| `baseline-phase3/` | Phase 3 Brain V2 local baseline logs from both machines (pytest, cargo, affect, latency, memory tune/held-out) | Mac `/private/tmp/brain-v3-baseline`, `home-gpu:/data/aif-v3/baseline-results` |
| `infra-validation/` | Phase 4b live Postgres + Qdrant + Neo4j consistency result | home-gpu |
| `scale/` | P8 scale-runner outputs (Mac smoke runs so far) | Mac `/private/tmp/aif-scale-p8*`, scratchpads |
| `codex/<session>/` | Codex CLI reports, full logs, prompts and tree snapshots for every delegated build and audit | Claude session scratchpads |
| `tests-and-ci/<session>/` | Test-suite outputs, CI logs, mutation-test runs and cross-checks behind each review verdict | Claude session scratchpads |
| `logs/` | Run logs and launch scripts (home-gpu `run-logs/`), Mac thermal and progress monitors | both |
| `critique/` | Critic-loop evidence | Mac |
| `earlier-runs/repo/` | Results that already live in the repo, at their original repo path: pre-V3 benchmarks (`scripts/results/`, `academic_benchmarks/datasets/`), measurement-tool outputs m4b and m11-m17 (`backend/tools/measure/out/`), code-quality baselines, evidence packs, benchmark notebooks | repo (tracked) |
| `earlier-runs/shared-checkout-untracked/` | Earlier results never committed: the `orchestration/` phase reports and benchmark JSONs, and `backend/evals/out/` probe runs (phase0, colab model comparisons, character pressure) | `~/Projects/PALabs/AI_friend` |
| `repo-results/` | Copy of the committed Brain V3 results and the V2 gate bands, so this tree is complete on its own | repo |

## The runs that matter

- **Brain V2 lifesim baseline** is the reference every Brain V3 workstream is measured against (`docs/brain-research-v3/06-benchmark-plan.md`). It was run at commit `5134eee`, the V2 code with the V3 benchmark harness, on home-gpu, with seeds 1000-1011 across 12 personas. All three runs completed with 0 errors.
  - **`brainbench/home-gpu/v2base-A`** covers attention, trust and resources in `architecture_only` mode, at horizons 1w to 10y. It has 216 cells.
  - **`brainbench/home-gpu/v2base-B`** covers proactive (4 sync/tick-order variants) and barge-in (50 scenarios per family), at horizons 1w to 1y. It has 240 cells.
  - **`brainbench/home-gpu/v2base-C`** is the one `llm_augmented` reference run. It covers memory, affect, personality, metacognition and resources for steady_professional, seed 1000, 1m. It has 7 cells.
  - Headline tables: `python3 scripts/research/baseline_digest.py <this dir>/repo-results/brain-research-v3/home-gpu/v2base`.
- **`brainbench/home-gpu/_aborted-*`**: runs stopped on 2026-09-25 because of harness bugs found mid-run. They are kept as evidence, but their numbers are not valid.
- **`brainbench/mac/w9-panel-final2`** is the last complete W9 proactive panel: 48/48 cells, built from Codex's uncommitted tree on top of `be4796ae`. It is the panel behind `ADR-W9-proactive.md`. The other `w9-panel*` folders are earlier or partial attempts. The panel must be rerun on home-gpu at the merged commit before it counts as a result.

## Comparing a future run

```bash
# on home-gpu, same grid as the baseline run, new code:
cd /data/aif-v3/backend
.venv/bin/python -m evals.brainbench run --mode architecture_only --suites proactive,bargein \
  --archetypes all --seeds 1000-1011 --horizons 1w,1m,6m,1y \
  --suite-arg bargein.n_scenarios_per_family=50 --workers 10 --out /data/aif-v3/runs/<name>
.venv/bin/python -m evals.brainbench compare /data/aif-v3/runs/v2base-B /data/aif-v3/runs/<name>
# then, on the Mac, from the repo:
bash scripts/research/collect_research_data.sh
```

`compare` pairs outcomes probe by probe within the same suite, variant, persona, seed
and horizon, and reports per metric the paired delta, a bootstrap p-value clustered by
persona seed, and Cliff's delta. It warns when the two runs' git SHAs differ or either
was dirty.
Compare only runs with the same grid (suites, seeds, horizons, suite args); each
run's `plan.json` is its grid.

## Not included, on purpose

- Model weights and caches: `home-gpu:/data/aif-v3/hf-cache` (2.8 GB) and `backend/models/`.
  They can be re-downloaded and are not results.
- Build outputs, virtualenvs, uv/pip caches and Rust `target/` directories.
- `backend/voice_samples/`, `.env` files and the personal/outreach folders in the shared checkout.
- `/data/glioma-idh` on home-gpu, which belongs to another project.
