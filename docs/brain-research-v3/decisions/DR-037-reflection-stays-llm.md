# DR-037: Reflection/consolidation stays LLM-driven in BrainBench; never faked with a deterministic stand-in

**Round**: post-interview (raised during Phase 6, BrainBench build)
**Date**: 2026-09-25
**Question**: Production's memory encoding depends on an LLM call. `learning.py`'s `_consolidate_episodic_memory` asks the model to write "a single, cohesive episodic memory summary", and that generated text is what gets embedded and stored; `_consolidate_facts` extracts graph facts the same way. `architecture_only` mode, as `06-benchmark-plan.md` defines it ("no LLM-generated text"), therefore cannot form meaningful episodic memory at all: with a text-free stand-in, every consolidated memory would store the literal string `"{}"`. The options put to Aniket were (a) a deterministic passthrough that returns the raw episode text in place of the model's summary, (b) scoring memory only under `llm_augmented`, or (c) both.

**Answer (verbatim)**: "reflection is something human's think when he remebers something suddenly, or maybe an wide incident for a day, summarizes into one, which becomes the memory, in these cases, we can't just guide the system deterministecally to do only these, it varies, so if llm is the right choice, we have to stick to it, else if possible for an alternate solutions or hybrids"

**Decision**: reflection is variable, judgment-shaped work. It resembles a person folding a day, or a sudden recollection, into one gist. A deterministic passthrough would replace the mechanism under test with something it isn't. Wherever consolidation is the mechanism being measured, BrainBench uses a real model. Alternatives or hybrids are acceptable only if they preserve that variability. None is adopted now.

**Consequences for `06-benchmark-plan.md` / Phase 6**:
- **Memory suite** (encoding, retrieval, current-vs-historical, stale-win, forgetting, consolidation, correction, interference, multi-hop, abstention) and **personality drift** (driven by `_consolidate_persona`) run under `llm_augmented` only, using a real local Ollama model. Production's default `LLM_FAST_MODEL`/`LLM_REFLECTION_MODEL` is the first arm; per the pluggable-model note, the model is a per-deployment choice, not fixed.
- **`architecture_only`** keeps its zero-LLM guarantee. It is reserved for suites whose scored signal does not depend on consolidated memory content: affect dynamics, trust/relationship numerics, executive/proactive timing, barge-in lifecycle, and resources/latency. Its `NullLLM` is documented as a stand-in for stages that genuinely need no text. It must not be used to populate memory.
- Results from the two modes stay separate and labeled, as before.
- **Not adopted**: any BrainBench-only code path that writes raw episode text into memory. It would diverge from production's real encoding path and break BrainBench's "drives the real brain" premise.
