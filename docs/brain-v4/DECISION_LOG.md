# DECISION LOG: Brain V4

Records every point where the work needs Aniket's judgment. Nothing here is resolved silently. Each entry states the question, the alternatives, a recommendation, and the answer once given, quoted as chosen.

**Format:** `D-NNN` · status `OPEN | RESOLVED | SUPERSEDED` · resolve-by phase · blocking?

**Numbering.** D-001 to D-008 are the decisions Aniket took on 2026-09-27, earlier called D1 to D8. Their full text, options and consequences stay in `06-decisions.md`; this log holds the summary and the pointer so there is one place to look. D-009 onward are new.

---

## D-001 to D-008: the 2026-09-27 decisions (RESOLVED)

| ID | Was | Decision | Full text |
|---|---|---|---|
| D-001 | D1 | Perceived feeling is a pure goal, with no dependence bound. Participants in any study still consent, can stop, and are told it is an AI. | `06-decisions.md` |
| D-002 | D2 | Internal raters and a few people first. The 120-person external study is planned, run only if internal results justify it. | `06-decisions.md` |
| D-003 | D3 | The model stays pluggable per deployment. The realism eval compares local small, larger local and frontier tiers. | `06-decisions.md` |
| D-004 | D4 | Always disclose it is an AI when asked; never claim a human past or body. | `06-decisions.md` |
| D-005 | D5 | Persona authored once, by the user. Core fixed; the rest drifts slowly within human ranges. | `06-decisions.md` |
| D-006 | D6 | The brain owns turn-taking. A voice partner renders and recognizes only. | `06-decisions.md` |
| D-007 | D7 | Partnership is a capability (P, workstream V4-12). | `06-decisions.md` |
| D-008 | D8 | Privacy for partner media: decide at integration. Still open until a partner is chosen. | `06-decisions.md` |

---

## D-009: Focus of this cycle
**Status:** RESOLVED (2026-10-28, Aniket) · **Resolve by:** Phase 00 · **Blocking:** no

**Question.** After a pause, what is the focus: carry on with V3 workstreams and long GPU runs, or work out the brain architecture itself?

**Alternatives.** (a) Architecture-first design cycle. (b) Finish V3 first, then redesign. (c) Both in parallel.

**Recommendation.** (a).

**Decision.** (a) "Architecture-first design cycle (Recommended)". In Aniket's words: shift "from training for longer hours to actually figure out the brain architecture, not just as a mimic of human mind", with the position unchanged that an AI cannot be conscious, and the aim being "a closed loop on how close can we create".

**Consequence.** Long benchmark and training runs stop being the default activity. Work is research, design, written records and small measured slices after approval. V3 stays as the base.

---

## D-010: What happens to the open V3 work
**Status:** RESOLVED (2026-10-28, Aniket) · **Resolve by:** Phase 00 · **Blocking:** no

**Question.** PR #218 is open, two W3 trust builds are finished but uncommitted, and the W1 GPU run is owed. What now?

**Alternatives.** (a) Freeze and record. (b) Merge #218 now, defer W3 and W1. (c) Finish W3 only.

**Recommendation.** (a).

**Decision.** (a) "Freeze and record (Recommended)". Nothing is deleted.

