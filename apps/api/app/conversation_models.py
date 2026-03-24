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
    mode: Mapped[str] = mapped_column(String(30), default="autonomous-lite", nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="idle", nullable=False)
    turn_limit: Mapped[int] = mapped_column(Integer, default=8, nullable=False)
    is_waiting_user: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    autonomy_level: Mapped[str] = mapped_column(String(30), default="autonomous-lite", nullable=False)
    current_agent: Mapped[str | None] = mapped_column(String(100))
    current_identity: Mapped[str | None] = mapped_column(String(100))
    suggested_next_agent: Mapped[str | None] = mapped_column(String(100))
    approved_next_agent: Mapped[str | None] = mapped_column(String(100))
    last_handoff_reason: Mapped[str | None] = mapped_column(Text)
    task_status: Mapped[str | None] = mapped_column(String(120))
    done: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    needs_user_input: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    loop_guard_counter: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_n_agents: Mapped[list] = mapped_column(JSON, default=list)
    anchor_message_id: Mapped[int | None] = mapped_column(Integer)
    last_message_id: Mapped[int | None] = mapped_column(Integer)
    last_user_goal_snapshot: Mapped[str] = mapped_column(Text, default="")
    completion_score: Mapped[int | None] = mapped_column(Integer)
    confidence_trend: Mapped[list] = mapped_column(JSON, default=list)
    selected_agents: Mapped[list] = mapped_column(JSON, default=list)
    artifact_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    export_ready: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
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
    visible_message: Mapped[str | None] = mapped_column(Text)
    suggested_next_agent: Mapped[str | None] = mapped_column(String(100))
    approved_next_agent: Mapped[str | None] = mapped_column(String(100))
    handoff_reason: Mapped[str | None] = mapped_column(Text)
    task_status: Mapped[str | None] = mapped_column(String(120))
    done: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    needs_user_input: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_progress_turn: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
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
    input_context_snapshot: Mapped[str] = mapped_column(Text, default="")
    input_snapshot: Mapped[str] = mapped_column(Text, default="")
    output_snapshot: Mapped[str] = mapped_column(Text, default="")
    suggested_next_agent: Mapped[str | None] = mapped_column(String(100))
    approved_next_agent: Mapped[str | None] = mapped_column(String(100))
    handoff_reason: Mapped[str | None] = mapped_column(Text)
    validation_result: Mapped[dict | None] = mapped_column(JSON)
    fallback_applied: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    progress_detected: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    termination_reason: Mapped[str | None] = mapped_column(String(120))
    provider: Mapped[str | None] = mapped_column(String(50))
    model: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(30), default="queued", nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, nullable=False)


class ConversationArtifactModel(Base):
    __tablename__ = "conversation_artifacts"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("team_tasks.id", ondelete="SET NULL"),
    )
    source_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("agent_runs.id", ondelete="SET NULL"),
    )
    artifact_type: Mapped[str] = mapped_column(String(50), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    content: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    created_by_handle: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, nullable=False)


class TeamRunModel(Base):
    __tablename__ = "team_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
    )
    title: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    mode: Mapped[str] = mapped_column(String(30), default="team-autonomous", nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="idle", nullable=False)
    requested_by: Mapped[str] = mapped_column(String(100), default="web_user", nullable=False)
    request_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    selected_agents: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    final_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("conversation_artifacts.id", ondelete="SET NULL"),
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, nullable=False)


class TeamTaskModel(Base):
    __tablename__ = "team_tasks"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("team_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    owner_handle: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="todo", nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=50, nullable=False)
    artifact_goal: Mapped[str] = mapped_column(String(50), default="", nullable=False)
    parent_task_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("team_tasks.id", ondelete="SET NULL"),
    )
    created_by_handle: Mapped[str | None] = mapped_column(String(100))
    review_required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, nullable=False)


class TeamTaskDependencyModel(Base):
    __tablename__ = "team_task_dependencies"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_task_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("team_tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
    depends_on_task_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("team_tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, nullable=False)


class TeamActivityEventModel(Base):
    __tablename__ = "team_activity_events"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("team_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("team_tasks.id", ondelete="SET NULL"),
    )
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    actor_handle: Mapped[str | None] = mapped_column(String(100))
    target_handle: Mapped[str | None] = mapped_column(String(100))
    summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    payload: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, nullable=False)
