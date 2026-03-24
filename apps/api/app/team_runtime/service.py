import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.conversation_models import (
    TeamActivityEventModel,
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
        requested_by: str,
        request_text: str = "",
        selected_agents: list[str] | None = None,
        status: str = "idle",
    ) -> TeamRunModel:
        run = TeamRunModel(
            conversation_id=conversation_id,
            title=title,
            mode=mode,
            requested_by=requested_by,
            request_text=request_text,
            selected_agents=list(selected_agents or []),
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
        priority: int = 50,
        parent_task_id: uuid.UUID | None = None,
        review_required: bool = False,
    ) -> TeamTaskModel:
        task = TeamTaskModel(
            team_run_id=team_run_id,
            title=title,
            description=description,
            owner_handle=owner_handle,
            artifact_goal=artifact_goal,
            created_by_handle=created_by_handle,
            status=status,
            priority=priority,
            parent_task_id=parent_task_id,
            review_required=review_required,
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
            and deps_by_task.get(task.id, set()).issubset(done_ids)
        ]
