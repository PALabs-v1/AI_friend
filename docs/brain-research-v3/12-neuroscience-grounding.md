# 12. Neuroscience grounding

Brain V3 borrows names from cognitive science (ACT-R, appraisal, global
workspace, dopamine and cortisol), but until now nothing checked the borrowed
mechanisms against the models they are named after, or the system against
human data. This document makes neuroscience a formal input to Phase 7, with
one rule:

**Neuroscience proposes, BrainBench decides.** A brain-inspired mechanism is a
pre-registered hypothesis like any other. It ships only if it beats the current
code on its workstream's BrainBench acceptance, with the same ablation and Holm
correction (`06-benchmark-plan.md`). "The brain does it this way" is where an
idea comes from, never the evidence that it helps a companion.

## What "using neuroscience" means here

We work at the level of computational models: the learning rules, memory
systems and control signals that cognitive neuroscience describes
mathematically and tests against behavior. We do not simulate neurons. A
spiking or biophysical model would not run at companion scale, and it would not
make a better friend. The useful unit is a model that makes a behavioral
prediction we can measure with lifesim.

That gives three uses:

1. **Diagnosis.** Where a current mechanism claims a human model, check it
   against that model. Most do not match (next section).
2. **Design.** For each Phase 7 workstream, the human mechanism, its
   computational model, and a falsifiable prediction for our system.
3. **Validation.** Established human findings become BrainBench probes (W12),
   so "human-like" is a number with a pass band, not an adjective.

Where neuroscience offers several competing models (it usually does), we name
them and let the benchmark choose.

## The fidelity table

The full audit is `research/neuro/fidelity-table.md`: 39 mechanisms (`NF-1`
through `NF-39`), each with the exact code location, the model it names or
implies with a verified citation, the model's published form where one exists,
what the code actually does, the deviation (bug, simplification, label-only, or
dead code), a concrete correction behind a `NEURO_<NAME>_ENABLED` flag, the
pinned unit test that formula needs before it ships, a BrainBench arm with a
pre-registered prediction, and an owning workstream. Every citation used
anywhere in that table or in this document is checked at its source in
`research/neuro/verification-log.md`: of 89 references, 63 are fully verified,
24 are partly verified (identity and broad finding checked, exact equation or
parameter not), and 2 are not verified at all (an unresolved "Amory" citation
in `learning.py`, and "Marsh (1994)" for the trust model — the closer match is
Mayer, Davis & Schoorman 1995, which does not supply the code's update rule
either). Each `NF-n` is a coverage-matrix row (`work/COVERAGE.md`); nothing the
audit found is left without an owner.

Two corrections to this document's own earlier draft, caught by that
verification pass:

- The neuromodulator row below previously said "no prediction error drives any
  update." That is false: `reappraisal.py` computes a signed outcome error and
  `pipeline.py` feeds it into the dopamine/cortisol burst release. The scalars
  are still a faithful exponential effect proxy, not a chemical concentration
  model — the defect is the label, not the absence of a driving signal
  (`NF-18`, `NF-19`).
- Trust asymmetry (`|loss| > gain`) is written up below as an established
  human finding. It is not: Duncan et al.'s 2026 meta-analysis of 68 repeated
  trust-game studies (8,285 participants) finds reciprocation rate is the
  dominant factor in trust learning, but no significant prior-trustworthiness
  x reciprocation interaction across the full sample. The W12 probe and the W3
  BrainBench prediction are corrected to treat asymmetry as exploratory and
  context-stratified, not a fixed pass condition (`NF-23`).

### Highest priority (P0): defects against the model's own stated behavior, touching a measured problem

