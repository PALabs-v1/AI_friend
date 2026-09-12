# Changelog

All notable changes to this project are documented in this file, starting
from v7.0.0. Tagged releases exist back to v2.0.0; the gap before v7.0.0 was
this file simply never being kept in sync with the release history that
GitHub Releases and `git tag` already record — see those for the raw list of
prior tags.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
from this entry onward.

For the real, detailed, dated development history — including what was
measured, what was deliberately left undone, and why — see
[`.agents/CONTEXT.md`](.agents/CONTEXT.md), the project's engineering ledger.
It is the source of truth; where any other document in this repo disagrees
with it, the ledger is right.

## v7.1.1

- Repository migrated from Aniket-a14/AI_friend to PALabs-v1/AI_friend.
- Installer, documentation and repository URLs updated.
- Distribution and release infrastructure verified under PALabs.

## v7.1.0

A repositioning release: the Brain (cognitive/affect/memory architecture) is
now presented as the project's primary novelty, with Voice and Vision as
explicitly secondary, supporting modalities. This release has two parts — a
repo content cleanup, and a full website revamp built around it — plus three
follow-up bug fixes found and closed after the revamp shipped.

**Repo cleanup.** `outreach/`, `partnership/`, `orchestration/`, and
`evidence/IP_REVIEW_CANDIDATES.md` — internal BD/process material that had
drifted onto public `main` in a prior cleanup pass, including named
individuals in cold-outreach drafts — are now gitignored, following this
repo's own existing convention for exactly this category of content (no
history rewrite). `FINAL_HUMANOID_BRAIN_ARCHITECTURE.md` was repurposed into
a public `ARCHITECTURE.md`.

**Website: honest, live playground.** Every remaining "Coming Soon" blurred
mockup with zero real functionality behind it is gone. Five real backend
formulas (ACT-R memory activation, Marsh trust + Bowlby attachment,
metacognitive Brier-calibration, theory-of-mind concept tracking, the persona
compiler's `_infer_temperament`) were ported to TypeScript, each with unit
tests asserting hand-computed values against the actual Python — not just
"renders without crashing." These back four new live demos (Trust &
Attachment, Memory Activation & Decay, Metacognitive Abstention, Theory of
Mind) plus a rewritten Persona Compiler that now recomputes synchronously
instead of faking a compile with `setTimeout`. The Voice Showcase drops its
fake play button for an honest static parameter table. Anything genuinely not
built (WebGPU in-browser inference, Colab training runners, a community
persona registry, real voice-clip assets) moved to a new `/roadmap` page that
says so plainly, with no mockup underneath.

**Website: reordered around the Brain.** The homepage now leads with
Endocrine → Cognitive Turn → Persona → Trust & Attachment → Memory Activation
→ Mesh → DevEx → Voice (moved down) → Benchmarks → Security → CTA. The
Showcase's companion recipes lead with Affective Dynamics (plus two new
recipes: trust/Theory-of-Mind tracking, and metacognitive honesty) and end
with Voice. The Research page was rewritten around 5 pillars (Affective
Computing, ACT-R Memory & Retrieval, Turn-Taking, Edge Middleware, Lifespan
Development) backed by a new typed citation list — every citation was
mechanically checked against its claimed title/authors before publishing; 6
of 35 had a real, fixable sourcing error (wrong author bylines, a wrong DOI
digit, two stale arXiv IDs, a wrong ISBN) and were corrected with
independently-verified identifiers rather than dropped or left wrong.

**Fixed after the revamp shipped:**
- The homepage hero heading could overlap the fixed nav bar on a narrow-tall
  phone or a short-landscape phone — width-only breakpoints couldn't see
  either failure mode. Fixed with a `vmin`-based `clamp()` (the smaller of
  viewport width/height) plus a last-resort safety net for genuinely extreme
  short viewports.
- Docs pages with LaTeX (`$...$` / `$$...$$`) rendered the raw text
  literally — no math-parsing plugin was wired into the markdown pipeline.
  Added `remark-math` + `rehype-katex`.
- The changelog's own unreleased-roadmap preview entry claimed a "Home
  Assistant integration agent" and "peer-to-peer friend syncing," neither of
  which has any grounding anywhere in the codebase or ledger. Replaced with
  the same 4 items already vetted on `/roadmap`, and linked the two pages
  together so they can't silently drift apart again.

Verified throughout: `vitest` (28/28), `tsc --noEmit`, `next build` (41/41
pages), a live lychee link-check (0 errors), and repeated headless-browser
passes confirming zero console errors across every route this release
touched, including a same-page slider-drag check proving the persona
compiler genuinely recomputes live rather than simulating a delay.

## v7.0.0

The community-release roadmap (`.agents/CONTEXT.md`, "Community roadmap
Phase 0" through "Phase 8"): ground-truth fixes to the safety floor and
persona validation, a fresh clone that boots and speaks, the `friend`
persona/voice creation flow, proactive outreach and affect persistence
across restarts, export/import portability, a real web UI, the nine
pressure-test benchmark scenarios run for real, code-quality gates flipped
from report-only to blocking, and a rewritten README and landing page.

On top of that roadmap, this release adds the distribution system: one-line
installers for macOS/Linux (`scripts/install.sh`) and Windows
(`scripts/install.ps1`), a lightweight standalone runtime bundle (~4.3 MB,
`scripts/package_release.py`), the unified `friend` CLI
(`scripts/friend_cli.py`) covering init/start/stop/status/model/vision/talk/
persona/voice/backup/logs/update, an interactive `friend init` environment
wizard that generates cryptographically secure credentials, and Moondream
VLM visual appraisal wired into setup and the CLI.

Fixed as part of preparing this release: `personal/` was briefly
whitelisted into the packaged runtime bundle and shipped the maintainer's
own persona data publicly; the packager no longer includes it and a new
`.distignore` guards against it recurring. `friend_cli.py` failed to parse
at all on Python 3.11 despite both installers accepting 3.11 as meeting
their stated minimum. `install.ps1` previously always cloned the full
monorepo and skipped the setup wizard; it now matches the macOS/Linux
installer's lightweight-bundle and interactive-wizard behavior.
