# DocFlow AI

DocFlow AI Phase 1 kickoff workspace.

## Implemented now

- Monorepo directory scaffold from the design doc
- PostgreSQL + SQLAlchemy integration with DB table models
- Alembic migration infrastructure and initial schema revision
- File upload endpoint and text extraction pipeline (TXT/MD/JSON/PDF/DOCX)
- Planner + LLM provider routing (Stub/OpenAI/Anthropic)
- Job dispatch and execution pipeline (inline default, Celery optional)
- PromptLog persistence and retrieval endpoint
- Real draft artifact generation (`md`, `docx`, `xlsx`, `pptx`)
- File download endpoint for uploaded/generated assets
- Role-based executor modules (parser/writer/spreadsheet/slide)
- Project-level job history endpoint
- Reviewer executor with deterministic quality scoring
- Celery retry policy and dead-letter fallback logging
- Dead-letter operational listing endpoint
- Dead-letter replay endpoint with preview/requeue/delete controls
- DB-backed Ops API key auth and replay audit retrieval endpoint
- Configurable reviewer quality rules and threshold-based guidance
- Dependency graph-based task orchestration for job execution

## Structure

- `apps/api`: FastAPI backend skeleton
- `apps/web`: frontend placeholder
- `services`: orchestration and processing services placeholders
- `workers`: async worker placeholders
- `packages`: shared schemas/utils placeholders
- `templates`: report/excel/ppt template placeholders
- `docs`: project docs

## Run API (local)

```bash
cd apps/api
cp .env.example .env
/home/user/docflow-ai/.venv/bin/pip install -r requirements.txt
docker compose -f docker-compose.postgres.yml up -d
DATABASE_URL='postgresql+psycopg://docflow:docflow@localhost:5432/docflow' \
PYTHONPATH=. /home/user/docflow-ai/.venv/bin/alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

Before running API, prepare PostgreSQL and a database/user matching `DATABASE_URL`.

Default `DATABASE_URL`:

```text
postgresql+psycopg://docflow:docflow@localhost:5432/docflow
```

Default execution mode:

```text
EXECUTION_BACKEND=inline
```

Optional Celery mode:

```text
EXECUTION_BACKEND=celery
REDIS_URL=redis://localhost:6379/0
QUEUE_MAX_RETRIES=3
QUEUE_RETRY_DELAY_SECONDS=5
DEAD_LETTER_DIR=storage/dead_letter
OPS_API_TOKEN=change-this-secret
```

Run Celery worker (optional):

```bash
cd apps/api
PYTHONPATH=. /home/user/docflow-ai/.venv/bin/celery -A app.workers.tasks worker --loglevel=info
```

Health check:

```bash
curl http://127.0.0.1:8000/health
```

## PostgreSQL Full Validation

Run full DB-backed validation (compose up -> migrate -> API run -> E2E smoke -> cleanup):

```bash
cd apps/api
./scripts/postgres_full_check.sh
```

Optional testcontainers-based validation (no compose file dependency):

```bash
cd apps/api
/home/user/docflow-ai/.venv/bin/python ./scripts/testcontainers_full_check.py
```

## Integration Tests

```bash
cd apps/api
PYTHONPATH=. /home/user/docflow-ai/.venv/bin/python -m pytest -q
```

## CI

- GitHub Actions workflow: `.github/workflows/api-ci.yml`
- Jobs:
	- `pytest`: API test suite
	- `postgres-smoke`: `./scripts/postgres_full_check.sh` end-to-end validation
- Optional manual workflow: `.github/workflows/api-testcontainers.yml`
	- `testcontainers-smoke`: `python ./scripts/testcontainers_full_check.py`

## API baseline

- `POST /api/projects`
- `POST /api/projects/{project_id}/files`
- `POST /api/projects/{project_id}/jobs`
- `GET /api/projects/{project_id}/jobs`
- `GET /api/jobs/{job_id}`
- `GET /api/jobs/{job_id}/artifacts`
- `GET /api/jobs/{job_id}/prompt-logs`
- `GET /api/files/{file_id}/download`
- `GET /api/ops/dead-letters`
- `POST /api/ops/dead-letters/replay`
- `POST /api/ops/api-keys`
- `GET /api/ops/replay-audit`
- `POST /api/jobs/{job_id}/retry`

## Ops replay notes

- `POST /api/ops/dead-letters/replay` supports preview by default (`requeue=false`).
- Duplicate replay is blocked after first successful replay.
- To override duplicate protection, set `force_requeue=true`.
- If `OPS_API_TOKEN` is configured, send `X-Ops-Token` header for ops endpoints.
- You can bootstrap DB-backed keys with `POST /api/ops/api-keys` and then use:
	- `X-Ops-Key-Id: <key_id>`
	- `X-Ops-Key-Secret: <key_secret>`
- Replay events are written to `storage/dead_letter/replay_audit.jsonl` and readable via `GET /api/ops/replay-audit`.

## UI and LLM API requirements

- UI is optional. You can use this backend directly via REST API/curl/Postman.
- OpenAI/Anthropic API keys are optional in development because `LLM_PROVIDER=stub` works without external APIs.
- For production-grade AI output quality, configure either OpenAI or Anthropic provider and corresponding API key.

## Reviewer quality rules

- `REVIEW_MIN_LENGTH`, `REVIEW_LENGTH_PENALTY`
- `REVIEW_TODO_PENALTY`
- `REVIEW_REQUIRED_KEYWORDS`, `REVIEW_KEYWORD_PENALTY`
- `REVIEW_REQUIRED_THRESHOLD`

These rules drive `quality_score` and recommendation (`REVIEW_REQUIRED` or `READY_FOR_HUMAN_REVIEW`).

## Next steps

1. Expand Celery-mode integration tests to include live worker runs in CI
2. Add optional real-provider smoke profile for OpenAI/Anthropic connectivity checks
