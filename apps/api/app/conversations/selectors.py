import uuid

from sqlalchemy.orm import Session

from app.conversation_models import ConversationModel, MessageModel, AgentRunModel


def get_conversation_with_history(
    db: Session,
    conversation_id: uuid.UUID,
    message_limit: int = 30,
) -> dict:
    conv = db.get(ConversationModel, conversation_id)
    if not conv:
        return {}
    messages = (
        db.query(MessageModel)
        .filter(MessageModel.conversation_id == conversation_id)
        .order_by(MessageModel.created_at)
        .limit(message_limit)
        .all()
    )
    runs = (
        db.query(AgentRunModel)
        .filter(AgentRunModel.conversation_id == conversation_id)
        .order_by(AgentRunModel.created_at)
        .all()
    )
    return {"conversation": conv, "messages": messages, "runs": runs}


def get_recent_agent_output(
    db: Session,
    conversation_id: uuid.UUID,
    agent_handle: str,
) -> str | None:
    run = (
        db.query(AgentRunModel)
        .filter(
            AgentRunModel.conversation_id == conversation_id,
            AgentRunModel.agent_handle == agent_handle,
            AgentRunModel.status == "done",
        )
        .order_by(AgentRunModel.created_at.desc())
        .first()
    )
    return run.output_snapshot if run else None


def build_context_prompt(
    db: Session,
    conversation_id: uuid.UUID,
    message_limit: int = 20,
) -> str:
    data = get_conversation_with_history(db, conversation_id, message_limit)
    if not data:
        return ""
    lines = []
    for msg in data["messages"]:
        prefix = f"[{msg.message_type.upper()}]"
        lines.append(f"{prefix} {msg.raw_text}")
    return "\n".join(lines)
