#!/usr/bin/env bash
# Research-run loop on the home GPU box: sync a branch, launch a detached job,
# wait for it, fetch its results. Codifies the flow the Brain V3 cycle ran by
# hand a dozen times (Phases 4-6).
#
#   bb_remote.sh sync [branch]              fast-forward the remote clone to origin/<branch>
#   bb_remote.sh run NAME -- CMD...         run CMD detached in backend/, log to $BB_LOGS_DIR/NAME.log
#   bb_remote.sh status NAME                running or finished (+exit code), and the log tail
#   bb_remote.sh wait NAME [poll_seconds]   block until NAME finishes; exit with its exit code
#   bb_remote.sh fetch NAME REMOTE_PATH LOCAL_DIR   copy results back (REMOTE_PATH relative to backend/)
#
# Env: GPU_HOST (default home-gpu), BB_REMOTE_DIR (default /data/aif-v3),
# BB_LOGS_DIR (default $BB_REMOTE_DIR/run-logs),
# BB_DRY_RUN=1 prints the commands instead of running them.
#
# A run writes <log>.exit with the command's exit code when it ends, so a
# finished run is distinguishable from one still going or one that was killed
# (no .exit file and no process).

set -euo pipefail

HOST="${GPU_HOST:-home-gpu}"
DIR="${BB_REMOTE_DIR:-/data/aif-v3}"
# Run outputs live in the clone but are listed in its .git/info/exclude
# (machine-local, never committed; `sync` maintains it), so a run never
# makes the tree it is measuring read dirty. /data itself is root-owned.
LOGS="${BB_LOGS_DIR:-$DIR/run-logs}"
EXCLUDES="runs/ run-logs/ baseline-results/ gpu-results/ hf-cache/ gpu_sweep.sh homegpu_baseline.sh memory_consistency.json"

remote() {
    if [ "${BB_DRY_RUN:-0}" = "1" ]; then
        printf 'ssh %s: %s\n' "$HOST" "$1"
    else
        ssh -o ConnectTimeout=10 -o BatchMode=yes "$HOST" "$1"
    fi
}

valid_name() {
    [[ "$1" =~ ^[A-Za-z0-9._-]+$ ]] || { echo "bad run name: $1" >&2; exit 2; }
}

cmd="${1:-}"
shift || true

case "$cmd" in
sync)
    branch="${1:-brain-v3}"
    # Fast-forward only: the remote clone never carries local commits, so a
    # non-ff means someone edited it by hand, which must stop the run.
    # The native extension is rebuilt only when its crate changed.
    remote "set -e; cd $DIR; \
for p in $EXCLUDES; do grep -qxF \"\$p\" .git/info/exclude || echo \"\$p\" >> .git/info/exclude; done; \
before=\$(git rev-parse HEAD); git fetch -q origin; \
git checkout -q $branch; git merge -q --ff-only origin/$branch; after=\$(git rev-parse HEAD); \
if ! git diff --quiet \$before \$after -- backend/crates/cognitive-rust; then \
  (cd backend/crates/cognitive-rust && VIRTUAL_ENV=$DIR/backend/.venv ../../.venv/bin/maturin develop --release -q); fi; \
git log --oneline -1"
    ;;
run)
    name="${1:-}"; valid_name "$name"; shift
    [ "${1:-}" = "--" ] && shift
    [ $# -gt 0 ] || { echo "run needs a command after --" >&2; exit 2; }
    # The job travels as a script file over ssh stdin, so its arguments are
    # quoted once (printf %q) instead of through three nested shells.
    script="cd $DIR/backend
$(printf '%q ' "$@")
echo \$? > $LOGS/$name.log.exit"
    if [ "${BB_DRY_RUN:-0}" = "1" ]; then
        printf 'script for %s:\n%s\n' "$name" "$script"
    else
        remote "mkdir -p $LOGS; if [ -f $LOGS/$name.pid ] && kill -0 \$(cat $LOGS/$name.pid) 2>/dev/null; \
then echo 'already running: $name' >&2; exit 3; fi; cat > $LOGS/$name.sh" <<<"$script"
    fi
    remote "rm -f $LOGS/$name.log.exit; nohup bash $LOGS/$name.sh > $LOGS/$name.log 2>&1 < /dev/null & \
echo \$! > $LOGS/$name.pid; disown; echo started $name pid \$(cat $LOGS/$name.pid)"
    ;;
status)
    name="${1:-}"; valid_name "$name"
    remote "if [ -f $LOGS/$name.log.exit ]; then echo \"finished exit=\$(cat $LOGS/$name.log.exit)\"; \
elif [ -f $LOGS/$name.pid ] && kill -0 \$(cat $LOGS/$name.pid) 2>/dev/null; then echo running; \
else echo 'not running, no exit code (killed?)'; fi; tail -n 15 $LOGS/$name.log 2>/dev/null || true"
    ;;
wait)
    name="${1:-}"; valid_name "$name"; poll="${2:-60}"
    remote "while [ ! -f $LOGS/$name.log.exit ]; do \
kill -0 \$(cat $LOGS/$name.pid 2>/dev/null || echo 0) 2>/dev/null || { [ -f $LOGS/$name.log.exit ] && break; \
echo 'died without an exit code' >&2; exit 4; }; \
sleep $poll; done; tail -n 15 $LOGS/$name.log; exit \$(cat $LOGS/$name.log.exit)"
    ;;
fetch)
    name="${1:-}"; valid_name "$name"; src="${2:?remote path}"; dst="${3:?local dir}"
    if [ "${BB_DRY_RUN:-0}" = "1" ]; then
        echo "scp -r $HOST:$DIR/backend/$src $dst"
    else
        mkdir -p "$dst"
        scp -q -r "$HOST:$DIR/backend/$src" "$dst"
    fi
    ;;
*)
    sed -n '2,19p' "$0"
    exit 2
    ;;
esac
