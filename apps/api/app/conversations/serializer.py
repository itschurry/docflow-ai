import uuid

from app.conversation_models import AgentRunModel, ConversationModel, MessageModel


def serialize_conversation(conv: ConversationModel) -> dict:
    return {
        "id": str(conv.id),
        "platform": conv.platform,
        "chat_id": conv.chat_id,
        "topic_id": conv.topic_id,
        "title": conv.title,
        "mode": conv.mode,
        "status": conv.status,
        "created_at": conv.created_at.isoformat(),
        "updated_at": conv.updated_at.isoformat(),
    }


def serialize_message(msg: MessageModel) -> dict:
    return {
        "id": str(msg.id),
        "conversation_id": str(msg.conversation_id),
        "participant_id": str(msg.participant_id) if msg.participant_id else None,
        "telegram_message_id": msg.telegram_message_id,
        "raw_text": msg.raw_text,
        "rendered_text": msg.rendered_text,
        "message_type": msg.message_type,
        "created_at": msg.created_at.isoformat(),
    }


def serialize_agent_run(run: AgentRunModel) -> dict:
    return {
        "id": str(run.id),
        "conversation_id": str(run.conversation_id),
        "agent_handle": run.agent_handle,
        "provider": run.provider,
        "model": run.model,
        "status": run.status,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "error": run.error,
        "created_at": run.created_at.isoformat(),
    }
