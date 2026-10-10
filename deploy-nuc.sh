#!/usr/bin/env bash
# Deploy the workbench to the NUC as a Docker container (host networking, so Broadlink UDP works).
#   ./deploy-nuc.sh          sync code, rebuild, restart. Never touches the remote store.
#   ./deploy-nuc.sh --data   additionally copy the local data/store.json over the remote one.
# The store holds the rolling-code counters: once the NUC runs the remotes, only it should press them.
set -euo pipefail
cd "$(dirname "$0")"
HOST="${NUC_HOST:-nuc.local}"
DIR="${NUC_DIR:-rf-workbench}"

rsync -az --delete \
  --exclude .venv --exclude data --exclude .claude --exclude '__pycache__' --exclude .pytest_cache \
  ./ "$HOST:$DIR/"

if [ "${1:-}" = "--data" ] || ! ssh "$HOST" "test -s $DIR/data/store.json"; then
  echo "copying data/store.json to $HOST"
  ssh "$HOST" "mkdir -p $DIR/data"
  rsync -az data/store.json "$HOST:$DIR/data/store.json"
fi

ssh "$HOST" "cd $DIR && docker compose up -d --build 2>&1 | tail -5"
echo "→ http://$HOST:8333"
