from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.conversations.service import ConversationService
from app.conversations.serializer import (
    serialize_agent_run,
    serialize_conversation,
    serialize_message,
    serialize_team_run,
)

router = APIRouter()


@router.get("/conversations/{conversation_id}")
def get_conversation(
    conversation_id: UUID,
    db: Session = Depends(get_db),
):
    svc = ConversationService(db)
    conv = svc.get_conversation(conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return serialize_conversation(conv)


@router.get("/conversations/{conversation_id}/messages")
def list_conversation_messages(
    conversation_id: UUID,
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    svc = ConversationService(db)
    conv = svc.get_conversation(conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    messages = svc.list_messages(conversation_id, limit=limit)
    return {"items": [serialize_message(m) for m in messages]}


@router.get("/conversations/{conversation_id}/runs")
def list_conversation_runs(
    conversation_id: UUID,
    db: Session = Depends(get_db),
):
    svc = ConversationService(db)
    conv = svc.get_conversation(conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    runs = svc.list_agent_runs(conversation_id)
    return {"items": [serialize_agent_run(r) for r in runs]}


@router.post("/conversations/{conversation_id}/stop", status_code=200)
def stop_conversation(
    conversation_id: UUID,
    db: Session = Depends(get_db),
):
    svc = ConversationService(db)
    conv = svc.get_conversation(conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    svc.update_conversation_status(conversation_id, "idle")
    db.commit()
    return {"ok": True, "status": "idle"}
