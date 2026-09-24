# Documentation Index

This document maps the repository's authoritative architecture references, technical
evidence, and published research. Where any of these disagree with
`.agents/CONTEXT.md`, the ledger is right.

---

## 1. Architecture & Engineering Documents

- [ARCHITECTURE.md](ARCHITECTURE.md): The Brain's target architecture — kernel
  definition, mechanism register (what's built/planned/rejected), state ownership,
  memory/appraisal/action-selection contracts, invariants, and the research it draws on.
- [README.md](README.md): Project overview, architecture summary, quickstart guide,
  and runtime deployment instructions.
- [CLAUDE.md](CLAUDE.md): Primary engineering conventions, build commands, and
  platform instructions for developer agents.
- [AGENTS.md](AGENTS.md): Operational guide and architectural invariants for
  autonomous agents working in the repository.
- [.agents/CONTEXT.md](.agents/CONTEXT.md): Chronological engineering ledger
  tracking architectural changes, empirical measurements, and deferred work.
- [docs/brain-research/README.md](docs/brain-research/README.md): Brain V2
  research trail — verified V1 architecture, problem register, benchmark-backed
  memory-retrieval and affect experiments, decision records (ADR-001..003),
  rejected approaches and the ordered research queue.
- [backend/evals/cognitive/README.md](backend/evals/cognitive/README.md): The
  model-free cognitive benchmark (`python -m evals.cognitive`) those results come from.

---

## 2. Technical Evidence

- [evidence/TECHNICAL_EVIDENCE_PACKAGE.md](evidence/TECHNICAL_EVIDENCE_PACKAGE.md):
  Index of the technical evidence below, plus a capability matrix and integration-fit
  notes by adapter type.
- [evidence/BENCHMARK_SUMMARY.md](evidence/BENCHMARK_SUMMARY.md): Empirical
  benchmarks — local micro-benchmarks, remote GPU metrics, and soak-test telemetry,
  with hardware/environment provenance for every figure.
- [evidence/DEMO_SCENARIOS.md](evidence/DEMO_SCENARIOS.md): Four reproducible
  scripted demonstration scenarios covering model independence, bi-temporal memory
  truth, fast barge-in, and governed rollback.
- [evidence/INTEGRATION_BOUNDARIES.md](evidence/INTEGRATION_BOUNDARIES.md): Formal
  inbound/outbound contracts (`PerceptEnvelope`, `SpeechIntent`,
  `ExternalActionIntent`, `StructuredVisionPercept`) and a guide for building a new
  voice, vision, or model adapter.

---

## 3. Published Research & Theoretical Foundations

- [CITATION.cff](CITATION.cff): Academic citation metadata for this project.
- [academic_benchmarks/](academic_benchmarks/): The research base this architecture
  draws structural inspiration from — algorithms/equations, literature references,
  and SOTA benchmark context. See `ARCHITECTURE.md` Appendix B and the website's
  `/research` page for curated, verified subsets of this material.
