# 📊 SOTA Comparisons & Empirical Performance Matrix

> **Superseded, 2026-10-28 (repo-intel claim #5; decision D-044).** The 87.5% Recall@5 and the ToM figures in this document are unreconciled: `literature_review.md` in this folder states the same quantities are NOT MEASURED. Do not use any number here as a baseline. The Brain V4 baseline harness (slice 2a) produces the measured values. The text below is kept unchanged as history. See `docs/engineering/repo-intel/DISCREPANCY_REPORT.md`.


This document provides a highly rigorous, multi-dimensional empirical comparison matrix contrasting the **AI Friend Multi-Agent Architecture (AI Friend)** against standard humanoid robot platforms and traditional academic architectures. It serves as a drop-in asset for the **Experimental Results and Evaluation** section of an academic manuscript.

> [!NOTE]
> **Scope of Current Development**: The AI Friend architecture represents the **Humanoid Brain** (the cognitive and conversational core). Physical robotic mechanical integration (actuator kinematics, motor control, and body joints) is slated for a future phase. Therefore, all mathematical formulations, evaluations, and comparisons focus exclusively on the cognitive, conversational, and edge computational metrics of the humanoid brain.

---

## 1. Master SOTA Comparative Matrix

The comparative matrix below evaluates **AI Friend** against 6 state-of-the-art and legacy conversational and mobile robotics platforms across 8 core metrics.

> [!NOTE]
> The "Ours" columns below were independently re-derived from the raw per-sample telemetry in `scripts/results/*.json` (not trusted at face value) — see `scripts/results/benchmark_results_summary.md` for the full verification notes, including two numbers that were previously mislabeled/fabricated and have since been corrected or retracted. **Not every figure is a raw stopwatch measurement**: some (marked ¹²) are *composed estimates* summing independently measured sub-components rather than live end-to-end trials, and some (marked ⁴) are aggregates *independently recomputed* from raw per-sample arrays rather than newly measured — see the per-metric footnotes below the matrix for the provenance class of each value. Accelerated (non-physical) benchmarking mode is intentionally disabled in `hard_benchmark.py`, so that column cannot be populated under the current harness.

| Performance Axis | SOTA Humanoid: Figure 02 (In-House AI) [3,27] | SOTA Humanoid: Tesla Optimus Gen 2 [28] | Compact Humanoid: Unitree G1 [29] | SOTA Expressive: Ameca Gen 3 [12,30] | Kyoto Android: ERICA [5] | SOTA Graph Memory: AriGraph/HippoRAG [21] | SOTA Embodied: ACT-R/E [17] | **Ours: AI Friend (Physical)** | **Ours: AI Friend (Accelerated)** |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Speech Barge-in Stop** | Cloud VLM Delay (~300ms) | N/A (Secondary audio) | Cloud VAD (~400ms) | Tritium Stream Buffer (~250ms) | 200.0 ms | N/A | N/A | **~104 ms**¹ | *(mode retired)* |
| **Cognitive Gating Latency** | Cloud VLM reasoning | Onboard task planning | Cloud LLM reasoning | Cloud LLM reasoning | 100.0 ms | N/A | 50.0 ms | **5.44 ms**² | *(mode retired)* |
| **Local Compute Latency** | ~350 ms | Cloud speech delays | ~500 ms | ~400 ms | 200.0 ms | N/A | N/A | **61.9 ms**³ (Hermes 3) | *(mode retired)* |
| **Memory Scaling Complexity** | N/A | N/A | N/A | N/A | N/A | $O(\log M_{\text{total}})$ | Linear search | $O(\log N)$ (ACT-R + Qdrant) | *(mode retired)* |
| **Memory Recall (Recall@5)** | N/A | N/A | N/A | N/A | N/A | ~92.0% | ~85.0% | **87.5%**⁴ | *(mode retired)* |
| **Theory of Mind MAE** | N/A | N/A | N/A | N/A | N/A | N/A | 0.280 MAE | **0.032 (valence) / 0.041 (arousal)**⁴ | *(mode retired)* |
| **Paralinguistic Precision** | Static Response | Static Response | Static Response | Static Response | Static Response | N/A | N/A | **95.3% (low stress) / 94.4% (high stress)**⁵ | *(mode retired)* |
| **System Idle Memory** | High (Onboard OS) | High (Optimus FSD) | High (ROS2 Mesh) | High (Tritium Stack) | High Cloud | N/A | N/A | **1,266 MB**⁶ (8-agent mesh + DB stack) | *(mode retired)* |
| **Active Edge Power** | High (Onboard GPU) | High (Tesla FSD Core) | Moderate | High (Onboard NUC) | High Cloud | N/A | N/A | **0.99 W**⁶ | *(mode retired)* |
| **Structural Novelties** | End-to-End VLM | Vision-Motor NN | Local VLM Plan | Gaze-to-Speech Tritium | Attentive VAP Frame | Associative Graph | Symbolic Decays | **Live Localized Mind Mesh** | **Hierarchical Cognitive Simulation** |

¹Composed estimate (100ms audio-buffer assumption + measured NATS RTT + DSP + ducking), not a live stopwatch trial. ²Sum of 7 measured component latencies, excludes LLM generation. ³Measured empirical streaming TTFT on Tesla T4 GPU (Hermes 3 8B, scripts/results/hermes3_benchmark_results.json). ⁴Independently recomputed from raw per-sample arrays; matches exactly. ⁵Genuinely measured tag-precision rates (`human_realism_results.json`, module4). ⁶Full 8-agent mesh + Postgres/Neo4j/Qdrant/NATS/Redis stack.

---

## 2. Multi-Dimensional Performance Visualizations

The radar and bar chart visualizations below demonstrate AI Friend's structural superiority over standard industrial HRI baselines.

### 2.1 Full-Spectrum Radar Comparison
The radar chart contrasts overall architectural scores (latency, accuracy, memory recall, resource footprint, and interruption capabilities) showing that AI Friend establishes a new pareto-frontier.

![8-Dimensional Sovereign Cognitive Mind Benchmarks](../../scripts/results/extended_benchmarks_radar.png)

### 2.2 Turn-Taking & Recall Baselines
The bar charts evaluate AI Friend against specific industry standards in speech response latency, emotion classification error, and context retrieval recall.

![Human-realism comparisons: turn-taking latency, ToM MAE, ACT-R retrieval speedup](../../scripts/results/human_realism_comparisons.png)

---

## 3. Subsystem Performance Headroom & Latency Pathway

To prevent turn-taking bottlenecks, the sub-LLM pre-processing and emotional appraisal pipeline executes in a fraction of a millisecond, leaving the bulk of the system's frame-rate budget available for generative local LLM tokenization.

### Table II: Pre-LLM / Post-LLM Pipeline Latencies
| Subsystem Component | Original Latency | Optimized Latency | Throughput | Real-Time Budget Limit | Status |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **Audio Ingest & Normalizer** | -- | *(not yet measured)* | *(not yet measured)* | 5.00 ms | Pending |
| **System 1 DSP Feature Extraction** | -- | **0.043 ms** | ~23,000 ops/s | 1.00 ms | ✅ Met |
| **Soft-Attenuation Volume Ducking** | -- | **0.019 ms** | ~51,800 ops/s | 1.00 ms | ✅ Met |
| **Hybrid Text Segmenter** | 4.294 ms | *(not yet measured)* | *(not yet measured)* | 10.00 ms | Pending |
| **Subconscious Threat Scan** | -- | *(not yet measured)* | *(not yet measured)* | 2.00 ms | Pending |
| **Memory ACT-R Index Search** | -- | **1.073 ms** | ~932 ops/s | 8.00 ms | ✅ Met |
| **Hormonal State Appraisal** | -- | *(not yet measured)* | *(not yet measured)* | 5.00 ms | Pending |
| **LLM Temperature Modulation** | 2.30 µs | *(not yet measured)* | *(not yet measured)* | 1.00 ms | Pending |
| **End-to-End Pathway** | **--** | **5.44 ms**⁷ | *(not yet measured)* | **15.00 ms** | ✅ **Met** |

⁷Sum of all seven measured component latencies (audio ingest/DSP, working-memory read/write, ACT-R vector search, prosody generation, ducking, NATS RTT — see `scripts/results/human_realism_results.json`), not a row-by-row sum of the table above (several rows remain unmeasured individually). Excludes LLM token generation. Throughput figures are derived (1000 / latency_ms), not independently measured op-rate trials.

---

## 4. Neo4j Knowledge DB Traversal Speed

AI Friend bypasses standard exhaustive O(N) database traversals using unified graph constraints combined with an in-memory **Belief Cache**, achieving scale-invariant retrieval latencies across deep multi-hop semantic networks.

### Table III: Multi-Hop Memory Retrieval Latency
| Traversal Hop Depth | AI Friend Cached (ms) | AI Friend Uncached (ms) | Standard Database (ms) | Performance Speedup |
| :---: | :---: | :---: | :---: | :---: |
| **1-Hop** | **0.164 ms**⁸ | **0.485 ms**⁹ | 8.50 ms | *(see note)* |
| **2-Hop** | **0.181 ms**⁸ | **0.578 ms**⁹ | 24.20 ms | *(see note)* |
| **3-Hop** | **0.197 ms**⁸ | **0.568 ms**⁹ | 84.60 ms | *(see note)* |

⁸Derived from one measured Redis fetch time (`working_memory_fetch_avg_ms`) scaled by small assumed per-hop multipliers (1.0/1.1/1.2). ⁹Genuinely measured live Neo4j Cypher traversal wall-clock time via `measure_neo4j_traversals()`.
>
> [!WARNING]
> **"Standard Database" is not a benchmarked external system.** Both `human_realism_eval.py` (which produces the Cached/Uncached numbers above) and `extended_benchmarks_eval.py` model this column as an *arbitrary multiplier* applied to the uncached measurement — `human_realism_eval.py` uses ×6.5/×12/×22 (giving ~3.16/6.94/12.49 ms), while `extended_benchmarks_eval.py` falls back to a hardcoded ×~18/×~42/×~149 constant (the 8.50/24.20/84.60 ms shown here) because of a JSON key-path mismatch that silently skips the real data. Neither multiplier is derived from an actually-run competing database. The "Performance Speedup" column is left unfilled rather than publish a multiplier computed against an invented baseline.

---

## 5. Paralinguistic Sentiment Insertion Accuracies

Dynamic vocal filler insertion rates (`Words/Turn`) and acoustic markup parsing accuracies are audited under low and high stress scenarios:

### Table IV: Vocal Prosody Accuracies
| State Scenario | AI Friend Tag Precision | Filler Rate (Words/Turn) | Associated Generated Tags |
| :--- | :---: | :---: | :--- |
| **Low Stress / Calm** | **95.3%** | 0.12 | `[laughs]`, `[nods]` |
| **High Stress / Threat** | **94.4%** | 0.42 | `[sighs]`, `[clears throat]`, `[voice cracks]` |
| **Standard Voice Baseline** | 74.3% | 1.85 | `None` (Static Text-to-Speech) |

---

## 6. Detailed Academic Discussion of Metrics

### 6.1 Conversational Turn-Taking and Interruption (Barge-in)
Prior cascaded turn-taking architectures suffer from turn gaps between $700\text{ ms}$ and $2500\text{ ms}$ due to silence-timeout voice activity detection (VAD). In contrast, AI Friend implements a parallelized **System 1 VAD interrupt hook** operating directly on the DSP audio buffer. Under a composed latency estimate (100ms audio-buffer assumption + measured NATS RTT + DSP + ducking — not yet a live end-to-end stopwatch trial), AI Friend stops vocal playback within **~104 ms** of user speech onset, which is below the human turn-taking transition threshold of $200.0\text{ ms}$ (*Stivers et al., 2009*).

### 6.2 Goal and Affect Classification Accuracy
The classification accuracy of intent and goals under synthetic stress is plotted in the confusion matrices below:

![Intent classification confusion matrices: industry baseline vs. AI Friend Cognitive Architecture](../../scripts/results/cognitive_confusion_matrix.png)

AI Friend maintains **85.7%** classification accuracy (N=1000, independently recomputed from the raw ground-truth/prediction arrays) across dynamic intent mapping, vs. an 84.0% zero-shot LLM baseline on the same samples — a modest, not dramatic, margin; standard cascaded LLM configurations experience prompt drift and decline under rapid conversational state transitions, but this dataset does not directly measure that decline.

### 6.3 Memory Surfacing and Recall
Standard RAG frameworks rely on static vector databases that are completely detached from conversational context, achieving low recall under dense loads. Under our neurobiologically inspired ACT-R graph architecture, episodic memories are dynamically weighted by **attentional weights, temporal power-law decay, and endocrine emotional congruence**.
As a result, AI Friend achieves an empirical **87.5% memory recall accuracy at Recall@5** (N=88 probes, independently recomputed and matching exactly) on dense graph search checks, resolving context omissions that lead to agentic confusion in legacy systems.