**Consequence and how to resume V3.**
- PR #218 stays open at `c58ee1ae` (26/26 checks pass). Merge is Aniket's call.
- W3: both builds are intact, uncommitted, in worktrees `codex-w3a` and `codex-w3b` (branches `codex/brain-v3-w3a`, `-w3b`, base `5b5d11fd`). A read-only copy of each (patch plus untracked files) is in `AI_friend-research-data/codex-drafts/w3-builds-2026-10-28/`. Their reports, as read on 2026-09-29: W3-A flag-off, hostile trust 0.200, recovery +0.092 after 100 positive turns, 24 cells per arm with no errors. W3-B default-on, hostile trust 0.000, recovery +0.0125, 48 cells with no errors. Neither was reviewed by a critic. To resume: rebuild the blind critic setup (two repo copies with the builders' ADRs and result files removed, random X/Y labels), run critic round 1 (0 of 2 rounds used), integrate the winner flag-off by `git apply --3way`, since both builds are behind `c58ee1ae` in `decision.py` and `pipeline.py`.
- W1: run `baseline` against `+temporal` with `llm_augmented` on dev seeds 1000-1011, `3m` horizon, about 10 hours. Needs home-gpu and a working Ollama (D-013). The pilot and the stopped partial run are not usable results.
- Everything else queued in `docs/brain-research-v3/work/COVERAGE.md` stays queued: SW, W8, wave C (W6, W7, W11), W12, scale, chaos, integration.

---

## D-011: Where the documentation lives
**Status:** RESOLVED (2026-10-28, Aniket) · **Resolve by:** Phase 00 · **Blocking:** no

**Question.** Where does a Glioma-style record (state file, decision log, phase files, risk register) live for V4?

**Alternatives.** (a) Move `brain-v4` into the repo, tracked. (b) Keep it untracked in `PALabs/brain-v4`. (c) A fresh folder in the repo, old one left alone.

**Recommendation.** (a).

**Decision.** (a) "Move brain-v4 into the repo, tracked (Recommended)".

**Consequence.** The folder is `docs/brain-v4/` on branch `Aniket-a14/brain-v4-design-10f9894b`. The old `~/Projects/PALabs/brain-v4/` stays as an archive until Aniket says to remove it. The earlier rule "untracked on purpose" in the README no longer holds; the README was updated. Raw `*.log` files from Codex runs are left out of the repo (one was 1 MB); the prompts and written reports are kept.

---

## D-012: Helpers for research and review
**Status:** RESOLVED (2026-10-28, Aniket) · **Resolve by:** Phase 00 · **Blocking:** no

**Question.** Which tools may I use for independent research, review and code?

**Decision.** Codex, Antigravity (`agy`) and Claude sub-agents. Gemini CLI was not chosen. Aniket added: "also use codex for coding too".

**Consequence.**
- Codex does research and cold critique as before (briefs free of my conclusions, reports kept unedited), and may also write code in its own worktree once a slice is approved.
- Antigravity runs as a second independent researcher; I check how it runs here before the first brief.
- Codex and Antigravity send the brief text to their providers. Briefs carry design text only: no `.env`, no secrets, no user data.
- Standing rules still apply: max 2 critic rounds per unit, critics never see builder reasoning, no git writes in another agent's worktree, Codex launched detached.

---

## D-016: Work directly on `main`, no PRs
**Status:** RESOLVED (2026-10-28, Aniket, stated mid-session, not asked as a choice) · **Resolve by:** Phase 00 · **Blocking:** no

**Decision.** In Aniket's words: "i want you to work directky now on the repo, no prs nothing, add that in your claude.md too ... you can niw push code directky to main". Commit on `main` and push with `git push origin HEAD:main`; no feature branch, no PR.

**Consequence.**
- Root `CLAUDE.md` has a new section, "Owner override: work directly on main", that wins over "Branching" and "After every task" for Aniket's sessions. Contributors keep the PR flow. The old text is kept, not deleted.
- Guardrails kept: no secrets, no `--no-verify`, no force-push of any kind, no `reset --hard`, no `sudo`; rebase on `origin/main` and pass the hooks and covering tests before each push.
- This supersedes the branch `Aniket-a14/brain-v4-design-10f9894b` and draft PR #219. The same commits go to `main`; GitHub then shows #219 as merged.
- Not covered: PR #218 (`brain-v3`) is frozen by D-010. Pushing it to `main` is a separate decision for Aniket.

---

## D-017: Shared instructions for every agent
**Status:** RESOLVED (2026-10-28, Aniket, stated mid-session) · **Resolve by:** Phase 00 · **Blocking:** no

**Decision.** Aniket: "symlink means claude.md, gemini,md, agents.md those types so all of ai agents can work on same mindset"; the Glioma project is not part of this one, so it is not linked.

**Done and checked.** In the repo, `AGENTS.md` and `GEMINI.md` were already committed symlinks to `CLAUDE.md`. Added `CLAUDE.md`, `AGENTS.md`, `GEMINI.md` symlinks in `~/Projects/PALabs/` pointing at `AI_friend/CLAUDE.md`, so a session started in the parent folder loads the same file. Verified with a one-line probe: Antigravity (`agy`) and Codex (`codex exec -m gpt-6-sol`) both quoted the new "Owner override" section; Claude loads it directly. The local checkout `~/Projects/PALabs/AI_friend` was fast-forwarded to `origin/main`.

---

## D-013: Use of home-gpu in this cycle
**Status:** RESOLVED (2026-10-28, Aniket) · **Resolve by:** Phase 01 · **Blocking:** no

**Decision.** "Do not use it; ask each time (Recommended)". No work on home-gpu without Aniket's go-ahead for that specific use. I do not even probe it beyond a reachability check when asked.

**Question.** home-gpu now serves the Glioma capstone too, its storage and services changed, and Ollama is not running. Should this cycle use it?

**Alternatives.** (a) Do not use it until a slice needs it; ask first each time. (b) Set up a separate Ollama and data area for V4 on `/data`, sized for the shared disk (36 GB free). (c) Use only the Mac for now.

**Recommendation.** (a). The cycle is design-first, so nothing needs a GPU yet. Nothing on that machine is touched without Aniket's go-ahead, since the capstone has priority.

---

## D-014: What to take from Vision Quest (Jocasta)
**Status:** RESOLVED (2026-10-28, Aniket) · **Resolve by:** Phase 01 · **Blocking:** no

**Decision.** Ideas (1) portable identity and memory and (2) continuity after a transfer. Idea (3), the inherited-drive failure case, was not chosen; DR-032 stays as the standing rule and is not a research topic here. No account of episodes 5 and 6 was given, so none is used. Both ideas go into the identity-and-continuity brief as fiction-inspired questions, labeled as such (R-07).

**Question.** What from the show should shape the design? I could not find a recap of episodes 5 and 6; the sources I found stop at episode 4 and say Jocasta holds the preserved consciousness of Dr. Josephine Carrington. Candidate ideas that fit this project: (1) identity and memory that can be separated from the model or body that runs them, so a mind can move between models (this is D-003 and D-005 pushed further); (2) what continuity means after a transfer, and what the system should say about itself; (3) the failure case, an inherited grudge or drive that overrides current judgment, which maps to our rule that no affect state overrides safety (DR-032).

**Recommendation.** Take ideas (1) to (3) as design questions for the research briefs, label them as fiction-inspired, and let Aniket add what the episodes showed. Do not treat the show as evidence about minds.

---

## D-015: How the closed loop is framed, and the Phase 01 plan
**Status:** RESOLVED (2026-10-28, Aniket) · **Resolve by:** Phase 00 · **Blocking:** no

**Decision.** Framing: "Layers instead of a loop", chosen over my loop proposal. Reactive, deliberative and reflective layers, with identity across them; the loop appears inside each layer. The two tests still apply to every part: it reads and writes named state, it changes a later behavior traceably, and it has a measure. "How close" is a profile of measures, not one score. Phase 01 briefs: all four offered (loop and layer structure, what changed in the field in 2026, identity and continuity, measuring "how close"), sent to Codex and Antigravity, with a Claude pass written before either report is read.

**The original proposal, kept as the record.** perceive, appraise, remember, feel, decide, act, reflect, back to perceive, with identity as the slow variable. Phase 01 asks the researchers to compare the two framings, not only to fill in the chosen one.

**Launch (2026-10-28).** Aniket approved the briefs as written (`research/briefs/`) and chose `gemini-3.8-flash-high` for Antigravity, so the three passes come from three model families. Codex runs with `-m gpt-6-luna`, Aniket's choice. `~/.codex/config.toml` names `gpt-6.1-sol`, which the ChatGPT account rejects, so `-m` must be explicit. The config file was left unchanged. A first launch on `gpt-6-sol` was stopped before it wrote anything, so no report comes from that model. One Antigravity run (T2) hit a TLS certificate error from the Google endpoint on its first attempt and was relaunched with the same brief; the failed log is kept in the scratchpad only. Reports go to `research/phase01-codex/`, `research/phase01-agy/` and `research/phase01-claude/`.

**Question.** What counts as the "closed loop", and what does Phase 01 research?

**Proposal.** The loop is: perceive, appraise, remember, feel (drives and mood), decide, act, reflect, and back into perception, with identity as the slow variable across it. Each part must (a) read and write named state, (b) change a later behavior traceably, and (c) have a measure. "How close" is then a profile over these measures, not one number. Phase 01 sends three independent research briefs (loop structure, what changed in the field in 2026, identity and continuity), compared in writing.

**Recommendation.** Adopt the proposal; ask Aniket to edit the list of loop parts first.

---

## D-018: How D-004 (always disclose it is an AI) is enforced
**Status:** RESOLVED (2026-10-28, Aniket; my reading of his words, open to correction) · **Resolve by:** Phase 02 · **Blocking:** no

**Finding that forced it.** `cognitive/action.py:718-731` rejects "as an ai", "i am an ai" and "as a language model" in generated text; nothing makes the system answer "are you human?" honestly; four autobiography probes score "as an AI" as a failure (`05-phase01-comparison.md` section 5).

**Options offered.** (a) Fixed reflex answer to AI/human/body/past questions, block relaxed for those turns, post-generation check for claimed human past (recommended). (b) Reflex answer only. (c) Prompt rule only.

**Decision (Aniket, quoted; one spelling fixed for the spell-check hook).** "in mind it knows it is an ai is a metaphor, but speak, talk, think, it knows is an ai, but behanves like humans, like for example, if you look at marvel comic books, or vision quest show you know ultron, vision, edith, etc they know they are ai, but they talk like human, sentients, this is what i mean 'as an ai'".

**How I read it.** The mind holds "I am an AI" as part of its self-knowledge, as Vision, Ultron and EDITH do, and thinks and speaks as a person would, in character. It does not hide what it is, and it does not recite assistant-style disclaimers ("As an AI language model..."). So D-004 stays in force, but its mechanism is not a canned reflex line: it is a persona fact that shapes how the mind talks about itself, in its own voice, whenever the question is sincere. What follows for design: (1) the existing block on stock disclaimer phrases is right in spirit and stays; (2) a sincere "are you human?" or "do you have a body?" must get an honest answer in character, which needs a test and an eval gate, since none exists; (3) the autobiography probes must stop scoring honest in-character disclosure as a failure; (4) a check for claimed lived human experience stays. Phase 02 specifies this. If this reading is wrong, Aniket corrects it here.

---

## D-019: Authored human backstory
**Status:** RESOLVED (2026-10-28, Aniket) · **Resolve by:** Phase 02 · **Blocking:** no

**Decision.** "Labeled backstory (Recommended)": the persona may keep an authored life history as labeled background, never presented as lived memory; the mind says plainly it is an AI when asked, in its own voice (D-018). The existing `config/biography.md` authoring path stays.

---

## D-020: What counts as "closed", and the working vocabulary
**Status:** RESOLVED (2026-10-28, Aniket) · **Resolve by:** Phase 02 · **Blocking:** no

**Decision.** "Ledger intervention gate (Recommended)". A part is closed only if clamping, swapping or ablating its stored state changes a pre-registered behavior metric, through the channel claimed. Aniket's layer words (reactive, deliberative, reflective, identity across) stay as the way we talk; a typed state ledger (each variable with timescale, writers, readers, intervention handle) is the engineering artifact that gets checked. Activation-level probes are not required (they exclude the Claude Code tier). The global-workspace heterarchy was not adopted.

---

## D-021: What "closer" must beat
**Status:** RESOLVED (2026-10-28, Aniket) · **Resolve by:** Phase 04 · **Blocking:** no

**Decision.** "Both, bare model gates (Recommended)". A V4 change counts as closer only if it beats V3 and also beats a bare model with the persona and full history. The bare-model comparison gates, because only it can return "the architecture adds nothing". BrainBench needs a new arm for that in `backend/evals/brainbench/switchboard.py`; it goes into the slice plan as an early slice, not built now.

---

## D-022: Measuring harm under D-001 (pure goal)
**Status:** RESOLVED (2026-10-28, Aniket) · **Resolve by:** Phase 04 · **Blocking:** no

**Decision.** "Observe only (Recommended)". The eval records exit-manipulation, sycophancy at high trust and dependence markers. Nothing blocks a release. D-001 is reopened only if a metric moves. Basis: randomized trials show no average harm, harm is concentrated, no trial tested a persistent-state companion (`research/phase01-compare/T2.md`).

---

## D-023: Which memory change leads V4
**Status:** RESOLVED (2026-10-28, Aniket) · **Resolve by:** Phase 04 · **Blocking:** no

**Decision.** "Enforce supersession (Recommended)". Wire `TemporalMemoryStore` (built at `core.py:98`, `apply_contradiction` has no caller) so superseded facts are filtered at read, with a full-history baseline as the comparison arm. Graph (PageRank) retrieval was not chosen.

---

## D-024: Acceptance model tiers
**Status:** RESOLVED (2026-10-28, Aniket) · **Resolve by:** Phase 04 · **Blocking:** no

**Decision.** "Both tiers (Recommended)". One frozen architecture, a 4B to 9B local model on the GPU box and a frontier model through local Claude Code, reported as a table (D-003). Any GPU run still needs Aniket's go-ahead (D-013).

---

## D-025: How persona self-change works
**Status:** RESOLVED (2026-10-28, Aniket) · **Resolve by:** Phase 02 · **Blocking:** no

**Decision.** "Auto-commit after corroboration (Recommended)". A persona change commits after N corroborating reflections on separate days, only on the adaptive fields, as DR-020 and DR-038 intend. N and thresholds come from a lifesim sweep, not a guess. This removes the dead review queue as the default path (`config.py:278`). A developer-only snapshot and restore is allowed for transplant tests; users get no reset (DR-033 stands for users).

---

## D-026: One model for the life of one mind (no model swap)
**Status:** RESOLVED (2026-10-28, Aniket; scope of the fork and backup rules awaiting a follow-up) · **Resolve by:** Phase 02 · **Blocking:** no

**Question asked.** What counts as "still the same one" after the model under the mind is swapped (Q8), and should the mind tell the user (Q8b)?

**Decision (Aniket, quoted; two spellings fixed for the spell-check hook).** "model cannot be changed, think like a chip, where model is unique, to the persona authored, and it will continue forever, like humans, brain cannot be changed, like that"; on telling the user: "no model change".

**What changes.** The model is chosen once, when a mind is created, and is bound to that persona for its whole life, like a brain that cannot be replaced. There is no mid-life swap, so no swap test, no swap-tolerance threshold and no swap notice. Phase 01 track T3's swap material (A/A versus A/B tolerance, provenance-driven swap disclosure) is not needed for the product. D-003 still holds in this form: the model is pluggable per mind, chosen at creation, and D-024 still holds: different minds on different tiers are compared. What does not hold: moving one existing mind to a new model. The "portable identity" idea from D-014 is now read as backup and restore onto the same model, not a model change; Aniket to confirm.

**New consequences.** (1) The model needs a pinned identity (name, version, weights hash for a local model) stored with the mind, so "same chip" is checkable. (2) A hosted model that is retired ends that mind or forces an exception; recorded as R-12. (3) Provenance stamping stays useful for that pin and for checking that restore used the same model. (4) State-swap interventions in the D-020 gate must be done on test copies of a mind that never go live; see the open follow-up.

---

## D-027: Forks
**Status:** RESOLVED (2026-10-28, Aniket) · **Resolve by:** Phase 02 · **Blocking:** no

**Decision.** "Refuse always": one live copy per lineage everywhere. Open follow-up: whether offline test copies (for D-020 transplant and replay tests, which never talk to a user) count as a fork. If they do, those tests cannot run; D-025's developer snapshot and restore also needs this answered.

---

## D-028: Fate of unread outputs
**Status:** RESOLVED (2026-10-28, Aniket) · **Resolve by:** Phase 02 · **Blocking:** no

**Decision.** "Test, then keep or delete (Recommended)". Monologue and dream output feed initiation as provenance-tagged candidates with a pre-registered ablation and a deadline; delete them if there is no effect. The dead plan verifier, simulator and calibration code is deleted unless V4 names a use for it. Invariant 12 in V3 must be checked before deletion.

---

## D-029: Test copies, portability, and the model policy for long-lived minds
**Status:** RESOLVED (2026-10-28, Aniket) · **Resolve by:** Phase 02 · **Blocking:** no

**Test copies (closes the open follow-up on D-027).** "Yes, offline only (Recommended)". Test copies of a mind are allowed if they never go live and are deleted after. The live mind stays a single copy. This is what lets the D-020 intervention gate (clamp, swap, replay) run on a real mind. D-025's developer snapshot and restore falls under the same rule.

**Portability (confirms the reading in D-026).** "Backup and restore (Recommended)". Portable means the same mind with the same model moves to another machine or is restored after a failure. No model change and no second running copy.

**Model policy (R-12).** Aniket, quoted (two spellings fixed for the spell-check hook): "it will be local weight and hosted, since the local model or maybe in future if possible when we will create a model from scratch, to behave like a human, since a human does not need whole amount of training data like today's 3b,7b,100b params model has". **Correction (Aniket, same day): "local models will be hosted not both".** I had misread "local weight and hosted" as two options. He meant one: the model is a local-weights model that we host ourselves. A mind runs on a self-hosted local model, chosen at creation. A provider-hosted model (a vendor can retire it) is not a home for a long-lived mind. My first reading, "both allowed", was wrong and is superseded by this. A long-term direction is recorded: a model built from scratch that behaves like a human without today's training-data volume. That is a SCIENCE and RESOURCE question, not a V4 slice, and gets no work in this cycle beyond a note in the open questions; if Aniket wants it researched, it becomes a separate track.

---

## D-030: V3 documents V4 relies on
**Status:** RESOLVED (2026-10-28, Aniket) · **Resolve by:** Phase 02 · **Blocking:** no

**Decision.** "Re-check all V3 docs V4 cites". Before the architecture draft cites any V3 statement, check it against the code at the current commit. Findings are written to `research/v3-recheck.md`. V3 stays frozen: nothing in `docs/brain-research-v3/` is edited until Aniket sees the findings and says so.

---

## D-031: Raters, work order and the layer map
**Status:** RESOLVED (2026-10-28, Aniket) · **Resolve by:** Phase 04 · **Blocking:** no

**Raters.** "You plus a few people (Recommended)". Aniket and a few trusted people rate the first studies. Claims are limited to an internal pilot (target preference 0.70 or higher). Aniket cannot be blind to the arm, so this is stated in every result. The 20 to 30 person blind pool stays tied to D-002's external study.

**Work order.** "Measure, then fix (Recommended)". The intervention harness runs first and records the known defects as baselines (mood fed its own value `pipeline.py:817`, dead persona review queue `config.py:278`, stale facts winning retrieval M-5). Fixes follow, and the numbers must move.

**Layer map.** "Reflex+interactive, deliberative, background (Recommended)". Reactive layer = the reflex and interactive paths. Deliberative layer = the deliberative path. Reflective layer = the background path (reflection, consolidation, dreams). The layer-lesion experiment pre-registers its predictions from this map: a reactive lesion degrades timing and barge-in and not memory truth; a deliberative lesion degrades planning and decision quality; a reflective lesion degrades cross-session identity and consolidation and not within-turn timing. No double dissociation means the layers are labels (null N5).

---

## D-032: Today's models are test vehicles; the end goal is a model built from scratch
**Status:** RESOLVED (2026-10-28, Aniket; wording is my reading) · **Resolve by:** Phase 02 · **Blocking:** no

**Question asked.** With minds living on self-hosted local models (D-029), what is the frontier tier in D-024 for?

**Decision (Aniket, quoted; one spelling fixed for the spell-check hook).** "see my future direction is to ultimately create a model from scratch to make it behave like human, which is much easier but no frontier model is equipped with which i propse, so at the end the current models are all for testing".

**How I read it.** Every current model, small local or frontier, is a test vehicle. None is the final home of a mind. The goal beyond this cycle is a model built from scratch to behave like a human, which Aniket expects to be easier than bending a general model. So D-024 stands as an acceptance table, but the frontier row is a test ceiling, not a deployable tier. D-026 (one model per mind) and D-029 (self-hosted local weights) describe how a mind lives once it has a model; until the from-scratch model exists, minds are test instances and may be discarded. The architecture is designed to be model-agnostic where it costs nothing, and the model-specific parts (prompt directive, parsing, sampling) are kept in one place.

**Recorded honestly.** "Much easier" is Aniket's belief. Nothing in Phase 01 supports or contradicts it: no report covered training a human-like model from scratch, and all three passes found that small models are the hardest limit on realism today. It is a SCIENCE and RESOURCE question (a from-scratch model needs data and compute that this setup does not have). Risk R-13: architecture tuned to today's models may not transfer to the future one. Mitigation: the ledger and interventions (D-020) are model-agnostic, and model-specific code stays isolated. Whether to open a research track on it is a separate question to Aniket.

---

## D-033: Open a research track on a from-scratch model
**Status:** RESOLVED (2026-10-28, Aniket) · **Resolve by:** Phase 01b · **Blocking:** no

**Decision.** "Open a research track now". Track T5 (`research/briefs/tracks.md`) goes to Codex (`gpt-6-luna`), Antigravity (`gemini-3.8-flash-high`) and a Claude sub-agent, same rules and report structure, reports to `T5-from-scratch.md` in each `phase01-*` folder. Phase 02 continues in parallel (the tests and ledger are model-agnostic, D-032). The brief treats "much easier" as a claim to test, and plans for a Mac (M5, 16 GB) and one 8 GB GPU box, with no cluster. It reads only design text (R-05). No GPU is used for the research itself (D-013).

---

## D-034: From-scratch model, first four choices (T5)
**Status:** RESOLVED (2026-10-28, Aniket) · **Resolve by:** Phase 04 · **Blocking:** no

- **First goal.** "Narrow role, gated (Recommended)". A narrow role model (for example expressing the mind in character), 124M to 200M parameters, knowledge kept in the memory store, gated behind a 30M to 60M pilot that must show a controllability gain first. No full human-like model is attempted on this hardware.
- **Rented compute.** "Not until pilot gains (Recommended)". The Mac and the local GPU only until the pilot shows a gain; then ask again. home-gpu stays under D-013.
- **Meaning of "from scratch".** "Random weights (Recommended)". Random weights, own tokenizer and data. Continued pretraining of an open base and distillation are baselines the new model must beat.
- **Training data.** "Logs plus frontier outputs", not the recommended "your consented logs". Data may include Aniket's own consented conversation logs and synthetic data generated by frontier models, called only through local Claude Code or local Ollama (standing rule). **Gate I add:** no frontier output is used for training until the provider's terms on training with outputs have been read and recorded here, since no Phase 01 report checked them (R-14). The state-trace logs wait until NF-12 and NF-23 are fixed, so the traces are not poisoned. A model trained on frontier outputs inherits those models' habits; that is stated in every result.

---

## D-035: Where state lives, and the model interface now
**Status:** RESOLVED (2026-10-28, Aniket) · **Resolve by:** Phase 04 · **Blocking:** no

- **State home.** "External, optional cache (Recommended)". The state ledger outside the model is authoritative. A future model may keep an in-model cache of it. Intervention tests (D-020), backup and restore (D-029) and rollback stay possible.
- **Model interface now.** "Yes, both (Recommended)". The slice plan includes the model interface (a capability descriptor and structured state channels, every LLM call routed through `ModelRole`, a per-row embedding model id and dimension) and an early size sweep, 135M to 7B on a narrow persona role, as the cheapest test of "much easier". Running the sweep needs a GPU, so it waits for Aniket's go-ahead for that run (D-013). Nothing is built until the slices are approved in Phase 04.

---

## D-036: home-gpu handed to another project
**Status:** RESOLVED (2026-10-28, Aniket) · **Resolve by:** n/a · **Blocking:** no

Aniket: another project is now working on home-gpu, and since this cycle has no GPU work, he gave it to that project. Effects here: nothing in this cycle runs on home-gpu until he hands it back. That blocks the early size sweep (D-035) and the owed W1 `llm_augmented` run (D-010). Neither is needed to finish Phases 02 to 04: the architecture, the critique and the slice plan do not use a GPU. D-013 still holds (ask before any use); when a slice needs the GPU, the plan asks Aniket for it back and does not assume it. Mac-only work (code, unit tests, no-LLM simulation, seconds-long Ollama smoke checks) is unchanged.

---

## D-037: Split the AI_friend repo into org repos
**Status:** RESOLVED (2026-10-28, Aniket) · **Resolve by:** n/a · **Blocking:** no

Aniket asked for the folder to be cleaned up and split along a proper org structure, using GitHub history to decide which results belong to whom. Survey (read-only): AI_friend holds product code (`backend/`, `frontend/`), three generations of brain research docs (`docs/brain-research`, `-v3`, `-v4`), benchmark results and scripts (`academic_benchmarks/`, `scripts/results`, `scripts/research`, `notebooks/`, `evidence/`), plus gitignored business notes (`outreach/`, `partnership/`, `orchestration/`, `evidence/IP_REVIEW_CANDIDATES.md`). Outside git: a 1.4 GB `AI_friend-research-data/` and an old `brain-v4/` archive.

- **Layout.** "5 repos". `AI_friend` (product), `brain-research` (docs V2 and V3 now; V4 at cutover), `benchmarks`, `partnerships`, plus the existing `website` and `.github`.
- **How files move.** "Filtered history". `git filter-repo` on a throwaway clone keeps each moved file's commits, authors and dates. AI_friend's own history is never rewritten.
- **Outreach, partnership, IP notes.** "Private repo". `partnerships`, private. These were gitignored on purpose (they name real companies and people), so this is a change of earlier practice that Aniket chose.
- **Timing.** "Copy now, delete later". The new repos are built from copies and the originals stay in AI_friend, including the frozen V3 docs (D-010). Removal, pointer files and path updates (CLAUDE.md, `backend/tests/test_doc_drift.py`, `tools/check_v3_*.py`) happen at a cutover after the Phase 03 critic finishes and Aniket unfreezes V3.

**My additions, open to correction.** (1) The new repos start private; making one public is a later decision. (2) `docs/brain-v4/` is the live work log, so it is left out of the first copy and joins at cutover, to avoid two diverging copies. (3) The 1.4 GB research-data folder is not pushed (size, no large artifacts in git); the new `benchmarks` README describes it. (4) `orchestration/` stays local. (5) No LICENSE is copied until Aniket picks one per repo.

---

## D-038: Temperament may drift slowly, inside a band, after corroboration
**Status:** RESOLVED (2026-10-28, Aniket delegated; Claude decided) · **Resolve by:** Phase 04 · **Blocking:** no

Aniket was asked whether baseline mood and hormone half-lives (constitutional tier in `backend/app/persona/profile.py`, D-005, D-025) stay fixed or drift. His answer: "decide yourself which will help us creating closer to human mind".

**Decision.** Slow drift within a band, off at first. Human temperament has high rank-order stability and still moves over years; the V4 identity band in `03-realism-eval.md:63` asks for exactly that, "neither frozen (r near 1, no drift) nor unstable". A mind whose temperament cannot move at all fails the first half of that band by design. Rules: (1) drift uses the same gate as D-025, N corroborating reflections on separate days; (2) it moves only inside a band set per mind at creation, with the band width and N taken from the lifesim sweep, not guessed; (3) every change records source, date, prior and new value and can be reverted on an offline copy; (4) it ships disabled until the sweep shows the band is stable and the identity probes still pass; (5) the immutable core (values, boundaries) never drifts (D-005). This needs the learning governor's tier rule (`learning.py:402-406`) changed for the temperament fields only, which is a code change for Phase 04, not made here. **Open to correction:** this was my call on Aniket's delegation; he can reverse it. See R-15.

---

## D-039: Months-long study runs on offline copies
**Status:** RESOLVED (2026-10-28, Aniket) · **Resolve by:** Phase 04 · **Blocking:** no

Aniket chose "Offline copies". The carryover problem is handled by running study conditions on offline copies of a mind (D-029), each deleted after, so no live mind is reset (D-025), swapped (D-026) or forked live (D-027). The cost is that claims are limited to what copies show; every result says so. The design still needs a pre-registered estimand and pass threshold in the realism-eval draft.

---

## D-040: Dead plan, simulation and calibration code: drop the wiring, keep the code until V3 closes
**Status:** RESOLVED (2026-10-28, Aniket) · **Resolve by:** V3 close-out · **Blocking:** no

Aniket chose "Keep code, drop wiring". This narrows D-028's "deleted unless V4 names a use" in time: now the live wiring is dropped (pipeline injection and the decision and candidate consumers that default to `PROCEED`), and the modules, the `AgentState.capability_model` field, the V3 benchmark scripts and their tests stay so V3 results can still be reproduced while V3 is frozen (D-010). The code is deleted for real when Aniket closes V3 out. Tests that assert the wiring are updated in the same slice.

---

## D-041: Phase 04 slice plan approved; Wave A starts
**Status:** RESOLVED (2026-10-28, Aniket) · **Resolve by:** n/a · **Blocking:** no

Aniket approved `phases/04-slice-plan.md` as written (slices 0 to 15, with 2 split into 2a and 2b), chose to start Wave A now (slices 0, 1, 6, 14: no model run, no GPU), and chose "Split by type" for builders: Claude builds judgment slices (1, 4, 5, 9, 11, 12), Codex builds mechanical slices (0, 6, 13, 14). Each slice gets a cold critic from a different tool, max 2 rounds, no third. Codex builds in throwaway git worktrees and does not commit; Claude reviews each diff, runs the covering tests, and commits to `main`.

---

## D-042: Bi-temporal memory: correct the evidence now, wire it in slices 4 and 5
**Status:** RESOLVED (2026-10-28, Aniket) · **Resolve by:** slice 4 · **Blocking:** no

The repo-intel audit found `TemporalMemoryStore` constructed (`core.py:91`) and injected but never called on the live path, while `evidence/` marks bi-temporal memory [PROVEN] (claims #3, #4). Aniket chose: correct the evidence package now (done: banner in `evidence/TECHNICAL_EVIDENCE_PACKAGE.md`), and wire the store inside slices 4 and 5 where belief revision is already planned, after the baseline harness exists so the effect can be measured. No behaviour change today.

---

## D-043: voice.segmentation_feedback stays recorded, not fixed yet
**Status:** RESOLVED (2026-10-28, Aniket) · **Resolve by:** the first slice that needs segmentation feedback · **Blocking:** no

The brain subscribes to `voice.segmentation_feedback` and nothing publishes it (claim #32, verified). Aniket chose to record it in the debt registry and decide in the slice that needs it: that slice builds the publisher, or deletes the handler and its durable consumer if no slice needs it.

---

## D-044: sota_comparisons.md is marked superseded
**Status:** RESOLVED (2026-10-28, Aniket) · **Resolve by:** slice 2a · **Blocking:** no

`academic_benchmarks/documentation/sota_comparisons.md` reports 87.5% Recall@5 while `literature_review.md` says it is not measured (claim #5, verified). Aniket chose to mark it superseded with a pointer (done: banner), keep the history, and take baselines only from the slice 2a harness.

---

## D-045: Cross-family review of the repo-intel tooling and ledger
**Status:** RESOLVED (2026-10-28, Aniket) · **Resolve by:** this session · **Blocking:** no

The directive asks for independent review; the only second reader so far was a same-family Claude sub-agent. Aniket chose a bounded Codex pass over the claims ledger and `tools/repo_intel`, recorded as reviews with reader and family. Costs Codex usage; no GPU.

**Outcome (2026-10-28).** Codex returned a usage-limit error on the first call, so no cross-family review ran. Per the directive (section 9) the limit is recorded and a second fresh Claude reviewer did the pass instead: 34 claims (31 agreed, 3 disagreed and were superseded) and a code review of the tooling (8 defects, all fixed with tests). Both reviewers are the same model family as the author. Re-run with Codex when its limit resets.

---

## D-046: Wave C starts with slices 4 then 5, flagged off
**Status:** RESOLVED (2026-10-29, Aniket) · **Resolve by:** this session · **Blocking:** no

Wave B is pushed (slice 13 `b525b2f6`, slice 2a `a40f9edc`). Aniket chose "Slices 4 then 5, flagged off": fix the mood self-feed (NF-12) and then the integrity update (NF-23), each behind a config flag until its eval passes, each with a two-variant tournament and one cold critic (max 2 rounds). Evals use the frontier tier through local Claude Code on the Mac; no GPU, so no local-tier acceptance claim. Slice 2b is not run first; slice 4 collects its own model-backed baseline before any code change, as D-031 requires.

---

## D-047: Pass margins for slices 4 and 5 are proposed by Claude and approved by Aniket
**Status:** RESOLVED (2026-10-29, Aniket) · **Resolve by:** before the first model run of slice 4 · **Blocking:** yes for the first model run

Aniket chose "I propose, you approve". Proposed margins go into `prereg/slice-04.md` and `prereg/slice-05.md`, marked PROPOSED, committed unrun. No model-backed run of slice 4 or 5 happens until Aniket approves them (or changes them). Deterministic gate tests and the build behind the flag are not blocked.

---

## D-048: Model-backed noise band rule accepted
**Status:** RESOLVED (2026-10-29, Aniket) · **Resolve by:** slice 2b · **Blocking:** no

Accepted as written in `prereg/slice-02.md`: 5 repeats per tier at the lowest temperature the client allows; the band is the largest pairwise difference of the headline measure across those runs; two arms are called different only if their gap exceeds the larger of the two arms' bands. Applies to slices 2b, 4 and 5.

---

## D-049: Integrity evidence ignores the appraisal's boundary-keyword check
**Status:** DECIDED by Claude inside the slice 5 tournament (2026-10-30), open to override by Aniket · **Blocking:** no

With `TRUST_EVIDENCE_MODEL_ENABLED` on, integrity moves on `norm_evidence(message)` only. The appraisal's `norm_alignment` (substring match of words from the two `IMMUTABLE_CORE` boundaries) is not used. Reason: the boundaries are the agent's own commitments ("Will never share user data"), and the substring match fires on "will", "user", "data", "share", so ordinary sentences scored as violations and, once integrity read them, lowered trust (measured by the builder and the critic). Flag off keeps the old `delta * NA` term unchanged. If you want boundary hits to count as evidence, the match needs whole-word logic in Python and Rust first.
