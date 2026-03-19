#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:8003}"

health=$(curl -sS "$BASE_URL/health")
echo "HEALTH=$health"

project=$(curl -sS -X POST "$BASE_URL/api/projects" \
  -H 'Content-Type: application/json' \
  -d '{"name":"Postgres Smoke","description":"e2e"}')
echo "PROJECT=$project"
project_id=$(printf '%s' "$project" | sed -n 's/.*"id":"\([^"]*\)".*/\1/p')

printf 'postgres smoke sample\n' > /tmp/docflow_pg_sample.md
upload=$(curl -sS -X POST "$BASE_URL/api/projects/$project_id/files" \
  -F "uploaded_file=@/tmp/docflow_pg_sample.md;type=text/markdown")
echo "UPLOAD=$upload"

job=$(curl -sS -X POST "$BASE_URL/api/projects/$project_id/jobs" \
  -H 'Content-Type: application/json' \
  -d '{"request":"generate report excel and ppt","output_types":["report","excel","ppt"]}')
echo "JOB=$job"
if [[ "$job" == *'"status":"FAILED"'* ]]; then
  echo "JOB failed at creation"
  exit 1
fi
job_id=$(printf '%s' "$job" | sed -n 's/.*"job_id":"\([^"]*\)".*/\1/p')

detail=$(curl -sS "$BASE_URL/api/jobs/$job_id")
echo "JOB_DETAIL=$detail"
if [[ "$detail" == *'"status":"FAILED"'* ]]; then
  echo "JOB failed during execution"
  exit 1
fi

artifacts=$(curl -sS "$BASE_URL/api/jobs/$job_id/artifacts")
echo "ARTIFACTS=$artifacts"

first_file_id=$(printf '%s' "$artifacts" | sed -n 's/.*"artifacts":\[{"id":"\([^"]*\)".*/\1/p')
if [[ -n "$first_file_id" ]]; then
  code=$(curl -sS -o /tmp/docflow_download.bin -w '%{http_code}' "$BASE_URL/api/files/$first_file_id/download")
  echo "DOWNLOAD_CODE=$code"
  if [[ "$code" != "200" ]]; then
    echo "download failed"
    exit 1
  fi
fi

logs=$(curl -sS "$BASE_URL/api/jobs/$job_id/prompt-logs")
echo "PROMPT_LOGS=$logs"
