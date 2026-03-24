import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy.orm import Session
from sqlalchemy import delete

from app.conversation_models import (
    TeamActivityEventModel,
    TeamInboxMessageModel,
    TeamMemberSessionModel,
    TeamRunModel,
    TeamTaskDependencyModel,
    TeamTaskModel,
)
from app.core.time_utils import now_utc


class TeamRunService:
    def __init__(self, db: Session):
        self.db = db

    def create_run(
        self,
        *,
        conversation_id: uuid.UUID | None,
        title: str,
        mode: str,
        oversight_mode: str = "auto",
        requested_by: str,
        request_text: str = "",
        output_type: str = "docx",
        document_provider: str = "internal_fallback",
        selected_agents: list[str] | None = None,
        source_file_ids: list[uuid.UUID] | list[str] | None = None,
        source_ir_summary: str = "",
        auto_review_max_rounds: int = 2,
        status: str = "idle",
    ) -> TeamRunModel:
        run = TeamRunModel(
            conversation_id=conversation_id,
            title=title,
            mode=mode,
            oversight_mode=oversight_mode,
            plan_status="pending",
            requested_by=requested_by,
            request_text=request_text,
            output_type=output_type,
            document_provider=document_provider,
            selected_agents=list(selected_agents or []),
            source_file_ids=[str(item) for item in (source_file_ids or [])],
            source_ir_summary=source_ir_summary or "",
            auto_review_max_rounds=int(auto_review_max_rounds or 2),
            status=status,
        )
        self.db.add(run)
        self.db.flush()
        return run

    def list_runs(self, limit: int = 50) -> list[TeamRunModel]:
        return (
            self.db.query(TeamRunModel)
            .order_by(TeamRunModel.updated_at.desc())
            .limit(limit)
            .all()
        )

    def get_run(self, team_run_id: uuid.UUID) -> TeamRunModel | None:
        return self.db.get(TeamRunModel, team_run_id)

    def get_run_by_conversation(self, conversation_id: uuid.UUID) -> TeamRunModel | None:
        return (
            self.db.query(TeamRunModel)
            .filter(TeamRunModel.conversation_id == conversation_id)
            .order_by(TeamRunModel.updated_at.desc())
            .first()
        )

    def update_run(self, team_run_id: uuid.UUID, **fields: Any) -> TeamRunModel | None:
        run = self.db.get(TeamRunModel, team_run_id)
        if not run:
            return None
        for key, value in fields.items():
            if hasattr(run, key):
                setattr(run, key, value)
        run.updated_at = now_utc()
        return run

    def create_task(
        self,
        *,
        team_run_id: uuid.UUID,
        title: str,
        description: str,
        owner_handle: str,
        artifact_goal: str,
        created_by_handle: str | None = None,
        status: str = "todo",
        claim_status: str = "open",
        priority: int = 50,
        parent_task_id: uuid.UUID | None = None,
        review_required: bool = False,
        task_kind: str = "draft",
    ) -> TeamTaskModel:
        task = TeamTaskModel(
            team_run_id=team_run_id,
            title=title,
            description=description,
            owner_handle=owner_handle,
            artifact_goal=artifact_goal,
            created_by_handle=created_by_handle,
            status=status,
            claim_status=claim_status,
            priority=priority,
            parent_task_id=parent_task_id,
            review_required=review_required,
            task_kind=task_kind,
        )
        self.db.add(task)
        self.db.flush()
        return task

    def get_task(self, task_id: uuid.UUID) -> TeamTaskModel | None:
        return self.db.get(TeamTaskModel, task_id)

    def update_task(self, task_id: uuid.UUID, **fields: Any) -> TeamTaskModel | None:
        task = self.db.get(TeamTaskModel, task_id)
        if not task:
            return None
        for key, value in fields.items():
            if hasattr(task, key):
                setattr(task, key, value)
        task.updated_at = now_utc()
        run = self.db.get(TeamRunModel, task.team_run_id)
        if run:
            run.updated_at = now_utc()
        return task

    def list_tasks(self, team_run_id: uuid.UUID) -> list[TeamTaskModel]:
        return (
            self.db.query(TeamTaskModel)
            .filter(TeamTaskModel.team_run_id == team_run_id)
            .order_by(TeamTaskModel.priority.asc(), TeamTaskModel.created_at.asc())
            .all()
        )

    def create_session(
        self,
        *,
        team_run_id: uuid.UUID,
        handle: str,
        role: str = "worker",
        display_name: str = "",
        status: str = "idle",
    ) -> TeamMemberSessionModel:
        session = TeamMemberSessionModel(
            team_run_id=team_run_id,
            handle=handle,
            role=role,
            display_name=display_name or handle,
            status=status,
            last_heartbeat_at=now_utc(),
        )
        self.db.add(session)
        self.db.flush()
        return session

    def get_session(self, session_id: uuid.UUID) -> TeamMemberSessionModel | None:
        return self.db.get(TeamMemberSessionModel, session_id)

    def list_sessions(self, team_run_id: uuid.UUID) -> list[TeamMemberSessionModel]:
        return (
            self.db.query(TeamMemberSessionModel)
            .filter(TeamMemberSessionModel.team_run_id == team_run_id)
            .order_by(TeamMemberSessionModel.created_at.asc())
            .all()
        )

    def find_session_by_handle(
        self,
        team_run_id: uuid.UUID,
        handle: str,
    ) -> TeamMemberSessionModel | None:
        return (
            self.db.query(TeamMemberSessionModel)
            .filter(
                TeamMemberSessionModel.team_run_id == team_run_id,
                TeamMemberSessionModel.handle == handle,
            )
            .order_by(TeamMemberSessionModel.created_at.asc())
            .first()
        )

    def list_sessions_by_handle(
        self,
        team_run_id: uuid.UUID,
        handle: str,
    ) -> list[TeamMemberSessionModel]:
        return (
            self.db.query(TeamMemberSessionModel)
            .filter(
                TeamMemberSessionModel.team_run_id == team_run_id,
                TeamMemberSessionModel.handle == handle,
            )
            .order_by(TeamMemberSessionModel.last_heartbeat_at.asc(), TeamMemberSessionModel.created_at.asc())
            .all()
        )

    def update_session(self, session_id: uuid.UUID, **fields: Any) -> TeamMemberSessionModel | None:
        session = self.db.get(TeamMemberSessionModel, session_id)
        if not session:
            return None
        for key, value in fields.items():
            if hasattr(session, key):
                setattr(session, key, value)
        session.updated_at = now_utc()
        session.last_heartbeat_at = now_utc()
        run = self.db.get(TeamRunModel, session.team_run_id)
        if run:
            run.updated_at = now_utc()
        return session

    def create_inbox_message(
        self,
        *,
        team_run_id: uuid.UUID,
        content: str,
        subject: str = "",
        message_type: str = "direct",
        from_session_id: uuid.UUID | None = None,
        to_session_id: uuid.UUID | None = None,
        related_task_id: uuid.UUID | None = None,
        status: str = "unread",
    ) -> TeamInboxMessageModel:
        item = TeamInboxMessageModel(
            team_run_id=team_run_id,
            from_session_id=from_session_id,
            to_session_id=to_session_id,
            related_task_id=related_task_id,
            message_type=message_type,
            subject=subject,
            content=content,
            status=status,
        )
        self.db.add(item)
        self.db.flush()
        run = self.db.get(TeamRunModel, team_run_id)
        if run:
            run.updated_at = now_utc()
        return item

    def list_inbox_messages(self, team_run_id: uuid.UUID, limit: int = 200) -> list[TeamInboxMessageModel]:
        rows = (
            self.db.query(TeamInboxMessageModel)
            .filter(TeamInboxMessageModel.team_run_id == team_run_id)
            .order_by(TeamInboxMessageModel.created_at.desc())
            .limit(limit)
            .all()
        )
        return list(reversed(rows))

    def list_session_inbox_messages(
        self,
        *,
        team_run_id: uuid.UUID,
        session_id: uuid.UUID,
        include_read: bool = True,
        limit: int = 40,
    ) -> list[TeamInboxMessageModel]:
        query = self.db.query(TeamInboxMessageModel).filter(
            TeamInboxMessageModel.team_run_id == team_run_id,
            (
                (TeamInboxMessageModel.to_session_id == session_id)
                | (TeamInboxMessageModel.to_session_id.is_(None))
            ),
        )
        if not include_read:
            query = query.filter(TeamInboxMessageModel.status == "unread")
        rows = query.order_by(TeamInboxMessageModel.created_at.desc()).limit(limit).all()
        return list(reversed(rows))

    def mark_inbox_messages_read(self, message_ids: list[uuid.UUID]) -> None:
        if not message_ids:
            return
        (
            self.db.query(TeamInboxMessageModel)
            .filter(TeamInboxMessageModel.id.in_(message_ids))
            .update({"status": "read"}, synchronize_session=False)
        )

    def claim_task(
        self,
        *,
        task_id: uuid.UUID,
        session_id: uuid.UUID,
        ttl_seconds: int = 900,
    ) -> TeamTaskModel | None:
        task = self.db.get(TeamTaskModel, task_id)
        session = self.db.get(TeamMemberSessionModel, session_id)
        if not task or not session or task.team_run_id != session.team_run_id:
            return None
        task.claim_status = "claimed"
        task.claimed_by_session_id = session_id
        task.claim_expires_at = now_utc() + timedelta(seconds=max(ttl_seconds, 60))
        task.updated_at = now_utc()
        session.current_task_id = task.id
        session.status = "busy"
        session.updated_at = now_utc()
        session.last_heartbeat_at = now_utc()
        return task

    def release_task_claim(
        self,
        task_id: uuid.UUID,
        *,
        reset_status: str | None = None,
    ) -> TeamTaskModel | None:
        task = self.db.get(TeamTaskModel, task_id)
        if not task:
            return None
        session = self.db.get(TeamMemberSessionModel, task.claimed_by_session_id) if task.claimed_by_session_id else None
        if session:
            session.current_task_id = None
            session.status = "idle"
            session.updated_at = now_utc()
            session.last_heartbeat_at = now_utc()
        task.claimed_by_session_id = None
        task.claim_expires_at = None
        task.claim_status = reset_status or "open"
        task.updated_at = now_utc()
        return task

    def add_dependency(
        self,
        *,
        team_task_id: uuid.UUID,
        depends_on_task_id: uuid.UUID,
    ) -> TeamTaskDependencyModel:
        dep = TeamTaskDependencyModel(
            team_task_id=team_task_id,
            depends_on_task_id=depends_on_task_id,
        )
        self.db.add(dep)
        self.db.flush()
        return dep

    def replace_dependencies(
        self,
        *,
        team_task_id: uuid.UUID,
        depends_on_task_ids: list[uuid.UUID],
    ) -> list[TeamTaskDependencyModel]:
        self.db.execute(
            delete(TeamTaskDependencyModel).where(
                TeamTaskDependencyModel.team_task_id == team_task_id
            )
        )
        created = []
        seen: set[uuid.UUID] = set()
        for depends_on_task_id in depends_on_task_ids:
            if depends_on_task_id in seen:
                continue
            seen.add(depends_on_task_id)
            created.append(
                self.add_dependency(
                    team_task_id=team_task_id,
                    depends_on_task_id=depends_on_task_id,
                )
            )
        return created

    def list_dependencies(self, team_run_id: uuid.UUID) -> list[TeamTaskDependencyModel]:
        ids = [
            task_id
            for (task_id,) in self.db.query(TeamTaskModel.id)
            .filter(TeamTaskModel.team_run_id == team_run_id)
            .all()
        ]
        if not ids:
            return []
        return (
            self.db.query(TeamTaskDependencyModel)
            .filter(TeamTaskDependencyModel.team_task_id.in_(ids))
            .all()
        )

    def create_activity(
        self,
        *,
        team_run_id: uuid.UUID,
        event_type: str,
        summary: str,
        actor_handle: str | None = None,
        target_handle: str | None = None,
        task_id: uuid.UUID | None = None,
        payload: dict | None = None,
    ) -> TeamActivityEventModel:
        event = TeamActivityEventModel(
            team_run_id=team_run_id,
            task_id=task_id,
            event_type=event_type,
            actor_handle=actor_handle,
            target_handle=target_handle,
            summary=summary,
            payload=payload,
        )
        self.db.add(event)
        self.db.flush()
        run = self.db.get(TeamRunModel, team_run_id)
        if run:
            run.updated_at = now_utc()
        return event

    def list_activity(self, team_run_id: uuid.UUID, limit: int = 100) -> list[TeamActivityEventModel]:
        rows = (
            self.db.query(TeamActivityEventModel)
            .filter(TeamActivityEventModel.team_run_id == team_run_id)
            .order_by(TeamActivityEventModel.created_at.desc())
            .limit(limit)
            .all()
        )
        return list(reversed(rows))

    def ready_tasks(self, team_run_id: uuid.UUID) -> list[TeamTaskModel]:
        tasks = self.list_tasks(team_run_id)
        done_ids = {task.id for task in tasks if task.status == "done"}
        deps_by_task: dict[uuid.UUID, set[uuid.UUID]] = {}
        for dep in self.list_dependencies(team_run_id):
            deps_by_task.setdefault(dep.team_task_id, set()).add(dep.depends_on_task_id)
        return [
            task
            for task in tasks
            if task.status == "todo"
            and task.claim_status == "open"
            and deps_by_task.get(task.id, set()).issubset(done_ids)
        ]
