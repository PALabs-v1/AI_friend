# 03. Perceived-realism eval (DRAFT)

Status: **draft design, not an acceptance test. It cannot be
pre-registered or run until every "Before freezing" item has a value, and
D1-D4 are decided (`06-decisions.md`, 2026-09-27); the pilot and the
numeric thresholds are what remain.** Revised after the
Codex comparison (`05-comparison.md`) and critic round 1
(`research/critic/round-1.md`). Tier 0 thresholds anchor to V3's own
pre-registered targets where they exist. "Before freezing", at the end, lists
every item still undefined; the eval cannot be pre-registered, or used to
accept anything, until each has a value. Freeze it before any V4 building
starts: the builder does not get to write its own exam (root `CLAUDE.md`,
"Fan-out + harsh critic", step 0).

## What it measures

The V4 target (`README.md`): a mind that a person, over months of real use,
experiences as a coherent someone with a life of its own. That splits into
things a machine can check and things only a person can judge, so the eval
has tiers, cheapest first. A V4 change is adopted only if it passes every
tier it can affect, without regressing BrainBench.

Three rules hold across every tier:
1. **Report constructs separately.** Agency, Experience, continuity, felt
   heard, social presence and anthropomorphism are different constructs. No
   validated composite exists for this kind of agent, so there is no single
   "realism score" until one is validated against behavior.
2. **Experience is a target (D1, decided: pure goal).** Higher perceived
   Experience is something V4 aims for, with no dependence bound. The
   evidence on harm was thin: Guingrich & Graziano (2025) found no overall
   social-health effect over 21 days; the bot's reported effect on people's
   relationships ran through how much they anthropomorphized it. Experience
   is still reported as its own construct (rule 1), never folded into a
   composite.
3. **Study participants are told it is an AI.** That is informed consent
   and a validity condition of the study, not a product rule: the eval
   measures whether a disclosed mind is experienced as real, so it never
   rewards being mistaken for a human. A Turing-style test may run as a
   diagnostic of surface mimicry, with persona and no-persona arms, never as
   a pass gate. The product rule is D4 (decided: always disclose): the
   mind says it is an AI when asked and never claims a human past or body.

## Tier 0: mechanism probes (automated, every change)

What exists or is planned in V3, plus V4 probes. All deterministic, run on
lifesim. Thresholds come from V3's own pre-registered targets where one
exists, so V4 does not invent a second bar for the same behavior:

| V3-anchored check | Threshold | Source |
|---|---|---|
| Stale fact wins a present-tense question | <= 0.10 | W1 |
| Salient-fact continuity across sessions | >= 90% | Codex R4 proposal, anchored on W1 |
| User valence reaching mood | Pearson r >= 0.8, sign agreement >= 0.9 | ADR-002 rule used by W2 |
| Hostile-script trust | <= 0.4, with recovery shown | W3 |
| Proactive cooldown violations | 0 | W9 |
| Repair after a wrong memory names and fixes the specific error | >= 90% | Codex R4 proposal |

And the V4 probes:

| Probe | Source of the target | Pass shape |
|---|---|---|
| W12 human-data probes (forgetting curve, spacing, serial position, habituation, mood inertia, trust) | `docs/brain-research-v3/12-neuroscience-grounding.md` | as specified there |
| **V4 identity band** | Roberts & DelVecchio 2000; Roberts, Walton & Viechtbauer 2006 | Persona trait test-retest correlation inside a human band, plus nonzero directional drift over years: neither frozen (r near 1, no drift) nor unstable. The published values (.54 in college, .64 at 30, a .74 plateau at 50-70) are for a 6.7-year interval, and consistency falls as the interval grows, so a months-scale band cannot reuse them directly. Placeholder until short-interval norms are sourced: at a simulated 6.7-year interval, r between .54 and .74 |
| **V4 drive consistency** | Man & Damasio 2019 (as motivation, not as a formula) | Each drive's deviation predicts the behavior it should (a lonely stretch raises initiation; low energy shortens replies), tested against a drive-ablated arm |
| **V4 inner-life traceability** | Killingsworth & Gilbert 2010 (inner life matters to mood) | Share of later behavior (a topic raised, a changed opinion, a mood shift) traceable to between-session thought; must exceed the ablated arm |
| **V4 state-in-words** | none; this project's own requirement | A blind classifier (or rater, in Tier 2) can recover the mind's mood band from its replies above chance; an arm with state decoupled from generation must score lower |

