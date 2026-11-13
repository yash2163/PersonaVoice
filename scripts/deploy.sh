#!/usr/bin/env bash
# Build, push and deploy to an always-on AWS Lightsail container service.
# Requires: aws CLI v2 + lightsailctl plugin, docker, and a populated .env (secrets are read from it).
#   scripts/deploy.sh            # build + push + deploy
#   scripts/deploy.sh --create   # first time: also create the service (Micro, scale 1)
set -euo pipefail
cd "$(dirname "$0")/.."

SERVICE="${LIGHTSAIL_SERVICE:-yash-voicebot}"
REGION="${AWS_REGION:-ap-south-1}"
[ -f .env ] || { echo ".env not found"; exit 1; }
set -a; . ./.env; set +a

if [ "${1:-}" = "--create" ]; then
  aws lightsail create-container-service --service-name "$SERVICE" --power micro --scale 1 --region "$REGION"
  echo "Waiting for the service to be READY..."
  until [ "$(aws lightsail get-container-services --service-name "$SERVICE" --region "$REGION" --query 'containerServices[0].state' --output text)" = "READY" ]; do sleep 10; done
fi

docker build --platform linux/amd64 -t "$SERVICE:latest" .
aws lightsail push-container-image --service-name "$SERVICE" --label app --image "$SERVICE:latest" --region "$REGION" >/dev/null
# The newest registered image (":<service>.app.<n>") is the one we just pushed.
IMAGE=$(aws lightsail get-container-images --service-name "$SERVICE" --region "$REGION" --query 'containerImages[0].image' --output text)

# Env vars for the container, taken from .env (only the keys the app reads).
KEYS="AWS_REGION AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY BEDROCK_MODEL_ID BEDROCK_FALLBACK_MODEL_ID ENABLE_PROMPT_CACHE LLM_TEMPERATURE LLM_MAX_TOKENS POLLY_VOICE_ID POLLY_ENGINE TTS_CONCURRENCY GROQ_API_KEY STT_MODEL STT_PROMPT FILLER_THRESHOLD_MS RATE_LIMIT_ASK RATE_LIMIT_STT CONTEXTUAL_FILLER LOG_LEVEL"
ENV_JSON=$(python3 - "$KEYS" <<'PY'
import json, os, sys
print(json.dumps({k: os.environ[k] for k in sys.argv[1].split() if os.environ.get(k)}))
PY
)

TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT  # holds secrets: keep it private and temporary
chmod 700 "$TMP"
cat > "$TMP/containers.json" <<JSON
{"app": {"image": "$IMAGE", "ports": {"8000": "HTTP"}, "environment": $ENV_JSON}}
JSON
cat > "$TMP/endpoint.json" <<'JSON'
{"containerName": "app", "containerPort": 8000, "healthCheck": {"path": "/api/health", "intervalSeconds": 10, "timeoutSeconds": 5, "healthyThreshold": 2, "unhealthyThreshold": 3, "successCodes": "200"}}
JSON

aws lightsail create-container-service-deployment --service-name "$SERVICE" --region "$REGION" \
  --containers "file://$TMP/containers.json" --public-endpoint "file://$TMP/endpoint.json"
echo "Deployed. URL:"
aws lightsail get-container-services --service-name "$SERVICE" --region "$REGION" --query 'containerServices[0].url' --output text
