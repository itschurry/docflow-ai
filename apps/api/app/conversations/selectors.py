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
        .order_by(MessageModel.created_at.desc())
        .limit(message_limit)
        .all()
    )
    runs = (
        db.query(AgentRunModel)
        .filter(AgentRunModel.conversation_id == conversation_id)
        .order_by(AgentRunModel.created_at)
        .all()
    )
    return {"conversation": conv, "messages": list(reversed(messages)), "runs": runs}


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


def get_recent_agent_output_since_last_user(
    db: Session,
    conversation_id: uuid.UUID,
    agent_handle: str,
) -> str | None:
    """Return agent output only if it was produced after the latest user message."""
    latest_user = (
        db.query(MessageModel)
        .filter(
            MessageModel.conversation_id == conversation_id,
            MessageModel.message_type == "user",
        )
        .order_by(MessageModel.created_at.desc())
        .first()
    )
    if not latest_user:
        return get_recent_agent_output(db, conversation_id, agent_handle)

    run = (
        db.query(AgentRunModel)
        .filter(
            AgentRunModel.conversation_id == conversation_id,
            AgentRunModel.agent_handle == agent_handle,
            AgentRunModel.status == "done",
            AgentRunModel.created_at >= latest_user.created_at,
        )
        .order_by(AgentRunModel.created_at.desc())
        .first()
    )
    return run.output_snapshot if run else None


def build_context_prompt(
    db: Session,
    conversation_id: uuid.UUID,
    message_limit: int = 20,
    max_chars: int = 3000,
) -> str:
    data = get_conversation_with_history(db, conversation_id, message_limit)
    if not data:
        return ""
    lines = []
    total = 0
    for msg in reversed(data["messages"]):  # 최신 메시지 우선
        prefix = f"[{msg.message_type.upper()}]"
        source = msg.visible_message or msg.raw_text
        body = source[:600] + "…" if len(source) > 600 else source
        line = f"{prefix} {body}"
        if total + len(line) > max_chars:
            break
        lines.append(line)
        total += len(line)
    return "\n".join(reversed(lines))