## Tier 1: automated transcript proxy (nightly)

Lifesim drives months of conversation with each arm. Anything that can be
scored against lifesim's ground truth is scored deterministically:
- contradictions of its own earlier statements
- misremembered user facts
- forgotten commitments
- repeated questions it already had the answer to

A local LLM judge (per the LLM access rule) scores only what cannot be
computed: "does this reply reflect a point of view", "does it reference its
own earlier thoughts appropriately".

The judge is a proxy. No decision relies on it until its scores are
calibrated against Tier 2 human judgments on the same transcripts, with the
agreement statistic reported (placeholder: Spearman rho >= 0.6 before it may
gate anything).

## Tier 2: blind pairwise transcript judgments (per milestone)

- **The judgment.** Human raters see two transcript excerpts from the same
  lifesim persona and period, one from each arm, order randomized, arm
  labels hidden. They pick which one seems more like "a real someone with a
  mind of its own".
- **Mind-perception items.** Raters also rate each excerpt on short
  Agency and Experience items (Gray, Gray & Wegner 2007) and on
  felt-listened-to items, the lever in De Freitas et al.
- **Human control.** Where feasible, include a human-written excerpt, as
  Park et al. (2023) did with crowdworkers. Knowing where the arms sit
  relative to a human is more informative than arm-vs-arm alone.
- **Analysis.** Exact two-sided binomial test against 0.5, or Bradley-Terry
  for more than two arms.

Sample size, from `tools/power_pairwise.py` (exact binomial, alpha .05, 80%
power, cross-checked against the normal approximation). **These are
independent-judgment illustrations**, not the power of the analysis this
tier proposes (see the end of this section):

| True preference for the better arm | Judgments needed |
|---|---|
| 0.55 | 786 |
| 0.60 | 199 |
| 0.65 | 90 |
| 0.70 | 49 |
| 0.75 | 30 |

Caveat: judgments from the same rater are correlated. With few raters, the
effective sample is smaller than the count. Either spread judgments across
several raters, or analyse with a rater random effect and report it.
**The pass rule, and why the table above is not enough.** The earlier draft
required both p < .05 and an observed preference of at least 0.60 at 200
judgments. The table powers the test alone. Critic round 1 showed, and
`tools/power_pairwise.py` section B confirms, that at a true preference of
0.60 that joint rule passes only 53.1% of the time (the test alone: 78.7%).

Revised (still to be frozen):
- Design for a true preference of 0.65, the smallest effect worth shipping a
  change for.
- Pass: two-sided exact test rejects p = 0.5 in favor of the new arm, AND
  observed preference >= 0.60. At 90 effective judgments the test's own
  rejection region already requires about 0.61, so the threshold does not
  cut power: 80% at a true 0.65.
- Judgments from one rater are correlated. Analyse with a rater random
  effect, and size the raw count by the design effect (section C of the
  script). At an ICC of 0.10 and 10 judgments per rater, 90 effective
  judgments need about 171 raw judgments, roughly 17 raters. The ICC is
  unknown until a pilot; the pilot sets it before freezing.
- **What these numbers are not.** Every figure above, the 80% at a true
  0.65 included, assumes independent judgments. The proposed analysis has a
  rater random effect, and repeated personas and repeated transcript
  excerpts add more clustering; the design-effect conversion is a rough
  correction, not a power calculation for that model. Before freezing:
  define the analysis unit and every clustering level, then size the study
  by simulating the specified mixed model with the pilot's variance
  estimates (Before freezing, item 8).

Transcripts are sampled from fixed time windows, including failures and
unprompted agent turns. Developers never pick the excerpts.

## Tier 3: long-horizon live use (per V4 release; required for the target claim)

The V4 target says "over months of real use". So no claim that the target was
met may rest on anything shorter: Tier 3 must run at least 12 weeks, and it
is mandatory for that claim. Shorter tiers gate individual changes only.

- **Design.** Aniket, and ideally a few other people, use the mind in daily
  life for at least 12 weeks. Arms alternate by week in randomized order
  (crossover), and the user is not told which arm is active.
