# T5: a model built from scratch for a human-like mind (Claude pass)

Date of research: 2026-10-28. Repository read: `docs/brain-research-v3/{README,00-current-architecture,12-neuroscience-grounding}.md`, `decisions/INDEX.md`, `docs/brain-v4/06-decisions.md`, and `backend/app/{llm,cognitive/action.py,cognitive/json_extract.py,config.py,state/memory_store.py,state/semantic_recall_store.py}`. Nothing else under `docs/brain-v4/` was read.

Frame used: for now every model is a test vehicle. The owner's end goal is a model built from scratch to behave like a human.

Compute figures marked "(my arithmetic)" come from `FLOPs = 6 x params x tokens` and the sustained-throughput assumptions stated in section 4. Those throughputs are assumptions, not measurements.

---

## 1. Bottom line

1. **"Much easier" is contradicted for general competence and silent for the thing the owner actually proposes.** Nothing I found shows that building from scratch is easier than adapting an existing model. The evidence on data and compute points the other way: every model that behaves fluently in open conversation was trained on 10^11 to 10^13 tokens, and the one result that looks like an exception (BabyLM, 100M words) produces models that pass grammar and linguistic-probe tests, not models that hold a conversation, remember a person, or keep a character. No published system trains a model from scratch with persistent self-state, drives or affect in the objective and then evaluates it for human-likeness. That specific proposal is untested, so the evidence cannot say it is easier or harder. It can say it is not cheaper in data, and not cheaper in evaluation.

2. **What a from-scratch model could plausibly buy is narrower than "behaves like a human".** The defensible gains are: (a) no inherited assistant priors (RLHF measurably reduces output diversity), (b) control of tokenizer, data provenance and licence, (c) state that lives in the model (recurrent or fast-weight) instead of in the prompt, (d) affect or drive signals as training-time conditioning instead of sampler knobs. Each is a testable claim. None has been shown at a size you can train here.

3. **On the stated hardware the ceiling is a GPT-2-small class model.** A 124M to 200M parameter model on 2.5B to 10B tokens is feasible on one 8 GB GPU (days to weeks, section 4). A 1B model is not: AdamW mixed-precision state alone is about 16 GB, and 20B tokens is 93 to 460 days at 15 to 3 sustained TFLOPs (my arithmetic). Such a model holds roughly 2 bits of factual knowledge per parameter, about 31 MB of facts at 124M, so world knowledge and biography must live in the memory store (which is what V3 already does), not in weights.

4. **What V4 can decide now, cheaply:** a model-backend interface with a capability descriptor and a structured request (text plus named state channels), a versioned per-turn "mind trace" log that doubles as future training data, embeddings decoupled from the generator, and per-role model routing using the `ModelRole` seam that already exists. These pay off with today's test-vehicle models and make a from-scratch model a plug-in, not a rewrite.

5. **Kinds of "no" in this report:** "much easier" is a `SCIENCE` and `RESOURCE` no (general capability), a "silent" for the narrow-persona case. Training 1B+ here is a `RESOURCE` no. Whether any of it feels anything is `UNKNOWABLE` and not a target.

---

## 2. Evidence

### 2.1 How much data and compute reaches human-like language

**Scaling laws and critiques**

- Kaplan et al. 2020 fit power laws in loss vs parameters, data and compute over more than seven orders of magnitude; larger models are more sample-efficient; compute-optimal training stops well before convergence. (arXiv 2001.08361.)
- Hoffmann et al. 2022 (Chinchilla): 400+ models, 70M to 16B+ parameters, 5B to 500B tokens; parameters and tokens should scale in equal proportion; Chinchilla 70B beat Gopher 280B at the same compute with 4x the data, 67.5% average MMLU. The "about 20 tokens per parameter" rule is the common reading of this paper; the abstract page I opened gives only the equal-scaling statement, so the 20:1 figure is **UNVERIFIED here** (Gadre et al. below imply a ~20:1 reference). (arXiv 2203.15556.)
- Critique, replication: Besiroglu et al. 2024 could not fit Hoffmann's third estimation method on data reconstructed from the paper's plots, found it inconsistent with the paper's first two methods, and found the reported confidence intervals implausibly narrow ("would require over 600,000 experiments" vs likely under 500 run). Their re-derivation agreed with methods 1 and 2. Reading: the exact coefficients are shaky, the broad shape is not. (arXiv 2404.10102.)
- Critique, the target is wrong for small deployed models: Sardana et al. 2024 add inference demand to the objective; with about 1B+ requests expected, train smaller and longer than Chinchilla-optimal; 47 models validated; quality kept improving up to 10,000 tokens per parameter; fitting only at typical ratios overestimates the value of extra tokens at extreme ratios. Gadre et al. 2024: 104 models, 0.011B to 6.9B parameters; predicted a 1.4B model on 900B tokens (about 32x over-trained) from runs using about 300x less compute. Reading: for a small model, "enough data" is far above 20 tokens per parameter. (arXiv 2401.00448 read via the PDF text; arXiv 2403.08540.)
- Critique, data limits: Muennighoff et al. 2023: 400 runs, up to 900B tokens and 9B parameters; up to 4 epochs of repeated data cost "negligible" loss vs unique data; beyond that the value of extra compute decays toward zero. Reading: a small private corpus can be repeated about 4 times at little cost, not 100 times. (arXiv 2305.16264.)
- Capacity: Allen-Zhu and Li 2024 (Physics of LMs 3.3) estimate about 2 bits of factual knowledge per parameter (7B model about 14B bits), holding under int8 quantization. The exposure-count conditions I remember (about 1000 exposures per fact for the 2-bit figure, lower at 100) were not on the page I opened and are **UNVERIFIED**. (arXiv 2404.05405.)

**Small-data pretraining**

