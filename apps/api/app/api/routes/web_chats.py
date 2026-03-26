import uuid
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.time_utils import now_utc
from app import conversation_models
from app.adapters.telegram.dispatcher import DispatchResult
from app.conversations.service import ConversationService
from app.conversations.serializer import (
    serialize_artifact,
    serialize_conversation,
    serialize_message,
)
from app.orchestrator.engine import orchestrator
from app.team_runtime.service import TeamRunService
from ._shared import (
    _normalize_web_selected_agents,
    WEB_UPLOAD_PROJECT_NAME,
)
from .web_runs import _build_progress_steps, _extract_web_deliverable

router = APIRouter()


class _WebDispatcher:
    """Dispatcher adapter that persists rendered agent turns without Telegram send."""

    def __init__(self, base_dispatcher):
        self._base = base_dispatcher
        self._registry = base_dispatcher._registry

    def resolve_identity(self, role: str) -> str:
        return self._base.resolve_identity(role)

    async def dispatch(
        self,
        role: str,
        chat_id: str | int,
        body: str,
        next_role: str | None = None,
        include_handoff_hint: bool = True,
        reply_to_message_id: int | None = None,
        message_thread_id: int | None = None,
    ) -> DispatchResult:
        rendered = self._base.build_message(
            role,
            body,
            next_role,
            include_handoff_hint=include_handoff_hint,
        )
        return DispatchResult(
            identity=self.resolve_identity(role),
            telegram_message_id=None,
            rendered_text=rendered,
        )

    async def dispatch_status(
        self,
        identity: str,
        chat_id: str | int,
        text: str,
        message_thread_id: int | None = None,
    ) -> int | None:
        return None


def _build_workspace_snapshot(db: Session, conv: conversation_models.ConversationModel) -> dict:
    svc = ConversationService(db)
    messages = svc.list_messages(conv.id, limit=200)
    artifacts = list(reversed(svc.list_artifacts(conv.id, limit=30)))
    return {
        "conversation": serialize_conversation(conv),
        "items": [serialize_message(m) for m in messages],
        "progress_steps": _build_progress_steps(conv, messages),
        "artifacts": [serialize_artifact(a) for a in artifacts],
        "deliverable": _extract_web_deliverable(db, conv.id),
    }


@router.post("/web/chats", status_code=201)
def create_web_chat(
    payload: dict,
    db: Session = Depends(get_db),
):
    title = str(payload.get("title") or "Web Team Chat").strip()[:120]
    mode = str(payload.get("mode") or settings.orchestrator_default_mode).strip().lower()
    valid = {a["handle"] for a in orchestrator.list_agents_info()}
    raw_selected = payload.get("selected_agents")
    if not isinstance(raw_selected, list):
        raw_selected = ["planner", "writer", "critic", "manager"]
    selected = _normalize_web_selected_agents(
        selected=raw_selected,
        valid_handles=valid,
        mode=mode,
    )

    conv = conversation_models.ConversationModel(
        platform="web",
        chat_id=f"web:{uuid.uuid4().hex}",
        topic_id=None,
        title=title or "Web Team Chat",
        mode=mode,
        autonomy_level=mode,
        selected_agents=selected,
        status="idle",
    )
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return serialize_conversation(conv)


@router.put("/web/chats/{conversation_id}/agents", status_code=200)
def update_web_chat_agents(
    conversation_id: UUID,
    payload: dict,
    db: Session = Depends(get_db),
):
    conv = db.get(conversation_models.ConversationModel, conversation_id)
    if not conv or conv.platform != "web":
        raise HTTPException(status_code=404, detail="Web conversation not found")

    selected = payload.get("selected_agents")
    if not isinstance(selected, list):
        raise HTTPException(status_code=400, detail="selected_agents must be a list")
    valid = {a["handle"] for a in orchestrator.list_agents_info()}
    selected = _normalize_web_selected_agents(
        selected=selected,
        valid_handles=valid,
        mode=conv.mode,
    )

    conv.selected_agents = selected
    conv.updated_at = now_utc()
    db.commit()
    db.refresh(conv)
    return {"ok": True, "conversation": serialize_conversation(conv)}


@router.get("/web/chats/{conversation_id}/deliverable", status_code=200)
def get_web_chat_deliverable(
    conversation_id: UUID,
    db: Session = Depends(get_db),
):
    conv = db.get(conversation_models.ConversationModel, conversation_id)
    if not conv or conv.platform != "web":
        raise HTTPException(status_code=404, detail="Web conversation not found")
    return {"item": _extract_web_deliverable(db, conversation_id)}


@router.get("/web/chats/{conversation_id}/workspace", status_code=200)
def get_web_chat_workspace(
    conversation_id: UUID,
    db: Session = Depends(get_db),
):
    conv = db.get(conversation_models.ConversationModel, conversation_id)
    if not conv or conv.platform != "web":
        raise HTTPException(status_code=404, detail="Web conversation not found")
    return _build_workspace_snapshot(db, conv)


@router.post("/web/chats/{conversation_id}/messages", status_code=202)
async def send_web_chat_message(
    conversation_id: UUID,
    payload: dict,
    db: Session = Depends(get_db),
):
    text = str(payload.get("text") or "").strip()
    sender_name = str(payload.get("sender_name") or "web_user").strip() or "web_user"
    if not text:
        raise HTTPException(status_code=400, detail="text is required")

    conv = db.get(conversation_models.ConversationModel, conversation_id)
    if not conv or conv.platform != "web":
        raise HTTPException(status_code=404, detail="Web conversation not found")

    valid = {a["handle"] for a in orchestrator.list_agents_info()}
    normalized_selected = _normalize_web_selected_agents(
        selected=list(conv.selected_agents or []),
        valid_handles=valid,
        mode=conv.mode,
    )
    if normalized_selected != list(conv.selected_agents or []):
        conv.selected_agents = normalized_selected

    conv.title = conv.title or text[:100]
    db.commit()

    base_dispatcher = orchestrator._get_dispatcher()
    web_dispatcher = _WebDispatcher(base_dispatcher)
    await orchestrator.process_message(
        db=db,
        chat_id=conv.chat_id,
        text=text,
        sender_name=sender_name,
        telegram_message_id=None,
        send_fn=None,
        topic_id=conv.topic_id,
        inbound_identity="pm",
        chat_type="web",
        dispatcher_override=web_dispatcher,
        available_handles=list(conv.selected_agents or []),
    )

    refreshed = (
        db.query(conversation_models.ConversationModel)
        .filter(
            conversation_models.ConversationModel.chat_id == conv.chat_id,
            conversation_models.ConversationModel.platform == "web",
        )
        .order_by(conversation_models.ConversationModel.updated_at.desc())
        .first()
    )
    if not refreshed:
        raise HTTPException(status_code=500, detail="Failed to refresh conversation")
    return _build_workspace_snapshot(db, refreshed)


@router.get("/agents")
def list_agents():
    """List all configured agents."""
    return {"agents": orchestrator.list_agents_info()}


@router.post("/agents/reload-config", status_code=200)
def reload_agent_config():
    """Hot-reload agents.yaml without restart."""
    orchestrator.reload_agents()
    return {"ok": True, "agents": orchestrator.list_agents_info()}
