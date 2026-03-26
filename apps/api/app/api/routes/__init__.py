from fastapi import APIRouter

from app.api.routes.health import router as health_router
from app.api.routes.projects import router as projects_router
from app.api.routes.files import router as files_router
from app.api.routes.jobs import router as jobs_router
from app.api.routes.web_knowledge import router as web_knowledge_router
from app.api.routes.ops import router as ops_router
from app.api.routes.telegram import router as telegram_router
from app.api.routes.conversations import router as conversations_router
from app.api.routes.web_runs import router as web_runs_router
from app.api.routes.web_chats import router as web_chats_router

# Re-export internal helpers used by tests
from app.api.routes.web_runs import (
    _build_structured_deliverable,
    _build_done_with_risks_content,
    _normalize_presentation_final_content,
    _presentation_user_visible_markdown,
    _task_execution_contract,
    _coerce_team_tasks,
)

# Re-export patching targets so monkeypatch on `routes.<name>` works
from app.api.routes.web_runs import (
    anthropic_skills_available,
    openai_document_generation_available,
    AnthropicSkillsDocumentGenerator,
    OpenAIDocumentIRGenerator,
)
from app.core.config import settings

router = APIRouter()
router.include_router(health_router)
router.include_router(projects_router)
router.include_router(files_router)
router.include_router(jobs_router)
router.include_router(web_knowledge_router)
router.include_router(ops_router)
router.include_router(telegram_router)
router.include_router(conversations_router)
router.include_router(web_runs_router)
router.include_router(web_chats_router)

__all__ = [
    "router",
    "_build_structured_deliverable",
    "_build_done_with_risks_content",
    "_normalize_presentation_final_content",
    "_presentation_user_visible_markdown",
    "_task_execution_contract",
    "_coerce_team_tasks",
    "anthropic_skills_available",
    "openai_document_generation_available",
    "AnthropicSkillsDocumentGenerator",
    "OpenAIDocumentIRGenerator",
]
