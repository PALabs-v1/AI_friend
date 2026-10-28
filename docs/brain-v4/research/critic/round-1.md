## Verdict: FAIL

The folder is not ready to guide a senior researcher without misleading them. The central realism claim is firmer than the evidence, the human evaluation cannot be preregistered or run as written, and the pairwise power calculation does not power the stated pass rule.

## Findings

1. **HIGH — `03-realism-eval.md` §Tier 2; `tools/power_pairwise.py`. The reported power does not apply to the pass rule.** The script calculates power to reject a 50% preference, but the eval also requires the observed preference to be at least 60%. At a true preference of 60% and 200 independent judgments, I reproduced rejection power of **78.7%** and power for the *combined* pass rule of only **53.1%**. The script’s `n=199` result reaches 80% for rejection alone; it does not mean 80% power to pass the eval’s rule. The table values themselves reproduce correctly for the script’s narrower test. **Fix:** recalculate sample sizes for the joint criterion and the planned clustered analysis; define the inferential model before selecting n.

2. **HIGH — `03-realism-eval.md` §§Tier 0–4 and “Open before freezing.” The eval is not preregisterable or runnable as written.** The file itself calls human-tier thresholds placeholders and says to freeze after D1–D4. Several measures and gates remain undefined: the adapted Agency/Experience and felt-heard items; transcript excerpt length and selection rules; rater population and number; Tier 1 scoring rubric, judge prompts and calibration set; what counts as a successful repair or salient-fact continuity; the Tier 0 drive and inner-life ablations; and the thresholds for Tier 3. Tier 4 has no selected composite items/scoring or numeric harm margins for Experience and dependence. The identity criterion gives a 6.7-year simulated interval but no defined threshold for the 6-month, 1-year and 10-year outcomes proposed in `04-candidate-workstreams.md` §V4-5. **Fix:** specify the stimuli, measures, denominators, scoring, thresholds, analysis plan, and decision rules for every tier before calling this a frozen eval.

3. **HIGH — `README.md` “The target”; `03-realism-eval.md` §§What it measures, Tier 3, Tier 4. The acceptance test does not require the stated “over months of real use” outcome.** Tier 3 uses “several weeks” and has no pass threshold. The 8–12-week cohort is optional under D2, while Tier 4’s main comparison lasts four weeks. A candidate could therefore pass without demonstrating the README’s months-long real-use target. **Fix:** make a specified months-long live-use tier mandatory for any claim that this target was met, and set its measures and pass criteria.

4. **HIGH — `00-origin.md` §3; `01-what-is-possible.md` summary and §B. The folder presents long-horizon perceived realism as reachable engineering despite acknowledging that its causal basis is unknown.** `01` says “almost everything” that makes a mind seem real over months is engineering and marks coherence over months “Yes.” Yet it also says no blind, preregistered months-long study was found and identifies which cues cause long-term mind attribution as an open science question. Codex’s R4 report likewise says the causal recipe is not established and classifies sustained attribution as SCIENCE. The evidence supports building memory, affect and identity mechanisms; it does not establish that those mechanisms will make users experience a coherent someone over months. **Fix:** distinguish implementable mechanisms from the unproven perceptual outcome; state long-horizon realism as a research hypothesis, not an established reachable result.

