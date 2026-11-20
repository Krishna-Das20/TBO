#!/usr/bin/env bash
# Deploy HAWK to Cloud Run: hawk-core (Voyage + gateway + HAWK API) and hawk-console (dashboard).
# Cheapest MVP settings: scale to zero, at most one instance each, 1 vCPU.
#   usage: deploy/deploy-cloudrun.sh <project> <secrets-file: 6 space-separated random tokens> core|console
# Environment variables go through --env-vars-file (YAML): delimiter tricks in --set-env-vars are
# mangled by gcloud.cmd on Windows.
set -euo pipefail
PROJECT=$1; SECRETS=$2; WHAT=$3
REGION=us-central1
G=${GCLOUD:-gcloud}
read -r API_TOKEN ADMIN_TOKEN AGENT_KEY OPS_TOKEN WEBHOOK_SECRET JWT_SECRET < "$SECRETS"
NUM=$("$G" projects describe "$PROJECT" --format="value(projectNumber)")
CORE_URL="https://hawk-core-$NUM.$REGION.run.app"
CONSOLE_URL="https://hawk-console-$NUM.$REGION.run.app"
cd "$(dirname "$0")/.."
ENVFILE=$(mktemp "${TMPDIR:-/tmp}/hawk-env.XXXXXX.yaml")
trap 'rm -f "$ENVFILE"' EXIT
winpath() { command -v cygpath >/dev/null && cygpath -w "$1" || echo "$1"; }

if [ "$WHAT" = core ]; then
  cat > "$ENVFILE" <<EOF
HAWK_API_TOKEN: "$API_TOKEN"
GATEWAY_ADMIN_TOKEN: "$ADMIN_TOKEN"
GATEWAY_AGENT_KEY: "$AGENT_KEY"
VOYAGE_OPS_TOKEN: "$OPS_TOKEN"
HAWK_WEBHOOK_SECRET: "$WEBHOOK_SECRET"
VOYAGE_JWT_SECRET: "$JWT_SECRET"
HAWK_AUTOSCAN_ON_START: "1"
HAWK_APPROVAL_TIMEOUT_S: "600"
HAWK_CORS: "$CONSOLE_URL"
EOF
  # CPU stays allocated while the instance lives: scans run in the background, not inside a request.
  "$G" run deploy hawk-core --project="$PROJECT" --region=$REGION --source=. --quiet \
    --allow-unauthenticated --min-instances=0 --max-instances=1 --cpu=1 --memory=1Gi \
    --no-cpu-throttling --concurrency=80 --timeout=3600 --env-vars-file="$(winpath "$ENVFILE")"
else
  cat > "$ENVFILE" <<EOF
HAWK_API_URL: "$CORE_URL"
HAWK_API_TOKEN: "$API_TOKEN"
EOF
  "$G" run deploy hawk-console --project="$PROJECT" --region=$REGION --source=apps/dashboard --quiet \
    --allow-unauthenticated --min-instances=0 --max-instances=1 --cpu=1 --memory=512Mi \
    --concurrency=80 --timeout=3600 --env-vars-file="$(winpath "$ENVFILE")"
fi