| ID | Mechanism | The defect |
|---|---|---|
| `NF-12` | User/event appraisal | Appraisal reads the agent's own mood as the event signal; a user's turn cannot reliably move valence in the measured direction (A-1) |
| `NF-39` | Novelty / habituation | Novelty is Jaccard overlap over the last 20 messages; the measured baseline has repeated content scoring *more* novel than fresh content |
| `NF-23` | Trust evidence | Trust (mislabeled Marsh 1994) is fed by the agent's own mood; neutral turns can inflate it and it saturates within 6-13 turns |
| `NF-17` | Arousal/energy separation | Derived arousal (energy + fatigue + adrenaline) is written back into persistent energy, contaminating a resource state with a transient (A-4) |
| `NF-16` | Elapsed-time affect decay | PAD decay uses the nominal tick interval, not actual elapsed time, so equal real gaps diverge under a delayed or missed tick (A-5) |

The full ranked order (12 items, with the reasoning for the sequence) is in
`research/neuro/fidelity-table.md`'s "Ranked order of work". It prioritizes
measured companion problems and code correctness over biological resemblance:
a `P3` (dead code) item can outrank a `P1` if wiring it fixes a real defect
(`NF-6`, temporal contradiction handling, ranks 6th despite being unwired code,
because M-5 stale-fact-wins is a measured, high-severity problem).

### Dead code the audit found (wire or delete, not both left standing)

`TemporalMemoryStore`/`classify_contradiction` (constructed, never called),
`PersonModel` success/failure/rupture/repair (implemented, no caller or
persistence), `LearningProgressCuriosity` and `learning_gain` (no consumer),
`evaluate_directive`/ECE calibration (not called, not updated), Rust
`apply_alma_decay` and `update_pad_from_appraisal` (unused, and the decay
helper disagrees with the Python path it would replace), the graph-edge
PPR/Hebbian weights (default hybrid retrieval never reads them). Each has an
`NF-n` row and a workstream owner; W1/W7/W8's specs name which they take.

## Per workstream: mechanism, model, prediction, probe

Each subsection is the readable synthesis; the fidelity table carries the
full formula, correction, flag, pinned test and BrainBench arm for every
`NF-n` it names.

### Memory: forgetting, spacing and consolidation (W1, W7, W12 — `NF-1` to `NF-10`)

- **Human mechanism.** Two complementary systems: the hippocampus stores
  episodes quickly, the neocortex slowly extracts general knowledge, and
  offline replay (notably in sleep) transfers and integrates one into the
  other (McClelland, McNaughton & O'Reilly 1995; Kumaran, Hassabis &
  McClelland 2016; Diekelmann & Born 2010). Forgetting follows a power
  function of time and depends on the spacing of past use (Wixted & Ebbesen
  1991; Cepeda et al. 2006).
- **Computational model.** Full ACT-R base-level learning with per-use traces
  (Anderson & Lebiere 1998), tested first because it is the closest exact
  correction to the currently named base level and can be pinned
  algebraically. Pavlik & Anderson's spacing-aware extension (2005) is a
  distinct model tested separately, not a synonym for optimized ACT-R.
  Consolidation as replay-driven abstraction during the subconscious's
  existing rest phase (`is_rest_phase`), keeping source episodes as DR-007
  requires.
- **Current gap.** `NF-1`: the default activation is neither full nor
  optimized ACT-R base-level learning — it uses only the count and the most
  recent use, so the spacing effect cannot occur by construction. `NF-2`:
  retrieval never strengthens a memory in production (both live callers pass
  `refresh_on_recall=False`), so ACT-R's reference-count history cannot be
  correct until real retrievals are recorded and separated from candidate
  exposure. `NF-4`: pruning uses fixed activation thresholds labeled ACT-R
  decay, though no published rule prescribes deletion at a threshold, and
  eventual archive expiry risks DR-007. `NF-6`: `TemporalMemoryStore` and
  `classify_contradiction` are unwired (M-5, stale facts win retrieval).
- **Prediction.** With per-use traces, facts mentioned spaced out are
  retrieved better at long delays than facts mentioned the same number of
  times in a burst, without the obsolete-win rate (M-5) rising.
