# AI_friend research results

The results and reports from AI_friend's brain research, in one place, from the
Mac and from home-gpu. `reports/` is what you open to show someone; `results/`
holds the numbers behind every report, and the benchmark runs that future runs
are compared against. No logs, transcripts or scratch runs.

- **`INDEX.md`**: generated. Size per folder, plus one row per benchmark run with its code commit, suites, dates and cells completed.
- **`MANIFEST.tsv`**: every file with its size and sha256.
- **Refresh after any new run:** from the AI_friend repo, `python3 scripts/research/collect_research_data.py`. It copies only new or changed files and never modifies a source. Every rule for what gets included is listed in that script's `LOCAL_RULES`.
- **This guide** is kept in the repo as `scripts/research/research_data_README.md`; edit it there.

## Start here

| To show | Open |
|---|---|
| Brain V2 baseline: what the current brain does over simulated years | `reports/brain-v3/11-v2-lifesim-baseline.md` |
| Real embedding models and local LLMs compared | `reports/brain-v3/09-gpu-experiment-results.md` |
| The synthetic life simulator and how it was validated | `reports/brain-v3/10-lifesim.md` |
| Live Postgres + Qdrant + Neo4j consistency check | `reports/brain-v3/08-live-infra-validation.md` |
| Phase 3 local baseline (both machines) | `reports/brain-v3/phase3-baseline/RESULTS.md` |
| Research status, and each workstream's design and acceptance | `reports/brain-v3/README.md`, `reports/brain-v3/adr/` |
| Full benchmark tables for any run | `reports/brainbench/<run>.md` |
| The previous cycle (Brain V2 research) | `reports/brain-v2/` |
| Earlier benchmarks, charts and evidence packs | `reports/earlier/` |

## Layout

| Folder | Contents |
|---|---|
| `reports/brain-v3/` | This cycle's write-ups: research README and status, live-infra validation, GPU experiments, lifesim, the V2 lifesim baseline, the Phase 3 baseline, the findings ledger, workstream ADRs |
| `reports/brain-v3/v2-lifesim-baseline/{A,B,C}/report.md` | Full per-metric tables of the three V2 baseline runs |
| `reports/brainbench/<run>.md` | Full report of every benchmark run on home-gpu (same as its `results/brainbench/<run>/report.md`) |
| `reports/brain-v2/` | The Brain V2 research write-ups and ADRs |
| `reports/earlier/` | Pre-V2 benchmark reports, charts and the PDF report (`benchmarks/`), academic benchmark documentation, evidence packs, the orchestration cycle's phase benchmark results and final audits |
| `results/brainbench/<run>/` | One folder per benchmark run: `plan.json` (the grid), `manifest.json` (commit, command, dates), `cells.jsonl`, `outcomes.jsonl` (raw per-probe results) and `report.json`/`report.md` |
| `results/brain-v3/` | GPU experiment results, Phase 3 baseline numbers (both machines), and the V2 gate bands CI checks against |
| `results/measurements/` | Measurement-tool outputs (m4b, m11-m17) |
| `results/earlier/` | The numbers behind `reports/earlier/`: earlier benchmark JSONs and CSVs, academic benchmark datasets, orchestration phase results, earlier probe runs |

## The benchmark runs

- **`v2base-A`, `v2base-B` and `v2base-C` are the Brain V2 lifesim baseline.** Every Brain V3 change is measured against them. They ran on home-gpu at the V2 application code, with seeds 1000-1011 across 12 personas, and completed 463 cells with 0 errors.
  - **A** covers attention, trust and resources in `architecture_only` mode, at horizons 1 week to 10 years.
  - **B** covers proactive outreach and barge-in, at horizons 1 week to 1 year.
  - **C** is the `llm_augmented` reference run: memory, affect, personality and metacognition with the local LLM.
- **Later runs** use the same grids on newer code, and each gets its own folder. `INDEX.md` shows which commit each run measured.

## Comparing a new run

```bash
# on home-gpu, same grid as the baseline run it is compared with (here v2base-B):
cd /data/aif-v3/backend
.venv/bin/python -m evals.brainbench run --mode architecture_only --suites proactive,bargein \
  --archetypes all --seeds 1000-1011 --horizons 1w,1m,6m,1y \
  --suite-arg bargein.n_scenarios_per_family=50 --workers 10 --out /data/aif-v3/runs/<name>
.venv/bin/python -m evals.brainbench report /data/aif-v3/runs/<name>
.venv/bin/python -m evals.brainbench compare /data/aif-v3/runs/v2base-B /data/aif-v3/runs/<name>
# then on the Mac, from the repo:
python3 scripts/research/collect_research_data.py
```

`compare` pairs outcomes probe by probe within the same suite, variant, persona,
seed and horizon. Per metric it reports the paired delta, a bootstrap p-value
clustered by persona seed, and Cliff's delta. It warns when the two runs' commits
differ or either was dirty. Compare only runs with the same grid; each run's
`plan.json` is its grid.
