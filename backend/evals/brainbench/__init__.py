"""BrainBench: drives real brain components against lifesim output.

Unlike `evals/cognitive` (deterministic, model-free scenario tests), this
package exercises the actual `app.*` cognitive services -- `CognitiveService`,
`MemoryStore`, `StateService` -- against a lifesim persona replayed through
`app.clock`'s simulated calendar. See `docs/brain-research-v3/06-benchmark-plan.md`.
"""
