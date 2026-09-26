`memory_tune.json` and `memory_heldout.json` (6.0 MB each) are not committed — too large for "small text only" raw-JSON storage. They're fully reproducible on demand: `evals.cognitive` is seed-deterministic (pinned by `tests/test_cognitive_bench.py`, and confirmed byte-identical to the Mac's run in this baseline), and the exact commands are in `../../baseline/manifest.json`'s `commands` block. A copy from this baseline run is archived at `/data/aif-v3/baseline-results/baseline/` on the box itself.

Everything else in this directory (affect, latency, pytest/cargo/mutation logs) is small and committed as-is.

`v2base/` and `v3waveA/` hold the small files of each BrainBench run (`manifest.json`, `plan.json`, `cells.jsonl`, `report.json`, `report.md`, and for wave A `compare-vs-v2base-B.md`). The large `outcomes.jsonl` files stay on home-gpu (`/data/aif-v3/runs/`) and in the research-data archive; their sha256 and sizes are in each `OUTCOMES_SHA256.txt`.
