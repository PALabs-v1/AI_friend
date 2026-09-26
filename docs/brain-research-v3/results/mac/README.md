`memory_tune.json` and `memory_heldout.json` (5.3 MB each) are not committed — too large for "small text only" raw-JSON storage. They're fully reproducible on demand: `evals.cognitive` is seed-deterministic (pinned by `tests/test_cognitive_bench.py`), and the exact commands are in `../../baseline/manifest.json`'s `commands` block. A copy from this baseline run is archived on home-gpu at `/data/aif-v3/baseline-results/baseline/` and can be regenerated on the Mac in under a minute with:

```
cd backend && PYTHONPATH=. .venv/bin/python -m evals.cognitive memory --seeds tune --workers 8 --out /tmp/memory_tune.json
```

Everything else in this directory (affect, latency, pytest/cargo/mutation logs) is small and committed as-is.