- **Measured by.** W12's spacing and forgetting-curve probes; W1 and W7
  acceptance on the memory suite.
- **Variant.** `actr_full_traces` in the W1/W7 tournament, compared against
  the current approximation and, separately, against `pavlik_anderson` once
  its parameters are transcribed from the source.

### Attention: novelty and habituation (W8, W12 — `NF-39`)

- **Human mechanism.** The orienting response to a stimulus habituates with
  repetition and recovers when something new appears (Sokolov 1963; Thompson
  & Spencer 1966; Rankin et al. 2009). Familiarity is long-term recognition
  memory, not a short buffer.
- **Computational model.** Novelty = 1 − familiarity, where familiarity draws
  on long-term semantic and exposure history rather than a lexical window.
  Itti & Baldi's (2009) Bayesian surprise is a candidate quantitative form,
  but it is visual-task evidence — transferring it to text familiarity is an
  untested analogy, not a source-derived equation, and the fidelity table
  blocks shipping a correction until a real worked example exists.
- **Current gap.** `NF-39` (P0): `1 − max Jaccard word overlap` over the last
  20 messages, each truncated to 100 characters. A fact repeated after it
  leaves the window scores as new again. Consistent with the measured
  inversion: repeated content 0.75 novelty vs fresh 0.63 at 1y, Cliff's δ ≈
  −0.31 (`11-v2-lifesim-baseline.md`).
- **Prediction.** Repeated events score less novel than fresh matched events
  across paraphrases, and novelty recovers after a repeated item leaves an
  antecedent-tracked window — reproducing the measured direction without
  worsening interference filtering.
- **Measured by.** The attention suite (`repetition`, `interference`) and
  W12's habituation probe.
- **Owner.** `NEURO_FAMILIARITY_NOVELTY_ENABLED`, a BrainBench arm in **W8**,
  which owns the attention-adjacent metacognition path. See `work/W8.md`.

### Affect: emotion, mood and the user's words (W2, W12 — `NF-11` to `NF-21`, `NF-33`, `NF-36`, `NF-37`)

- **Human mechanism.** Core affect (valence and arousal) moves quickly with
  events; mood is a slower state that integrates them (Russell 2003).
  Emotions are appraisals of what an event means for one's concerns (Scherer
  2009), and in conversation the other person's words are the main event.
  Affect shows inertia: it carries over from moment to moment (Kuppens, Allen
  & Sheeber 2010).
- **Computational model.** Two timescales: a fast emotion state driven by
  appraisal of each user turn, including its social meaning, and a slow mood
  that low-pass filters emotion toward a personal baseline, with measurable
  inertia (lag-1 autocorrelation).
- **Current gap.** `NF-12` (P0): the synchronous appraisal path feeds the
  agent's own mood back in as the event signal, so user words move valence by
  exactly 0 (A-1, confirmed across every scripted conversation including
  `hostile`). `NF-17` (P0): derived arousal (energy + fatigue + adrenaline)
  writes back into persistent energy (A-4). `NF-16` (P0): decay uses the
  nominal tick interval rather than elapsed time, and the unused Rust helper
  disagrees with the Python path (A-5). `NF-19`: the reward-prediction-error
  path is a turn-surprise heuristic (reward and value are not on one
  calibrated scale, no next-state value or discount), not TD/RPE as named —
  but it *does* drive the dopamine/cortisol bursts, correcting this
  document's earlier claim that nothing does.
- **Prediction.** r(user valence, agent mood) > 0 and significant; mood
  inertia inside a human-like band (positive autocorrelation, recovery over
  hours of simulated time, not one turn); no residual arousal/energy
  contamination after a transient recovers; equal elapsed time gives equal
  affect state regardless of tick timing.
- **Measured by.** W2 acceptance on the affect suite; W12's mood-inertia
  probe.
