#!/bin/sh
# Start the three processes of hawk-core. If any one exits, the container exits and Cloud Run restarts it.
set -e
cd /app
export GATEWAY_URL=http://127.0.0.1:8088
export VOYAGE_DEPLOY_WEBHOOK="http://127.0.0.1:${PORT}/events/deploy"
export TARGET_VOYAGE_URL=http://127.0.0.1:8081

python -m uvicorn voyage.app:app --host 127.0.0.1 --port 8081 --log-level warning &
PORT_SAVED="$PORT"
GATEWAY_CONFIG=deploy/gateway.targets.json GATEWAY_VAULT=deploy/vault.dev.json \
  GATEWAY_AUDIT=data/gateway-audit.jsonl PORT=8088 gateway &
export PORT="$PORT_SAVED"
python -m uvicorn hawk.api:app --host 0.0.0.0 --port "$PORT" --log-level warning --timeout-keep-alive 75 &

wait -n 2>/dev/null || wait
exit 1
