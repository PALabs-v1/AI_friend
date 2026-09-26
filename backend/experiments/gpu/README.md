# GPU / model-server experiments (Brain V2)

The cognitive benchmark (`python -m evals.cognitive ...`) runs anywhere with no
model: it uses a seeded synthetic embedder and scripted affect inputs. Two
Brain V2 conclusions depend on things a model-free environment cannot provide,
so they are packaged here to run on a machine with Ollama (the RTX box):

| Script | Question | Decision it can change |
|---|---|---|
| `real_embedding_retrieval.py` | Do the retrieval results (hybrid >> V1, the weight plateau, pool IDF) hold with the production embedder `nomic-embed-text`? Do nomic task prefixes help? | ADR-001 weights; whether to add `search_query:`/`search_document:` prefixes in `MemoryStore.get_embedding` |
| `tom_valence_affect.py` | Is the fast LLM's Theory-of-Mind `inferred_valence` accurate enough to drive appraisal? | ADR-002 follow-up: switch appraisal's goal congruence from the agent's own mood to the user's inferred valence |

## Setup

```bash
cd backend
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt -r experiments/gpu/requirements.txt
(cd crates/cognitive-rust && maturin build --release) && pip install target/wheels/cognitive_rust-*.whl
ollama pull nomic-embed-text && ollama pull llama3.2:3b     # or your LLM_FAST_MODEL
mkdir -p results
```

## Run

```bash
# 1. Retrieval with the production embedder (about 3 regimes x 20 seeds x ~420 texts)
python -m experiments.gpu.real_embedding_retrieval --backend ollama \
    --model nomic-embed-text --seeds 1-20 --out results/retrieval_nomic.json
# 1b. H-R3 needs a PAIRED delta (prefixed - unprefixed on the same seed/probe).
#     Two separate --prefix runs cannot produce that -- each only pairs its
#     arms against v1@sqlite, not against each other. Use --compare-prefix,
#     which embeds every scenario both ways in one process and reports the
#     paired bootstrap CI directly, under report["hr3_prefix_paired_delta"].
python -m experiments.gpu.real_embedding_retrieval --backend ollama --compare-prefix \
    --model nomic-embed-text --seeds 1-20 --out results/retrieval_nomic_prefix_compare.json
# 2. ToM valence accuracy + affect replay
python -m experiments.gpu.tom_valence_affect --model llama3.2:3b --repeats 3 \
    --out results/tom_valence.json
```

Plumbing smoke test with no model (numbers are NOT evidence):
`python -m experiments.gpu.real_embedding_retrieval --backend synthetic --seeds 1-2 --regimes summary --out /tmp/smoke.json`

## Expected output and how to read it

`retrieval_*.json`'s `results` key has the same shape as one experiment's
`results` field from `python -m evals.cognitive memory` (NOT the same shape
as that command's full output -- there is no `experiments` wrapper here,
since this script is one implicit experiment per invocation): per
`backend:model/regime` cell, every arm's hit@3 / MRR / obsolete-win / trap
rates with 95% bootstrap intervals and a paired delta against `v1@sqlite`,
plus `embedding` and `hardware` blocks (nvidia-smi, torch, CUDA). The stdout
headline prints V1, cosine-only and the hybrid. To render a table with
`evals.cognitive.report`, wrap the loaded JSON's `results` under a synthetic
experiment key first: `evals.cognitive.report.memory_worst_case({"experiments": {"x": report}}, "x")`.
With `--compare-prefix`, the output also has `hr3_prefix_paired_delta`
(regime -> arm -> paired bootstrap delta), which is what H-R3's rule
actually needs.

Pre-registered decision rules (do not move them after seeing results):

* **H-R1** hybrid - V1 >= 0.30 hit@3 on `summary`: keep ADR-001 as is.
  Below that, re-open ADR-001.
* **H-R2** the best `hybrid:w_lex:w_act` arm is within 0.02 hit@3 of
  `hybrid:1.5:0.2`: keep the shipped weights; otherwise move them to the
  centre of the new plateau and re-validate on held-out seeds 101-130.
* **H-R3** prefixed - unprefixed >= +0.02 hit@3 with a paired CI excluding 0:
  add prefixes to `get_embedding` (queries) and `add_memory` (documents).
  Existing stored vectors were embedded without the document prefix, so this
  needs a re-embedding migration; plan it before flipping.
* **ToM** adopt `inferred_valence` as appraisal input only if Pearson r >= 0.8,
  sign agreement >= 0.9 on non-neutral messages, and the simulated
  `hostile` script ends with trust <= 0.5 (see the script docstring).

Send the JSON files back (or commit them under `docs/brain-research/results/`)
and the corresponding ADR is updated from them.
