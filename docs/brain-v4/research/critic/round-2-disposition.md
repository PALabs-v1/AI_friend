# Critic round 2: disposition

Verdict: FAIL, 3 HIGH, 4 MED, 1 LOW (`round-2.md`). Every finding was acted
on, 2026-09-26. No round 3: the two HIGH findings still open are blocked on
Aniket's decisions D1-D4, and a fresh critic would re-find them unchanged.

| # | Finding | What changed | State |
|---|---|---|---|
| 1 | HIGH: 80% power is for independent judgments, not the proposed rater random-effects analysis | `03` Tier 2: the power table is labeled "independent-judgment illustrations"; a new bullet says what the numbers are not and requires a pilot-based simulation of the specified mixed model, with every clustering level (rater, persona, excerpt) defined first | Fixed as far as possible before a pilot. The simulation needs the pilot's variance estimates |
| 2 | HIGH: the eval cannot be pre-registered or run as written | `03` status now says "draft design, not an acceptance test", and names D1-D4 as the blockers. New "Before freezing" item 13: every qualitative pass rule ("above chance", "scores lower", "appropriate", "must exceed the ablated arm") needs a number and a scoring procedure | **Blocked on D1-D4.** Correctly labeled; not resolvable by editing |
| 3 | HIGH: Tier 3 has no decision rule; weekly crossover has carryover | `03` Tier 3: a carryover bullet (memory, mood and identity persist across weeks; three design options), a decision-rule bullet (estimand plus numeric threshold, frozen before week 1), and a statement of what a 1-5 user case series can support (an existence claim only; the general claim needs Tier 4). Item 9 expanded | **Blocked on D1-D4** for the actual rule; the gaps are now explicit |
| 4 | MED: verification log lacks traceable support for `02`'s V3 claims | New `tools/check_v3_claims.py`: 30 ownership claims and 7 status facts, read from `COVERAGE.md` and the repo. It found a real error: `02` put DR-011 under W2; W11 owns it (fixed). Mutation-checked. Blank "How checked" cells filled; existence and content checks now separated in the log | Fixed |
| 5 | MED: "already solved", "any planning horizon", and `00` still leading with "reachable" | `01` §A: bounded to the five-minute, three-party, prompted setup. §O and its table row: "not supported by current evidence", explicitly not a proof. `00` §3 now leads with the corrected conclusion; the original words are kept, labeled as the record | Fixed |
| 6 | MED: table and section drift (C, F, M) | §C heading matches the table (+ RESOURCE). §F's drive list now includes uncertainty. Row M: "implemented; active only when the optional MediaPipe support and its model asset are present" | Fixed |
| 7 | MED: "six studies" across two versions | `01` §C: six in the arXiv version, five in the journal version; the findings appear in both abstracts | Fixed |
| 8 | LOW: "open problems for every system" | `01` §J: Full-Duplex-Bench evaluates those capabilities; only Moshi's result is cited; no claim about unmeasured systems | Fixed |

Round-1 items the critic still marked PARTLY or NOT FIXED: 1 (power) and 8
(V3 facts log) are covered by rows 1 and 4 above; 2, 3 and 4 by rows 2, 3
and 5.
