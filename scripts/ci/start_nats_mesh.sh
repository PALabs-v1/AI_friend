#!/usr/bin/env bash
# Start the NATS server CI runs against from the same nats-accounts.conf
# production uses, with throwaway per-agent passwords. Since W10a, clients
# and the stream provisioner refuse to connect without credentials, so CI
# must run the authenticated mesh rather than an anonymous server.
#
#   scripts/ci/start_nats_mesh.sh docker   # Linux runners (default)
#   scripts/ci/start_nats_mesh.sh native   # macOS runners, local nats-server
#
# Exports NATS_<ROLE>_PASSWORD for every role (and to $GITHUB_ENV on
# Actions). Provision streams afterwards as the provisioner:
#   NATS_USER=nats_provisioner NATS_PASSWORD=$NATS_PROVISIONER_PASSWORD \
#     python backend/scripts/bootstrap/setup_nats_streams.py
set -euo pipefail

MODE=${1:-docker}
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
ROLES="PROVISIONER SIGNALING BRAIN SUBCONSCIOUS SURFACING SYSTEM TRANSPORT STT VISION VOICE"

env_args=()
for role in $ROLES; do
  value="ci_only_$(echo "$role" | tr '[:upper:]' '[:lower:]')"
  export "NATS_${role}_PASSWORD=$value"
  env_args+=(-e "NATS_${role}_PASSWORD=$value")
  if [ -n "${GITHUB_ENV:-}" ]; then
    echo "NATS_${role}_PASSWORD=$value" >> "$GITHUB_ENV"
  fi
done

LOG=${NATS_LOG:-/tmp/nats.log}
case "$MODE" in
  docker)
    docker run -d --name nats -p 4222:4222 -p 8222:8222 "${env_args[@]}" \
      -v "$ROOT/nats-accounts.conf:/etc/nats/accounts.conf:ro" \
      nats:latest -c /etc/nats/accounts.conf -m 8222
    ;;
  native)
    nats-server -c "$ROOT/nats-accounts.conf" -p 4222 -m 8222 > "$LOG" 2>&1 &
    ;;
  *)
    echo "usage: $0 [docker|native]" >&2
    exit 2
    ;;
esac

for _ in $(seq 1 15); do
  if curl -fsS http://localhost:8222/healthz > /dev/null 2>&1; then
    echo "NATS (authenticated, nats-accounts.conf) is ready"
    exit 0
  fi
  sleep 2
done
echo "NATS failed to start" >&2
if [ "$MODE" = docker ]; then docker logs nats >&2 || true; else cat "$LOG" >&2 || true; fi
exit 1
