## Context (shared by all four research tracks)

You are a research engineer on AI_friend, an autonomous "humanoid mind": a
brain-first cognitive architecture (memory, affect, trust, attention,
initiative, a subconscious that thinks between conversations, a personality
that evolves slowly) with a voice and vision front end. It is not an
assistant. Its previous research cycle, Brain V3, fixed measured defects in
the existing mechanisms. The owner is now designing the architecture of the
next version, Brain V4, and wants independent research before anything is
decided.

The owner's position, in his words (2026-10-28):

> "shift the focus from training for longer hours to actually figure out the
> brain architecture, not just as a mimic of human mind, well i know we already
> talked about how it is not possible for ai to have consciousness, but rather
> a closed loop on how close can we create"

So the question is not "is it conscious". It is: how close can a closed loop of
internal state get to the organization of a mind, and how would anyone tell.
A working definition used so far: internal state that persists, was not
scripted, and causally drives what the system says and does. Whether it feels
anything is recorded as unknowable and is not a target. The system always
discloses that it is an AI when asked.

The owner's working frame (to be tested, not accepted): three layers, reactive,
deliberative and reflective, with identity running across them. An earlier
frame was a loop: perceive, appraise, remember, feel, decide, act, reflect,
and back to perceive, with identity as the slow variable. Each part of any
frame must (a) read and write named state, (b) change a later behavior
traceably, and (c) have a measure.

Your job is research, not advocacy. Say what the evidence supports, where it
does not, and where our current design looks wrong or out of date. Be as
willing to say "no" as "yes", and say which kind of "no" it is.

## What you may read (read-only, repository root is your starting point)

Paths are relative to the repository root. From your working directory under
`docs/brain-v4/research/<your tool>/`, that root is `../../../..`.

- `docs/brain-research-v3/README.md`, `00-current-architecture.md`,
  `12-neuroscience-grounding.md` (what the system is and how it was audited)
- `docs/brain-research-v3/decisions/INDEX.md` (the owner's recorded
  architecture decisions)
- `docs/brain-v4/06-decisions.md` (the owner's product rules: persona
  authored once with a fixed core and slow drift; the brain, not a voice
  partner, owns turn-taking; always disclose being an AI; the model backing a
  deployment is a per-deployment choice)
- the code under `backend/app/` as needed to check a claim about the system

Do not read other files under `docs/brain-v4/` (they hold earlier conclusions
that you should not inherit) and do not read other tools' reports.

Hardware: a Mac (M5, 16 GB) and a GPU box with 8 GB VRAM. LLM calls go through
local models or a local Claude Code install. Treat that as a constraint on
what can be built, not on what you may discuss.

Tools: you have live web search. Use it. Open primary sources (papers, DOIs,
arXiv, official project pages) rather than relying on memory. The current date
is 2026-10-28; your training data may be older than recent months, so search
for what changed.

## Rules

- Every citation must be one you actually opened: authors, year, title, venue,
  and a DOI or URL. Anything you know only from memory and could not find gets
  "UNVERIFIED" beside it. Never launder an unchecked claim into confident
  prose.
- Numbers need their conditions: sample size, task, duration, population,
  what exactly was measured.
- Separate established findings from contested ones, and name the competing
  positions and the evidence that separates them.
- Classify every limit you report as one of: `SCIENCE` (nobody knows how yet),
  `RESOURCE` (known how, blocked by compute, model size, data or time),
  `UNKNOWABLE` (no test could settle it), `ENGINEERING` (known how, feasible
  here, not yet built).
- Do not edit any code or any file outside your own report. Do not commit. Do
  not run anything heavy. Do not send any file contents to a service other than
  your own search.
- Be concrete about this project: tie findings to specific parts of the
  current system, to the owner's decisions, or to the V3 fidelity table rows
  (`NF-n`) where they apply.

## Report structure (markdown)

1. **Bottom line** (half a page).
2. **Evidence**: findings, each with source and conditions.
3. **State of the art**: the best existing systems or methods for this track,
   with numbers.
4. **Where our design is weak or out of date**: specific, with file or
   decision references.
5. **Limits**, each with its class.
6. **What this means for V4**: concrete, ranked candidate design moves, each
   with the measurable outcome that would show it worked and what it depends
   on.
7. **Open questions for the owner**: only the ones the evidence cannot settle.
8. **Citation list**: every source, VERIFIED (URL or DOI) or UNVERIFIED.
