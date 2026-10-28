# 04. Candidate V4 workstreams (DRAFT)

Ranked by how much each is expected to move perceived realism, given the
evidence in `01-what-is-possible.md`, and by what has to exist first. These
are candidates, not commitments. The V4 plan is written after V3's results
and Codex's research are in. Each item names the outcome that would show it
worked (root `CLAUDE.md`: every change names a measurable outcome).

IDs use a `V4-` prefix so they never collide with V3's `W1`..`W12`. IDs are
stable labels; the ranking below is revised after the Codex comparison
(`05-comparison.md`) and is the order to read them in:

| Rank | Item | Why this position |
|---|---|---|
| 1 | V4-1 realism eval | the judge for everything else |
| 2 | V4-2 state in the words | makes V3's affect, memory and trust visible |
| 3 | V4-4 inner life with consequences | the causal loop both passes converged on |
| 4 | V4-5 identity in a human band | self-authored evolution needs a guardrail with numbers |
| 5 | V4-12 partnership, shared goals | the Diana reference: partnership carries most of the felt realism; a design reference, not a study (`07`) |
| 6 | V4-7 conversational timing (instrument first) | nothing measures audible timing today |
| 7 | V4-10 theory of mind with uncertainty | cheap on top of W8, prevents confident wrong guesses |
| 8 | V4-9 vision as shared perception | engineering on a working base |
| 9 | V4-3 functional drives (narrowed) | promising, but social needs lack units; start small |
| 10 | V4-8 voice that carries state | waits on W2 and V4-3 |
| 11 | V4-6 model tier study | unblocked by D3 (pluggable, test all tiers) |
| 12 | V4-11 indicator audit | report only |

## V4-1. Perceived-realism eval (build first)

- **What**: Tiers 0-3 of `03-realism-eval.md`, frozen before anything else is
  built.
- **Why first**: it is the judge for every other V4 item. Without it, "more
  real" is an opinion.
- **Outcome**:
  - the eval runs end to end on V3-final
  - it produces a baseline number per tier
  - Tier 1's proxy is calibrated against Tier 2
- **Depends on**: V3's BrainBench, lifesim and W12 probes.

## V4-2. Internal state in the words

- **What**: mood, drives, memory of the user and the mind's own recent
  thoughts measurably shape what it says (not only prosody). Today
  `backend/app/cognitive/expression.py` maps valence/arousal to a coarse
  label and breath level, and nothing measures state-to-words coupling.
- **Outcome**:
  - Tier 0 "state-in-words": mood band recoverable from replies above chance
  - a decoupled arm scores lower in Tier 2
  - no rise in sycophancy: state shows, it does not just agree more
- **Depends on**: W2 (affect has to move before it can show).
- **Limit class**: ENGINEERING, with a RESOURCE component (small models
  follow such conditioning less reliably).

## V4-3. Functional drive layer

- **What**: start with energy/rest (with a sleep rhythm) and uncertainty,
  where inputs and costs are observable. Social need and competence are
  exploratory: they have no validated units or set points (SCIENCE, per
  `01-what-is-possible.md` F). Deviations drive affect and initiative;
  homeostatic RL (Keramati & Gutkin 2014) is the formal model to borrow.
- **Replaces** mislabeled fragments rather than adding beside them: `NF-18`,
  `NF-21`, `NF-33` (inverted night recovery) and `NF-34` (curiosity and
  `learning_gain` code with no caller). Behind a flag with a BrainBench arm,
  per V3's rule.
- **Outcome**:
  - Tier 0 drive-consistency probe passes
  - decisions improve under resource shocks against no-drive and
    static-priority arms (fewer missed goals, less overwork or annoyance) at
    equal foreground latency
  - Experience attribution rises against the no-drive arm (a target, per D1)
- **Depends on**: W2's three affect layers (DR-010), W9's initiation, W11
  (drives must not rewrite the persona).
- **Limit class**: ENGINEERING. The felt side is UNKNOWABLE and never
  claimed.

## V4-4. Inner life with consequences

- **What**: between-session thought (the subconscious, DR-019) changes
  memory, mood and later conversation, traceably. A thought it had can lead
  it to bring something up, reconsider, or feel differently next time.
  Rule (from Codex R5): every stored reflection must change a future
  decision or be discarded, and each records its plan, evidence and
  outcome.
- **Outcome**:
  - Tier 0 traceability probe exceeds the ablated arm
  - Tier 3 diaries mention "has a life of its own" more often in the active
    arm
- **Depends on**: W9 (merged), W1 (thoughts become memories under truth
  maintenance), W7 (consolidation).

## V4-5. Identity in a human band

- **What**: hold W11's self-authored evolution to measured human norms:
  stable rank order, directional drift over long horizons, no freezing and no
  wandering off.
- **Outcome**: the Tier 0 identity-band probe passes at simulated 6-month,
  1-year and 10-year horizons.
- **Depends on**: W11, the personality suite, DR-020/021/033/038.

## V4-6. Model tier study

