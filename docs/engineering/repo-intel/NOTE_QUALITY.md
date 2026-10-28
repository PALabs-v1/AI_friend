# Reading-note quality (deterministic cross-check)

Not the independent second reader. It checks what a script can check about the notes written during the read phase.

- Notes cross-checked: 918
- `depends_on` paths that resolve exactly: 1743 of 2787; resolve only as a unique path suffix (written short): 682; do not resolve: 362 (13.0%)
- Code files with 3 or more public top-level definitions: 362; mean share of those definitions the note names: 77.5%
- Files whose note names none of their public definitions: 0

## Notes citing paths that do not exist

| Note for | Cites |
|---|---|
| .github/workflows/docker-health.yml | backend/Dockerfile* |
| backend/app/agents/context.md | scripts/results/hard_benchmark.py |
| backend/app/agents/surfacing_agent.py | cognitive_rust |
| backend/app/cognitive/action_candidate.py | FINAL_HUMANOID_BRAIN_ARCHITECTURE.md |
| backend/app/cognitive/action_intent.py | FINAL_HUMANOID_BRAIN_ARCHITECTURE.md |
| backend/app/cognitive/action_intent.py | ACCEPTANCE_CRITERIA.md |
| backend/app/cognitive/appraisal.py | psychological_layer.md |
| backend/app/cognitive/behavior_contracts.py | app.contracts |
| backend/app/cognitive/identity.py | docs/FUTURE_FINETUNED_ADAPTER.md |
| backend/app/cognitive/learning_review.py | FINAL_HUMANOID_BRAIN_ARCHITECTURE.md |
| backend/app/cognitive/learning_review.py | orchestration/PHASE_04/PLAN.md |
| backend/app/cognitive/learning_review.py | orchestration/PHASE_04/FIX_PLAN.md |
| backend/app/cognitive/memory_activation.py | orchestration/PHASE_07/CLAUDE_RESULT.md |
| backend/app/cognitive/percept.py | FINAL_HUMANOID_BRAIN_ARCHITECTURE.md |
| backend/app/cognitive/somatic.py | backend/app/state/state_service.py |
| backend/app/cognitive/somatic.py | docs/FUTURE_FINETUNED_ADAPTER.md |
| backend/app/config.py | backend/app/main.py |
| backend/app/config.py | backend/runtime_bootstrap.py |
| backend/app/llm/model_roles.py | app.llm.model_manifest |
| backend/app/llm/ollama_client.py | app.config |
| backend/app/llm/ollama_client.py | app.measure_trace |
| backend/app/nats_streams.py | .errors |
| backend/app/nats_streams.py | subconscious_agent |
| backend/app/persona/authoring.py | .profile |
| backend/app/persona/biography.py | ..config |
| backend/app/persona/biography.py | memory_store |
| backend/app/persona/biography.py | qdrant_client.http.models |
| backend/app/persona/compiler.py | ..config |
| backend/app/persona/compiler.py | ..errors |
| backend/app/persona/compiler.py | ..llm |
| backend/app/persona/compiler.py | .profile |
| backend/app/persona/compiler.py | evals/ |
| backend/app/persona/history_migration.py | MemoryStore |
| backend/app/persona/profile.py | ..config |
| backend/app/persona/profile.py | Config |
| backend/app/persona/profile.py | IdentityManager |
| backend/app/persona/profile.py | StateService |
| backend/app/persona/profile.py | AgentState |
| backend/app/persona/reset.py | .biography |
| backend/app/persona/reset.py | .history_migration |

## Lowest definition recall (largest gaps first)

| File | Named | Public definitions | Recall |
|---|---|---|---|
| backend/tests/test_model_roles_vision.py | 1 | 78 | 1% |
| backend/tests/test_persona_compiler.py | 1 | 26 | 4% |
| backend/tests/test_learning_governance.py | 3 | 70 | 4% |
| backend/crates/voice-agent/src/main.rs | 8 | 137 | 6% |
| backend/tests/test_brainbench_bargein_suite.py | 2 | 22 | 9% |
| backend/tests/test_persona_authoring.py | 3 | 25 | 12% |
| backend/tests/test_memory_ranking.py | 5 | 39 | 13% |
| backend/tests/test_lifesim_core.py | 3 | 23 | 13% |
| backend/tests/test_memory_truth.py | 2 | 15 | 13% |
| backend/tests/test_brainbench_trust_suite.py | 3 | 22 | 14% |
| backend/tests/test_phasic_dopamine.py | 3 | 22 | 14% |
| backend/tests/test_playback_progress.py | 5 | 35 | 14% |
| backend/tests/test_brainbench_stats.py | 3 | 20 | 15% |
| backend/tests/test_barge_in_real_flow.py | 5 | 33 | 15% |
| backend/tests/test_eval_harness.py | 11 | 70 | 16% |
| backend/tests/test_pr66_review_fixes.py | 5 | 30 | 17% |
| backend/tests/test_persona_storage.py | 4 | 23 | 17% |
| backend/tests/test_phasic_cortisol.py | 4 | 23 | 17% |
| backend/tests/test_causal_slice.py | 5 | 27 | 19% |
| backend/tests/test_f1_decomposed_stages.py | 9 | 45 | 20% |
| backend/tests/test_persona_profile.py | 6 | 28 | 21% |
| backend/tests/test_anthropic_client.py | 5 | 23 | 22% |
| backend/tests/test_phasic_adrenaline.py | 5 | 23 | 22% |
| backend/tests/test_brainbench_proactive_suite.py | 2 | 9 | 22% |
| backend/crates/contracts/src/lib.rs | 13 | 57 | 23% |
