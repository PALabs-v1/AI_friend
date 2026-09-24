# DR-035: Don't try to distinguish deliberate testing from genuine forgetfulness

**Round**: 10 (long-horizon, final)
**Date**: 2026-09-24
**Question**: Should the system distinguish a user testing it on purpose from one who's genuinely forgetful/mistaken?

**Decision**: don't try, per individual contradiction. Not reliably detectable from text, and a wrong detector is worse than none. Treat all contradictions the same way regardless of apparent intent, matching DR-006's classifier design (correction vs. genuine change is about the *content's* shape — recency, explicit language — not about inferring the user's motive).

**Consequence for W1**: confirms DR-006's classifier should stay motive-agnostic. No new "is this user testing me" detection work gets added to W1's scope.