- **What**: run the realism eval with the same architecture on different
  generation models (local 3-4B, the largest that fits local hardware, and
  a frontier model through local Claude Code). This separates what the
  architecture buys from what the model buys.
- **Outcome**: a table of Tier 1/2 scores by model tier, and a decision
  (Aniket's) on the default tier for V4.
- **Depends on**: V4-1. Needs Aniket's decision on which tiers are
  acceptable to run.
- **Limit class**: RESOURCE, measured instead of assumed.

## V4-7. Human conversational timing

- **What**, in order:
  1. Instrument the audible path: last user speech, endpoint decision,
     partial and final transcript, first brain token, first audible sample,
     playout start and end, interruptions, terminal outcome. Nothing measures
     this today.
  2. A timing benchmark: gap distribution, overlap type, yield and interrupt
     correctness, backchannel rate and placement.
  3. Then predictive turn-end detection in the cascade, and a full-duplex
     model as a replaceable speech front end (the M5 Mac is the only machine
     that can try Moshi; the 8 GB GPU cannot), never as the mind.
- **Outcome**:
  - p95 speech-end to first audible sample <= 500 ms for short turns
    (DR-024)
  - the gap distribution compared against Stivers et al. 2009 (mean +208 ms,
    mode 0), stratified by language
  - >= 99% heard-history match under interruptions
  - backchannels judged appropriate by blind raters
- **Depends on**: W4 (merged), W5, Codex R6's feasibility findings.
- **Limit class**: RESOURCE.

## V4-8. Voice that carries the mind's state

- **What**: prosody driven by actual affect and drives (replacing `NF-36`'s
  hand-set coefficients), checked by listeners.
- **Outcome**: listeners identify the mind's mood band from audio above
  chance, better than with the current coefficients.
- **Depends on**: W2, V4-3, Codex R6.

## V4-9. Vision as shared perception

- **What**: cheap per-frame change and object tracking; the VLM reserved
  for meaning; timestamped visual episodes written as memories under W1's
  truth maintenance. The mind notices change and brings up what it saw.
  Facial cues stay weak, context-conditioned evidence (Barrett et al. 2019),
  and the live reflex channel's "unambiguously positive" labels get fixed
  (candidate V3 finding, `05-comparison.md`).
- **Outcome** (proposed thresholds from Codex R6):
  - >= 90% of user-referenced objects resolved to the right entity
  - >= 85% correct change / no-change calls on held-out scenes
  - correct recall of an earlier visual state after 1, 5 and 20 minutes of
    distraction
  - no trust change driven by facial cues alone (a test)
- **Depends on**: W1, `backend/app/vision/agent.py`, Codex R6.

## V4-10. Theory of mind with uncertainty

- **What**: `NF-26`'s fix carried into conversation. The mind holds beliefs
  about the user with confidence and source, and says so when unsure.
- **Outcome**: fewer confident wrong claims about the user (lifesim ground
  truth), with no drop in Tier 2 "felt heard".
- **Depends on**: W8.

## V4-12. Partnership: joint activity toward shared goals

- **What**: goals the user and the mind own together. The mind tracks
  progress without being asked, notices setbacks and feels them (through
  V4-3's competence and progress drive), and takes initiative to help,
  through W9's initiation gate so it never becomes a flood. Shared
  perception (V4-9) lets it react to what the user is doing, not only to
  what they say.
- **Why**: the design reference (PRAGMATA's Diana, `07-partner-architecture.md`)
  gets its humanness from a partnership: a shared goal, mutual dependence,
  doing things together, an arc. No V4 capability covered this before P.
- **Outcome**:
  - Tier 0 probe: a shared goal's progress and setbacks change the mind's
    later initiative and mood, traceably (V4-4's rule)
  - Tier 2/3 items: "felt like a partner, not a tool", against a no-shared-goal arm
  - W9's annoyance and cooldown bands still hold
- **Depends on**: W9 (goals, commitments, initiation), V4-3 (competence and
  progress drive), V4-9 (shared perception), W1 (goal facts stay true).
- **Limit class**: ENGINEERING for the mechanisms; whether it is
  experienced as partnership is the eval's question (SCIENCE).

## V4-11. Indicator-property audit (report only)

- **What**: a documented audit of which consciousness-science indicator
  properties (Butlin et al. 2023) the architecture has and lacks. Nothing is
  built to pass it, and nothing is claimed from it.
- **Why**: it tells Aniket where the architecture stands against the science,
  and it surfaces the ethics question before it arises by accident.
- **Outcome**: the report, plus a V4 decision record on whether to build
  toward any indicator.
- **Depends on**: `NF-38`.

## Not on the list, and why

- **Bottom-up brain emulation**: SCIENCE and RESOURCE limits on any
  planning horizon (`01-what-is-possible.md`, O).
- **Learning in the weights**: continual learning is unsolved, and local
  hardware limits fine-tuning. Memory-based learning stays (I).
- **Reading emotion from faces as a signal of record**: the science does not
  support it (M).
- **A physical body**: explicitly later (`00-origin.md`, section 4).