5. **HIGH — `03-realism-eval.md` lines 23–30 and 154–163; `05-comparison.md` D1/D4. The risk and disclosure rules overstate the evidence and pre-empt an open decision.** The eval says evidence ties perceived Experience to “dependence,” but Guingrich & Graziano’s abstract reports no significant overall social-health or relationship impact after 21 days; its mediation concerns reported social impacts among people who anthropomorphized the chatbot, not a measured dependence outcome ([source abstract](https://arxiv.org/abs/2509.19515)). Separately, “Users always know it is an AI” is imposed as an eval rule while D4 remains open and asks whether the system must identify as AI when asked. `DR-001` records honesty and privacy generally; it does not supply that specific disclosure rule. **Fix:** describe dependence as a hypothesis requiring a defined measure, and resolve D4 before making disclosure an eval constraint.

6. **MED — `01-what-is-possible.md` summary, §§F and J. Limit classes are inconsistent between the summary and section text.** F’s summary and heading say ENGINEERING / UNKNOWABLE, but the section says social-need units and set points have no validated basis and labels that part SCIENCE (lines 209–212). J’s summary says ENGINEERING + RESOURCE, while its section heading says RESOURCE, even though the section later classifies turn-taking behavior as ENGINEERING (lines 317–319). **Fix:** assign classes to each sub-capability and repeat the same classification in the table, heading and prose.

7. **MED — `01-what-is-possible.md` §B lines 99–103; §N lines 400–405; `05-comparison.md` lines 91–106; `verification-log.md` lines 43–57. Some claims are presented more strongly than their logged verification status.** The Skjuve abstract supports the 28-person, 12-week study and four trajectories. The log marks poor memory and generic scripts as costs **CODEX-REPORTED**, but `01` presents those details without that qualifier. The “scales converge only modestly” claim in `03` relies on a citation that `05` identifies as misattributed; the verification log says the relevant correlation figures remain unverified. **Fix:** qualify the Skjuve details as CODEX-REPORTED, and remove the scale-convergence claim or verify it against the correctly identified Ruijten et al. paper.

8. **MED — `verification-log.md` “Local facts checked in the repo”; `01-what-is-possible.md`; `02-v3-foundation.md`. The log does not cover all factual V3 claims as the docs promise.** For example, `01`’s “0 of 3” estimator result relies on `09-gpu-experiment-results.md`, and `02` reports merged workstreams, wave-A status and next steps. The local-facts table lists neither the estimator result nor those status claims; it lists only selected baseline, voice, vision, expression and decision facts. The checker verifies that paths exist, not that claims about them are true. **Fix:** log each relied-on V3 fact with its exact source location and how it was checked, keeping existence checks separate from content verification.

9. **MED — `01-what-is-possible.md` §N and the limit-class definition. “UNKNOWABLE” is stated more absolutely than the cited evidence supports.** The class is defined as “No test could settle it,” and N says experience “cannot be established.” The cited theories and indicator-property assessments support that there is no accepted way to establish subjective experience from these behavioral measures; they do not establish that no test could ever settle the question. **Fix:** describe the current evidentiary limit—no established test or accepted behavioral criterion—without asserting impossibility in principle.

## What I checked

**Resume steps and commands**

- Read `README.md` first, then every top-level `.md`: `00-origin.md`, `01-what-is-possible.md`, `02-v3-foundation.md`, `03-realism-eval.md`, `04-candidate-workstreams.md`, `05-comparison.md`, and `verification-log.md`.
- Checked the linked checkout status: `## brain-v3...origin/brain-v3`.
- Ran `python3 tools/check_v3_paths.py /Users/aniketsaha/.claude-worktrees/AI_friend-2527627756/10f9894b`. Exit 0: `checked 17 repo paths named in brain-v4/*.md`. This checks existence, not claim accuracy.
- Ran `python3 tools/power_pairwise.py`. Exit 0; output:

  ```text
  true preference | exact n (80% power, alpha .05) | normal approx
  0.55            |   786                         | 782.5
  0.60            |   199                         | 193.8
  0.65            |    90                         | 84.8
  0.70            |    49                         | 46.6
  0.75            |    30                         | 28.9
  ```

  I also computed power under the eval’s combined pass rule; at `n=200`, `p=true=.60`, rejection power is 0.7868 and combined-gate power is 0.5307.

- Did not run the abstract-fetch command: its README example redirects over `research/abstracts.md`, and the review was explicitly read-only.

**Scientific claims spot-checked**

- Jones & Bergen: `research/abstracts.md` confirms the reported five- and 15-minute humanlikeness percentages and that the measure is a Turing-test judgment. The participant/game details are marked full-text checked in the log.
- Skjuve et al.: the abstract confirms n=28, 12 weeks and four relationship-depth trajectories; it does not contain the memory-failure or generic-script details.
- Guingrich & Graziano: the abstract confirms n=183, 10 minutes/day for 21 days, no significant overall social-health/relationship impact, and the anthropomorphism mediation. It does not report a dependence measure.
- Keramati & Gutkin and Laurençon et al.: the abstracts support homeostatic reinforcement learning and the continuous-time extension; they do not establish human-like emotion or social needs.
- Reece et al.: checked corpus size in `research/abstracts.md` and the primary paper. The 33.7% and 65.5% backchannel rates are reported in the paper, with a defined turn-segmentation method; they are not universal rates. ([Primary paper](https://pmc.ncbi.nlm.nih.gov/articles/PMC10065445/))
- Peng et al.: checked the reported 61.7%/34.1% Moshi figures in the benchmark paper. They are specific to its simulated-input interruption conditions, not general conversation competence. ([Paper DOI](https://doi.org/10.21437/Interspeech.2025-739))
- Lavan et al.: the abstract supports voice clones sounding as real as human voices and no observed hyperrealism effect; finer values are separately marked CODEX-REPORTED.
- Ehret et al.: the abstract supports the 40-participant prosody/embodiment finding.
- Barrett et al.: the abstract supports cross-cultural, situational and individual variability in facial-expression interpretation.
- Roberts & DelVecchio, Roberts et al. 2006, Gray et al., and Strachan et al.: spot-checked the personality estimates, mean-level-change directions, Agency/Experience distinction, and theory-of-mind summary against `research/abstracts.md`.

**V3 paths opened and claims checked**

- `docs/brain-research-v3/README.md`: phase and workstream status.
- `docs/brain-research-v3/09-gpu-experiment-results.md`: 0/3 valence estimators pass the stated rule.
- `docs/brain-research-v3/12-neuroscience-grounding.md`: neuroscience proposes; BrainBench decides, and the audit’s mechanism claims.
- `docs/brain-research-v3/work/W1.md`, `W2.md`, `W3.md`: memory, affect and trust criteria. W2 specifies sign agreement on non-neutral messages.
- `docs/brain-research-v3/decisions/DR-024-latency-budgets.md`: 300–500 ms first-audio-byte budget.
- `docs/brain-research-v3/11-v2-lifesim-baseline.md`: 1,203 ms p50 / 2,276 ms p95 foreground processing; the document explicitly distinguishes this from time-to-first-audio.
- `docs/brain-research-v3/04-local-infrastructure.md`: the voice-weights statement is in a “not yet re-verified against code” section.
- `backend/app/vision/reflex.py`, `vision/agent.py`, `agents/brain_agent.py`, and `state/agent_state.py`: thresholds/deltas, conditional camera/reflex wiring, subscription, and affect application. Runtime activation depends on the optional dependency and model asset.
- `backend/app/cognitive/expression.py`: valence/arousal map to an affect label and breath level; this does not itself show that state changes generated words.

No files were edited. V4 IDs 1–11, ranking references, capability letters A–O, and D1–D4 references were cross-checked; I found no ID or ranking mismatch.