- BabyLM challenge (Warstadt et al. 2023): over 30 submissions; the winners used LTG-BERT and outperformed models trained on trillions of words on the challenge's evaluations; curriculum learning was the most common idea and was "mostly unsuccessful, with only modest gains in some cases". (arXiv 2504.08165, CoNLL 2023 findings.)
- Second challenge (Hu et al. 2024): 10M-word and 100M-word text tracks plus a 100M-word multimodal track; a hybrid causal-masked architecture did best; no submission beat the baselines in the multimodal track; training FLOPs correlated strongly with score. (arXiv 2412.05149.)
- Third challenge (BabyLM Team 2025, ACL anthology 2025.babylm-main.28, 32 models from 26 countries): added an Interaction track where a student learns from teacher-model feedback; added "human-likeness" evaluations (reading-time correlation, age-of-acquisition style tests); capped epochs; the best approaches changed the objective or architecture (a diffusion masked LM, a mixture-of-experts model, an RL-based interactive approach); the human-likeness award in the Strict track went to CLASS-IT, which fine-tunes a small LM on a general instruction-following set; training FLOPs and score were correlated only in the Interaction track. Corpus rows I could see: CHILDES child-directed speech, BNC dialogue, Project Gutenberg children's stories, OpenSubtitles, Simple English Wikipedia, Switchboard Dialog Act. (Read from the PDF text.)
- BabyLM 2026 (workshop page): Strict (100M words) and Strict-Small (10M) stay; multimodal and interaction become options inside those tracks; a new MultiLingual track; epochs capped at 10. (https://babylm.github.io/.)
- Baby Llama (Timiryasov and Tastet 2023): a 58M-parameter LLaMA-style student distilled from an ensemble of a GPT-2 and small LLaMA teachers, on the 10M-word BabyLM set, beat both teachers and a non-distilled twin. Hardware and scores were not on the page. (arXiv 2308.02019.)
- Zhang et al. 2020, MiniBERTas on 1M, 10M, 100M, 1B words: 10M to 100M words suffice for most syntactic and semantic features probed; commonsense and typical NLU skills need much more. (arXiv 2011.04946.)
- TinyStories (Eldan and Li 2023): models under 10M parameters (even one transformer block) produced fluent multi-paragraph stories, graded by GPT-4, **on a synthetic corpus written by GPT-3.5/4 in a 3-to-4-year-old vocabulary**; 125M-class GPT-Neo/GPT-2 small rarely stay coherent beyond a few words on ordinary text. The result is about a closed narrow domain, not general behavior. (arXiv 2305.07759.)
- Synthetic data: phi-1 (Gunasekar et al. 2023): 1.3B parameters, about 6B "textbook-quality" web tokens plus about 1B GPT-3.5-generated tokens, 50.6% HumanEval pass@1 (code only). Counter-evidence: Shumailov et al. 2023 report "model collapse", the tail of the original distribution disappearing when models train on model-generated data; the setup details (mixing ratio, recursion depth) were not on the page I opened. (arXiv 2306.11644; arXiv 2305.17493.)
- Overtraining recipes keep improving: SmolLM2 1.7B on about 11T tokens beats Qwen2.5-1.5B and Llama3.2-1B per its abstract. Qwen3 ships 0.6B to 235B; its report says small models are built using knowledge from the flagship models; the "36T tokens" figure I remembered is **UNVERIFIED**. (arXiv 2502.02737; arXiv 2505.09388.)

**What children receive vs what models receive**

- Frank 2023 (Trends in Cognitive Sciences forum, DOI 10.1016/j.tics.2023.08.007, text read from the PDF): LLMs get "four or five orders of magnitude" more language than children (abstract); the figure caption says "three to five orders" between estimated human input and LLM input. Candidate explanations: prior conceptual knowledge, multimodal grounding, and the interactive social nature of the input; he also notes that evaluation differences may inflate the apparent gap.
- Scale marker: the BabyLM 2026 page says Chinchilla saw "well over 10000 words for every one word a 13 year old child has heard"; that implies about 10^8 words by 13 (the page does not state 100M explicitly). A news article quotes about 100M words for a preteen in a language-rich home; that is secondary, treat as indicative.
- Word-count claims about specific populations (Hart and Risley's "30 million word gap") failed to replicate in Sperry, Sperry and Miller (Child Development, 42 families), but I only saw secondary summaries of that paper: **UNVERIFIED primary**. LENA all-day recordings (Gilkerson et al. 2017, AJSLP 26(2):248-265, 3,213 twelve-hour recordings) found adult word counts roughly age independent after early infancy; I did not open the hourly norms. Do not quote a words-per-day figure from this report.
- Vong et al. 2024 (Science, "Grounded language acquisition through the eyes and ears of a single child") trained a model on one child's head-camera video and speech. I could not open it (HTTP 403 on science.org; search returned nothing usable): **UNVERIFIED**, cited only as the existence of the result.

**Reading of 2.1 for this project.** Language form (grammar, basic semantics) is learnable from 10M to 100M words. Everything a "mind" needs beyond that (world knowledge, social cognition, instruction following, long-horizon consistency) is not shown to be learnable from child-scale data, and the gap is probably in data type (grounded, interactive) more than data amount, which a text-only, one-GPU project cannot supply.

### 2.2 What "behaves like a human" could mean for a from-scratch model

Three distinct readings, with different evidence:

1. **Behavioral distribution match** (predict what people choose). Centaur (Binz et al., Nature 2025, DOI 10.1038/s41586-025-09215-4): Llama 3.1 70B fine-tuned with LoRA on Psych-101, more than 10,000,000 choices from more than 60,000 participants in 160 experiments; better than existing cognitive models on held-out participants, with some generalization to new cover stories and domains. Critiques: Bowers and colleagues (Bristol news item) report its short-term memory and reaction times differ considerably from humans; Liu and Ding (National Science Open 2025, as reported secondhand) argue it overfits and does not understand instructions (**UNVERIFIED primary**). Lesson: matching choices is not matching process, and it was done by fine-tuning a general model, not from scratch.
2. **Process signature** (do the internal statistics look human). Oh and Schuler (TACL 2023, DOI 10.1162/tacl_a_00548): across 5 GPT-Neo and 8 OPT variants and two datasets, larger, lower-perplexity models fit human reading times worse (a monotonic log-linear trend). Oh and Schuler (Findings of EMNLP 2023, arXiv 2304.11389): surprisal fit to reading times peaks at about 2B training tokens for most capacities and then diverges from human-like expectations. Reading: **human-likeness at the mechanism level is not monotone in capability**; a deliberately small, less-trained model can be more human-like on that axis. This is the single strongest piece of evidence for a from-scratch route, and it is evidence about reading times, not about conversation.
3. **Organization of a mind** (the owner's working definition: persistent, unscripted internal state that causally drives speech and action). Here the surveyed work splits:

| Mechanism | Built, at what size, what shown | Source |
|---|---|---|
| Recurrent or state-space state in the model | RWKV scaled to 14B, "on par" with similarly sized transformers, constant memory at inference. Mamba-3B matches transformers twice its size, about 5x inference throughput. | arXiv 2305.13048; arXiv 2312.00752 |
| Their known weakness | Pure SSMs lag on copying and retrieval from context; 8B Mamba and Mamba-2 trail on 5-shot MMLU and Phonebook; a hybrid (43% Mamba-2, 7% attention, 50% MLP) beat the 8B transformer on 12 tasks by +2.65 average. | Jelassi et al. arXiv 2402.01032; Waleffe et al. arXiv 2406.07887 |
| Memory built into the model | Titans: neural long-term memory learned at test time, claims context beyond 2M tokens and needle-in-haystack gains; model sizes not on the page I opened. TTT layers: hidden state is itself a model updated at test time, 125M to 1.3B, keeps improving past 16k context where Mamba stalls. All author-reported. | arXiv 2501.00663; arXiv 2407.04620 |
| Continual learning | Nested Learning / Hope (NeurIPS 2025): a proposal framed against catastrophic forgetting, only the authors' own evidence. SEAL: a model writes its own finetuning data and update directives, rewarded by downstream performance (sizes and forgetting not on the page I opened). LoRA "learns less and forgets less" than full finetuning (continued pretraining on 20B tokens; instruction finetuning on about 100K pairs). | arXiv 2512.24695 (found via search); arXiv 2506.10943; arXiv 2405.09673 |
| Affect or preference in the objective | Pretraining with human preferences (Korbak et al., ICML 2023): conditional training on preference scores reduced undesirable content by up to an order of magnitude and beat pretrain-then-finetune; model sizes not on the page. RLVER (ICLR 2026): Qwen2.5-7B-Instruct with PPO on simulated users' deterministic emotion scores, Sentient-Benchmark 13.3 to 79.2, general math and coding largely kept. Both are conditioning or post-training on a text model, not a drive state. | arXiv 2302.08582; arXiv 2507.03112 |
| Interactive training | GLAM: FLAN-T5 variants as RL policies in a text environment (ICML 2023; sizes and numbers not on the page). BabyLM Interaction track: teacher-feedback students that outperformed baselines. | arXiv 2302.02662; ACL 2025.babylm-main.28 |
| Conversation from scratch | Meena (2020): 2.6B parameters trained end-to-end on public social-media conversations; 72% SSA for the best-perplexity model, 79% with filtering and tuned decoding, vs 86% quoted as human-level. | arXiv 2001.09977 |
| Persona and character | Character-LLM (EMNLP 2023): trains on reconstructed "experiences" of a character; no numbers on the page I opened. Generative Agents: 25 agents with a natural-language memory record, reflection and planning, wrapped around an existing LLM; ablations show observation, planning and reflection each matter. This is architecture around a model, which is what AI_friend already is. | arXiv 2310.10158; arXiv 2304.03442 |
| Post-training narrows output | RLHF generalizes better than SFT out of distribution but "significantly reduces output diversity" (two base models, summarization and instruction following). | arXiv 2310.06452 |

Nothing in this table is a from-scratch model whose training objective includes a persistent self-state or drive variable and which was then judged for human-likeness. That cell is empty in the literature I could open.

### 2.3 Evidence for and against small from-scratch models in a narrow human-like role

**For**

- Luo, Sun and Love 2024 (ICML 2024 LLM-and-cognition workshop): a 124M-parameter GPT-2 trained from scratch on 1.3B neuroscience tokens, with a domain tokenizer, reached expert-level performance predicting neuroscience results. **Condition that cuts against the headline:** the paper says performance held in both setups, from scratch and fine-tuning a pretrained GPT-2, so it does not show from-scratch beats adaptation. (arXiv 2405.09395.)
- Gu et al. 2020/2021 (ACM HEALTH): for domains with abundant unlabeled text such as biomedicine, pretraining from scratch gave "substantial gains over continual pretraining" on the BLURB benchmark. Condition: abundant in-domain text, BERT-sized encoders, not generative chat. (arXiv 2007.15779.)
- TinyStories and BabyLM above: tiny models do well when the domain is closed and the data is clean. Oh and Schuler: smaller or less-trained is more human-like on reading times.
- BabyLM 2025: an instruction-fine-tuned small model won the human-likeness award in the Strict track.

**Against**

- BloombergGPT (Wu et al. 2023): 50B parameters, 363B finance tokens plus 345B general tokens, trained from scratch for finance. Li et al. (EMNLP 2023 Industry) found ChatGPT and GPT-4 beat it and fine-tuned models on many tasks across 8 datasets, though GPT-4 struggled on NER and sentiment where domain knowledge matters. Scale and a huge domain corpus did not secure a lasting advantage. (arXiv 2303.17564; arXiv 2305.05862.)
- Gururangan et al. 2020: second-phase in-domain pretraining (continued pretraining) helped in all four domains; the cheap adaptation route works. (arXiv 2004.10964.)
- LoRA Land: 310 fine-tuned models (10 base models, 31 tasks); 4-bit LoRA models averaged about 10 points above GPT-4 and 34 above their base models. Task-level, mostly classification-style; it says small adapted models beat big prompted ones, which supports fine-tuning, not from-scratch. (arXiv 2405.00732.)
- Capacity and data: 2 bits per parameter; commonsense needs more data than syntax (Zhang). A 124M model cannot hold biography plus world knowledge.
- Centaur-style critiques: a model can match outputs without matching process.
- Business signal, not evidence: Character.AI's cofounders joined Google in a 2024 licensing deal. The search results did not say why; **I make no claim about its cause**.
- Practitioner opinion (a Hugging Face forum thread) says small domain corpora almost always underperform an adapted base model; that is opinion, not an experiment.

**Net.** Small from-scratch wins are on closed or abundant-text domains with narrow metrics. For a conversational character that needs world knowledge and a long life of shared history, the evidence favors adaptation of an existing small base, with knowledge in memory. The one counter-thread is human-likeness at the mechanism level (Oh and Schuler), which is real but about reading times.

---

## 3. State of the art for this track (with numbers)

- **Cheapest known "train a language model" recipes (single-owner scale):**
  - llm.c GPT-2 (124M) reproduction (Karpathy, 2024): 10B FineWeb tokens, 18,865 steps at about 0.5M tokens, about 90 minutes on one 8x A100 80 GB node, about $20; HellaSwag 29.9 vs 29.4 for OpenAI's GPT-2 124M (GPT-3 Small, 300B tokens, 33.7). On one GPU the author says roughly 4 to 24 hours depending on the card. (https://github.com/karpathy/llm.c/discussions/481.)
  - modded-nanogpt: reaches 3.28 FineWeb validation loss in under 330M tokens vs 10B for the llm.c baseline (a roughly 30x token reduction through recipe changes); the page lists record 92 at 0.665 minutes on 8x H100 dated 08/30/26. The record time is as the page states it and I did not check it independently. (https://github.com/KellerJordan/modded-nanogpt.)
  - nanochat (Karpathy): README claims GPT-2-level capability (CORE 0.2565 threshold) for about $48, about 2 hours on 8x H100, "closer to ~$15" on spot; leaderboard entry at 1.65 hours; the same page's "4e19 FLOPs" label is not consistent with the stated time at plausible utilization, so I do not use it. The GPT-2 original is listed at about $43,000 and 168 hours. (https://github.com/karpathy/nanochat.)
- **Best small open models, overtrained:** SmolLM2 1.7B (about 11T tokens); Gemma 3 270M (270M parameters, about 170M in embeddings for a 256k vocabulary and about 100M in transformer blocks; Google positions it for fine-tuning on narrow high-volume tasks; training tokens not stated); OLMoE 1B-7B (1B active, 7B total, 5T tokens). (arXiv 2502.02737; Google Developers Blog; arXiv 2409.02060.)
- **Distillation and pruning:** Minitron (Muralidharan et al. 2024): 8B and 4B models derived from a 15B model with up to 40x fewer training tokens per model, 1.8x less compute for the family, up to 16% higher MMLU than from-scratch models. (arXiv 2407.14679.)
- **Small-data human-likeness:** BabyLM winners above; Baby Llama 58M on 10M words.
- **Hybrids for state:** Mamba-2-Hybrid 8B (+2.65 over the transformer on 12 tasks, up to 8x faster generation predicted); TTT up to 1.3B.
- **Apple side:** M5 GPU adds a neural accelerator per core (Apple says more than 4x peak GPU compute vs M4 for AI work); unified memory bandwidth 153 GB/s vs 120 GB/s on M4; MLX needs macOS 26.2 or later for the accelerators; Apple's published numbers are inference time-to-first-token (3.33x to 4.06x vs M4, 24 GB MacBook Pro, 4096-token prompt, 128 tokens), with **no TFLOPS and no training benchmark**. (https://machinelearning.apple.com/research/exploring-llms-mlx-m5.)

---

## 4. The smallest realistic from-scratch project on a Mac M5 (16 GB) plus one 8 GB GPU

### 4.1 Compute table (my arithmetic)

FLOPs = 6 x N x D. Sustained throughput is an **assumption**: I take 3, 6, 10 and 15 TFLOPs. I could not find a verified training-throughput figure for either machine; the one spec page I saw listed an RTX 4060 at 30 TFLOPs bf16 dense with a note to double-check it. Measure for 30 minutes with `llm.c` or nanoGPT before trusting any row.

| Model and tokens | FLOPs | at 3 TF | at 6 TF | at 10 TF | at 15 TF | AdamW mixed state |
|---|---|---|---|---|---|---|
| 124M, 1.33B tokens (BabyLM 100M words x10 epochs) | 9.9e17 | 3.8 d | 1.9 d | 1.1 d | 0.8 d | 2.0 GB |
| 124M, 2.5B tokens (20 per param) | 1.9e18 | 7.2 d | 3.6 d | 2.2 d | 1.4 d | 2.0 GB |
| 124M, 10B tokens (llm.c recipe) | 7.4e18 | 29 d | 14 d | 8.6 d | 5.7 d | 2.0 GB |
| 350M, 7B tokens (20 per param) | 1.5e19 | 57 d | 28 d | 17 d | 11 d | 5.6 GB |
| 350M, 10B tokens | 2.1e19 | 81 d | 41 d | 24 d | 16 d | 5.6 GB |
| 1B, 20B tokens | 1.2e20 | 463 d | 232 d | 139 d | 93 d | 16 GB (does not fit) |
| 1B, 100B tokens | 6.0e20 | 2315 d | 1157 d | 694 d | 463 d | 16 GB (does not fit) |

Memory column is 16 bytes per parameter (weights, gradients, Adam moments, fp32 master copy) before activations; 8-bit optimizers and activation checkpointing could stretch the 8 GB card to roughly 400M to 500M parameters, at the cost of speed. Treat that stretch as untested.

### 4.2 A staged plan

**Stage 0 (about 2 to 4 weeks elapsed, Mac or GPU): the pipeline and the interface, 30M to 60M parameters.**
- Data: BabyLM-style 10M to 100M words (CHILDES, dialogue, subtitles, simple prose), plus your own consented conversation traces and persona-conditioned synthetic dialogue generated through local Claude Code (see open question 3 on licence).
- Tokenizer: BPE with a small vocabulary (8k to 16k). Gemma 3 270M shows the cost of a 256k vocabulary: about 170M of its 270M parameters are embeddings. For a small model, a small vocabulary leaves the budget for layers.
- Train 1 to 3 days. Hold out an evaluation set before looking at any output.
- Result could show: the whole loop works (data, tokenizer, train, serve through the `LLMClient` protocol, eval); that conditioning tokens for affect state can be added to the input format; early signal on whether the model's output valence follows an injected affect token more tightly than temperature-knob control does, at matched compute.
- Result could not show: anything about general competence, world knowledge, long-horizon consistency, or a human-like mind.

**Stage 1 (gated on Stage 0's controllability result): 124M to 200M parameters, 2.5B to 10B tokens, 3 to 30 days of GPU time depending on measured throughput.**
- Data: a licensed general slice (FineWeb-Edu-class web text) mixed with the conversational and synthetic persona data; up to about 4 epochs of the small private part is cheap per Muennighoff.
- Expected capability: GPT-2-small class (llm.c: HellaSwag about 30), possible coherent short replies, no reliable JSON, small context. With the owner's persona and affect conditioning in training, it could be a REALIZATION-role model (speak the already-decided content in character) and not a planner.
- Result could show: matched-compute ablations of architecture or conditioning (transformer vs hybrid with recurrent state; affect tokens vs none), and a head-to-head against a fine-tuned open small model (SmolLM2-135M or 360M class) on the narrow persona and consistency metrics.
- Result could not show: that from scratch beats adaptation in general (Luo et al. found the two equal), or anything about 1B+ behavior.

**Stage 2 (not feasible here): 350M to 1B.** 28 to 232 days at 6 TF for the 350M and 1B rows with Chinchilla-style tokens, and the 1B optimizer state does not fit. `RESOURCE`. A rented 8x H100 node for roughly two hours is the cost scale the nanochat README quotes for GPT-2-class (about $48); that would contradict the "no cluster" planning constraint, so it is listed as an owner decision, not a recommendation.

### 4.3 Cheaper alternatives and what each gives up

| Alternative | What it gives | What it gives up |
|---|---|---|
| Continued pretraining or full finetune of an open small base (SmolLM2 135M/360M/1.7B, Qwen3 0.6B, Gemma 3 270M) | Knowledge and instruction following for free; 1 to 3 days on the GPU for sub-1B | Tokenizer and inherited priors (RLHF diversity loss if you start from an instruct model); provenance; the "built from scratch" claim |
| LoRA adapters | Cheap; this repo already has `llm/adapter_gate.py` qualification | Learns less (Biderman et al.); cannot change architecture, so no in-model state |
| Distillation, prune-then-distill (Minitron, Baby Llama) | 40x fewer tokens (Minitron), better than non-distilled twin (Baby Llama) | Inherits the teacher's biases; needs a teacher, and a Claude-Code teacher raises the licence question |
| Recurrent or state-space architectures (RWKV, Mamba, xLSTM, TTT) or hybrids | State lives in the model; constant-memory inference; fits the "persistent state" definition | Weak at copying and in-context retrieval when pure (Jelassi, Waleffe); serving on Ollama/llama.cpp for non-transformer architectures is **UNVERIFIED** by me |
| Mixture of experts (OLMoE-style) | More capacity per active parameter | Total parameters still must fit in memory; no help on 8 GB |
| Synthetic data (TinyStories, phi-1 style) | Dramatically better data per token in a closed domain | Model-collapse risk; inherits the generator's style; licence |

---

## 5. Where our design is weak or out of date for a future from-scratch model

Interfaces where a model is assumed, with what would change. Paths are in `backend/app/`.

1. **Provider factory and client protocol.** `llm/__init__.py`: `build_llm_client` accepts only `"ollama"` or `"anthropic"` and raises otherwise; `LLMClient` is a 5-method protocol (`generate_stream`, `generate`, `describe_image`, `check_health`, `close`) taking `prompt: str` and `system: str | None`. Assumption: text in, text out, no channel for non-text inputs. Change: a structured request type carrying named state channels (affect vector, trust, drive levels, recurrent-state handle, memory slots), with the string path kept as an adapter. `describe_image` assumes a vision-capable model; a text-only model needs a capability flag.
2. **Prompt directive.** `cognitive/action.py` builds `system_instruction = f"{identity_prompt}\n\n{_CHAT_GUIDELINE}"` (line 1477, with the same pattern at 1623, 1782 and 1821); persona is compiled to prose (`persona/compiler.py`). A model trained with the persona in its weights would ignore it, and a model with a 1k to 2k context would choke on it. Change: compile persona to both prose and a structured record, and add a per-model prompt budget.
3. **Sampling as the affect channel.** `_compute_endocrine_options` in `cognitive/action.py` (around lines 940 to 997) maps cortisol to temperature (`0.9 - 0.6 x cortisol`), dopamine to `top_p` (`0.70 + 0.25 x dopamine`) and fatigue to `num_predict` (250 down to 100), passed as `options_override` to Ollama (`llm/ollama_client.py` builds the `options` dict in `_build_payload_attempts`). Assumption: affect reaches the model only as sampler settings. A from-scratch model can take affect as conditioning input, which is exactly the thing the project wants to test. Change: an "affect conditioning" abstraction with two realizations (sampler options, conditioning tokens or vector) selected by capability. This also gives the clean A/B for the Stage 0 test.
4. **Output parsing.** Chain-of-thought stripping of `<thought>`/`<think>` blocks (`cognitive/action.py`, around lines 1017 to 1060 and 1645); the optional typed-realization JSON envelope (lines 21 to 60, off by default via `LLM_TYPED_REALIZATION_ENABLED`); JSON extracted from free text for decision, learning and appraisal (`cognitive/json_extract.py`, used by `decision.py`, `learning.py`, `appraisal.py`). A 125M from-scratch model will not emit reliable JSON or think-tags. Change: grammar-constrained decoding for structured roles, or route structured roles to a different model.
5. **Capability manifest.** `llm/model_manifest.py` has four Ollama tags (`llama3.2:3b`, `qwen2.5:3b`, `phi4-mini`, `llama3.2:1b`) with context window, thinking tokens, streaming, structured output, language, quantization, latency. Missing: tokenizer id, chat template, state interface, conditioning inputs, persona-in-weights, maximum sequence. Unregistered tags always abstain (`classify_scenario` / `evaluate_capability`), so the current default for a new model is "not allowed to run any role".
6. **The best existing seam: `ModelRole`.** `llm/model_roles.py` defines six narrow roles (INTERPRETATION, CANDIDATE_GENERATION, PLANNING, EVALUATION, COMPRESSION, REALIZATION), per-role requirements (three of six require structured output; COMPRESSION requires a 65,536-token context), a negotiator with fallbacks (NATIVE, TEMPLATE_PROCEDURE, ROLE_DEGRADATION, ABSTAIN) and a fail-closed `validated=False` default with `ensure_committable`. This already encodes the principle that the brain kernel owns authoritative state and models are replaceable specialists. A from-scratch model would register for REALIZATION and CANDIDATE_GENERATION and abstain elsewhere. The gap is that the seam is not yet on the call path: `model_roles` is imported in only one place outside `llm/`, `cognitive/core.py` (line 24 import, line 116 constructs `ProviderCapabilityNegotiator()`), and I found no call that executes a role; the clients are built directly in `agents/brain_agent.py`, `agents/subconscious_agent.py`, `vision/agent.py`, `api/persona.py`, `persona/compiler.py` and `persona/profile.py` (my grep for `build_llm_client` or `OllamaClient`). Routing every LLM call through a role would make per-role model choice real.
7. **Embeddings and memory format.** `state/memory_store.py`: `_EMBEDDING_DIMENSION` is 768 (`_validated_embedding`, lines 139 to 146) and `self.embedding_model = "nomic-embed-text"` (line 621); `state/semantic_recall_store.py` hard-codes `vector_size=768` for Qdrant. Memory rows are text plus vector plus metadata and are injected into the prompt as prose (`_build_shared_history`). Embeddings are already separate from the generator, which is the right default; do not let a from-scratch model's own encoder replace them. Weakness: no per-row record of embedding model id or dimension, so changing the embedder is a silent corruption risk. Change: store `embedding_model` and `dim` per row and ship a re-embed migration.
8. **State ownership.** Authoritative state lives outside the model (`agent_state.py`, Redis/Neo4j mirrors, per `model_roles.py` docstring). In-model state (recurrent state, fast weights) would be a second copy. Decide now whether it is a cache (rebuildable from the stores) or authoritative (needs a checkpoint format, versioning and rollback). `DR-033` (no reset by design) makes the second option high-stakes.
9. **Qualification gate.** `llm/adapter_gate.py` requires zero regression on a held-out probe set, a matching prompt digest and a matching constitution digest before activation; `AdapterRecord` stores `training_set_hash` and `base_model_hash`. Reusable for a from-scratch checkpoint, but the prompt digest assumes a prompt-driven model; add a conditioning-schema digest.
10. **Context length and serving.** `Config.LLM_NUM_CTX` defaults to 8192; evals deliberately construct `OllamaClient` directly. A custom architecture needs either a llama.cpp-supported shape (Llama-style) or its own server implementing `LLMClient`.
11. **Training-data availability.** The system already computes per-turn state (PAD, trust, hormones, appraisal, retrieval traces; `last_search_trace`, `measure_trace.py`). There is no versioned record joining state, percept, retrieved memory, reply and outcome, so there is nothing to train from or replay.
12. **Judging human-likeness.** The V3 audit concerned mechanism fidelity (`NF-n` rows); for example `NF-12` (appraisal reads the agent's own mood, so a user's turn cannot reliably move valence) and `NF-23` (trust fed by the agent's own mood) would be inherited by any from-scratch model trained on traces produced by the current state machine. Training on traces made by a flawed state machine bakes the flaw in. The V3 P0 fixes should land before any trace becomes training data.

---

## 6. Limits, each classified

| Limit | Class |
|---|---|
| Pretraining 1B+ parameters (16 GB optimizer state, 93 to 460 days) on the stated hardware | `RESOURCE` |
| World knowledge capacity of a sub-200M model (about 2 bits per parameter, about 31 MB at 124M) | `RESOURCE` (plus `ENGINEERING`: put knowledge in memory and tools) |
| Child-level data efficiency: 3 to 5 orders of magnitude gap, children's grounded and interactive input | `SCIENCE` |
| Whether affect or drive state in the training objective yields a more human-like mind than state held outside the model | `SCIENCE` |
| In-model persistent memory and continual learning without forgetting at useful size | `SCIENCE` (only author-reported evidence for Titans, TTT, Hope) |
| Reliable structured output from a tiny from-scratch model | `ENGINEERING` (constrained decoding, role routing) |
| Matching a person's behavior distribution without matching process (Centaur dispute) | `SCIENCE` |
| An accepted measure of "behaves like a human" for open-ended conversation | `SCIENCE` for the construct; `ENGINEERING` for an internal protocol (rater panels, counterfactual state interventions) |
| Whether the system feels anything | `UNKNOWABLE`; not a target |
| A human-behavior corpus long and rich enough to train a "life" (no public single-person lifetime corpus; synthetic data risks collapse and inherits the generator's style) | `RESOURCE` (data) and `SCIENCE` (collapse conditions) |
| Decoupling the model behind an interface, per-role routing, embedding versioning, mind-trace logging | `ENGINEERING` |
| Serving a non-transformer or custom architecture through the current Ollama-based stack | `ENGINEERING` (support status **UNVERIFIED**) |
| Licence of using frontier-model outputs as training data | **UNVERIFIED**; read the terms of each provider before any synthetic-data run |

---

## 7. What this means for V4: ranked candidate moves

Ranked by value-per-effort and by how little they depend on the from-scratch bet paying off. Moves 1 to 5 are useful with today's test-vehicle models.

1. **Model-backend contract v2 with a capability descriptor and a conformance suite.** Structured request (text plus named state channels), response with usage and finish reason, and a descriptor (context, tokenizer id, chat template, structured-output mode, conditioning inputs, state interface, persona-in-weights, vision). Add the descriptor to `llm/model_manifest.py`; add contract tests that today's Ollama and Anthropic clients must pass.
   Measured by: number of call sites with model-specific strings or options outside `llm/` (target 0); conformance suite pass for both existing clients; adding a stub backend requires edits only in `llm/`.
   Depends on: nothing external.
2. **Versioned mind-trace record per turn** (state before and after, percept, retrieved memory ids, prompt-or-conditioning inputs, reply, later outcome), with provenance, consent flags and a schema version.
   Measured by: share of turns with a complete record; a replay test that reproduces the recorded state transitions deterministically; trace volume per week (the training-set size is a derived number).
   Depends on: owner consent for using his own conversations; the V3 P0 appraisal and trust fixes (`NF-12`, `NF-23`, `NF-39`, `NF-17`, `NF-16`) landing first so traces are not poisoned.
3. **Route every LLM call through a `ModelRole`** so that per-role model choice is real (structured roles to a model that can do them, realization to whatever model is under test).
   Measured by: share of LLM calls issued through a role request (target 100%); per-role failure rate by model size on a fixed prompt set.
   Depends on: move 1.
4. **Embedding versioning:** per-row embedding model id and dimension, a re-embed tool, a recall regression check.
   Measured by: recall@k unchanged within a stated tolerance after a swap on a fixed query set; zero rows without an embedder id.
   Depends on: nothing.
5. **Test-vehicle size sweep before any training:** run the realization role and the persona-consistency eval on 135M, 360M, 1.7B and 7B-class open models plus the current default, to find where character, consistency and affect-following degrade.
   Measured by: a curve of metric vs parameters; the smallest size where the metric stays within a stated margin of the largest. This sets what a from-scratch Stage 1 model would have to match, and it is the cheapest honest test of the owner's "much easier" claim for the narrow role.
   Depends on: move 1; GPU time (the owner's rule: ask before using the shared GPU box).
6. **Affect-conditioning abstraction with two realizations** (sampler options as now; conditioning tokens or vector), A/B-tested on an open small model fine-tuned with conditioning tokens before any pretraining.
   Measured by: agreement between the injected affect state and an independent valence classifier on generated text (effect size and confidence interval vs the sampler-only condition); an ablation that removes the conditioning and must make the effect disappear.
   Depends on: moves 1 and 5.
7. **Stage 0 from-scratch pilot** (30M to 60M parameters, small-vocab tokenizer, BabyLM-scale plus consented traces and synthetic persona dialogue), purely to validate the pipeline and the conditioning interface.
   Measured by: end-to-end run reproducible from a config; held-out perplexity; the conditioning effect from move 6 reproduced from scratch at matched compute.
   Depends on: moves 1, 2, 6; corpus licences; open question 3.
8. **Stage 1 (124M to 200M) only if move 7 shows a controllability gain over adapting a pretrained base,** with an architecture ablation (transformer vs a hybrid with recurrent state) at matched compute, and the in-model-memory vs external-memory comparison scored on memory-dependent consistency tasks (watch the copying and retrieval weakness).
   Measured by: the Stage 1 model vs the best fine-tuned open small model at similar size on the frozen persona-consistency and affect-following rubric, judged blind.
   Depends on: move 7 passing; measured throughput (section 4); an owner decision on rented compute.

Do not do, on this evidence: plan for a 1B+ from-scratch run on the stated hardware, or write down a target that says the from-scratch model will "behave like a human" without a metric (move 5's curve and the frozen rubric are the minimum).

---

## 8. Open questions for the owner (the evidence cannot settle these)

1. **What counts as "from scratch"?** Random-initialized weights only, or also your own tokenizer and data, or also a new architecture? Starting from an open base and continuing pretraining on your data satisfies the first two partially; the answer changes cost by 10x to 100x.
2. **Is one-off rented compute acceptable for a single pretraining run?** Quoted scale for GPT-2-class is tens of dollars on 8x H100 for a couple of hours (nanochat README), which conflicts with "no cluster". Without it, the ceiling is about 200M parameters.
3. **May training data include your conversation logs and frontier-model outputs generated through local Claude Code?** Provider terms on using outputs for training are **UNVERIFIED** in this report and must be read before any synthetic-data run; consent and privacy rules (`DR-001`, D8 "decide at integration") apply to your own logs.
4. **Which meaning of "human-like" is the target?** Behavior distribution (what people choose), process signature (reading times, memory span), or organization (persistent state that drives behavior)? The literature supports different models for each, and only the third matches your stated definition.
5. **Must the model know the world, or can knowledge live in memory and tools?** If memory and tools carry it, a 100M to 200M model for speech and state-following is coherent; if the model itself must know things, the project needs at least an order of magnitude more than this hardware.

---

## 9. Citation list

VERIFIED means I opened the page or PDF text and read the stated content. Where a page showed only an abstract, the report says so.

**Scaling and data**
- Kaplan, McCandlish, Henighan, et al. 2020. Scaling Laws for Neural Language Models. arXiv 2001.08361. VERIFIED (abstract page). https://arxiv.org/abs/2001.08361
- Hoffmann, Borgeaud, Mensch, et al. 2022. Training Compute-Optimal Large Language Models. arXiv 2203.15556. VERIFIED (abstract page); the 20 tokens-per-parameter figure itself is UNVERIFIED here. https://arxiv.org/abs/2203.15556
- Besiroglu, Erdil, Barnett, You. 2024. Chinchilla Scaling: A replication attempt. arXiv 2404.10102. VERIFIED (abstract page). https://arxiv.org/abs/2404.10102
- Sardana, Portes, Doubov, Frankle. 2024. Beyond Chinchilla-Optimal: Accounting for Inference in Language Model Scaling Laws. arXiv 2401.00448v3. VERIFIED (PDF text, abstract and intro). https://arxiv.org/abs/2401.00448
- Gadre, Smyrnis, Shankar, et al. 2024. Language models scale reliably with over-training and on downstream tasks. arXiv 2403.08540. VERIFIED (abstract page). https://arxiv.org/abs/2403.08540
- Muennighoff, Rush, Barak, et al. 2023. Scaling Data-Constrained Language Models. arXiv 2305.16264. VERIFIED (abstract page). https://arxiv.org/abs/2305.16264
- Allen-Zhu, Li. 2024. Physics of Language Models: Part 3.3, Knowledge Capacity Scaling Laws. arXiv 2404.05405. VERIFIED (abstract page); exposure-count conditions UNVERIFIED. https://arxiv.org/abs/2404.05405

**Small-data and developmental**
- Warstadt, Mueller, Choshen, et al. 2023. Findings of the BabyLM Challenge. CoNLL 2023. arXiv 2504.08165. VERIFIED (abstract page). https://arxiv.org/abs/2504.08165
- Hu, Mueller, Ross, et al. 2024. Findings of the Second BabyLM Challenge. arXiv 2412.05149. VERIFIED (abstract page). https://arxiv.org/abs/2412.05149
- BabyLM Team (Charpentier, Choshen, Cotterell, et al.). 2025. Findings of the Third BabyLM Challenge. Proc. First BabyLM Workshop, pp. 399-420, ACL Anthology 2025.babylm-main.28. VERIFIED (PDF text). https://aclanthology.org/2025.babylm-main.28.pdf
- BabyLM 2026 challenge page. VERIFIED. https://babylm.github.io/
- Timiryasov, Tastet. 2023. Baby Llama. arXiv 2308.02019. VERIFIED (abstract page). https://arxiv.org/abs/2308.02019
- Zhang, Warstadt, Li, Bowman. 2020. When Do You Need Billions of Words of Pretraining Data? arXiv 2011.04946. VERIFIED (abstract page). https://arxiv.org/abs/2011.04946
- Eldan, Li. 2023. TinyStories. arXiv 2305.07759. VERIFIED (abstract page). https://arxiv.org/abs/2305.07759
- Gunasekar, et al. 2023. Textbooks Are All You Need (phi-1). arXiv 2306.11644. VERIFIED (abstract page). https://arxiv.org/abs/2306.11644
- Shumailov, Shumaylov, Zhao, Gal, Papernot, Anderson. 2023. The Curse of Recursion. arXiv 2305.17493. VERIFIED (abstract page). https://arxiv.org/abs/2305.17493
- Frank, M. C. 2023. Bridging the data gap between children and large language models. Trends in Cognitive Sciences. DOI 10.1016/j.tics.2023.08.007. VERIFIED (PDF text from https://sites.socsci.uci.edu/~lpearl/colareadinggroup/readings/Frank2023_ChildrenLLMs.pdf).
- Gilkerson, et al. 2017. Mapping the early language environment using all-day recordings and automated analysis. AJSLP 26(2):248-265. DOI 10.1044/2016_AJSLP-15-0169. Search summary only: UNVERIFIED primary.
- Sperry, Sperry, Miller. 2019 (online 2018). Reexamining the verbal environments of children from different socioeconomic backgrounds. Child Development. Secondary summaries only: UNVERIFIED primary.
- Vong, Wang, Orhan, Lake. 2024. Grounded language acquisition through the eyes and ears of a single child. Science. DOI 10.1126/science.adi1374. Could not open (HTTP 403): UNVERIFIED.
- Oh, Schuler. 2023. Why Does Surprisal From Larger Transformer-Based Language Models Provide a Poorer Fit to Human Reading Times? TACL 11:336-350. DOI 10.1162/tacl_a_00548. Search summary of the abstract: partially verified. https://aclanthology.org/2023.tacl-1.20
- Oh, Schuler. 2023. Transformer-Based Language Model Surprisal Predicts Human Reading Times Best with About Two Billion Training Tokens. Findings of EMNLP 2023. arXiv 2304.11389. VERIFIED (abstract page). https://arxiv.org/abs/2304.11389

**Architectures with state, memory, continual learning**
- Gu, Dao. 2023. Mamba. arXiv 2312.00752. VERIFIED (abstract page). https://arxiv.org/abs/2312.00752
- Peng, et al. 2023. RWKV. arXiv 2305.13048. VERIFIED (abstract page). https://arxiv.org/abs/2305.13048
- Beck, et al. 2024. xLSTM. arXiv 2405.04517. VERIFIED (abstract page; no sizes). https://arxiv.org/abs/2405.04517
- Behrouz, Zhong, Mirrokni. 2024/2025. Titans: Learning to Memorize at Test Time. arXiv 2501.00663. VERIFIED (abstract page; author-reported). https://arxiv.org/abs/2501.00663
- Sun, Li, Dalal, et al. 2024. Learning to (Learn at Test Time): RNNs with Expressive Hidden States. arXiv 2407.04620. VERIFIED (abstract page). https://arxiv.org/abs/2407.04620
- Jelassi, Brandfonbrener, Kakade, Malach. 2024. Repeat After Me. arXiv 2402.01032. VERIFIED (abstract page). https://arxiv.org/abs/2402.01032
- Waleffe, et al. 2024. An Empirical Study of Mamba-based Language Models. arXiv 2406.07887. VERIFIED (abstract page). https://arxiv.org/abs/2406.07887
- Behrouz, Razaviyayn, Zhong, Mirrokni. 2025. Nested Learning: The Illusion of Deep Learning Architectures. NeurIPS 2025; arXiv 2512.24695. Search-result summary only: partially verified. https://neurips.cc/virtual/2025/poster/116123
- Zweiger, Pari, Guo, Akyurek, Kim, Agrawal. 2025. Self-Adapting Language Models (SEAL). arXiv 2506.10943. VERIFIED (abstract page). https://arxiv.org/abs/2506.10943
- Biderman, et al. 2024. LoRA Learns Less and Forgets Less. TMLR; arXiv 2405.09673. VERIFIED (abstract page). https://arxiv.org/abs/2405.09673
- Korbak, et al. 2023. Pretraining Language Models with Human Preferences. ICML 2023; arXiv 2302.08582. VERIFIED (abstract page). https://arxiv.org/abs/2302.08582
- Wang, Ma, et al. 2025. RLVER. arXiv 2507.03112 (ICLR 2026). VERIFIED (abstract page). https://arxiv.org/abs/2507.03112
- Carta, Romac, Wolf, Lamprier, Sigaud, Oudeyer. 2023. Grounding LLMs in Interactive Environments with Online RL (GLAM). ICML 2023; arXiv 2302.02662. VERIFIED (abstract page; no numbers). https://arxiv.org/abs/2302.02662
- Adiwardana, et al. 2020. Towards a Human-like Open-Domain Chatbot (Meena). arXiv 2001.09977. VERIFIED (abstract page). https://arxiv.org/abs/2001.09977
- Shao, Li, Dai, Qiu. 2023. Character-LLM. EMNLP 2023; arXiv 2310.10158. VERIFIED (abstract page; no numbers). https://arxiv.org/abs/2310.10158
- Park, O'Brien, Cai, Morris, Liang, Bernstein. 2023. Generative Agents. arXiv 2304.03442. VERIFIED (abstract page). https://arxiv.org/abs/2304.03442
- Kirk, et al. 2023. Understanding the Effects of RLHF on LLM Generalisation and Diversity. arXiv 2310.06452. VERIFIED (abstract page). https://arxiv.org/abs/2310.06452

**Human-likeness and the Centaur dispute**
- Binz, et al. 2025. A foundation model to predict and capture human cognition (Centaur). Nature. DOI 10.1038/s41586-025-09215-4. Verified through search-result summaries (PMC PMC12390832), not the paper text: partially verified.
- Bowers and colleagues, as reported in the University of Bristol news item. VERIFIED as a news item only; the underlying paper's title and venue were not on the page. https://www.bristol.ac.uk/psychology-neuroscience/news/2025/121---ai-model-of-human-mind.html
- Liu, Ding. 2025. Can Centaur truly simulate human cognition? National Science Open. Search summary only: UNVERIFIED primary.

**Small vs general for narrow roles**
- Luo, Sun, Love. 2024. Matching domain experts by training from scratch on domain knowledge. arXiv 2405.09395. VERIFIED (abstract page). https://arxiv.org/abs/2405.09395
- Gu, et al. 2020/2021. Domain-Specific Language Model Pretraining for Biomedical NLP. ACM HEALTH; arXiv 2007.15779. VERIFIED (abstract page). https://arxiv.org/abs/2007.15779
- Gururangan, et al. 2020. Don't Stop Pretraining. ACL 2020; arXiv 2004.10964. VERIFIED (abstract page). https://arxiv.org/abs/2004.10964
- Wu, et al. 2023. BloombergGPT. arXiv 2303.17564. VERIFIED (abstract page). https://arxiv.org/abs/2303.17564
- Li, et al. 2023. Are ChatGPT and GPT-4 General-Purpose Solvers for Financial Text Analytics? EMNLP 2023 Industry; arXiv 2305.05862. Search summary of abstract: partially verified. https://arxiv.org/abs/2305.05862
- Zhao, et al. 2024. LoRA Land. arXiv 2405.00732. VERIFIED (abstract page). https://arxiv.org/abs/2405.00732
- Character.AI and Google licensing deal, August 2024 (Bloomberg report via search results). Search summary only; no claim about cause. https://www.bloomberg.com/news/articles/2024-08-02/character-ai-co-founders-hired-by-google-in-licensing-deal

**Recipes, small models, hardware**
- Ben Allal, et al. 2025. SmolLM2. arXiv 2502.02737. VERIFIED (abstract page). https://arxiv.org/abs/2502.02737
- Yang, et al. 2025. Qwen3 Technical Report. arXiv 2505.09388. VERIFIED (abstract page); 36T tokens UNVERIFIED. https://arxiv.org/abs/2505.09388
- Muennighoff, et al. 2024. OLMoE. arXiv 2409.02060. VERIFIED (abstract page). https://arxiv.org/abs/2409.02060
- Muralidharan, et al. 2024. Compact Language Models via Pruning and Knowledge Distillation (Minitron). arXiv 2407.14679. VERIFIED (abstract page). https://arxiv.org/abs/2407.14679
- Google Developers Blog, Introducing Gemma 3 270M, 2025. VERIFIED. https://developers.googleblog.com/en/introducing-gemma-3-270m/
- Karpathy. llm.c GPT-2 (124M) reproduction discussion. VERIFIED. https://github.com/karpathy/llm.c/discussions/481
- Karpathy. nanochat README. VERIFIED (one internal inconsistency noted in section 3). https://github.com/karpathy/nanochat
- Jordan. modded-nanogpt README. VERIFIED as shown by the page at fetch time. https://github.com/KellerJordan/modded-nanogpt
- Apple Machine Learning Research. Exploring LLMs with MLX and the Neural Accelerators in the M5 GPU. VERIFIED. https://machinelearning.apple.com/research/exploring-llms-mlx-m5
- RTX 4060 8 GB bf16 throughput figure: one spec page via search, flagged by the search tool as needing a check against NVIDIA's whitepaper: UNVERIFIED.

**Repository files read** (paths from repository root): `docs/brain-research-v3/00-current-architecture.md`, `docs/brain-research-v3/12-neuroscience-grounding.md` (NF-n index), `docs/brain-research-v3/decisions/INDEX.md`, `docs/brain-v4/06-decisions.md`, `backend/app/llm/__init__.py`, `llm/model_manifest.py`, `llm/model_roles.py`, `llm/adapter_gate.py`, `llm/adapter_registry.py`, `llm/ollama_client.py`, `cognitive/action.py`, `cognitive/json_extract.py`, `config.py`, `state/memory_store.py`, `state/semantic_recall_store.py`.
