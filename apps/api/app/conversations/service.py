import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.conversation_models import (
    AgentRunModel,
    ConversationArtifactModel,
    ConversationModel,
    MessageModel,
    MentionModel,
    ParticipantModel,
)
from app.core.time_utils import now_utc
from app.core.config import settings


class ConversationService:
    def __init__(self, db: Session):
        self.db = db

    # ── Conversation ──────────────────────────────────────────────────────

    def get_or_create_conversation(
        self,
        chat_id: str,
        topic_id: str | None = None,
        title: str = "",
        mode: str = "autonomous-lite",
        platform: str = "telegram",
        selected_agents: list[str] | None = None,
    ) -> ConversationModel:
        q = self.db.query(ConversationModel).filter(
            ConversationModel.platform == platform,
            ConversationModel.chat_id == chat_id,
            ConversationModel.status.notin_(["done", "failed"]),
        )
        if topic_id:
            q = q.filter(ConversationModel.topic_id == topic_id)
        conv = q.order_by(ConversationModel.updated_at.desc()).first()
        if conv:
            timeout_minutes = max(1, settings.orchestrator_conversation_idle_timeout_minutes)
            age_limit = now_utc() - timedelta(minutes=timeout_minutes)
            waiting_user = bool(conv.is_waiting_user or conv.needs_user_input)
            updated_at = _to_utc_aware(conv.updated_at)
            if (
                conv.status == "idle"
                and not waiting_user
                and updated_at
                and updated_at < age_limit
            ):
                conv = None
            else:
                if selected_agents:
                    conv.selected_agents = list(selected_agents)
                    conv.updated_at = now_utc()
                return conv
        conv = ConversationModel(
            platform=platform,
            chat_id=chat_id,
            topic_id=topic_id,
            title=title,
            mode=mode,
            autonomy_level=mode,
            status="idle",
            selected_agents=list(selected_agents or []),
        )
        self.db.add(conv)
        self.db.flush()
        return conv

    def close_active_conversations(
        self,
        chat_id: str,
        topic_id: str | None = None,
    ) -> int:
        q = self.db.query(ConversationModel).filter(
            ConversationModel.chat_id == chat_id,
            ConversationModel.status.notin_(["done", "failed"]),
        )
        if topic_id:
            q = q.filter(ConversationModel.topic_id == topic_id)
        items = q.all()
        now = now_utc()
        for conv in items:
            conv.status = "done"
            conv.done = True
            conv.needs_user_input = False
            conv.is_waiting_user = False
            conv.updated_at = now
        return len(items)

    def get_conversation(self, conversation_id: uuid.UUID) -> ConversationModel | None:
        return self.db.get(ConversationModel, conversation_id)

    def update_conversation_status(
        self, conversation_id: uuid.UUID, status: str
    ) -> ConversationModel | None:
        conv = self.db.get(ConversationModel, conversation_id)
        if conv:
            conv.status = status
            conv.updated_at = now_utc()
        return conv

    def set_conversation_mode(
        self, conversation_id: uuid.UUID, mode: str
    ) -> ConversationModel | None:
        conv = self.db.get(ConversationModel, conversation_id)
        if conv:
            conv.mode = mode
            conv.autonomy_level = mode
            conv.updated_at = now_utc()
        return conv

    def update_runtime_state(
        self,
        conversation_id: uuid.UUID,
        **fields: Any,
    ) -> ConversationModel | None:
        conv = self.db.get(ConversationModel, conversation_id)
        if not conv:
            return None
        for key, value in fields.items():
            if hasattr(conv, key):
                setattr(conv, key, value)
        conv.updated_at = now_utc()
        return conv

    # ── Participant ───────────────────────────────────────────────────────

    def get_or_create_participant(
        self,
        conversation_id: uuid.UUID,
        handle: str,
        type: str,
        display_name: str = "",
        provider: str | None = None,
        model: str | None = None,
    ) -> ParticipantModel:
        p = (
            self.db.query(ParticipantModel)
            .filter(
                ParticipantModel.conversation_id == conversation_id,
                ParticipantModel.handle == handle,
            )
            .first()
        )
        if p:
            return p
        p = ParticipantModel(
            conversation_id=conversation_id,
            type=type,
            handle=handle,
            display_name=display_name,
            provider=provider,
            model=model,
        )
        self.db.add(p)
        self.db.flush()
        return p

    def list_participants(self, conversation_id: uuid.UUID) -> list[ParticipantModel]:
        return (
            self.db.query(ParticipantModel)
            .filter(ParticipantModel.conversation_id == conversation_id)
            .all()
        )

    # ── Message ───────────────────────────────────────────────────────────

    def create_message(
        self,
        conversation_id: uuid.UUID,
        raw_text: str,
        message_type: str,
        participant_id: uuid.UUID | None = None,
        telegram_message_id: int | None = None,
        reply_to_message_id: int | None = None,
        rendered_text: str = "",
        speaker_role: str | None = None,
        speaker_identity: str | None = None,
        speaker_bot_username: str | None = None,
        visible_message: str | None = None,
        suggested_next_agent: str | None = None,
        approved_next_agent: str | None = None,
        handoff_reason: str | None = None,
        task_status: str | None = None,
        done: bool = False,
        needs_user_input: bool = False,
        is_progress_turn: bool = False,
        is_agent_message: bool = False,
    ) -> MessageModel:
        msg = MessageModel(
            conversation_id=conversation_id,
            participant_id=participant_id,
            telegram_message_id=telegram_message_id,
            reply_to_message_id=reply_to_message_id,
            raw_text=raw_text,
            rendered_text=rendered_text or raw_text,
            message_type=message_type,
            speaker_role=speaker_role,
            speaker_identity=speaker_identity,
            speaker_bot_username=speaker_bot_username,
            visible_message=visible_message,
            suggested_next_agent=suggested_next_agent,
            approved_next_agent=approved_next_agent,
            handoff_reason=handoff_reason,
            task_status=task_status,
            done=done,
            needs_user_input=needs_user_input,
            is_progress_turn=is_progress_turn,
            is_agent_message=is_agent_message,
        )
        self.db.add(msg)
        self.db.flush()
        return msg

    def list_messages(
        self, conversation_id: uuid.UUID, limit: int = 50
    ) -> list[MessageModel]:
        rows = (
            self.db.query(MessageModel)
            .filter(MessageModel.conversation_id == conversation_id)
            .order_by(MessageModel.created_at.desc())
            .limit(limit)
            .all()
        )
        return list(reversed(rows))

    # ── Mention ───────────────────────────────────────────────────────────

    def create_mention(
        self,
        message_id: uuid.UUID,
        mention_text: str,
        target_participant_id: uuid.UUID | None = None,
    ) -> MentionModel:
        mention = MentionModel(
            message_id=message_id,
            target_participant_id=target_participant_id,
            mention_text=mention_text,
        )
        self.db.add(mention)
        self.db.flush()
        return mention

    # ── AgentRun ──────────────────────────────────────────────────────────

    def create_agent_run(
        self,
        conversation_id: uuid.UUID,
        agent_handle: str,
        trigger_message_id: uuid.UUID | None = None,
        provider: str | None = None,
        model: str | None = None,
        speaker_identity: str | None = None,
        input_context_snapshot: str = "",
    ) -> AgentRunModel:
        run = AgentRunModel(
            conversation_id=conversation_id,
            agent_handle=agent_handle,
            trigger_message_id=trigger_message_id,
            provider=provider,
            model=model,
            status="queued",
            speaker_identity=speaker_identity,
            input_context_snapshot=input_context_snapshot,
        )
        self.db.add(run)
        self.db.flush()
        return run

    def start_agent_run(self, run_id: uuid.UUID) -> AgentRunModel | None:
        run = self.db.get(AgentRunModel, run_id)
        if run:
            run.status = "running"
            run.started_at = now_utc()
        return run

    def finish_agent_run(
        self,
        run_id: uuid.UUID,
        output: str,
        input_snapshot: str = "",
        input_context_snapshot: str = "",
        error: str | None = None,
        output_message_id: int | None = None,
        suggested_next_agent: str | None = None,
        approved_next_agent: str | None = None,
        handoff_reason: str | None = None,
        validation_result: dict | None = None,
        fallback_applied: bool = False,
        progress_detected: bool = False,
        provider: str | None = None,
        model: str | None = None,
        termination_reason: str | None = None,
    ) -> AgentRunModel | None:
        run = self.db.get(AgentRunModel, run_id)
        if run:
            run.status = "failed" if error else "done"
            run.finished_at = now_utc()
            run.output_snapshot = output
            run.input_snapshot = input_snapshot
            run.input_context_snapshot = input_context_snapshot
            run.error = error
            run.suggested_next_agent = suggested_next_agent
            run.approved_next_agent = approved_next_agent
            run.handoff_reason = handoff_reason
            run.validation_result = validation_result
            run.fallback_applied = fallback_applied
            run.progress_detected = progress_detected
            if provider is not None:
                run.provider = provider
            if model is not None:
                run.model = model
            run.termination_reason = termination_reason
            if output_message_id is not None:
                run.output_message_id = output_message_id
        return run

    def list_agent_runs(
        self, conversation_id: uuid.UUID
    ) -> list[AgentRunModel]:
        return (
            self.db.query(AgentRunModel)
            .filter(AgentRunModel.conversation_id == conversation_id)
            .order_by(AgentRunModel.created_at)
            .all()
        )

    # ── Artifacts ──────────────────────────────────────────────────────────

    def create_or_replace_artifact(
        self,
        conversation_id: uuid.UUID,
        artifact_type: str,
        content: str,
        created_by_handle: str | None = None,
        source_run_id: uuid.UUID | None = None,
        task_id: uuid.UUID | None = None,
        replace_latest: bool = True,
    ) -> ConversationArtifactModel:
        artifact_type = artifact_type.strip().lower()
        status = "final" if artifact_type == "final" else "active"
        if replace_latest:
            (
                self.db.query(ConversationArtifactModel)
                .filter(
                    ConversationArtifactModel.conversation_id == conversation_id,
                    ConversationArtifactModel.artifact_type == artifact_type,
                    ConversationArtifactModel.status.in_(("active", "final")),
                )
                .update({"status": "superseded"}, synchronize_session=False)
            )

        latest = (
            self.db.query(ConversationArtifactModel)
            .filter(
                ConversationArtifactModel.conversation_id == conversation_id,
                ConversationArtifactModel.artifact_type == artifact_type,
            )
            .order_by(ConversationArtifactModel.version.desc())
            .first()
        )
        version = (latest.version if latest else 0) + 1
        artifact = ConversationArtifactModel(
            conversation_id=conversation_id,
            task_id=task_id,
            source_run_id=source_run_id,
            artifact_type=artifact_type,
            version=version,
            content=content,
            status=status,
            created_by_handle=created_by_handle,
        )
        self.db.add(artifact)
        self.db.flush()
        return artifact

    def list_artifacts(
        self,
        conversation_id: uuid.UUID,
        limit: int = 20,
    ) -> list[ConversationArtifactModel]:
        return (
            self.db.query(ConversationArtifactModel)
            .filter(ConversationArtifactModel.conversation_id == conversation_id)
            .order_by(ConversationArtifactModel.created_at.desc())
            .limit(limit)
            .all()
        )

    def get_latest_artifact(
        self,
        conversation_id: uuid.UUID,
        artifact_type: str,
    ) -> ConversationArtifactModel | None:
        artifact_type = artifact_type.strip().lower()
        return (
            self.db.query(ConversationArtifactModel)
            .filter(
                ConversationArtifactModel.conversation_id == conversation_id,
                ConversationArtifactModel.artifact_type == artifact_type,
                ConversationArtifactModel.status.in_(("active", "final")),
            )
            .order_by(ConversationArtifactModel.version.desc())
            .first()
        )


def _to_utc_aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
