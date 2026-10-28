#!/bin/bash
# Launch the cold Codex critic on the brain-v4 folder, detached, read-only.
# Usage: bash launch.sh <round-number>
N=${1:?round number}
D=/Users/aniketsaha/Projects/PALabs/brain-v4
C=$D/research/critic
nohup codex --search exec --skip-git-repo-check -s read-only \
  -C "$D" -o "$C/round-$N.md" - \
  < "$C/round-$N.prompt.md" > "$C/round-$N.log" 2>&1 &
disown
sleep 5
pgrep -fl "codex --search exec" | head -3
