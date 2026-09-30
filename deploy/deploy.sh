#!/usr/bin/env bash
# Build and (re)start the HSA-app stack. Idempotent; touches only the "hsaapp" compose project.
#   deploy/deploy.sh            build + up + health check
#   OFFLINE_WHEELS=1 deploy/deploy.sh   fill backend/wheels from the host pip cache first (slow networks)
set -euo pipefail
cd "$(dirname "$0")/.."

[ -f .env ] || { echo "missing .env (cp .env.example .env and fill in secrets)"; exit 1; }
set -a; . ./.env; set +a
for v in POSTGRES_PASSWORD HSA_SECRET_KEY; do
  val="${!v:-}"
  if [ -z "$val" ] || [[ "$val" == change-me* ]]; then echo "set $v in .env"; exit 1; fi
done
mkdir -p "${BACKUP_PATH:-./backups}"

if [ "${OFFLINE_WHEELS:-0}" = "1" ]; then
  echo "== filling backend/wheels from the pip cache"
  python3 -m pip download ./backend "setuptools>=68" wheel -d backend/wheels --prefer-binary -q
  rm -f backend/wheels/hsa_app-*.whl
fi

if [ "${WEB_DOCKERFILE:-Dockerfile}" = "Dockerfile.prebuilt" ]; then
  echo "== building the SPA on the host"
  (cd frontend && { [ -d node_modules ] || npm ci --no-audit --no-fund; } && npm run build)
fi

echo "== building images"
nice -n 10 docker compose build
echo "== starting"
docker compose up -d --remove-orphans
echo "== waiting for health"
for i in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:${WEB_PORT:-8620}/api/health" >/dev/null 2>&1; then
    echo "healthy: http://127.0.0.1:${WEB_PORT:-8620}"; docker compose ps; exit 0
  fi
  sleep 3
done
echo "stack did not become healthy"; docker compose ps; docker compose logs --tail 50 api; exit 1
