#!/usr/bin/env bash
# Collect every Brain V3 research artifact (benchmark runs, experiment
# results, lifesim corpora, scale runs, Codex reports, CI/test logs) from the
# Mac and home-gpu into ONE archive outside the repo, then write a manifest.
#
#   bash scripts/research/collect_research_data.sh            # collect + manifest
#   AIF_DATA_DIR=/path bash scripts/research/collect_research_data.sh
#   SKIP_REMOTE=1 bash scripts/research/collect_research_data.sh   # Mac only
#
# Copy-only and incremental (rsync -a, never --delete): rerun it after any new
# run and only new or changed files move. Sources are never modified. Layout
# and meaning of every directory: $AIF_DATA_DIR/README.md. Why outside git:
# the raw outcomes are GBs (CLAUDE.md "Safety": no large artifacts in the repo);
# the small summaries are also committed under docs/brain-research-v3/results.
set -uo pipefail

DATA=${AIF_DATA_DIR:-$HOME/Projects/PALabs/AI_friend-research-data}
REMOTE=${AIF_REMOTE:-home-gpu}
RDIR=${AIF_REMOTE_DIR:-/data/aif-v3}
REPO=$(cd "$(dirname "$0")/../.." && pwd)
SHARED=$(dirname "$(git -C "$REPO" rev-parse --path-format=absolute --git-common-dir)")
TMP=/private/tmp
SESSIONS=/private/tmp/claude-501/-Users-aniketsaha-Projects-PALabs
EXCLUDES=(--exclude __pycache__ --exclude '*-uv-cache' --exclude .venv --exclude target --exclude '*.pyc')
LOG="$DATA/COLLECT_LOG.tsv"
failures=0

mkdir -p "$DATA"
[ -f "$LOG" ] || printf 'collected_at\tstatus\tsource\tdestination\n' > "$LOG"

record() { printf '%s\t%s\t%s\t%s\n' "$(date -u +%FT%TZ)" "$1" "$2" "$3" >> "$LOG"; }

# local SRC DEST: copy a Mac file or directory (skipped quietly if absent).
local_copy() {
  local src=$1 dest=$DATA/$2
  [ -e "$src" ] || return 0
  mkdir -p "$(dirname "$dest")"
  if [ -d "$src" ]; then
    mkdir -p "$dest" && rsync -a "${EXCLUDES[@]}" "$src/" "$dest/"
  else
    rsync -a "$src" "$dest"
  fi
  local rc=$?
  record "$([ $rc -eq 0 ] && echo ok || echo "rsync=$rc")" "mac:$src" "$2"
  [ $rc -eq 0 ] || failures=$((failures + 1))
}

# remote SRC DEST: copy from home-gpu.
remote_copy() {
  local src=$1 dest=$DATA/$2
  ssh -o ConnectTimeout=10 "$REMOTE" "test -e '$src'" 2>/dev/null || return 0
  mkdir -p "$dest"
  if ssh "$REMOTE" "test -d '$src'"; then
    rsync -a "${EXCLUDES[@]}" "$REMOTE:$src/" "$dest/"
  else
    rsync -a "$REMOTE:$src" "$dest/"
  fi
  local rc=$?
  record "$([ $rc -eq 0 ] && echo ok || echo "rsync=$rc")" "$REMOTE:$src" "$2"
  [ $rc -eq 0 ] || failures=$((failures + 1))
}

echo "[collect] archive: $DATA"

# ---- home-gpu -------------------------------------------------------------
if [ "${SKIP_REMOTE:-0}" != "1" ]; then
  if ssh -o ConnectTimeout=10 -o BatchMode=yes "$REMOTE" true 2>/dev/null; then
    echo "[collect] $REMOTE: brainbench runs, experiments, baselines, logs"
    for run in $(ssh "$REMOTE" "ls $RDIR/runs 2>/dev/null"); do
      remote_copy "$RDIR/runs/$run" "brainbench/home-gpu/$run"
    done
    remote_copy "$RDIR/gpu-results" "gpu-experiments/home-gpu"
    remote_copy "$RDIR/baseline-results" "baseline-phase3/home-gpu"
    remote_copy "$RDIR/run-logs" "logs/home-gpu/run-logs"
    remote_copy "$RDIR/memory_consistency.json" "infra-validation/home-gpu"
    remote_copy "$RDIR/backend/.benchmarks" "baseline-phase3/home-gpu/pytest-benchmark"
    for s in gpu_sweep.sh homegpu_baseline.sh start.sh; do
      remote_copy "$RDIR/$s" "logs/home-gpu/launch-scripts"
    done
  else
    echo "[collect] $REMOTE unreachable; Mac only this time" >&2
    record "unreachable" "$REMOTE" "-"
  fi
