# Interview Round 10 — Long-Horizon Edge Cases (final round)

## Q1. Over truly long horizons (a decade+), does emotional protection (DR-007) ever give way too?

**What's already decided**: DR-007 said emotionally significant memories decay far more slowly than trivial ones, bounded (not boosted in unrelated retrieval). This question is about the limit case — a memory that was once significant but genuinely never revisited for 15-20 years: does it eventually fade toward the same practical unreachability as trivia (just on a much longer curve), or does emotional significance mean a near-permanent floor on retrievability that never fully fades?

## Q2. Should the system try to distinguish a user being deliberately inconsistent (testing it) from one who's genuinely forgetful or mistaken?

**What exists**: no such distinction anywhere — every contradiction goes through the same detection path (`find_contradiction`) regardless of apparent intent. Worth naming directly: this may not be reliably distinguishable from text alone in most real cases, so the answer likely needs to acknowledge that limitation rather than assume a clean detector is buildable.

## Q3. Any specific sensitivities for lifesim's synthetic emotional scenarios (grief, loss, failure) beyond the master prompt's own default guardrail ("only if synthetic and appropriate")?

**Context**: this is a benchmark-design question, not a runtime-behavior one — it affects what `evals/lifesim/events.py` is allowed to generate as synthetic life events for the simulated human.