- **Variant.** `two_timescale_affect`, `arousal_separation` and
  `elapsed_affect_time` as separate W2 BrainBench arms — each is an
  independent correction and should not be bundled into one flag.

### Trust and relationship (W3, W12 — `NF-23` to `NF-25`)

- **Human mechanism.** Trust is learned from evidence about the other
  person's behavior, through prediction errors in social reward (King-Casas
  et al. 2005; Behrens et al. 2008, volatility-aware learning). Whether trust
  is generally destroyed faster than it is built is contested: Duncan et
  al.'s 2026 meta-analysis (68 studies, 8,285 participants) finds
  reciprocation rate dominates trust learning but no significant
  prior-trustworthiness x reciprocation interaction overall, so a universal
  negativity multiplier is not established.
- **Computational model.** A delta-rule learner (Rescorla & Wagner 1972) per
  relationship variable, driven by the gap between expected and observed
  reliability, run in the W3 tournament against a Bayesian volatility-aware
  learner (Behrens et al. 2008) as a distinct competing model, not a synonym.
- **Current gap.** `NF-23` (P0): trust (the code cites "Marsh 1994," which the
  verification pass could not locate — the closer match by field labels is
  Mayer, Davis & Schoorman 1995, which does not supply an update rule
  either) is fed by the agent's own mood on every appraisal, not evidence
  about the user. Neutral or even hostile turns can inflate it, and it hits
  its ceiling in 6-13 measured turns. `NF-24`: `PersonModel`'s
  success/failure/rupture/repair evidence store exists but nothing calls it.
  `NF-25`: attachment only ever rises with message volume.
- **Prediction.** Neutral-turn mean Δtrust ≈ 0; reliability response is
  monotonic; a breach remains measurable below the ceiling within 100
  positive turns; volatile sources adapt faster than stable ones. Asymmetry
  itself is tested, not assumed, and may come back context-stratified rather
  than universal.
- **Measured by.** W3 acceptance on the trust suite; W12's trust probe (now
  written as a model comparison, not a fixed pass/fail on a universal law).
- **Variant.** `rw_social_pe` vs `bayesian_volatility` vs current, in the W3
  tournament.

### Proactive initiative and goals (W9, W12 — `NF-27` to `NF-32`, `NF-34`)

- **Human mechanism.** Intentions resurface through prospective memory,
  triggered by cues or time (Einstein & McDaniel 2005); silence is not
  necessarily rejection. Utility-based choice among competing goals has no
  single published human formula (Keeney & Raiffa's MAUT is itself an
  elicitation framework, not a source of universal weights).
- **Computational model.** The re-raise value of a thought as a learned
  quantity, updated by the user's actual response, instead of a fixed linear
  schedule. Goal-utility learning kept in ACT-R's delta-rule form, with its
  reward and credit-assignment defects fixed rather than replaced.
- **Current gap.** `NF-28` (P1): the "ACT-R goal utility RL" reward input
  (`gaze`) is never produced by anything in the codebase, so it defaults to a
  constant; combined with utilities starting above the reward ceiling,
  chosen goals can only lose utility and unchosen goals never do. `NF-30`:
  re-raise decay is a linear policy that treats silence the same as
  dismissal. `NF-27`: MAUT weights are a hand-tuned, auditable heuristic, not
  an elicited utility model — worth keeping as an engineering choice, not
  worth calling sourced. `NF-34`: `LearningProgressCuriosity` and
  `learning_gain` are dead code.
- **Prediction.** Distinguishing no-response from dismissal, and learning
  re-raise value from attributable outcomes under hard user-contact caps,
  raises the useful-initiation rate at equal or lower annoyance without
  letting a goal's utility grow from evidence that never happened.
- **Measured by.** The proactive suite (useful initiation, annoyance,
  re-raise decay); W9 acceptance.
- **Variant.** `learned_reraise` as a proactive BrainBench arm, tested after
  wave A's results are in; `goal_utility_pe` fixing `NF-28`'s reward input
  and credit assignment.

