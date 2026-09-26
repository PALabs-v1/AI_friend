# DR-031: Governor freezes safety-relevant CONSTITUTIONAL fields too

**Round**: 9 (boundaries/security)
**Date**: 2026-09-24
**Question**: Should the governor's protected-field list extend beyond `IMMUTABLE_CORE`?

**Decision**: yes. `IMMUTABLE_CORE` stays exactly as DR-001 defined it, but the refusal/avoid-list specifically gets the same hard freeze — self-evolution can change tone and traits, but never what the agent refuses to do.

**Consequence for W11**: `learning_governance.py`'s protected-key list needs an explicit addition covering the avoid-list (and any other field `validate_response` reads for boundary/refusal checks). This is the concrete mitigation for DR-020's removal of human review — the one thing that would have been most dangerous to leave adaptable is now walled off at the same level as the immutable core, even though it's architecturally CONSTITUTIONAL rather than IMMUTABLE.
