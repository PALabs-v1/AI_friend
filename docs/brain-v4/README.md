# Brain V4: base

Started 2026-09-26. Tracked in the repo since 2026-10-28 (D-011) and worked
directly on `main` (D-016); an untracked copy remains at
`~/Projects/PALabs/brain-v4/` as an archive. Nothing here changes code.

**Where are we:** `PROJECT_STATE.md` is the live source of truth. Decisions
are in `DECISION_LOG.md`, risks in `RISK_REGISTER.md`. To pick up in a new
session: "Read `docs/brain-v4/prompts/RESUME_PROMPT.md` and continue working."

## The target, in Aniket's words

> "if we skip the physical body and look at the emotional, mental body, the
> brain, voice, vision, can't we achieve human realism there, like the humanoid
> is real, thinking, not just mere code, but an actual depth minus the body,
> which eventually my first target to achieve a humanoid mind so real it is
> close to human realism, body is long shot later"

Working definition used in these docs (`00-origin.md` explains why): **a mind
that a person, over months of real use, experiences as a coherent someone with
a life of its own, and that we can show does so with a pre-registered, blind,
measured test.** Whether it has experience is recorded as unknowable and is
not a target.

## The answer so far, in five lines

1. Buildable: almost every mechanism a disembodied mind needs is
   engineering (memory truth, affect the user can move, identity that changes
   slowly, an inner life with consequences, human turn timing, shared visual
   memory). Whether they add up to a mind people experience as a coherent
   someone over months is V4's hypothesis, untested anywhere; the eval exists
   to test it.
2. Pinch points: the language model and the user-valence estimator (W2) are
   resource limits; turn-taking behavior is unsolved even at frontier scale.
3. Open science: which cues make people attribute a mind of its own over
   months, and what units an artificial social need should have.
4. Out of reach today: establishing that it feels anything (no accepted
   test), and reading emotion reliably from faces.
5. Aniket's decisions D1-D4, taken 2026-09-27 (`06-decisions.md`):
   perceived feeling is a pure goal; internal studies first, the external
   cohort later; the model stays pluggable and every tier is tested; the
   mind always discloses it is an AI.

## Plan of record

Changed 2026-10-28 (D-009, D-010). The earlier plan was to finish V3 first.
Now:

1. V3 is frozen, not deleted. PR #217 is merged; PR #218 and the W3 builds
   are parked, with resume steps in `DECISION_LOG.md` (D-010).
2. V4 starts now as an architecture-first design cycle: research, a written
   design of the closed loop, a cold critique, then a slice plan. No code
   until Aniket approves the slices. Phases are in `PROJECT_STATE.md`.
3. The perceived-realism eval (`03-realism-eval.md`) is still built first
   among the slices, since it judges every V4 change.

## Files

| File | What it holds | Status |
|---|---|---|
| `00-origin.md` | The conversation that started V4: questions, answers, decisions, what was not decided, and the Mac-restart incident | done |
| `01-what-is-possible.md` | Capability by capability: achievable or not, and which kind of limit, with evidence | done, revised after the Codex comparison |
| `02-v3-foundation.md` | Which V3 workstream, decision or `NF-n` finding each V4 capability depends on | done |
| `03-realism-eval.md` | The perceived-realism acceptance test (Tiers 0-4) | draft; freezes after D1-D4 |
| `04-candidate-workstreams.md` | Ranked V4 work items with measurable outcomes and V3 dependencies | draft |
| `05-comparison.md` | Claude pass vs Codex pass: agreements, disagreements, resolutions, Codex citation errors, decisions D1-D4 as asked | done |
| `05-phase01-comparison.md` | Phase 01 (2026-10-28): three research passes on four tracks compared, our code defects, contradictions, the 11 queued decisions. Per-track comparisons in `research/phase01-compare/` | done |
| `06-decisions.md` | Aniket's answers to D1-D8 (2026-09-27) and what each changed | done |
| `07-partner-architecture.md` | Our brain plus partner voice and vision; the contracts between them; the PRAGMATA Diana reference; capability P | direction set, not built |
| `PROJECT_STATE.md` | Live status, phases, progress log | live |
| `DECISION_LOG.md` | Every decision, D-001 onward, with alternatives and the answer | live |
| `RISK_REGISTER.md` | Risks, mitigations, owners | live |
| `prompts/RESUME_PROMPT.md` | One-line resume for a new session | live |
| `verification-log.md` | Every source, how it was checked, what it supports | done |
| `research/abstracts.md` | Source text fetched by `tools/fetch_abstracts.py` (the evidence behind the log) | generated |
| `research/codex/R4-realism.md`, `R5-mind.md`, `R6-senses.md` | Codex's independent reports, kept unedited as a record | done |
| `research/codex/briefs/` | The exact briefs Codex was given | done |
| `tools/fetch_abstracts.py` | Re-fetches every source's abstract (Europe PMC, Crossref, arXiv) | done |
| `tools/check_v3_paths.py` | Fails if any V3 repo path these docs name has moved (existence only) | done, mutation-tested |
| `tools/check_v3_claims.py` | Fails if an owner or status `02` states for a V3 item no longer matches the repo (content) | done, mutation-tested |
| `tools/power_pairwise.py` | Sample sizes for the eval's blind pairwise tier | done |

## How to resume in a new session

1. Read this file, then `00-origin.md` and `05-comparison.md`.
2. Check V3 status only if the task touches it: `docs/brain-research-v3/README.md`
   (PR #217 is merged to `main`; PR #218 is open and frozen).
3. Run `python3 tools/check_v3_paths.py <path-to-AI_friend-checkout>` and
   `python3 tools/check_v3_claims.py <path-to-AI_friend-checkout>`.
4. Re-check sources any time with
   `python3 tools/fetch_abstracts.py > research/abstracts.md` (it rewrites
   that file from the live APIs; diff it against the previous version).

## Open items

- D-013 to D-015 are open (`DECISION_LOG.md`).
- D1-D4 decided 2026-09-27 (`06-decisions.md`). Still needed before the
  eval freezes: the Tier 2 pilot, the Tier 3 carryover design and pass
  rule, and a number for every qualitative pass rule.
- One candidate V3 finding surfaced here (the facial-reflex labels in
  `backend/app/vision/reflex.py`); to be filed in V3's ledger.
- Cold Codex critic: round 1 FAIL (9 findings), round 2 FAIL (8 findings); all acted on
  (`research/critic/round-2-disposition.md`). Two HIGH findings stay open: the
  eval is a draft design, not an acceptance test, until a pilot and frozen
  thresholds give every pass rule a number (D1-D4 are now decided). Round 3
  runs then.
