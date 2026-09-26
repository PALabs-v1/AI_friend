# Interview Round 4 — Trust and Relationship

Gates W3 (register A-3, medium severity per both my audit and Codex's independently). Verified: `agent_state.py:1342-1355` adds `0.1 * NA` to `trust_integrity` on every user turn, where `NA` only drops below 1.0 if the message contains one of seven hardcoded boundary-keyword substrings. Plain hostility never touches `NA`. Measured in the affect sim: trust ≈0.83 after 60 hostile turns, regardless of script.

DR-003 already settled global-vs-per-person (single relationship, this cycle). DR-010 already introduced "relationship sentiment" as the slowest affect layer, explicitly near trust/attachment territory — this round has to reconcile that with trust itself, not treat them as unrelated.

## Q1. Is trust one number, or does it need real separate dimensions?

**What exists**: three sub-dimensions already exist in code — `trust_benevolence`, `trust_competence`, `trust_integrity` — but `agent_state.py` averages them into a single `trust` value everywhere they're consumed (MAUT scoring, the relational-stance bucket, contracts). The separate dimensions are computed but never used separately.

**Why it matters**: if benevolence, competence, and integrity move independently but get flattened into one number before anything reads them, they're not actually three dimensions in any behaviorally meaningful sense — they're one dimension computed three redundant ways.

## Q2. Should hostility lower trust directly? How sharply, and can it recover?

**What exists**: it doesn't lower trust at all today (the bug). `PersonModel` (unused in production) already implements an asymmetric rupture/repair rule: rupture is `-1.5 × magnitude`, repair is `+0.5 × magnitude` — a real person's trust drops fast from a betrayal and rebuilds slowly, not symmetrically.

## Q3. Should reliability (did it do what it said) move trust differently from emotional warmth?

**What exists**: `trust_competence` moves from `0.6·G + 0.4·R` (G = agent's own mood, R = 1.0 for any user message) — it's not actually driven by whether the agent was reliable in any behavioral sense, just by its own mood plus "a message happened."

## Q4. How does "relationship sentiment" (DR-010's slow affect layer) relate to trust?

**What exists**: no relationship between them today — trust and affect are entirely separate subsystems that happen to update on the same event. DR-010 introduced relationship sentiment as a concept without specifying what it's made of.
