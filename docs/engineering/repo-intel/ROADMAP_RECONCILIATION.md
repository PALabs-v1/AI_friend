# Roadmap reconciliation (Phase 6)

Written for Aniket, who decides whether and in what order to resume the Brain V4 build. Compares the audit with the interrupted plan in `docs/brain-v4/phases/04-slice-plan.md` and `docs/engineering/ROADMAP_CHECKPOINT.md`. Product objectives are unchanged. Nothing here is a decision; Phase 7 starts only on your approval.

## 1. What the audit changes about the plan

| Earlier statement | Source | Audit verdict | Evidence |
|---|---|---|---|
| `TemporalMemoryStore` has no writer and no reader in the live path | Phase 03 critic round 2; checkpoint section 7 | Still supported, with one correction: it **is constructed** (`core.py:91`) and passed to the pipeline (`core.py:166`, stored `pipeline.py:109`), but no method is called on the instance anywhere in `backend/app` | claims #3 (verified, runtime grep), #4 (verified) |
| The output filter blocks "as an AI", so D4 is not met | Phase 01 comparison; checkpoint section 7 | Reframed: D-018 says the block on stock disclaimer phrases stays. The real gap is that no backend test or eval asks a sincere "are you human?" question | `action.py:722-727`; claim #54 (corrects #42, which blamed the filter) |
| Brain V3 W1, W2, W3, W11 are unbuilt | PROJECT_STATE | Narrowed: W11 is not on main (`learning.py:24,70`); W1 exists on the unmerged `brain-v3` branch, flag off (commit `9fd49a25`, not an ancestor of main) | claim #39; claim #18 disputed by the independent reader |
| W4, W9, W10a, P8 landed | PROJECT_STATE | Still supported; `BackgroundScheduler` was removed by W9, not W4 | #52 (corrects #19), `backend/tools/scale` present (#29) |
| Local Claude Code client is missing (V4 critic F-10) | Phase 03 round 1 | Superseded: `llm/__init__.py:86-91` builds `ClaudeCodeClient` | #17 verified |
| Hybrid search skips embedding provenance (Wave A critic F-02) | Wave A critic round 1 | Overstated: the scope assert is called at one site, but the row-level `_check_row_embedding` is called five times on other paths | #41; #28 disputed |
| No local claude-code `finally` or cancellation handling (Wave A critic F-01) | Wave A critic round 1 | Still supported | #16 |
| F-003 Qdrant healthcheck, F-022 realism-eval fallback constant | Brain V3 findings | Still open | #14 verified, #15 verified |
| Persona reset path forbidden by DR-033 | V3 decisions | Contradicted by `backend/scripts/reset_persona.py` | #20 verified |
| Ledger says it is right where documents disagree | `CLAUDE.md` | Stale: it has no entry after 2026-09-26, so the V3 freeze and the whole V4 cycle are missing | #24 verified |

364 citations in 58 documents point at paths that do not exist (256 of them in the historical `.agents/CONTEXT.md`; `ARCHITECTURE_MAP.md` section 9), and the evidence package disagrees with the code on the cortisol half-life (600 s against 4,500 s, #2 verified). Full list: `DISCREPANCY_REPORT.md`. Historical documents were not edited.

## 2. Blocking and near-blocking issues, in the order they bite the roadmap

1. **Two benchmark documents contradict each other on measured recall** (#5 verified). Slice 2a builds the baseline harness; its numbers must not be compared against `sota_comparisons.md` until that document is retracted or sourced.
2. **Bi-temporal memory is constructed but dead on the live path** (#3). Slices 4 and 5 (mood self-feed NF-12, integrity update NF-23) and any belief-revision work assume a working temporal store. Decide first whether slices wire it in or the evidence package stops calling it proven.
3. **A subscribed subject with no publisher** (`voice.segmentation_feedback`, #32 verified). The brain's voice-feedback handler cannot fire; any slice that relies on segmentation feedback needs the publisher built first.
4. **Healthchecks that cannot fail or only prove NATS** (#14 verified: Qdrant `|| exit 0`; #53: seven bare `nc -z nats_mesh 4222` probes). Any slice that ships a new service or depends on "healthy" for gating inherits this.
5. **Four disputed claims were author errors** (see `BENCHMARK.md`). Treat any earlier audit statement that has no check and no independent reader as a hypothesis.

## 3. Recommended order

The slice plan's waves still hold. Changes proposed from the audit:

| Step | What | Why |
|---|---|---|
| 0 | Run `inventory`, `index`, `claims recheck` and the covering tests at the start of every slice; record each slice's design decisions as claims | Makes staleness visible; this is the Phase 7 acceptance criterion |
| 1 | Finish Wave A cleanups: the 12 unbound ledger rows (3 bound, #58; 9 stay unbound with reasons), Wave A critic F-04 (fixed, #57), the CI items you chose to leave, the cold critic round (max 2) | Unchanged |
| 2 | Add a pre-slice "wiring check" to Wave B: for each store or agent a slice touches, run `refs` and `subjects` and record the result as a claim | Prevents building on a component that is constructed but unused (#3) and handlers with no publisher (#32) |
| 3 | Slice 2a (baseline harness) before any slice that claims an improvement | Unchanged; add the rule that benchmark documents with unreconciled numbers are not baselines |
| 4 | Decide the temporal store question (wire or retract) before Wave C slices 4, 5 | See blocking issue 2 |
| 5 | Waves C and D as planned; GPU slices wait for the hand-back (D-036) | Unchanged |

Owner decisions this audit surfaces, none taken here: whether to apply the documentation corrections (the V3 freeze still holds them), whether to move the benchmark and evidence documents to a "superseded" folder, whether to publish `voice.segmentation_feedback` or delete the handler, and whether the unmerged `brain-v3` branch lands before or after the V4 slices that touch the same files.

## 4. What stays uncertain

- Behaviour at runtime: no services, GPU or LiveKit session were used. Test logs from the Mac and home-gpu are cited, not re-run on GPU.
- The 4 disputed claims were corrected; the corrected claims #38 to #41 and #52 to #54 have **not** had an independent reader. They are `supported`, not `verified`. A second Claude reviewer later checked 34 more claims and disagreed with 3 (#19, #38, #42), each an overstatement of mine, now superseded by #52, #53 and #54.
- Dynamic dispatch: 16 dynamic constructs in production code; any "unused" conclusion near them is a lead.
- `.agents/CONTEXT.md` (17,202 lines) was read in part. The 2026-04 to 2026-09 entries it holds were not individually checked against the code.

## 5. Phase 7 gate

Resume the roadmap only after you approve this plan. Start by running the commands in `USAGE.md` section 1, then continue from Wave A's cold critic round.
