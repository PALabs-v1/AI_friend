V3 RECHECK. Read-only. Checkout AI_friend on main. HEAD moved to c7e51d4a mid-run, but `git diff caf7f1dd HEAD -- backend docs/brain-research-v3` is empty, so code and V3 docs equal the Phase 01 baseline. Paths: b/ = backend/app/, d/ = docs/brain-research-v3/, 05p = 05-phase01-comparison.md. Row cells: # | V4 doc:line | V3 source | claim | result | evidence (fix).

TOOLS
check_v3_paths.py: "checked 33 repo paths named in brain-v4/*.md", exit 0, none missing.
check_v3_claims.py: 30 ownership claims, 0 failing, README merged-status OK, vision OK, personality suite OK. Both check ownership and file existence only, never behavior; neither caught any row below.

TABLE
1|02:3|git|PR #217 unmerged, V3 docs only on brain-v3|STALE|b0d54e0d merged 2026-09-27, ancestor of main (fix: "merged 2026-09-27, docs on main")
2|02:61|d/13|wave-A write-up pending|STALE|d/13-wave-a-results.md exists, commit 50624ff6 (fix: point to it)
3|02:11|d/13:13-15|240/240 cells, 0 errors, e3fcb9a|RIGHT|same lines
4|02:10|fidelity-table|39 findings|RIGHT|39 rows
5|02:12-14|COVERAGE.md|W5, W1, W2 open|STALE vs branch|true on main; origin/brain-v3 is 14 commits ahead (c58ee1ae): W5 ledger, W1 and W2 flag-off, DR-039 (commit subjects only, code unread). Fix: "open on main, built behind flags on brain-v3"
6|02:7|README|W4, W9, W10a, P8 runner merged|RIGHT|transport_agent.py lifecycle, backend/tools/scale
7|02:25,01:104|00:28,58; 01-v2-delta:136|TemporalMemoryStore built, apply_contradiction never called, valid_until/contradicts_id never read|RIGHT|b/cognitive/core.py:98, pipeline.py:113 only assign; memory_ranking.py reads neither
8|05p:42|same|temporal_store.py:267, core.py:98|RIGHT|exact
9|01:164,05p:39|00:64,F-009|user text moves mood by 0|RIGHT|b/cognitive/pipeline.py:817; appraisal.py:225,228; example-JSON prompt still at appraisal.py:453-465
10|02:28|00:64|text only feeds novelty and keywords|STALE|user voice shifts G: appraisal.py:234-236, fed pipeline.py:818 (pitch>250 or energy>0.15 gives G-0.3)
11|-|00:30|execute at pipeline.py:751|STALE|pipeline.py:721
12|-|00:64|pipeline.py:865; appraisal.py:206-227|STALE|pipeline.py:817; appraisal.py:206-247
13|-|00:58|add_memory memory_store.py:1408|STALE|memory_store.py:1574 (1408 is a payload dict). Write order RIGHT, 1640-1740
14|-|00:60|hybrid formula, 60 pool, trace|RIGHT|memory_ranking.py:61-63,177-190; config.py:327,330; memory_store.py:4042,4085
15|-|00:68|agent_state.py:1342-1355 trust math|STALE line|agent_state.py:1801-1816; math RIGHT, delta 0.1 (profile.py:175)
16|-|00:74|seven keywords, NA=1.0 on hostility|RIGHT|profile.py:136; appraisal.py:28,164-176 ("will" counts twice)
17|-|00:78|identity.py:477|RIGHT|exact
18|05p:40|00:82|learning.py:273|STALE|learning.py:329; queue gate RIGHT at 367-374, config.py:278
19|05p:40|00:82|approve never called; queue in memory|RIGHT|no `.approve(` caller in app/; learning_review.py `_proposals` dict
20|-|00:86|"no distinct STARTED event"|WRONG|contracts.py:483; emitted transport_agent.py:717; topic list omits audio.playback.lifecycle (contracts.py:50)
21|-|00:86|audio.pre_generate is a contract topic|WRONG|absent from contracts.py and lib.rs; mesh signal at pipeline.py:506
22|05p:83|00:88|voice agent emits no trailer (main.rs:807-821)|WRONG|main.rs:834-851 calls publish_stream_trailer (1840-1868); AudioStreamTrailer contracts.py:502
23|05p:83|00:88,13|transport sets is_done only for dicts; completion never reaches brain|WRONG|transport_agent.py:366-406 queue trailer; 548-580 COMPLETED after wait_for_playout; 789 publish
24|05p:83|00:88|brain COMPLETED branch 1150-1177 never fires|WRONG|brain_agent.py:1223-1260; progress `completed` now always False (transport:693, brain:1219)
25|-|00:99|no hypothesis in repo|WRONG|requirements-dev.txt:6; tests/test_playback_lifecycle.py:4, test_memory_security.py:7, test_brainbench_stats.py:8
26|-|00:93|grace 0.15 s|RIGHT|config.py:467; brain_agent.py:817-831
27|-|00:99|ordinary barge-in leaves unheard text in history|RIGHT|brain_agent.py:1303-1304 returns early; d/13:96
28|-|00:43|BackgroundScheduler removed|RIGHT|no match in app/ or tests/; tests/test_proactive_importance.py:317 prints, not asserts, 500 ms
29|05p:41|00:10|monologue unconsumed, dreams log-only|RIGHT|subconscious_agent.py:773-776, 836-838; check_subject_wiring.py:96; no subscriber
30|-|00:24|Qdrant None if down or under pytest|RIGHT|semantic_recall_store.py:35-40; memory_store.py:675
31|01:166|d/09:77|0 of 3 models pass ADR-002 rule|UNCHECKABLE|needs model runs; qwen3:4b had 100% parse failure so 2 measured, 21 messages
32|01:168|NF-17|arousal writes back into energy|RIGHT|agent_state.py:306-324, 1663
33|01:169|NF-16|decay uses nominal tick|RIGHT|agent_state.py:2287
34|01:210|NF-33|night recovery inverted|RIGHT|agent_state.py:2800-2815
35|01:211|NF-34|curiosity, learning_gain no caller|RIGHT|learning_governance.py:521 never imported; global_controls.py:94,116 only
36|01:208|NF-18|half-life curves|RIGHT|agent_state.py:378; config.py:448-453
37|01:157|NF-26|point estimates|RIGHT|tom.py:17-30; agent_state.py:2652-2700
38|01:351|NF-36|voice consumes modulation|RIGHT|voice-agent main.rs:773; surfacing_agent.py:163; expression.py:63-82
39|01:432,02:37|NF-38,12:295|workspace is a "per-turn object"|STALE|workspace.py:15-26 per-session epoch/revision snapshot (fix: "per-session CAS snapshot rewritten each turn")
40|01:311|11-v2-lifesim|1.203 s p50, 2.276 s p95|RIGHT|run C table; one persona, seed 1000, 1m, 3B
41|01:347|04-local-infra:127|GPT-SoVITS weights empty|RIGHT|models/GPT_weights, SoVITS_weights empty
42|01:360|vision|1 fps, VLM 5 s, suspended in turns|RIGHT|vision/agent.py:61,372; config.py:391,410
43|01:363|vision|no store of timestamped visual episodes|WRONG|subconscious_agent.py:615-665 add_memory(modality="visual"), gated on novelty plus arousal/valence; screen traces get a TTL (fix: "stored only when salient; no change tracking")
44|01:390|reflex.py|+0.04/+0.08/-0.03, "unambiguously positive"|RIGHT|reflex.py:32,87-89; live via agent.py:319, brain_agent.py:384,408
45|05p:43|agent_state.py:2743-2772|only state-to-LLM path is a 3-bin directive|WRONG|also emotion label (action.py:882), endocrine temperature/top_p (948-965), retrieval gating (773-775). Fix: "main PAD-to-words channel, beside label and sampling"
46|05p:44|switchboard.py:28-76|no bare-model arm|RIGHT|arms 28-75
47|05p:45|agent_state.py:282; config.py:355|no baseline writer; weights constant|STALE wording|setter has no caller; flag is REAPPRAISAL_WEIGHT_LEARNING_ENABLED (fix: "reappraisal", not "reflection")
48|05p:46|core.py:101-103; calibration.py:58|built, unused|RIGHT|no use of verifier, executor, simulator, evaluate_directive
49|05p:47|agent_state.py:1816|hostility raises integrity|RIGHT|exact
50|05p:48|config.py:197; ollama_client.py:32|3b and 1b defaults|RIGHT|exact
51|05p:57|action.py:718-731|D4 filter, probes|RIGHT|calls 1134, 1388, 1748; identity.py:784-789; character_pressure.json 4 probes
52|05p:62|ADR-001:30; 01-v2-delta:31|hit@3 0.02 to 0.69|STALE|ADR-001 says worst held-out cell (mean 0.91); delta doc says production-like cell; real nomic 0.023 to 0.810 (d/09:13)
53|05p:63|F-005 decision.py:702|strip at 721-727|RIGHT|function starts 682; 702 is prompt text
54|-|F-010|trust ceiling 0.833|RIGHT|(0.5+1+1)/3; agent_state.py:1801-1816
55|-|F-009|valence never written by update_from_appraisal|WRONG|valence aliases mood (agent_state.py:304-312); update_from_appraisal writes mood (1793). Conclusion holds