### Turn-taking and knowing what was heard (W4, W5)

- **Human mechanism.** Conversation is organized in turns with very short
  gaps across languages (Stivers et al. 2009; Levinson 2016). Speakers
  monitor their own speech (Levelt 1983) and track what has been mutually
  established, the common ground (Clark & Brennan 1991). Stopping a started
  action is a dedicated inhibitory function (Aron, Robbins & Poldrack 2014).
- **Mapping.** W4's playback lifecycle is self-monitoring (knowing how far a
  reply got); history recording only heard text is common-ground tracking;
  barge-in stop is inhibition. `NF-31` notes the proactive "turn-taking
  probability" gate is a separate, unrelated mechanism (initiative
  propensity, not live speaker-transition prediction) and should not borrow
  the same citations.
- **Status.** Already the design. No new variant; W5's property tests are the
  validation. This subsection exists so the mapping is explicit, not to add
  work.

### Everything else the audit found (W2, W8, W12 — `NF-13`, `NF-14`, `NF-15`, `NF-18`, `NF-20`..`NF-22`, `NF-26`, `NF-33`, `NF-35`..`NF-38`)

Label-only or partly-faithful mechanisms that are not on today's ranked
priority list, each with its own `NF-n` row, correction, flag and BrainBench
arm in the fidelity table: appraisal weight heuristics labeled CPM/EMA
(`NF-13`), an expectedness-to-arousal scale that breaks its own stated bounds
(`NF-14`, P1), the mood-pull reducer labeled ALMA (`NF-15`), phasic hormone
curves as a faithful proxy mislabeled as neurochemical kinetics (`NF-18`),
"reappraisal" as outcome feedback rather than Gross's cognitive-reinterpretation
strategy (`NF-20`), cortisol/adrenaline as behavioral-proxy labels with no
HPA/circadian state (`NF-21`), hand-weighted "global controls" labeled
LC-NE/Doya adaptive gain (`NF-22`), Theory-of-Mind point estimates with no
uncertainty or provenance (`NF-26`), fatigue/circadian modeling that inverts
its own intended night-recovery direction (`NF-33`), prosody coefficients
labeled hormone-to-acoustic signatures (`NF-36`), the persona compiler labeled
psychometric trait theory (`NF-37`), and global workspace as a per-turn CAS
object rather than competing-specialist broadcast (`NF-38`, owned directly by
W12 since no other workstream claims it).

## Human-data validation probes (W12)

Established human findings, each turned into a BrainBench probe with a
qualitative shape target and, where the literature supports one, a band. The
spec is `work/W12.md`.

| Probe | Human finding | What lifesim measures | Pass shape |
|---|---|---|---|
| `forgetting_curve` | Retention falls with delay, fast then slow, close to a power function (Ebbinghaus 1885; Wixted & Ebbesen 1991) | Hit rate for facts stated once, by delay since statement | Monotone decreasing; a power fit beats a linear fit |
| `spacing` | Spaced repetition beats massed at long delays (Cepeda et al. 2006) | Facts stated k times spaced vs k times in one session, probed at the same long delay | Spaced > massed, significant |
| `serial_position` | Recency and primacy within a list (Murdock 1962) | Recall of items disclosed in one session by position | U-shape: first and last above middle. Report only; a companion may reasonably lack primacy |
| `habituation` | Response falls with repetition, recovers to a new stimulus (Rankin et al. 2009) | Novelty of an item across repeats, then of a new item | Decreasing over repeats; a new item recovers |
| `mood_inertia` | Positive affective carry-over (Kuppens, Allen & Sheeber 2010) | Lag-1 autocorrelation of mood across turns; time to half recovery after a valenced event | Autocorrelation > 0; recovery spans hours of simulated time, not one turn |
| `trust_asymmetry` | Contested: reciprocation dominates trust learning; no established universal loss > gain law (Duncan et al. 2026) | Trust change after one violation vs one confirmation of equal size, stratified by prior/reciprocation | Reported as a model comparison (RW vs Bayesian-volatility vs current), not scored against a fixed `abs(loss) > gain` pass condition |

