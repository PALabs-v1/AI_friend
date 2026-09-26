# 13. Wave A results: V3 wave A vs the V2 lifesim baseline

Wave A merged W4 (playback lifecycle), W9 (importance-weighted proactive
initiation) and W10a (security part 1). This document measures what those
changes did to the whole brain, on exactly the grid the V2 baseline used for
the same two suites.

## The runs

| | V2 baseline | Wave A |
|---|---|---|
| Run | `v2base-B` | `v3waveA-B` |
| Commit | `5134eee` (V2 brain, V3 BrainBench harness) | `e3fcb9a` (W4 + W9 + W10a + the harness memory fix) |
| Grid | proactive + barge-in, `architecture_only`, 12 archetypes, seeds 1000-1011, horizons `1w`/`1m`/`6m`/`1y`, 50 barge-in scenarios per family | identical argv |
| Cells | 240/240 ok | 240/240 ok, 0 errors |
| Where | home-gpu, `/data/aif-v3/runs/v2base-B` | home-gpu, `/data/aif-v3/runs/v3waveA-B`, 2026-09-26 08:32-16:35 UTC |

Committed here: `results/home-gpu/v3waveA/B/` (`manifest.json`, `plan.json`,
`cells.jsonl`, `report.json`, `report.md`, `compare-vs-v2base-B.md`) and the
outcomes checksum in `results/home-gpu/v3waveA/OUTCOMES_SHA256.txt`. The
235 MB `outcomes.jsonl` stays on home-gpu and in the research-data archive.

Tables below come from `python3 scripts/research/baseline_digest.py
docs/brain-research-v3/results/home-gpu/v3waveA` (per-day rates pooled as
total events over total simulated days, the way `gates.py` pools them).
Significance comes from `python -m evals.brainbench compare
runs/v2base-B runs/v3waveA-B`: paired by cell and probe, 95% intervals
clustered by persona, Holm correction within each suite. Its only warning is
the expected "Git SHAs differ"; no cell or probe was unpaired.

## Proactive initiation

Per simulated day, pooled over 12 personas. "Broadcast" is production's
state-sync shape; "none" is V2's control with the broadcast removed, the
fair comparison for how often the mind *chose* to reach out.

| Horizon | Metric | V2 broadcast | V2 none (control) | Wave A (every variant) |
|---|---|---|---|---|
| 1w | initiations/day | 1,225 | 21.1 | 0.125 |
| 1w | useful/day | 2.60 | 0.047 | 0.031 |
| 1w | annoyance/day | 1,222 | 21.0 | 0.094 |
| 1y | initiations/day | 1,215 | 20.8 | 1.26 |
| 1y | useful/day | 131 | 2.25 | 1.11 |
| 1y | useful rate | 0.108 | 0.108 | 0.880 |
| 1y | annoyance/day | 1,084 | 18.6 | 0.150 |
| 1y | cooldown violations/day | 1,214 | 0 | 0 |
| 1y | night fraction | 0.354 | 0.347 | 0.089 |

What changed:
- **V-4 / F-011 is gone at full scale.** Cooldown violations and
  marked-attempt resets are 0 in all 240 cells, and the four sync and
  tick-order variants now give identical results: the broadcast no longer
  changes behavior.
- **Annoyance fell about 99%** against the V2 control at every horizon
  (18.6 to 0.15 a day at `1y`). Every annoyance and irrelevant-initiation
  delta is Holm-significant.
- **Useful rate rose from 0.108 to 0.880**, and night outreach fell from
  about 35% to 9%.
- **The first outreach comes later**: about 8 hours later on average (Holm
  significant), which is the importance gate working.

The trade-off, stated plainly: **wave A also makes fewer useful initiations
in absolute terms.** At `1y` it averages 1.11 useful a day against 2.25 for
the V2 control; at `1w`, 0.031 against 0.047. The `useful_initiations` deltas
are negative and Holm-significant. V2's control reached more useful moments
by reaching out 17 times as often and annoying the user 18 times a day.
W9 trades half the useful outreach for 1% of the annoyance. That follows
DR-016 (importance decides urgency). Whether the rate is right, about one
initiation a day at a year and one every eight days in the first week, is a
product call for Aniket, noted since W9 merged and now measured on the full
panel.

(V2 broadcast's "131 useful a day" is not a better number: it is 1,215
initiations a day, a fraction of which happen to land on a due commitment.)

`re_raise_decay.raises_per_ignored_thought` is 1.0 in the 11 cells that had
an ignored thought (one cell had none): an ignored thought is raised once and
not again, the DR-017 floor.

## Barge-in lifecycle

Per scenario, all horizons identical (the suite does not depend on horizon).

| Metric | V2 | Wave A | Holm significant |
|---|---|---|---|
| replies with zero terminal outcomes | 0.143 | 0 | yes |
| started replies left without a terminal outcome | 1.416 | 0 | yes |
| terminal-count violations | 0.143 | 0 | yes |
| completed outcomes | (none) | +1.27 per scenario | yes |
| history differs from what was heard | 0.143 | 0.143 | no change |
| stale stop applied / current turn harmed / hung | 0 | 0 | no change |

W4's lifecycle fixes F-002 in code (the live voice-mesh check is still
pending) and the terminal-outcome half of F-013: every
reply that starts now ends with exactly one outcome, and replies that play in
full are recorded as COMPLETED, which V2 never did. The other half of F-013
is unchanged: an ordinary barge-in still leaves the unheard text in history
(0.143 per scenario, the same `confirmed_barge_in` family). That is DR-028's
decision and W5's job. No barge-in guarantee regressed.

## Verdict

Wave A did what it was built to do. Every Holm-significant change moved in
the intended direction except one, the lower absolute useful outreach above,
which is a direct consequence of the stricter gate:
- The proactive flood is fixed at full scale.
- Outreach is rarer, far better targeted and mostly off the night.
- Every reply now gets a terminal outcome.

Two items carry forward:
1. **The outreach rate (product call).** Wave A halves absolute useful
   outreach against the V2 control. Aniket to confirm DR-016's intended
   rate, or ask W9 for a looser importance gate measured against this run.
2. **Unheard text in history**: W5, next in the plan.

## Method note: the comparison tool

The first `compare` on this pair ran for more than half an hour without
output. Its Cliff's delta compared every pair in Python, and the proactive
`1y` groups hold about 30,000 probes per variant: roughly 900 million
comparisons per metric. `48ebdf47` counts the same pairs by binary search
over the sorted arm, with identical results (pinned by a property test
against the pairwise definition). On that commit the full comparison took
52 seconds. The numbers in this document come from that run, from a separate
checkout of `48ebdf47`; the stats code is the only difference from
`e3fcb9a`.
