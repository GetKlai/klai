#!/usr/bin/env bash
# first-deploy.sh
#
# Run this ONCE on core-01 before starting scribe-api for the first time.
# It pre-downloads the whisper model into the named Docker volume and runs
# the Alembic migration.
#
# Usage:
#   cd /opt/klai
#   bash /path/to/first-deploy.sh

set -euo pipefail

COMPOSE="docker compose"
SCRIBE_API_IMAGE="ghcr.io/getklai/scribe-api:latest"
WHISPER_IMAGE="ghcr.io/getklai/whisper-server:latest"

echo "==> Pulling images..."
docker pull "$SCRIBE_API_IMAGE"
docker pull "$WHISPER_IMAGE"

echo ""
echo "==> Pre-downloading whisper model into whisper-models volume..."
echo "    This downloads ~3 GB for large-v3-turbo and may take a while."
$COMPOSE run --rm whisper-server python -c "
from faster_whisper import WhisperModel
import os
model = os.getenv('WHISPER_MODEL', 'large-v3-turbo')
device = os.getenv('WHISPER_DEVICE', 'cpu')
compute = os.getenv('WHISPER_COMPUTE_TYPE', 'int8')
root = os.getenv('WHISPER_DOWNLOAD_ROOT', '/models')
print(f'Downloading {model} ({device}/{compute}) to {root}...')
WhisperModel(model, device=device, compute_type=compute, download_root=root)
print('Download complete.')
"

echo ""
echo "==> Running Alembic migration (CREATE SCHEMA scribe + transcriptions table)..."
$COMPOSE run --rm --no-deps scribe-api \
    sh -c "cd /app && alembic upgrade head"

echo ""
echo "==> Starting services..."
$COMPOSE up -d whisper-server scribe-api

echo ""
echo "==> Waiting for whisper-server to become ready (model warmup takes 30-60s)..."
for i in $(seq 1 30); do
    if $COMPOSE exec whisper-server curl -sf http://localhost:8000/health > /dev/null 2>&1; then
        echo "    whisper-server is ready."
        break
    fi
    echo "    ($i/30) Still warming up..."
    sleep 5
done

echo ""
echo "==> Health check..."
$COMPOSE exec scribe-api curl --connect-timeout 2 --max-time 3 -sf http://localhost:8020/health && echo "scribe-api OK"

echo ""
echo "==> Reloading Caddy to activate the /scribe/* route..."
docker exec klai-core-caddy-1 caddy reload --config /etc/caddy/Caddyfile

echo ""
echo "Done. Scribe is live at https://{your-slug}.getklai.com/scribe/v1/"
