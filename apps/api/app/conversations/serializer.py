import uuid

from app.conversation_models import (
    AgentRunModel,
    ConversationArtifactModel,
    ConversationModel,
    MessageModel,
    TeamActivityEventModel,
    TeamRunModel,
    TeamTaskDependencyModel,
    TeamTaskModel,
)


def serialize_conversation(conv: ConversationModel) -> dict:
    return {
        "id": str(conv.id),
        "platform": conv.platform,
        "chat_id": conv.chat_id,
        "topic_id": conv.topic_id,
        "title": conv.title,
        "mode": conv.mode,
        "turn_limit": conv.turn_limit,
        "is_waiting_user": conv.is_waiting_user,
        "autonomy_level": conv.autonomy_level,
        "current_agent": conv.current_agent,
        "current_identity": conv.current_identity,
        "suggested_next_agent": conv.suggested_next_agent,
        "approved_next_agent": conv.approved_next_agent,
        "expected_next_handle": conv.approved_next_agent,
        "task_status": conv.task_status,
        "done": conv.done,
        "needs_user_input": conv.needs_user_input,
        "selected_agents": list(conv.selected_agents or []),
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
        "visible_message": msg.visible_message,
        "speaker_role": msg.speaker_role,
        "speaker_identity": msg.speaker_identity,
        "speaker_bot_username": msg.speaker_bot_username,
        "suggested_next_agent": msg.suggested_next_agent,
        "approved_next_agent": msg.approved_next_agent,
        "handoff_reason": msg.handoff_reason,
        "task_status": msg.task_status,
        "done": msg.done,
        "needs_user_input": msg.needs_user_input,
        "is_progress_turn": msg.is_progress_turn,
        "message_type": msg.message_type,
        "created_at": msg.created_at.isoformat(),
    }


def serialize_agent_run(run: AgentRunModel) -> dict:
    return {
        "id": str(run.id),
        "conversation_id": str(run.conversation_id),
        "agent_handle": run.agent_handle,
        "suggested_next_agent": run.suggested_next_agent,
        "approved_next_agent": run.approved_next_agent,
        "handoff_reason": run.handoff_reason,
        "validation_result": run.validation_result,
        "fallback_applied": run.fallback_applied,
        "progress_detected": run.progress_detected,
        "termination_reason": run.termination_reason,
        "provider": run.provider,
        "model": run.model,
        "status": run.status,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "error": run.error,
        "created_at": run.created_at.isoformat(),
    }


def serialize_artifact(artifact: ConversationArtifactModel) -> dict:
    return {
        "id": str(artifact.id),
        "conversation_id": str(artifact.conversation_id),
        "task_id": str(artifact.task_id) if artifact.task_id else None,
        "source_run_id": str(artifact.source_run_id) if artifact.source_run_id else None,
        "artifact_type": artifact.artifact_type,
        "version": artifact.version,
        "content": artifact.content,
        "status": artifact.status,
        "created_by_handle": artifact.created_by_handle,
        "created_at": artifact.created_at.isoformat(),
    }


def serialize_team_run(run: TeamRunModel) -> dict:
    return {
        "id": str(run.id),
        "conversation_id": str(run.conversation_id) if run.conversation_id else None,
        "title": run.title,
        "mode": run.mode,
        "status": run.status,
        "requested_by": run.requested_by,
        "request_text": run.request_text,
        "selected_agents": list(run.selected_agents or []),
        "final_artifact_id": str(run.final_artifact_id) if run.final_artifact_id else None,
        "created_at": run.created_at.isoformat(),
        "updated_at": run.updated_at.isoformat(),
    }


def serialize_team_task(task: TeamTaskModel) -> dict:
    return {
        "id": str(task.id),
        "team_run_id": str(task.team_run_id),
        "title": task.title,
        "description": task.description,
        "owner_handle": task.owner_handle,
        "status": task.status,
        "priority": task.priority,
        "artifact_goal": task.artifact_goal,
        "parent_task_id": str(task.parent_task_id) if task.parent_task_id else None,
        "created_by_handle": task.created_by_handle,
        "review_required": task.review_required,
        "created_at": task.created_at.isoformat(),
        "updated_at": task.updated_at.isoformat(),
    }


def serialize_team_dependency(dep: TeamTaskDependencyModel) -> dict:
    return {
        "id": str(dep.id),
        "team_task_id": str(dep.team_task_id),
        "depends_on_task_id": str(dep.depends_on_task_id),
        "created_at": dep.created_at.isoformat(),
    }


def serialize_team_activity(event: TeamActivityEventModel) -> dict:
    return {
        "id": str(event.id),
        "team_run_id": str(event.team_run_id),
        "task_id": str(event.task_id) if event.task_id else None,
        "event_type": event.event_type,
        "actor_handle": event.actor_handle,
        "target_handle": event.target_handle,
        "summary": event.summary,
        "payload": event.payload,
        "created_at": event.created_at.isoformat(),
    }
