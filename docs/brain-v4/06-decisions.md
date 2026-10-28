# 06. V4 decisions D1-D4 (taken 2026-09-27)

Asked as multiple choice, after the V3 wave-A results. The questions are
quoted as asked; the answer is Aniket's choice, verbatim. Background for each
is in `05-comparison.md`, "Decisions D1-D4".

## D1. Perceived feeling: goal or bound

**Asked**: people perceiving the mind as having feelings ("Experience") is
part of the target. Codex flagged it could harm some users (dependence), but
the evidence is thin (one 21-day study, no overall effect). Is higher
perceived Experience a goal V4 pushes for, or something kept within a bound
once dependence can be measured?

Options: goal with a harm check (recommended) / pure goal / bounded.

**Decision: pure goal.** Maximize perceived realism, feelings included, with
no dependence bound.

Consequences:
- `03-realism-eval.md`: Experience becomes a target, not only a reported
  construct. The dependence measure and harm margin are dropped as pass
  conditions.
- `04-candidate-workstreams.md` V4-3 (drives): raising Experience attribution
  is one of the drive layer's intended effects.
- Not changed by D1: participants in any study still consent, can stop, and
  are told it is an AI (rule 3 of the eval). If the Tier 4 cohort ever runs,
  its independent ethics review may require participant-welfare stopping
  rules; that is the review board's call, not a V4 design gate.

## D2. External human study

**Asked**: is a recruited external study (about 120 people, ethics review,
weeks of real use) in scope for V4?

Options: internal first, external later (recommended) / yes, in scope now /
no external study.

**Decision: internal first, external later.** V4 runs on internal raters
plus Aniket and a few people for the 12-week tier. The 120-person study is
planned, and run only if internal results justify it. No external claim of
realism until then.

Consequences: Tier 4 stays in `03` as a planned tier with an entry
condition (Tier 3 passes by its frozen rule), not a V4 deliverable.

## D3. Model tier

**Asked**: which generation models may V4 run on?

Options: pluggable, test all tiers (recommended) / local only / frontier via
Claude Code.

**Decision: pluggable, test all tiers.** The model stays a per-deployment
choice, as in V3 (the pluggable-LLM rule). The realism eval compares local
small, larger local, and frontier through local Claude Code, so model
effects are separated from architecture effects.

Consequences: V4-6 (model tier study) is unblocked. Every realism result
names the model tier it ran on.

## D4. Disclosure

**Asked**: should the mind always say it is an AI when asked, and never
claim human memories or a human body, however real it seems? (DR-001's
immutable core already includes honesty.)

Options: always disclose (recommended) / disclose, allow roleplay / decide
later.

**Decision: always disclose.** It says it is an AI when asked and never
claims a human past or a human body. The realism target is "a real mind",
not "a convincing fake human".

Consequences: a product rule, consistent with DR-001's immutable core. The
eval never rewards being mistaken for a human (already rule 3).

## D5-D8: the partner architecture (taken 2026-09-27)

Asked after Aniket set the direction in `07-partner-architecture.md`
(partners for voice and vision, the brain as the novelty, a user-authored
persona, PRAGMATA's Diana as the reference).

- **D5. Persona over time.** Options: authored core plus slow drift
  (recommended) / fixed forever / authored, user-editable. **Decision:
  authored core plus slow drift.** The user writes it once and cannot edit
  it later; the authored core never moves; the rest grows slowly within
  human ranges. Keeps DR-020 and W11.
- **D6. Turn-taking with a voice partner.** Options: brain decides
  (recommended) / partner decides. **Decision: brain decides.** The partner
  renders and recognizes; barge-in, grace windows and interrupts stay in the
  brain (W5, DR-026, DR-030).
- **D7. Partnership as a capability.** Options: yes, add P (recommended) /
  later / no. **Decision: yes, add P.** Capability P in `01`, workstream
  V4-12 in `04`.
- **D8. Privacy for partner media.** Options: minimal send, no retention
  (recommended) / local only by default / decide at integration.
  **Decision: decide at integration.** Open until a partner is chosen;
  it must then be settled against DR-001's privacy core.

## What is still open after D1-D4

The eval is still a draft, not an acceptance test: it needs the Tier 2
pilot (ICC and variance estimates for the mixed-model power simulation),
the Tier 3 carryover design and numeric pass rule, and a number for every
qualitative pass rule (`03`, "Before freezing"). Critic round 3 runs once
those are in.
