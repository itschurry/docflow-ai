# DocFlow AI

DocFlow AI is now a web-first document workspace built around `FastAPI + React + SQLite`. The runtime is intentionally single-host and simple: `api` and `web` run in Docker, jobs execute inline, and `Redis/Celery/Postgres` are no longer part of the default stack.

## Runtime

- `apps/api`: FastAPI backend, Alembic migrations, team-run orchestration, document export
- `apps/web`: React/Vite frontend built into an Nginx container
- `storage/`: persisted SQLite DB, uploads, generated exports

## LLM Providers

- `stub`: safe local fallback for development and tests
- `openai`: requires `OPENAI_API_KEY`
- `anthropic`: requires `ANTHROPIC_API_KEY`
- `ollama`: requires a reachable Ollama daemon and `OLLAMA_HOST` / `OLLAMA_MODEL`

For Docker, the default Ollama host is `http://host.docker.internal:11434`.

Per-agent overrides are also supported through `apps/api/.env`:

- `AGENT_PLANNER_PROVIDER`, `AGENT_PLANNER_MODEL`
- `AGENT_WRITER_PROVIDER`, `AGENT_WRITER_MODEL`
- `AGENT_CRITIC_PROVIDER`, `AGENT_CRITIC_MODEL`
- `AGENT_QA_PROVIDER`, `AGENT_QA_MODEL`
- `AGENT_MANAGER_PROVIDER`, `AGENT_MANAGER_MODEL`

If only `AGENT_<HANDLE>_PROVIDER` is set, the runtime will automatically use that provider's default model from `OPENAI_MODEL`, `ANTHROPIC_MODEL`, or `OLLAMA_MODEL`.
The override values can also reference other env vars, for example `AGENT_PLANNER_MODEL=${OPENAI_MODEL}`.

## Docker Start

```bash
cp apps/api/.env.example apps/api/.env
docker compose up --build
```

- Web UI: `http://localhost:8082`
- API health: `http://localhost:8002/health`

The API container runs Alembic migrations on startup and stores the SQLite DB at `storage/db/docflow.db`.

## Local Development

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cd apps/api
python run_migration.py
uvicorn app.main:app --app-dir . --host 0.0.0.0 --port 8000
```

```bash
cd apps/web
npm ci
npm run dev
```

## API Surface

Primary routes kept for the current web app:

- `GET /health`
- `POST /web/files`
- `GET /web/knowledge`
- `POST /web/team-runs`
- `GET /web/team-runs`
- `GET /web/team-runs/{id}/board`
- `POST /web/team-runs/{id}/requests`
- `POST /web/team-runs/{id}/exports`
- `GET /web/agents`
- `GET /api/jobs/{id}`
- `GET /api/jobs/{id}/steps`
- `GET /api/files/{id}/download`

Removed from the default product surface:

- `/api/projects/*`
- `/api/ops/*`
- `/telegram/*`
- Celery/Redis worker flows

## Testing

```bash
PYTHONPATH=apps/api python3 -m pytest apps/api/tests
cd apps/web && npm run build
```
