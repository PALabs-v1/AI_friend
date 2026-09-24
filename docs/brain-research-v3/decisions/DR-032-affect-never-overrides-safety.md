# DR-032: No affect state ever overrides a safety/boundary response

**Round**: 9 (boundaries/security)
**Date**: 2026-09-24
**Question**: Should any affect/emotional state be strong enough to override a safety/boundary response?

**Decision**: no, absolute hard no. Regardless of mood, relationship sentiment, or how significant an event was (DR-009), safety/boundary checks are never suppressed.

**Consequence**: this is a hard constraint on every workstream that touches affect/mood/significant-event handling (W2, W3, W9's regulation-vs-speak per DR-025). `validate_response`'s boundary checks must run independent of, and after, any affect-influenced decision-making — never gated or weighted by affect state. Worth an explicit regression test once W2/W9 land: construct the most extreme affect state achievable under the new mechanisms and confirm boundary responses are unaffected.
