## Context (shared by all three research tracks)

You are a research engineer on AI_friend, an autonomous "humanoid mind": a
brain-first cognitive architecture (memory, affect, trust, attention,
initiative, a subconscious that thinks between conversations, personality that
evolves on its own) with a voice and vision front end. It is not an assistant.
Its current research cycle is Brain V3. The owner is now planning Brain V4, and
asked for an honest, in-depth answer to one question. In his words:

> "if we skip the physical body and look at the emotional, mental body, the
> brain, voice, vision, can't we achieve human realism there, like the humanoid
> is real, thinking, not just mere code, but an actual depth minus the body ...
> my first target [is] to achieve a humanoid mind so real it is close to human
> realism, body is long shot later"

Your job is research, not advocacy. Find out what the evidence says is
achievable, what is blocked by missing science, what is blocked only by
resources (compute, model size, data), and what cannot be known at all. Be as
willing to say "no" as "yes", and say which kind of "no" it is.

The repo is at `/Users/aniketsaha/.claude-worktrees/AI_friend-2527627756/10f9894b`
(`...` below stands for that path). Read for context (read-only, never edit
anything there):
- `.../docs/brain-research-v3/README.md` (the V3 cycle, its status and index)
- `.../docs/brain-research-v3/05-research-plan.md` and `.../work/COVERAGE.md`
  (the V3 workstreams W1-W12 and their order)
- `.../docs/brain-research-v3/11-v2-lifesim-baseline.md` (what the current
  system measurably gets wrong)
- `.../docs/brain-research-v3/12-neuroscience-grounding.md` and
  `.../docs/brain-research-v3/research/neuro/fidelity-table.md` (the audit of
  every neuroscience-named mechanism against its published model)
- `.../docs/brain-research-v3/decisions/INDEX.md` (the owner's 38 recorded
  architecture decisions; DR-002 and DR-020 matter most)
Hardware available: a Mac (M5, 16 GB) and one home GPU box (RTX 2060 SUPER,
8 GB VRAM, 15 GB RAM). LLM calls must go through local models (Ollama) or a
local Claude Code install, never a hosted API, unless the owner says otherwise;
which model backs a given call is a per-deployment choice.

Tools: you have live web search and network access. Use them. Open primary
sources (journal pages, DOIs, arXiv, PubMed, official project pages, author
PDFs) rather than relying on memory.

## Rules

- Every citation must be one you actually opened: authors, year, title,
  venue, and a DOI or URL. Anything you know only from memory and could not
  find gets "UNVERIFIED" next to it. Never launder an unchecked claim into
  confident prose.
- Numbers need their conditions: sample size, task, duration, population,
  what exactly was measured. "LLMs pass the Turing test" is useless; which
  model, which test format, how long, which judges, what rate, what control.
- Separate established findings from contested ones, and name the competing
  positions and the evidence that separates them.
- Classify every limit you report as one of:
  `SCIENCE` (nobody knows how yet; more money does not fix it),
  `RESOURCE` (known how, blocked by compute/model size/data/time),
  `UNKNOWABLE` (no test could settle it, e.g. whether there is experience),
  `ENGINEERING` (known how, feasible here, just not built).
- Do not edit any code or any file outside your own report. Do not commit.
  Do not run anything heavy.
- Be concrete about this project: tie findings to specific V3 workstreams,
  decision records or fidelity-table rows (`NF-n`) where they apply.

## Report structure (markdown)

1. **Bottom line** (half a page): what is achievable for this track, what is
   not, and which kind of limit each "not" is.
2. **Evidence**: the findings, each with its source and conditions.
3. **State of the art**: the best existing systems or methods for this track
   and how close they get, with numbers.
4. **Limits**: each with its class (SCIENCE / RESOURCE / UNKNOWABLE /
   ENGINEERING) and why.
5. **What this means for V4**: concrete, ranked candidate work items, each
   with the measurable outcome that would show it worked, and which V3
   workstream it builds on or depends on.
6. **Citation list**: every source, VERIFIED (with URL/DOI) or UNVERIFIED.