fi

# ---- Mac ------------------------------------------------------------------
echo "[collect] mac: brainbench dev runs, lifesim, scale, baselines, Codex, CI"
for d in "$TMP"/w9-panel* "$TMP"/w9-cells* "$TMP"/w9-mpl "$TMP"/brainbench-*; do
  [ -e "$d" ] || continue
  case "$d" in *-uv-cache) continue ;; esac
  local_copy "$d" "brainbench/mac/$(basename "$d")"   # run dirs and their .log files
done
local_copy "$TMP/lifesim" "lifesim/mac"
local_copy "$TMP/aif-scale-p8" "scale/mac/aif-scale-p8"
local_copy "$TMP/aif-scale-p8-final" "scale/mac/aif-scale-p8-final"
local_copy "$TMP/brain-v3-baseline" "baseline-phase3/mac"
local_copy "$TMP/gpu-results-pull" "gpu-experiments/mac-pull-of-home-gpu"
local_copy "$TMP/mac-thermal" "logs/mac/thermal"
local_copy "$TMP/v2base" "logs/mac/v2base-monitor"
local_copy "$TMP/critique" "critique/mac"

# Every Claude session scratchpad for this project: Codex reports and logs,
# prompts, snapshots, and the test/CI evidence each review relied on.
for sp in "$SESSIONS"/*/scratchpad; do
  [ -d "$sp" ] || continue
  sid=$(basename "$(dirname "$sp")" | cut -c1-8)
  local_copy "$sp/codex" "codex/$sid/reports-and-logs"
  local_copy "$sp/codex-snapshots" "codex/$sid/snapshots"
  local_copy "$sp/parallel" "codex/$sid/prompts"
  local_copy "$sp/scale" "scale/mac/scratch-$sid"
  local_copy "$sp/mutants" "tests-and-ci/$sid/mutants"
  local_copy "$sp/xcheck" "tests-and-ci/$sid/xcheck"
  local_copy "$sp/oldlifesim" "lifesim/mac-oldlifesim-$sid"
  for f in "$sp"/*.log "$sp"/*.txt "$sp"/*.out "$sp"/*.json; do
    [ -f "$f" ] && local_copy "$f" "tests-and-ci/$sid/$(basename "$f")"
  done
done

# Earlier runs kept inside the repo itself (pre-V3 benchmarks, the V1/V2
# measurement tools' outputs, code-quality baselines, evidence packs). They
# stay tracked in git where they are; the archive copies them under their
# original repo path so one tree holds every result ever produced.
for p in scripts/results academic_benchmarks/datasets backend/tools/measure/out \
         backend/tools/quality/baseline evidence notebooks/ai_friend_llm_benchmark.ipynb \
         notebooks/ai_friend_eval_harness.ipynb scripts/research/research_pad_trajectory.csv; do
  local_copy "$REPO/$p" "earlier-runs/repo/$p"
done
# ...and the ones only the shared checkout has (never committed).
local_copy "$SHARED/orchestration" "earlier-runs/shared-checkout-untracked/orchestration"
local_copy "$SHARED/backend/evals/out" "earlier-runs/shared-checkout-untracked/backend/evals/out"

# The committed summaries, so the archive is complete on its own.
local_copy "$REPO/docs/brain-research-v3/results" "repo-results/brain-research-v3"
local_copy "$REPO/backend/evals/brainbench/baseline" "repo-results/brainbench-gate-bands"

# The hand-written guide is versioned in the repo; install the current copy.
cp "$REPO/scripts/research/research_data_README.md" "$DATA/README.md"

echo "[collect] writing manifest"
python3 "$REPO/scripts/research/data_manifest.py" "$DATA" || failures=$((failures + 1))
du -sh "$DATA" | awk '{print "[collect] total " $1}'
[ $failures -eq 0 ] || { echo "[collect] $failures copy step(s) failed; see $LOG" >&2; exit 1; }
echo "[collect] done"
