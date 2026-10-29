# Reading-note quality (deterministic cross-check)

Not the independent second reader. It checks what a script can check about the notes written during the read phase.

- Notes cross-checked: 951
- `depends_on` paths that resolve exactly: 1636 of 2649; resolve only as a unique path suffix (written short): 664; do not resolve: 349 (13.2%)
- Code files with 3 or more public top-level definitions: 375; mean share of those definitions the note names: 73.4%
- Files whose note names none of their public definitions: 7

## Notes citing paths that do not exist

| Note for | Cites |
|---|---|
| .github/workflows/docker-health.yml | backend/Dockerfile* |
| backend/app/agents/context.md | scripts/results/hard_benchmark.py |
| backend/app/agents/surfacing_agent.py | cognitive_rust |
| backend/app/cognitive/action_candidate.py | FINAL_HUMANOID_BRAIN_ARCHITECTURE.md |
| backend/app/cognitive/action_intent.py | FINAL_HUMANOID_BRAIN_ARCHITECTURE.md |
| backend/app/cognitive/action_intent.py | ACCEPTANCE_CRITERIA.md |
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
| backend/app/nats_streams.py | .errors |
| backend/app/nats_streams.py | subconscious_agent |
| backend/app/persona/authoring.py | .profile |
| backend/app/persona/biography.py | ..config |
| backend/app/persona/biography.py | memory_store |
| backend/app/persona/biography.py | qdrant_client.http.models |
| backend/app/persona/history_migration.py | MemoryStore |
| backend/app/persona/profile.py | ..config |
| backend/app/persona/profile.py | Config |
| backend/app/persona/profile.py | IdentityManager |
| backend/app/persona/profile.py | StateService |
| backend/app/persona/profile.py | AgentState |
| backend/app/persona/reset.py | .biography |
| backend/app/persona/reset.py | .history_migration |
| backend/app/persona/reset.py | ConversationHistoryStore |
| backend/app/persona/reset.py | agent_configs |
| backend/app/persona/reset.py | memories |
| backend/app/persona/reset.py | archived_memories |
| backend/app/persona/wizard.py | .authoring |
| backend/app/persona/wizard.py | .compiler |
| backend/app/persona/wizard.py | .profile |
| backend/app/persona/wizard.py | validate_persona_file.validate |

## Lowest definition recall (largest gaps first)

| File | Named | Public definitions | Recall |
|---|---|---|---|
| backend/app/api/persona.py | 0 | 7 | 0% |
| backend/app/cognitive/action.py | 0 | 4 | 0% |
| backend/app/cognitive/appraisal.py | 0 | 4 | 0% |
| backend/app/cognitive/subconscious.py | 0 | 4 | 0% |
| backend/tests/test_model_role_routing.py | 0 | 12 | 0% |
| backend/tests/test_pr66_review_fixes.py | 0 | 30 | 0% |
| backend/tests/test_vision.py | 0 | 5 | 0% |
| backend/tests/test_model_roles_vision.py | 1 | 78 | 1% |
| backend/tests/test_eval_harness.py | 1 | 70 | 1% |
| backend/tests/test_persona_compiler.py | 1 | 26 | 4% |
| backend/tests/test_learning_governance.py | 3 | 70 | 4% |
| backend/crates/voice-agent/src/main.rs | 8 | 137 | 6% |
| backend/tests/test_bare_model_arm.py | 2 | 24 | 8% |
| backend/tests/test_brainbench_bargein_suite.py | 2 | 22 | 9% |
| tools/repo_intel/cli.py | 1 | 11 | 9% |
| backend/tests/test_interventions.py | 2 | 18 | 11% |
| backend/tests/test_persona_authoring.py | 3 | 25 | 12% |
| backend/tests/test_memory_ranking.py | 5 | 39 | 13% |
| backend/tests/test_lifesim_core.py | 3 | 23 | 13% |
| backend/tests/test_memory_truth.py | 2 | 15 | 13% |
| backend/tests/test_brainbench_trust_suite.py | 3 | 22 | 14% |
| backend/tests/test_phasic_dopamine.py | 3 | 22 | 14% |
| backend/tests/test_playback_progress.py | 5 | 35 | 14% |
| tools/repo_intel/tests/test_workflow.py | 2 | 14 | 14% |
| backend/tests/test_brainbench_stats.py | 3 | 20 | 15% |
