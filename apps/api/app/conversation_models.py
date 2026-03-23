from datetime import datetime
import uuid

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.time_utils import now_utc


class ConversationModel(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    platform: Mapped[str] = mapped_column(String(30), default="telegram", nullable=False)
    chat_id: Mapped[str] = mapped_column(String(100), nullable=False)
    topic_id: Mapped[str | None] = mapped_column(String(100))
    title: Mapped[str] = mapped_column(String(500), default="")
    mode: Mapped[str] = mapped_column(String(30), default="pipeline", nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="idle", nullable=False)
    # reply chain: identity → last sent telegram_message_id
    last_message_ids: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, nullable=False)


class ParticipantModel(Base):
    __tablename__ = "participants"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    type: Mapped[str] = mapped_column(String(20), nullable=False)  # user | agent | system
    handle: Mapped[str] = mapped_column(String(100), nullable=False)  # gpt, claude, manager, ...
    display_name: Mapped[str] = mapped_column(String(200), default="")
    provider: Mapped[str | None] = mapped_column(String(50))
    model: Mapped[str | None] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class MessageModel(Base):
    __tablename__ = "conv_messages"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    participant_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("participants.id", ondelete="SET NULL"),
    )
    telegram_message_id: Mapped[int | None] = mapped_column(Integer)
    reply_to_message_id: Mapped[int | None] = mapped_column(Integer)
    raw_text: Mapped[str] = mapped_column(Text, default="")
    rendered_text: Mapped[str] = mapped_column(Text, default="")
    message_type: Mapped[str] = mapped_column(String(30), nullable=False)  # user|agent|status|artifact|system
    speaker_role: Mapped[str | None] = mapped_column(String(100))
    speaker_identity: Mapped[str | None] = mapped_column(String(100))
    speaker_bot_username: Mapped[str | None] = mapped_column(String(100))
    is_agent_message: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, nullable=False)


class MentionModel(Base):
    __tablename__ = "mentions"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    message_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("conv_messages.id", ondelete="CASCADE"),
        nullable=False,
    )
    target_participant_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("participants.id", ondelete="SET NULL"),
    )
    mention_text: Mapped[str] = mapped_column(String(100), nullable=False)


class AgentRunModel(Base):
    __tablename__ = "agent_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_handle: Mapped[str] = mapped_column(String(100), nullable=False)
    speaker_identity: Mapped[str | None] = mapped_column(String(100))
    output_message_id: Mapped[int | None] = mapped_column(Integer)  # Telegram message_id of sent output
    trigger_message_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("conv_messages.id", ondelete="SET NULL"),
    )
    input_snapshot: Mapped[str] = mapped_column(Text, default="")
    output_snapshot: Mapped[str] = mapped_column(Text, default="")
    provider: Mapped[str | None] = mapped_column(String(50))
    model: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(30), default="queued", nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, nullable=False)
