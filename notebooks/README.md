# Colab notebooks

Three notebooks, each doing a real GPU/CUDA-heavy job this project has that a
laptop either can't do at all, or can only do slowly and hot. All three are
self-contained: open the Colab badge, run top to bottom, download the
result, close the tab. None of them require you to have Docker, Postgres,
Neo4j, or NATS running anywhere.

| Notebook | Colab Launch | What it does | Needs a GPU? | Typical runtime |
| :--- | :---: | :--- | :--- | :--- |
| [`ai_friend_voice_training.ipynb`](ai_friend_voice_training.ipynb) | [![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/PALabs-v1/AI_friend/blob/main/notebooks/ai_friend_voice_training.ipynb) | Fine-tunes a GPT-SoVITS voice clone from your own recordings | Yes, hard requirement | 30–90+ min, depends on dataset size and epochs |
| [`ai_friend_eval_harness.ipynb`](ai_friend_eval_harness.ipynb) | [![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/PALabs-v1/AI_friend/blob/main/notebooks/ai_friend_eval_harness.ipynb) | Runs the behavioral eval gate (`backend/evals/`) against real Ollama models | Helps a lot, not required | 5–20 min per model |
| [`ai_friend_llm_benchmark.ipynb`](ai_friend_llm_benchmark.ipynb) | [![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/PALabs-v1/AI_friend/blob/main/notebooks/ai_friend_llm_benchmark.ipynb) | Measures raw generation throughput/latency/VRAM across model sizes | Yes, that's the point | 5–15 min for 3–4 models |

If you've never opened a Colab notebook before: click the "Open in Colab"
badge at the top of any notebook's first cell, then `Runtime → Change
runtime type → T4 GPU` (or better, if your account has access to one)
before running anything. A free Colab account gets you a T4 with time
limits and no guaranteed session length; that's enough for everything
below except the largest end of the voice-training and benchmark model
lists.

---

## Why only these three

This project has one other place that looks like it wants a Colab notebook
and deliberately doesn't have one: `backend/tools/measure/` (the `m11`–`m17`
pressure-scenario and latency harness) and the archived corpus-fitted
benchmarking suite (`_archive/research/`, moved out of live `scripts/research/`
during the 2026-08-29 docs de-fabrication pass). Neither made the cut, and
it's worth saying why rather than leaving it as an omission:

- **`backend/tools/measure/`** calls `ensure_bootstrapped()`, which runs the
  same schema/stream bootstrap a real deployment does — it needs Postgres,
  Neo4j, Qdrant and NATS JetStream actually running, not just Ollama.
  Docker does not reliably run inside Colab's own container (nested
  containerization there is unsupported, not merely inconvenient), so a
  "measure harness in Colab" notebook would either silently fall back to
  something smaller than what it claims to measure, or just fail. Either
  outcome is worse than not having the notebook. If you need these numbers,
  run them locally per `CLAUDE.md`'s "Getting a reliable test count" section
  — that's still the source of truth for `m11`–`m17`.
- **`_archive/research/`** (the old academic benchmarking suite) is built
  around a *simulated* cognitive engine (`cognitive_engine.py` — random
  procedural chitchat templates and closed-form math, not a real LLM call)
  layered on the same full Docker mesh requirement as above. Porting it to
  Colab wouldn't make it a real measurement; it would make a simulation
  look more official by association. `CLAUDE.md`'s integrity constraints
  section is explicit that this repo's documented benchmark numbers are
  placeholders until measured against real infrastructure, and that
  discipline applies here too — a Colab badge is not the fix for that. This
  is also why the suite was archived out of live `scripts/research/`
  entirely, not just left uncovered by a notebook: it also compiled a
  fabricated academic-paper PDF comparing itself against named real systems.
  The real, live measurement tools (`monitor.py`, `collector.py`,
  `injector.py`, `visualizer.py`, `human_realism_eval.py`,
  `estimate_realtime_latency.py`) stayed in `scripts/research/` — see its
  README.
- **QLoRA / fine-tuned-adapter training (Fine-Tuned Adapter)** is explicitly listed under
  the roadmap's "Explicitly not doing" section — `Fine-tuned models / QLoRA
  / Fine-Tuned Adapter consolidation. Roadmap-only, and the whole point of "generic
  models only" is that it works without them.` No notebook for it here,
  on purpose, not because it was forgotten.

If you find yourself wanting a fourth notebook for a real, infra-light,
GPU-bound job this list doesn't cover (a vision/VLM model's `describe_image`
latency, say), the throughput-benchmark notebook's pattern generalizes —
swap the model tag and prompt set, it doesn't need a new notebook.

---

## `ai_friend_voice_training.ipynb`

Trains the actual voice your friend speaks with, via
[GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS). This is a genuine
fine-tune, not the 8-second zero-shot clone `backend/scripts/audio/
record_voice.py` records locally — that script's clip is a **reference
clip** GPT-SoVITS conditions delivery on at inference time; this notebook
changes the model's actual weights, needs 10+ minutes of clean source audio,
and produces a stronger, more stable voice. You don't need this notebook to
have a working voice at all (Phase 1 ships a bundled default, and Phase 2.3
records a personal reference clip in seconds) — you need it only if you want
your friend's voice to be a deliberately trained clone rather than a
zero-shot approximation.

### Before you start: recording your source audio

- **10–15 minutes minimum** for a usable clone; **30–60+ minutes** for a
  strong one.
- **32kHz or higher**, mono, consistent loudness, minimal background noise
  or reverb, no clipped words. A quiet room and a decent USB mic beat a
  phone recording in an echoey space every time.
- Read naturally, varied sentences — not a monotone list. GPT-SoVITS learns
  prosody as well as timbre; flat source audio produces a flat clone.
- **Consent matters here as much as it does for the local enrollment flow**:
  this should be your own voice, or a voice you have explicit rights to
  clone. `backend/scripts/audio/record_voice.py` prints a consent notice
  before every local recording for the same reason.

### Step-by-step

1. **Open the notebook**, attach a GPU runtime (`Runtime → Change runtime
   type → GPU`).
2. **Cell 1** confirms the GPU is actually attached. If it raises, fix the
   runtime type before going further — don't try to push through on CPU,
   GPT-SoVITS training on CPU is impractical, not just slow.
3. **Cell 2 (recommended): mount Google Drive.** Training sessions run long
   enough that a Colab disconnect mid-run is a real risk on the free tier.
   Mounting Drive means your `training_data/` and any partially-trained
   checkpoints survive a disconnect; skip this only if you're doing a quick
   smoke test you don't mind re-running from scratch.
4. **Upload your recordings** into the `training_data/` folder this cell
   creates (via Drive directly, or the Colab file sidebar into
   `/content/training_data` if you skipped Drive).
5. **Cell 3, the Launcher**, installs GPT-SoVITS and its dependencies,
   downloads the ~2GB of pretrained base weights (skipped automatically if
   already present, so re-running after a forced "Restart Session" popup —
   normal after the CUDA/torch reinstall — won't re-download anything), and
   launches the Gradio WebUI with a public link.
6. **Open the Gradio link** and follow the workflow steps tab by tab:
   - **Phase 2**: slice your recordings and run ASR (transcription) on the
     slices.
   - **Phase 3**: format the dataset under the experiment name
     `ai_friend_voice`.
   - **Phase 4**: train SoVITS (batch size 12, 8 epochs is the cheatsheet's
     starting point) then GPT (15 epochs) on top of it.
   - **Phase 5**: load your trained checkpoints in the inference tab and
     listen. If it sounds wrong, that's cheaper to catch here than after
     export — go back and add more/cleaner data or more epochs before
     moving on.
7. **Back in this notebook, run the export cell** at the bottom. It finds
   your newest `.ckpt`/`.pth` (or a specific pair, if you set
   `GPT_CKPT_NAME`/`SOVITS_CKPT_NAME`), renames them to
   `ai_friend_voice.ckpt` / `ai_friend_voice.pth` — the exact names
   `.env.example`'s `CUSTOM_GPT_PATH`/`CUSTOM_SOVITS_PATH` defaults point
   at — bundles the vocoder alongside them, and zips the three files for
   download.

### Installing the result locally

Unzip into the repo root:

```
models/GPT_weights/ai_friend_voice.ckpt
models/SoVITS_weights/ai_friend_voice.pth
models/SoVITS_weights/vocoder.pth
```

These are exactly the paths `docker-compose.infra.yml`'s `gpt-sovits`
service bind-mounts (`./models/GPT_weights`, `./models/SoVITS_weights`), so
no `.env` edit is needed unless you renamed the files. Recreate the
container to pick them up:

```bash
docker compose -f docker-compose.infra.yml up -d --force-recreate gpt-sovits
```

`sovits_healthcheck.sh` reports unhealthy until it can actually load the new
weights — first load of a real checkpoint is memory-heavy and can take
several minutes, longer again on CPU fallback. Check `docker compose logs
local_voice` if it doesn't come up.

**Before making this your everyday voice**, run the A/B gate in the
cheatsheet's Phase 7 (Validation and Safe Promotion): generate a fixed
prompt set with the old and new weights, compare pronunciation/pacing/
stability, and keep the previous `.ckpt`/`.pth` pair around under different
names so you can roll back immediately if the new clone regresses.

---

## `ai_friend_eval_harness.ipynb`

Runs `backend/evals/` — the harness that answers "did this model + persona
combination change behavior?" — against real models on a GPU, instead of on
a hot laptop capped at 3B-parameter models.

**Why this is the direct continuation of the roadmap's Phase 6.3**: a
baseline eval run on the local Mac (`llama3.2:3b`, shipped neutral persona)
passed 5 of 9 probes and left two failures — `name-recall` and
`prompt-disclosure` — flagged but not chased, specifically because
continuing would have meant more live-infra load on an already-hot machine.
This notebook is where that continues: same harness, same shipped persona,
bigger models, a cool machine.

### What it needs, and — importantly — what it doesn't

The harness's default path (`--path llm`, the one this notebook uses)
probes the LLM boundary only: the real persona prompt from the real
`IdentityManager`, through the real `OllamaClient`, sampling pinned. **No
NATS, no Postgres, no Neo4j, no Qdrant** — see `backend/evals/README.md`'s
opening section, which says this explicitly. That's the entire reason this
notebook can run in Colab with nothing installed but Ollama and the
`backend/` Python dependencies.

The one exception is `--retrieval memory` on the multi-turn recall suite —
that strategy needs the real `MemoryStore`, i.e. Postgres+Qdrant+Neo4j, and
this notebook deliberately doesn't stand any of that up. It uses `bm25`
instead: a fifty-line Okapi BM25 retriever that needs no infrastructure and
serves as the harness's own control condition. If you specifically need
`memory`-retrieval numbers, that has to run against a live deployment's
databases, not from here.

### Step-by-step

1. **Cell 1**: GPU check (a missing GPU is a warning, not a hard stop here —
   the harness still runs on CPU, just slower per probe).
2. **Cell 2**: clones the repo (`--depth 1`, `backend/` is all that's
   needed) and installs `backend/requirements-dev.txt`. Change the `branch`
   field first if you're testing something other than `main`.
3. **Cell 3**: installs Ollama and starts it as a background process,
   polling `/api/tags` until it actually answers rather than guessing a
   fixed sleep.
4. **Cell 4**: edit the comma-separated model list and pull them.
   `llama3.2:3b` is included by default as the direct point of comparison
   against the local Mac's own results.
5. **Cell 5**: runs `evals run --model <tag>` once per model in your list,
   writing one report per model into `evals/out/`.
6. **Cell 6 (optional)**: `evals compare` between any two of those reports.
7. **Cell 7 (optional)**: the multi-turn recall suite
   (`run-conversation`), `--num-ctx 8192` to match the harness's own pinned
   default so results stay comparable to any local run, `--retrieval bm25`
   as the infra-free strategy.
8. **Cell 8**: zips every report in `evals/out/` and downloads it.

### Reading a report without fooling yourself

- **Check the header line first**: `model=... persona=... provenance=...`.
  `CLAUDE.md` calls a missing model or non-`live` provenance the "silent
  0/48" trap — it produces a report that *looks* like a clean pass (0
  attempted, 0 failed) rather than an obvious error. Never trust a pass
  count without checking this line first.
- **`persona=` should name the shipped neutral persona** currently in
  `backend/app/personality.json` — a fresh clone has no `config/
  persona.toml`, so there's nothing else it could pick up. If it names
  something else, an authored persona file leaked into your checkout and
  every number describes that persona, not the shipped default.
- **A regression is pass → fail, full stop.** `compare`'s gate is
  deliberately blunt (see `evals/README.md`); a probe's score moving a
  little between two passing runs is not evidence of anything by itself.
- **Give a freshly-loaded model one full run before trusting a comparison.**
  The harness's own `reset_model_state` (unload, reload, one throwaway
  generation before the first scored probe) exists because two runs from
  different starting states measurably disagreed on 3 of 16 probes even
  with byte-identical prompts and pinned sampling — it runs automatically
  here, but if you interrupt a cell mid-run against an already-loaded
  model, that guarantee is what you just skipped.
- **This notebook does not touch the ledger.** If a result here changes what
  you believe about the persona's behavior, write the `.agents/CONTEXT.md`
  entry by hand — don't paste raw report JSON into it.

---

## `ai_friend_llm_benchmark.ipynb`

Answers a narrower, more mechanical question than the eval harness: **how
many tokens/sec, at what time-to-first-token, and using how much VRAM, does
each candidate model size actually deliver on a real GPU?** This is the
cheap first look before the roadmap's "rent a real GPU" step —
the project's own hardware notes already treat the local Mac's 3B ceiling
as temporary, with a rented GPU as the planned next step; this notebook is
how you'd find out what going bigger actually costs before paying for it.

Standalone by design — it talks to Ollama's HTTP API directly and never
clones the repo, so there's nothing for a code change elsewhere to break.
It borrows the MEASURED/ESTIMATED/UNKNOWN provenance discipline from
`backend/tools/measure/`'s `schema.py` without importing it, since dragging
in `app.config` for what's fundamentally five HTTP calls in a loop isn't
worth the coupling.

### What it measures, and what it doesn't

Measured, for each model in your list, over a fixed 5-prompt set: mean
tokens/sec, mean time-to-first-token, and VRAM delta (via `nvidia-smi`).
Every model is unloaded and reloaded with one throwaway warm-up generation
before it's timed — the same reset the eval harness uses, for the same
reason: a model still holding a previous run's KV cache warm measurably
outperforms a freshly-loaded one, and conflating the two would make the
comparison meaningless.

It does **not** measure the actual cognitive-turn pipeline. A real
conversational turn in this project is up to six sequential LLM calls
(appraisal, decision, the action stream, an optional self-correction pass,
plus background reflection) — see `docs/FUTURE_WORK.md`'s note on
per-turn call count. This notebook's numbers are an input to reasoning
about that pipeline's cost, not a substitute for measuring it directly.

It's also **not** one of `backend/tools/measure/`'s registered `m11`–`m17`
reports, and its output should never be relabeled as one — those measure
the full mesh under contention; this measures raw generation in isolation.
If you fold a result from here into a discussion or the ledger, describe it
for what it is.

### Step-by-step

1. **Cell 1**: GPU check, then installs and starts Ollama the same way the
   eval notebook does.
2. **Cell 2**: edit the model list. The default set — `llama3.2:3b`,
   `qwen2.5:7b`, `llama3.1:8b`, `qwen2.5:14b` — brackets the local 3B
   ceiling on both sides; a bigger tag needs more VRAM than `nvidia-smi` in
   Cell 1 showed you, so check that before adding one.
3. **Cell 3**: runs the benchmark. This is the longest cell — each model
   gets unloaded, reloaded, warmed up, then timed on all 5 prompts with
   streaming enabled so time-to-first-token is measured directly rather
   than estimated from total latency.
4. **Cell 4**: a summary table (pandas) and two bar charts (tokens/sec,
   time-to-first-token) across your model list.
5. **Cell 5**: writes and downloads a JSON report carrying a provenance
   header (GPU name, prompt set, sampling options, timestamp) — so a number
   pulled out of this later still says what produced it.

### Reading the result

Bigger models cost tokens/sec and add VRAM pressure — the table exists to
put a real number on that tradeoff instead of a guess. What it can't tell
you is whether the *quality* difference is worth the cost — that's what the
eval harness notebook is for. Run both against the same candidate model
before deciding it's worth moving past the current default.