On the V2 baseline, several of these should fail today. Recording that before
any fix is the point: the probes must be able to fail.

## Competing models: which arm runs first

Where the fidelity table names more than one candidate model for the same
mechanism, the order to test is:

- **Memory accessibility.** ACT-R full-trace base-level learning first (the
  closest exact correction to the currently named mechanism, pinned
  algebraically); the optimized approximation as a storage-cost comparison;
  Pavlik & Anderson's spacing model separately, after its parameters are
  transcribed from the source — it is a distinct model, not a synonym for
  optimized ACT-R.
- **Contradictory evidence.** W1's temporal versioning/supersession against a
  latent-cause/context-retention arm (Gershman, Blei & Niv 2010). Fear-memory
  reconsolidation motivates the hypothesis; it does not settle autobiographical
  memory updates.
- **Trust.** Rescorla-Wagner social prediction error and Bayesian
  volatility-aware learning as separate W3 arms. `|loss| > gain` is not
  pre-registered as a general law (see the correction above).
- **Affect dynamics.** The current deterministic reducer, elapsed-time
  OU-like mean reversion, and a two-timescale event/mood state, compared
  against fixed-interval observations. Russell's core-affect account does not
  itself specify a fast/slow numerical filter.

## What we will not do

- Simulate neurons or claim biological fidelity. The claim is behavioral:
  the system shows the human effect, measured.
- Treat a neuro label as a reason to keep a mechanism. A name stays only if
  the code starts to mean it, or it gets renamed (P10's simplification pass).
- Add a mechanism without a probe that could show it failing.
- Ship a correction whose unit test cannot cite a source-verified worked
  example. Several `NF-n` rows are explicitly blocked on this (`NF-1`
  optimized BLL, `NF-3` Pavlik-Anderson, `NF-5` CLS replay, `NF-33`
  Borbély two-process) until that example exists; a locally constructed
  fixture is not a substitute and the table says so at each blocked row.

## Order of work

Ranked by measured companion impact and code correctness, not biological
resemblance (full reasoning per item in `research/neuro/fidelity-table.md`):

1. `NF-23` trust evidence path — neutral-turn credit and the ceiling erase the
   ability to react to harm.
2. `NF-39` familiarity and novelty — the measured repeated/fresh ordering is
   inverted.
3. `NF-12` user/event appraisal — a user's turn cannot reliably move valence.
4. `NF-17` arousal/resource separation — a transient gets baked into a
   persistent resource state.
5. `NF-16` elapsed-time affect evolution — equal elapsed time gives divergent
   results under delayed or missed ticks.
6. `NF-6` temporal contradiction wiring — a measured problem (M-5) sitting on
   unwired code, not evidence it participates in anything yet.
7. `NF-2` successful-retrieval reference bookkeeping — the base-level
   correction (next item) cannot be evaluated honestly until real retrievals,
   not candidate exposure, are what gets counted.
8. `NF-1` default base-level correction, compared against the hybrid ranker's
   existing terms and its storage cost.
9. `NF-30` proactive outcome handling — distinguish silence from dismissal
   before treating a learned re-raise rule as a permission model.
10. `NF-4` archive/retention policy — separate from activation, honoring
    DR-007.
11. `NF-14` semantic expectedness normalization — a straightforward scale-bug
    fix.
12. `NF-26` ToM evidence and calibration — point estimates hide uncertainty
    and self-report corrections.

Sequencing after that: W12's probes are built and run on the V2 baseline and
wave A (the before picture), then neuro variants enter their workstreams in
this order, then P10's simplification review applies the labeling rule above
to every remaining mechanism the audit did not prioritize.
