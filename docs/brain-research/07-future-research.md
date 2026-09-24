# 07 — Research queue (ordered by measured or estimated leverage)

Each item: the hypothesis, how to test it with the existing harness, the
metric and target, and what it costs. Items marked **GPU** need a model.

## 1. Write-time validity for changed facts (M-5) — highest measured payoff

* **Evidence:** no ranker fixes stale facts (obsolete-wins 0.64 V1, 0.67
  hybrid, hard/summary). A perfect detector takes it to 0.00 and lifts
  updated-fact hit@3 0.33 → 0.54 (E7 upper bound).
* **Hypothesis:** classifying each new memory against its nearest same-subject
  neighbours as ELABORATION / UPDATE / CORRECTION / CONFLICT at write time,
  closing `valid_until` on UPDATE/CORRECTION and filtering closed rows at
  retrieval, reaches ≥ 70% of the E7 gain.
* **Method:** the `TemporalMemoryStore.apply_contradiction` state machine
  already exists and is tested; it is constructed and never called. Wire it
  into `add_memory` behind a flag. Candidate detectors, cheapest first:
  (a) nearest-neighbour cosine ≥ τ **and** polarity/negation cue (deterministic);
  (b) the fast LLM on the top-3 neighbours (**GPU**). Score each with a new
  `E8_validity_detector` arm next to `vec60+validity`.
* **Metric/target:** obsolete-wins ≤ 0.10; updated hit@3 ≥ 0.48; false-closure
  rate (a still-valid fact closed) ≤ 0.02 on the `unique` regime.
* **Cost:** one write-path hook + one retrieval filter; LLM variant adds a
  fast-model call per stored memory (background, not per turn).

## 2. Is the graph worth its infrastructure? (multi-hop, M-4)

* **Hypothesis:** PageRank over Neo4j relations improves recall of memories
  linked to the query only through an entity chain ("my brother's wife works
  where?") by ≥ 0.15 hit@3, without hurting single-hop retrieval.
* **Method:** add a multi-hop scenario family (entity chains plus a fake
  `graph_db` serving the scenario's relations as neo4j `Record`s) and a
  `hybrid+ppr` arm (PageRank mass as a fourth z-scored term).
* **Decision rule:** < 0.05 gain → drop the graph from retrieval and keep
  Neo4j only for fact storage, or retire it (ARCHITECTURE.md register #69).

## 3. Per-turn memory freshness (M-10)

* **Hypothesis:** tagging each surfaced memory with the utterance it was
  retrieved for, and discarding entries older than the previous user turn,
  raises the chance that the prompt's memories concern the current question.
* **Method:** replay a scripted multi-turn conversation through
  `CognitiveService` with a stub LLM; measure how many rendered memories match
  the current turn's probe vs earlier turns'.
* **Cost:** small; changes what the prompt sees, so it needs an A/B check.

## 4. Evidence-based trust (A-3)

* **Hypothesis:** routing trust through `PersonModel` (asymmetric:
  success +0.05·stake, failure −0.15·stake, rupture −1.5·m, repair +0.5·m)
  gives hostile-script trust ≤ 0.4 and rupture→repair recovery to within
  0.1 of the pre-rupture level, with no ceiling hit on a positive script
  within 100 turns.
* **Method:** `affect_sim` already reports `final_trust`/`min_trust`; add
  rupture/repair events to the scripts.

## 5. Self-correction that is actually heard (V-2)

* **Design:** a `flush` flag on `AudioStop` (contract bump in
  `crates/contracts` and `app/contracts.py`) meaning "discard queued audio
  for this turn, do not abort it". The brain ignores flush stops for
  cancellation. The voice agent clears its queue without setting `abort_flag`.
* **Test:** a brain-level test that a self-correction retry's chunks reach
  `chat.output` after the flush; a Rust test that flush does not set `abort_flag`.
* **Why deferred:** a cross-language contract change needs the Rust
  toolchain test cycle and a voice-path live check.

## 6. Abstention in retrieval

* **Hypothesis:** returning fewer than `limit` memories when the best z-scored
  relevance is low reduces irrelevant memories in the prompt without losing
  answerable probes.
* **Method:** add unanswerable probes (questions about facts never stated);
  measure false-recall rate vs a relevance floor on `z(cos)` or raw cosine.
  **GPU** to calibrate the floor on real embeddings.

## 7. Attention and regulation (A-6, register #2/#21)

* `GlobalControls` derives `urgency ≥ 0.65` on every user turn, so regulation
  candidates can never beat SPEAK. Measure candidate selection over the affect
  scripts before changing the control derivation.

## 8. Real-embedding validation (**GPU**, already packaged)

`backend/experiments/gpu/real_embedding_retrieval.py` — H-R1 (hybrid ≫ V1),
H-R2 (weight plateau), H-R3 (nomic task prefixes). The pre-registered decision
rules are in that package's README.

## 9. ToM valence as appraisal input (**GPU**, already packaged)

`backend/experiments/gpu/tom_valence_affect.py` — the ADR-002 part-2 gate.
