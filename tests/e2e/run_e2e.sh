#!/usr/bin/env bash
# End-to-end test of the three n8n workflows – no Docker, no API key, no real e-mails.
#
#   bash tests/e2e/run_e2e.sh
#
# Needs: n8n 2.42.4 on Node 24 (npm install -g n8n@2.42.4), the Python packages from requirements-dev.txt,
# a hosts entry "127.0.0.1 mcp" (the workflows call http://mcp:8100/mcp like in docker compose) and a writable
# /home/node/runs (the run log path inside the n8n container). The CI job sets all of this up.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
PY=${PYTHON:-python}
N8N_CMD=${N8N_CMD:-n8n}
export E2E_DIR=${E2E_DIR:-$(mktemp -d)}
export MCP_PID_FILE=$E2E_DIR/mcp.pid REGULATION_API_URL=http://127.0.0.1:8000
export N8N_USER_FOLDER=$E2E_DIR/n8n N8N_ENCRYPTION_KEY=e2e-only-not-a-secret N8N_LISTEN_ADDRESS=0.0.0.0 \
       WEBHOOK_URL=http://localhost:5678/ N8N_RESTRICT_FILE_ACCESS_TO=/home/node/runs \
       GENERIC_TIMEZONE=Europe/Berlin TZ=Europe/Berlin N8N_DIAGNOSTICS_ENABLED=false N8N_PERSONALIZATION_ENABLED=false

getent hosts mcp > /dev/null || { echo "Add '127.0.0.1 mcp' to /etc/hosts"; exit 1; }
[ -w /home/node/runs ] || { echo "/home/node/runs must exist and be writable"; exit 1; }

pids=()
start() { local name=$1; shift; "$@" > "$E2E_DIR/$name.log" 2>&1 & pids+=($!); }
cleanup() { kill "${pids[@]}" "$(cat "$MCP_PID_FILE" 2>/dev/null)" 2>/dev/null || true; }
show_logs() { for f in setup n8n mcp llm; do echo "--- $f.log"; tail -n 25 "$E2E_DIR/$f.log" 2>/dev/null; done; }
trap cleanup EXIT

cd "$ROOT"
start llm  "$PY" tests/e2e/fakes.py llm 9999
start smtp "$PY" tests/e2e/fakes.py smtp 1025
start p3   "$PY" tests/e2e/fakes.py p3 8000
"$PY" -m mcp_server.server > "$E2E_DIR/mcp.log" 2>&1 & echo $! > "$MCP_PID_FILE"

echo "Importing credentials and workflows ..."
WAIT_FOR_N8N=0 OPENAI_API_KEY=sk-e2e-not-real OPENAI_BASE_URL=http://127.0.0.1:9999/v1 SMTP_HOST=127.0.0.1 N8N_CMD="$N8N_CMD" \
  sh n8n/setup.sh > "$E2E_DIR/setup.log" 2>&1 || { show_logs; exit 1; }

echo "Starting n8n ..."
# shellcheck disable=SC2086  # N8N_CMD may contain arguments
start n8n $N8N_CMD start
for _ in $(seq 1 90); do
  curl -sf -o /dev/null http://localhost:5678/form/5f0c7a52-8a64-4d8e-9a43-1b9d6bcd4e01 && break
  sleep 2
done
curl -sf -o /dev/null http://localhost:5678/form/5f0c7a52-8a64-4d8e-9a43-1b9d6bcd4e01 || { show_logs; exit 1; }

"$PY" tests/e2e/scenarios.py || { show_logs; exit 1; }
