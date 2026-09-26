# DR-030: Proactive-vs-user priority depends on the proactive turn's significance

**Round**: 8 (voice/turn-taking)
**Date**: 2026-09-24
**Question**: When a proactive turn is speaking and the user starts talking, who wins?

**Decision**: depends on significance. A highly important proactive turn (DR-016's importance weighting) can finish a short grace window even if the user starts speaking, rather than always cutting instantly. Not symmetric with DR-026 — DR-026 was about a self-initiated *thought* rarely being allowed to interrupt an *already-in-progress user turn*; this is about an *already-speaking proactive turn* facing new user speech, a different collision, and Aniket did not choose the "user always wins unconditionally" option here.

**Consequence for W5 and W9**: needs a concrete grace-window design — how long is "short," and is it gated on the same importance score DR-016 introduces for initiation eligibility, or a separate significance check at cutoff time. This is a new interleaving case for W5's state machine: {proactive turn active, importance=high} + {user speech begins} needs a defined outcome (grace window, then cede) distinct from the {proactive turn active, importance=low} + {user speech begins} case (presumably instant cede, though this wasn't asked explicitly and should default to instant cede for anything not marked high-importance, consistent with proactive behavior being opportunistic per DR-016/DR-019 by default). Flagging for W9/W5 to define the actual importance cutoff and grace-window duration during implementation, not here.
