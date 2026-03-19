#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

export DATABASE_URL="${DATABASE_URL:-postgresql+psycopg://docflow:docflow@localhost:5432/docflow}"
export AUTO_CREATE_TABLES=false
export PYTHONPATH=.
PYTHON_BIN="${PYTHON_BIN:-python3}"

cleanup() {
  if [[ -n "${API_PID:-}" ]] && kill -0 "$API_PID" 2>/dev/null; then
    kill "$API_PID" || true
  fi
  docker compose -f docker-compose.postgres.yml down >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker compose -f docker-compose.postgres.yml up -d

for _ in $(seq 1 40); do
  status="$(docker inspect -f '{{.State.Health.Status}}' docflow-postgres 2>/dev/null || true)"
  if [[ "$status" == "healthy" ]]; then
    break
  fi
  sleep 1
done

"$PYTHON_BIN" -m alembic upgrade head
"$PYTHON_BIN" -m uvicorn app.main:app --port 8003 >/tmp/docflow_api_8003.log 2>&1 &
API_PID=$!

for _ in $(seq 1 30); do
  if curl -sS http://127.0.0.1:8003/health >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

BASE_URL=http://127.0.0.1:8003 ./scripts/postgres_e2e_smoke.sh

echo "postgres-full-check: success"