COUNTS (55): RIGHT 33, STALE 12 (1,2,5,10,11,12,13,15,18,39,47,52), WRONG 9 (20-25,43,45,55), UNCHECKABLE 1 (31).

V3 DOC LINES TO CORRECT (exact wording)
d/00-current-architecture.md
:30 "pipeline.py:751" -> "pipeline.py:721".
:58 "memory_store.py:1408" -> "memory_store.py:1574".
:64 "pipeline.py:865" -> "pipeline.py:817"; "appraisal.py:206-227" -> "appraisal.py:206-247, where user pitch or energy also lowers G".
:68 "agent_state.py:1342-1355" -> "agent_state.py:1801-1816".
:82 "learning.py:273" -> "learning.py:329".
:13 delete "the only place that currently *could* know when audio finished playing".
:86 replace the STARTED sentence: "audio.playback.lifecycle carries ordered STARTED, PLAYING, COMPLETED, INTERRUPTED and FAILED states (contracts.py:483). audio.pre_generate is a mesh signal, not a contract topic."
:88 replace the paragraph: "On chat.output.done the voice agent publishes an AudioStreamTrailer on audio.stream (main.rs:834-851). transport_agent.py queues it behind the PCM, waits for LiveKit playout, then publishes COMPLETED or FAILED on audio.playback.lifecycle (transport_agent.py:366-406, 548-580). The brain maps COMPLETED to a COMPLETED OutcomeRecord (brain_agent.py:1223-1260)."
:99 "there is no hypothesis or equivalent anywhere in the repo" -> "hypothesis is used in tests/test_playback_lifecycle.py, test_memory_security.py and test_brainbench_stats.py; barge-in interleavings still lack a state-machine test (W5)".
d/01-v2-delta.md: :35 config.py:307 -> :327; :49 memory_store.py:3866 -> :4085; :137-139 line cites -> rows 11-15 above; :139 item 4 "voice completion signal" -> "fixed by W4".
d/findings.md: F-012 config.py:276 -> :278, learning.py ~276-327 -> 329-379; F-010 and F-011 "~1342-1356" -> 1801-1816; F-009 drop "never valence" (row 55) and "~line 1088" -> 1021; F-002 Where -> transport_agent.py:548-580.
d/research/neuro/fidelity-table.md NF-17 "writes at 1796-1798, 2166-2172" -> "1663, 2087".
d/12-neuroscience-grounding.md:295 "per-turn CAS" -> "per-session CAS".

Next action: fix V4 rows 1, 2, 5, 10, 43, 45, 47, 52 and 55's V4 echoes in 02, 01 and 05p; hold the V3 doc edits for Q11.