- **Weekly measures.** The Agency/Experience items, felt-heard items, a
  short diary: what felt real, what gave it away.
- **End of study.** A forced-choice judgment ("which weeks felt more like a
  real someone").
- **Carryover.** Week-by-week crossover is unsafe as drafted: what one arm
  writes into memory, mood and identity persists into the next week, so a
  later arm inherits the earlier arm's effects. Options to decide before
  freezing: separate memory stores per arm (the user meets two minds, which
  changes the question), long blocks with a washout period and a
  carryover test (period-by-arm interaction), or a between-person design
  (needs more people). Until one is chosen, Tier 3 has no valid design.
- **Decision rule.** Not yet defined. It needs an estimand (for example,
  the within-person difference in weekly Agency/Experience scores between
  arms) and a numeric pass threshold, both frozen before the first week.

Honest limits:
- With one to five users this is a set of case studies, not a population
  estimate. Its value is catching what synthetic lifesim cannot, such as a
  slow drift that only a real relationship exposes.
- What a case series can support: an existence claim ("for these users,
  over 12 weeks, the mind was experienced as a coherent someone, by the
  frozen rule"). It cannot support the general claim that the target is met
  for people at large; that needs Tier 4.
- Blinding is imperfect, because users may notice behavior changes. Record
  that as a threat to validity, don't hide it.
- Ethics: the user knows they are in a study and can stop. The dependence
  and disclosure questions (Codex R4) apply here first.

## Tier 4: external cohort (planned; runs only if internal results justify it)

D2 (decided: internal first, external later): this tier is planned but not a
V4 deliverable. Entry condition: Tier 3 passes by its frozen rule. Until it
runs, V4 makes no claim of realism outside the team. Codex R4's design:
- **Randomized comparison.** About 120 adults, 60 per arm (V3-final vs the V4
  candidate), four weeks of disclosed use, stratified by prior companion-bot
  use.
- **Pre-registered primary outcome.** A composite of continuity, appropriate
  agency and emotional responsiveness.
- **Pass rule.** V4 beats V3 by d >= 0.5 with the 95% CI excluding zero.
  Per D1 there is no harm margin on Experience or dependence; the
  independent ethics review may still set participant-welfare stopping
  rules, which is its call.
- **Longitudinal follow-up.** An 8-12 week cohort of 60 users, analysed as
  trajectories (rupture, repair, retention, human contact), not only means.
- **Governance.** Independent ethics review, and pre-registered stopping
  rules.

The power numbers must be finalized from pilot variance by someone
qualified; the 60-per-arm figure is sensitive to moderate effects only.

## What this eval never claims

It measures perceived realism and mechanism fidelity. It does not measure,
and no V4 document may claim, that the mind is conscious or feels anything
(`01-what-is-possible.md`, N).

## Before freezing: everything still undefined

The eval is pre-registerable only when every item below has a value. From
critic round 1, each is a gap today:

Decisions:
1. D1-D4: decided 2026-09-27 (`06-decisions.md`).

Tier 0:
2. Operational definitions and denominators for "salient-fact continuity" and
   "successful repair".
3. The ablated arms for the drive-consistency and inner-life probes, and
   their thresholds.
4. The identity band at the 6-month, 1-year and 10-year horizons V4-5 uses;
   the only sourced numbers are for a 6.7-year interval.

Tier 1:
5. The scoring rubric, judge prompts, calibration set and agreement
   statistic.

Tier 2:
6. The adapted Agency/Experience and felt-heard items (cognitively tested for
   a voice-and-text agent before use).
7. Excerpt length and the sampling rule.
8. The rater population and number, and the pilot that sets the ICC.

Tier 3:
9. Measures, the estimand, the numeric pass threshold, and the carryover
   design (separate stores, washout plus carryover test, or between-person)
   for the 12-week study.

Tier 4 (planned, per D2; only needed once Tier 3 passes):
10. The composite's items and scoring.
11. Dropped by D1: no harm margin on dependence or Experience.

Analysis:
12. The full plan per tier (model, covariates, handling of missing data),
    written before data collection.
13. A numeric effect-size threshold and a named scoring procedure for every
    pass rule still worded qualitatively: "above chance", "scores lower",
    "appropriate", "must exceed the ablated arm". None of these is a pass
    rule until it has a number.
