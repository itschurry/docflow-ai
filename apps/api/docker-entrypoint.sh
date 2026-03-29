#!/bin/sh
set -eu

mkdir -p /app/storage/db /app/storage/uploads /app/storage/exports

cd /app/apps/api
python run_migration.py

exec uvicorn app.main:app --app-dir /app/apps/api --host 0.0.0.0 --port 8000
