# DocFlow AI

DocFlow AI is an intelligent multi-agent platform designed for automated document analysis and generation. It leverages a specialized "Team Runtime" where multiple LLM-powered agents (Planner, Writer, Critic, Manager, etc.) collaborate to produce high-quality professional documents including Word, Excel, and PowerPoint files.

## 🚀 Key Features

- **Multi-Agent Orchestration**: Autonomous and guided handoffs between specialized agents.
- **Team Runtime**: A persistent collaborative environment with session management, task dependencies, and activity logging.
- **Advanced Document IR**: A unified Intermediate Representation pipeline that normalizes various formats (PDF, DOCX, XLSX, PPTX, HWP) for LLM processing.
- **Rich Artifact Generation**: Native generation of `.docx`, `.xlsx`, and `.pptx` files using specialized executors.
- **Modern Web Workspace**: A React-based SPA for visualizing agent collaboration, monitoring task boards, and managing document runs.
- **Telegram Integration**: Full support for interacting with the orchestration engine via Telegram bots.
- **Enterprise-Ready Ops**: DB-backed API key authentication, dead-letter job recovery, and detailed audit logging.

## 🏗️ Project Structure

- `apps/api`: FastAPI backend providing REST endpoints, orchestration engine, and team runtime.
- `apps/web`: Modern React frontend built with Vite and TypeScript.
- `storage/`: Local persistent storage for database (`db/`), uploads, and dead-letter logs.

## 🛠️ Tech Stack

- **Backend**: Python 3.10+, FastAPI, SQLAlchemy (PostgreSQL/SQLite), Alembic, Celery, Redis.
- **Frontend**: React 18, Vite, TypeScript, Lucide Icons, Axios.
- **AI/LLM**: Support for OpenAI, Anthropic, and Stub (dev) providers.
- **Infrastructure**: Docker Compose for PostgreSQL/Redis, GitHub Actions for CI/CD.

## 🏃 Getting Started

### 1. Backend Setup (apps/api)

```bash
cd apps/api
cp .env.example .env
# Edit .env with your DATABASE_URL and LLM API keys
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Database Migration
cd ../..
mkdir -p storage/db storage/uploads storage/dead_letter
PYTHONPATH=. alembic upgrade head

# Run FastAPI
cd ~/docflow-ai
uvicorn app.main:app --app-dir apps/api --host 0.0.0.0 --port 8000
```

### 2. Frontend Setup (apps/web-react)

```bash
cd apps/web-react
npm install
npm run dev
npm run build
```
The frontend will be available at `http://localhost:5173`.

### 3. Background Workers (Optional)

If `EXECUTION_BACKEND=celery` is configured:
```bash
cd apps/api
celery -A app.workers.tasks worker --loglevel=info
```

## 📋 API Overview

### Core API
- `POST /api/projects`: Manage document projects.
- `POST /api/projects/{id}/files`: Upload source documents for analysis.
- `POST /api/projects/{id}/jobs`: Dispatch traditional generation jobs.
- `GET /api/jobs/{id}/status/stream`: Real-time job tracking via SSE.

### Team Runtime (Web Workspace)
- `POST /web/team-runs`: Initiate a multi-agent collaborative run.
- `GET /web/team-runs/{id}/board`: Fetch the Kanban-style task board.
- `POST /web/tasks/{id}/claim`: Allow agents/users to claim specific tasks.
- `POST /web/team-runs/{id}/plan/approve`: Approve agent-generated execution plans.

### Ops & Maintenance
- `GET /api/ops/dead-letters`: List failed jobs in the dead-letter queue.
- `POST /api/ops/dead-letters/replay`: Retry failed jobs with audit tracking.
- `POST /api/ops/api-keys`: Manage administrative API keys.

## 🧪 Testing & Validation

Run the full test suite (unit + integration):
```bash
cd apps/api
PYTHONPATH=. pytest
```

Execute end-to-end smoke tests (requires Docker):
```bash
cd apps/api
./scripts/postgres_full_check.sh
```

## 🗂️ Backend Structure & Import Convention

API routes live at `apps/api/app/routes/` (previously `app/api/routes/`).
All Python imports use `app.*` absolute paths:

```python
# ✅ correct
from app.routes import router
from app.routes.web_runs import _review_mode_policy
from app.routes._shared import require_auth

# ❌ avoid
from .web_runs import ...         # relative import
from app.api.routes import ...    # old path (deleted)
```

## 🛡️ Security & Quality

- **Reviewer Engine**: Automatic quality scoring based on configurable rules (length, keywords, TODO checks).
- **Ops Auth**: Protected administrative endpoints via `X-Ops-Token` or DB-backed API Keys.
- **Audit Logs**: Detailed tracking of dead-letter replays and critical system actions.

---
© 2026 DocFlow AI Team. Built for autonomous professional productivity.
