import uuid
from datetime import datetime

from sqlalchemy.orm import Session

from app.conversation_models import (
    AgentRunModel,
    ConversationModel,
    MessageModel,
    MentionModel,
    ParticipantModel,
)
from app.core.time_utils import now_utc


class ConversationService:
    def __init__(self, db: Session):
        self.db = db

    # ── Conversation ──────────────────────────────────────────────────────

    def get_or_create_conversation(
        self,
        chat_id: str,
        topic_id: str | None = None,
        title: str = "",
        mode: str = "pipeline",
    ) -> ConversationModel:
        q = self.db.query(ConversationModel).filter(
            ConversationModel.chat_id == chat_id,
            ConversationModel.status.notin_(["done", "failed"]),
        )
        if topic_id:
            q = q.filter(ConversationModel.topic_id == topic_id)
        conv = q.first()
        if conv:
            return conv
        conv = ConversationModel(
            chat_id=chat_id,
            topic_id=topic_id,
            title=title,
            mode=mode,
            status="idle",
        )
        self.db.add(conv)
        self.db.flush()
        return conv

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
    ) -> MessageModel:
        msg = MessageModel(
            conversation_id=conversation_id,
            participant_id=participant_id,
            telegram_message_id=telegram_message_id,
            reply_to_message_id=reply_to_message_id,
            raw_text=raw_text,
            rendered_text=rendered_text or raw_text,
            message_type=message_type,
        )
        self.db.add(msg)
        self.db.flush()
        return msg

    def list_messages(
        self, conversation_id: uuid.UUID, limit: int = 50
    ) -> list[MessageModel]:
        return (
            self.db.query(MessageModel)
            .filter(MessageModel.conversation_id == conversation_id)
            .order_by(MessageModel.created_at)
            .limit(limit)
            .all()
        )

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
    ) -> AgentRunModel:
        run = AgentRunModel(
            conversation_id=conversation_id,
            agent_handle=agent_handle,
            trigger_message_id=trigger_message_id,
            provider=provider,
            model=model,
            status="queued",
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
        error: str | None = None,
    ) -> AgentRunModel | None:
        run = self.db.get(AgentRunModel, run_id)
        if run:
            run.status = "failed" if error else "done"
            run.finished_at = now_utc()
            run.output_snapshot = output
            run.input_snapshot = input_snapshot
            run.error = error
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
