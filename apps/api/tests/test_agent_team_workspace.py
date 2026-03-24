import asyncio
from dataclasses import dataclass
from datetime import timedelta
import uuid

from app.conversation_models import ConversationModel
from app.conversation_models import MessageModel
from app.conversations.selectors import build_context_prompt
from app.conversations.service import ConversationService
from app.core.config import settings
from app.core.database import Base, SessionLocal, engine
from app.core.time_utils import now_utc
from app.orchestrator.engine import orchestrator
from app.adapters.telegram.dispatcher import DispatchResult
from app.agents.base import AgentResult


def _ensure_schema() -> None:
    Base.metadata.create_all(bind=engine)


def test_conversation_service_preserves_platform_and_recent_history(client):
    _ensure_schema()
    with SessionLocal() as db:
        svc = ConversationService(db)
        chat_id = f"shared-{uuid.uuid4().hex}"
        conv_web = svc.get_or_create_conversation(
            chat_id=chat_id,
            platform="web",
            selected_agents=["planner", "writer"],
        )
        conv_web.status = "idle"
        conv_web.updated_at = now_utc() - timedelta(
            minutes=settings.orchestrator_conversation_idle_timeout_minutes + 2
        )
        db.commit()

        rolled = svc.get_or_create_conversation(
            chat_id=chat_id,
            platform="web",
            selected_agents=["planner", "writer"],
        )
        assert rolled.id != conv_web.id
        assert rolled.platform == "web"
        assert rolled.selected_agents == ["planner", "writer"]

        conv_tg = svc.get_or_create_conversation(chat_id=chat_id, platform="telegram")
        assert conv_tg.platform == "telegram"
        assert conv_tg.id != rolled.id

        for idx, text in enumerate(("one", "two", "three"), start=1):
            msg = svc.create_message(
                conversation_id=rolled.id,
                raw_text=text,
                message_type="user",
            )
            msg.created_at = now_utc() + timedelta(seconds=idx)
        db.commit()

        recent = svc.list_messages(rolled.id, limit=2)
        assert [m.raw_text for m in recent] == ["two", "three"]

        context = build_context_prompt(db, rolled.id, message_limit=2, max_chars=500)
        assert "two" in context
        assert "three" in context
        assert "one" not in context


def test_web_workspace_prefers_final_artifact(client):
    _ensure_schema()
    payload = {
        "title": "Artifact Chat",
        "mode": "autonomous-lite",
        "selected_agents": ["planner", "writer", "critic", "manager", "coder"],
    }
    created = client.post("/web/chats", json=payload)
    assert created.status_code == 201
    conv = created.json()

    with SessionLocal() as db:
        svc = ConversationService(db)
        svc.create_message(
            conversation_id=uuid.UUID(conv["id"]),
            raw_text="기준 정리",
            message_type="agent",
            speaker_role="planner",
            visible_message="기준 정리",
            approved_next_agent="writer",
            task_status="기준 확정",
            done=False,
        )
        svc.create_message(
            conversation_id=uuid.UUID(conv["id"]),
            raw_text="초안 작성",
            message_type="agent",
            speaker_role="writer",
            visible_message="초안 작성",
            approved_next_agent="critic",
            task_status="초안 전달",
            done=True,
        )
        svc.create_or_replace_artifact(
            conversation_id=uuid.UUID(conv["id"]),
            artifact_type="draft",
            content="초안 본문",
            created_by_handle="writer",
        )
        svc.create_or_replace_artifact(
            conversation_id=uuid.UUID(conv["id"]),
            artifact_type="final",
            content="최종 결과물",
            created_by_handle="manager",
        )
        db.commit()

    workspace = client.get(f"/web/chats/{conv['id']}/workspace")
    assert workspace.status_code == 200
    data = workspace.json()
    assert data["deliverable"]["artifact_type"] == "final"
    assert data["deliverable"]["content"] == "최종 결과물"
    assert [item["artifact_type"] for item in data["artifacts"]] == ["draft", "final"]
    assert len(data["progress_steps"]) >= 2
    assert data["progress_steps"][0]["actor_handle"] == "planner"
    assert data["progress_steps"][0]["target_handle"] == "writer"
    assert "planner -> writer" in data["progress_steps"][0]["label"]
    assert data["progress_steps"][1]["actor_handle"] == "writer"
    assert data["progress_steps"][1]["target_handle"] == "critic"

    deliverable = client.get(f"/web/chats/{conv['id']}/deliverable")
    assert deliverable.status_code == 200
    assert deliverable.json()["item"]["artifact_type"] == "final"


def test_web_chat_enforces_required_chain_roles(client):
    _ensure_schema()
    created = client.post(
        "/web/chats",
        json={
            "title": "Required Roles",
            "mode": "autonomous-lite",
            "selected_agents": ["planner", "writer"],
        },
    )
    assert created.status_code == 201
    conv = created.json()
    assert conv["selected_agents"] == ["planner", "writer", "critic", "manager"]

    updated = client.put(
        f"/web/chats/{conv['id']}/agents",
        json={"selected_agents": ["planner", "critic"]},
    )
    assert updated.status_code == 200
    selected = updated.json()["conversation"]["selected_agents"]
    assert selected == ["planner", "critic", "writer", "manager"]


def test_team_run_request_bootstraps_board_and_activity(client):
    _ensure_schema()
    created = client.post(
        "/web/team-runs",
        json={
            "title": "Team Run",
            "selected_agents": ["planner", "writer", "critic", "manager"],
        },
    )
    assert created.status_code == 201
    bootstrap = created.json()
    assert bootstrap["run"]["mode"] == "team-autonomous"
    assert bootstrap["tasks"] == []

    planned = client.post(
        f"/web/team-runs/{bootstrap['run']['id']}/requests",
        json={"text": "시장 조사 보고서를 작성해줘", "sender_name": "ceo"},
    )
    assert planned.status_code == 202
    board = planned.json()
    assert board["run"]["status"] == "done"
    assert board["conversation"]["mode"] == "team-autonomous"
    assert len(board["tasks"]) >= 4
    assert any(task["owner_handle"] == "planner" and task["status"] == "done" for task in board["tasks"])
    assert any(task["owner_handle"] == "writer" and task["status"] == "done" for task in board["tasks"])
    assert any(task["owner_handle"] == "manager" and task["status"] == "done" for task in board["tasks"])
    assert any(item["artifact_type"] == "brief" for item in board["artifacts"])
    assert any(item["artifact_type"] == "final" for item in board["artifacts"])
    assert board["deliverable"]["artifact_type"] == "final"
    assert len(board["activity"]) >= 8

    board_resp = client.get(f"/web/team-runs/{bootstrap['run']['id']}/board")
    assert board_resp.status_code == 200
    assert len(board_resp.json()["tasks"]) == len(board["tasks"])


def test_team_run_task_patch_updates_activity(client):
    _ensure_schema()
    created = client.post(
        "/web/team-runs",
        json={
            "title": "Patch Run",
            "selected_agents": ["planner", "writer", "critic", "manager"],
        },
    )
    run_id = created.json()["run"]["id"]
    planned = client.post(
        f"/web/team-runs/{run_id}/requests",
        json={"text": "발표 자료 초안을 만들어줘", "sender_name": "ceo"},
    )
    tasks = planned.json()["tasks"]
    writer_task = next(task for task in tasks if task["owner_handle"] == "writer")

    updated = client.patch(
        f"/web/tasks/{writer_task['id']}",
        json={"status": "in_progress"},
    )
    assert updated.status_code == 200
    payload = updated.json()
    refreshed = next(task for task in payload["tasks"] if task["id"] == writer_task["id"])
    assert refreshed["status"] == "in_progress"
    assert any(event["event_type"] == "task_started" for event in payload["activity"])


def test_team_run_task_detail_exposes_artifacts_and_review_state(client):
    _ensure_schema()
    created = client.post(
        "/web/team-runs",
        json={
            "title": "Detail Run",
            "selected_agents": ["planner", "writer", "critic", "manager"],
        },
    )
    run_id = created.json()["run"]["id"]
    planned = client.post(
        f"/web/team-runs/{run_id}/requests",
        json={"text": "AI 에이전트 현황 보고서를 작성해줘", "sender_name": "ceo"},
    )
    assert planned.status_code == 202
    board = planned.json()

    critic_task = next(task for task in board["tasks"] if task["owner_handle"] == "critic")
    assert critic_task["latest_artifact_type"] == "review_notes"
    assert critic_task["review_state"] == "reviewed"
    assert critic_task["artifact_count"] >= 1

    detail = client.get(f"/web/tasks/{critic_task['id']}")
    assert detail.status_code == 200
    payload = detail.json()
    assert payload["task"]["id"] == critic_task["id"]
    assert any(item["artifact_type"] == "review_notes" for item in payload["artifacts"])
    assert any(item["event_type"] == "task_started" for item in payload["activity"])
    assert payload["run"]["id"] == run_id


def test_team_run_task_rerun_reopens_downstream_and_republishes_final(client):
    _ensure_schema()
    created = client.post(
        "/web/team-runs",
        json={
            "title": "Rerun Run",
            "selected_agents": ["planner", "writer", "critic", "manager"],
        },
    )
    run_id = created.json()["run"]["id"]
    planned = client.post(
        f"/web/team-runs/{run_id}/requests",
        json={"text": "AI 에이전트 시장 보고서를 작성해줘", "sender_name": "ceo"},
    )
    assert planned.status_code == 202
    board = planned.json()
    writer_task = next(task for task in board["tasks"] if task["owner_handle"] == "writer")
    final_before = board["deliverable"]["version"]

    rerun = client.patch(
        f"/web/tasks/{writer_task['id']}",
        json={"action": "rerun", "actor_handle": "planner"},
    )
    assert rerun.status_code == 200
    payload = rerun.json()
    assert payload["run"]["status"] == "done"
    assert payload["deliverable"]["artifact_type"] == "final"
    assert payload["deliverable"]["version"] > final_before
    assert any(event["event_type"] == "task_reopened" for event in payload["activity"])
    assert any(task["owner_handle"] == "manager" and task["status"] == "done" for task in payload["tasks"])


def test_team_run_task_reassignment_records_activity(client):
    _ensure_schema()
    created = client.post(
        "/web/team-runs",
        json={
            "title": "Reassign Run",
            "selected_agents": ["planner", "writer", "critic", "manager", "coder"],
        },
    )
    run_id = created.json()["run"]["id"]
    planned = client.post(
        f"/web/team-runs/{run_id}/requests",
        json={"text": "서비스 운영 가이드를 정리해줘", "sender_name": "ceo"},
    )
    task = next(task for task in planned.json()["tasks"] if task["owner_handle"] == "writer")

    updated = client.patch(
        f"/web/tasks/{task['id']}",
        json={"owner_handle": "coder", "actor_handle": "planner"},
    )
    assert updated.status_code == 200
    payload = updated.json()
    refreshed = next(item for item in payload["tasks"] if item["id"] == task["id"])
    assert refreshed["owner_handle"] == "coder"
    assert any(event["event_type"] == "task_assigned" for event in payload["activity"])


def test_team_run_review_actions_update_state_and_rerun_branch(client):
    _ensure_schema()
    created = client.post(
        "/web/team-runs",
        json={
            "title": "Review Run",
            "selected_agents": ["planner", "writer", "critic", "manager"],
        },
    )
    run_id = created.json()["run"]["id"]
    planned = client.post(
        f"/web/team-runs/{run_id}/requests",
        json={"text": "시장 동향 브리프를 작성해줘", "sender_name": "ceo"},
    )
    board = planned.json()
    critic_task = next(task for task in board["tasks"] if task["owner_handle"] == "critic")
    final_before = board["deliverable"]["version"]

    approved = client.patch(
        f"/web/tasks/{critic_task['id']}",
        json={"action": "approve_review", "actor_handle": "manager"},
    )
    assert approved.status_code == 200
    approved_payload = approved.json()
    approved_task = next(item for item in approved_payload["tasks"] if item["id"] == critic_task["id"])
    assert approved_task["review_state"] == "approved"
    assert any(event["event_type"] == "review_approved" for event in approved_payload["activity"])

    rejected = client.patch(
        f"/web/tasks/{critic_task['id']}",
        json={"action": "reject_review", "actor_handle": "manager"},
    )
    assert rejected.status_code == 200
    rejected_payload = rejected.json()
    rejected_task = next(item for item in rejected_payload["tasks"] if item["id"] == critic_task["id"])
    assert rejected_task["review_state"] == "rejected"
    assert rejected_payload["deliverable"]["version"] > final_before
    assert any(event["event_type"] == "review_rejected" for event in rejected_payload["activity"])


def test_team_run_can_create_manual_task_and_execute_when_ready(client):
    _ensure_schema()
    created = client.post(
        "/web/team-runs",
        json={
            "title": "Manual Task Run",
            "selected_agents": ["planner", "writer", "critic", "manager", "coder"],
        },
    )
    run_id = created.json()["run"]["id"]
    planned = client.post(
        f"/web/team-runs/{run_id}/requests",
        json={"text": "서비스 개요를 정리해줘", "sender_name": "ceo"},
    )
    assert planned.status_code == 202

    created_task = client.post(
        f"/web/team-runs/{run_id}/tasks",
        json={
            "title": "추가 표 작성",
            "description": "요약 표를 만들고 핵심 포인트를 정리한다.",
            "owner_handle": "coder",
            "artifact_goal": "draft",
            "priority": 80,
            "review_required": False,
            "depends_on_task_ids": [],
        },
    )
    assert created_task.status_code == 201
    payload = created_task.json()
    added = next(task for task in payload["tasks"] if task["title"] == "추가 표 작성")
    assert added["owner_handle"] == "coder"
    assert added["status"] == "done"
    assert any(event["event_type"] == "task_created" for event in payload["activity"])


def test_team_run_rejects_dependency_cycle(client):
    _ensure_schema()
    created = client.post(
        "/web/team-runs",
        json={
            "title": "Cycle Run",
            "selected_agents": ["planner", "writer", "critic", "manager"],
        },
    )
    run_id = created.json()["run"]["id"]
    planned = client.post(
        f"/web/team-runs/{run_id}/requests",
        json={"text": "보고서 초안을 만들어줘", "sender_name": "ceo"},
    )
    tasks = planned.json()["tasks"]
    writer_task = next(task for task in tasks if task["owner_handle"] == "writer")
    critic_task = next(task for task in tasks if task["owner_handle"] == "critic")

    cycle = client.put(
        f"/web/tasks/{writer_task['id']}/dependencies",
        json={"depends_on_task_ids": [critic_task["id"]]},
    )
    assert cycle.status_code == 400
    assert "cycle" in cycle.json()["detail"]


def test_team_run_done_task_edit_reopens_branch_and_republishes(client):
    _ensure_schema()
    created = client.post(
        "/web/team-runs",
        json={
            "title": "Edit Run",
            "selected_agents": ["planner", "writer", "critic", "manager"],
        },
    )
    run_id = created.json()["run"]["id"]
    planned = client.post(
        f"/web/team-runs/{run_id}/requests",
        json={"text": "시장 브리프를 작성해줘", "sender_name": "ceo"},
    )
    board = planned.json()
    writer_task = next(task for task in board["tasks"] if task["owner_handle"] == "writer")
    final_before = board["deliverable"]["version"]

    updated = client.patch(
        f"/web/tasks/{writer_task['id']}",
        json={
            "title": writer_task["title"],
            "description": "업데이트된 기준으로 내용을 다시 정리한다.",
            "artifact_goal": writer_task["artifact_goal"],
            "priority": writer_task["priority"],
            "review_required": True,
        },
    )
    assert updated.status_code == 200
    payload = updated.json()
    refreshed = next(task for task in payload["tasks"] if task["id"] == writer_task["id"])
    assert refreshed["status"] == "done"
    assert payload["deliverable"]["version"] > final_before
    assert any(event["event_type"] == "task_reopened" for event in payload["activity"])


@dataclass
class _FakeBot:
    username: str
    display_name: str
    emoji: str = "🤖"


class _FakeRegistry:
    inbound_identity = "pm"

    def get(self, identity: str):
        return _FakeBot(username=identity, display_name=identity)


class _FakeDispatcher:
    def __init__(self):
        self._registry = _FakeRegistry()

    def resolve_identity(self, role: str) -> str:
        return role

    async def dispatch(
        self,
        role: str,
        chat_id: str | int,
        body: str,
        next_role: str | None = None,
        include_handoff_hint: bool = True,
        reply_to_message_id: int | None = None,
        message_thread_id: int | None = None,
    ) -> DispatchResult:
        return DispatchResult(identity=role, telegram_message_id=None, rendered_text=body)

    async def dispatch_status(
        self,
        identity: str,
        chat_id: str | int,
        text: str,
        message_thread_id: int | None = None,
    ) -> int | None:
        return None


class _FakeAgent:
    def __init__(self, handle: str, results: list[AgentResult]):
        self.handle = handle
        self.display_name = handle
        self.emoji = "🤖"
        self.config = type("Cfg", (), {"provider": "stub", "model": "stub"})()
        self._results = list(results)

    async def run(self, user_request: str, context: str = "") -> AgentResult:
        return self._results.pop(0)


def _agent_result(
    handle: str,
    visible: str,
    suggested: str | None,
    *,
    done: bool,
    artifact_type: str | None = None,
    artifact_content: str = "",
) -> AgentResult:
    artifact_update = None
    if artifact_type and artifact_content:
        artifact_update = {
            "type": artifact_type,
            "content": artifact_content,
            "replace_latest": True,
        }
    return AgentResult(
        handle=handle,
        display_name=handle,
        emoji="🤖",
        text=visible,
        provider="stub",
        model="stub",
        visible_message=visible,
        suggested_next_agent=suggested,
        handoff_reason="test",
        task_status="done" if done else "in_progress",
        done=done,
        needs_user_input=False,
        artifact_update=artifact_update,
    )


def test_orchestrator_runs_fixed_collaboration_chain(client):
    _ensure_schema()
    original_agents = orchestrator._agents
    original_loaded = orchestrator._loaded
    orchestrator._agents = {
        "planner": _FakeAgent(
            "planner",
            [_agent_result("planner", "기준을 고정합니다", "planner", done=False, artifact_type="brief", artifact_content="브리프")],
        ),
        "writer": _FakeAgent(
            "writer",
            [_agent_result("writer", "초안 작성 완료", None, done=True, artifact_type="draft", artifact_content="초안")],
        ),
        "critic": _FakeAgent(
            "critic",
            [_agent_result("critic", "검토 완료", None, done=True, artifact_type="review_notes", artifact_content="검토 메모")],
        ),
        "manager": _FakeAgent(
            "manager",
            [_agent_result("manager", "최종본 정리", None, done=False, artifact_type="final", artifact_content="최종본")],
        ),
    }
    orchestrator._loaded = True

    try:
        with SessionLocal() as db:
            svc = ConversationService(db)
            chat_id = f"web:{uuid.uuid4().hex}"
            conv = svc.get_or_create_conversation(
                chat_id=chat_id,
                platform="web",
                mode="autonomous",
                selected_agents=["planner", "writer", "critic", "manager"],
            )
            db.commit()

            asyncio.run(
                orchestrator.process_message(
                    db=db,
                    chat_id=chat_id,
                    text="@writer 팀으로 작성해줘",
                    sender_name="tester",
                    telegram_message_id=None,
                    topic_id=None,
                    inbound_identity="pm",
                    chat_type="web",
                    dispatcher_override=_FakeDispatcher(),
                    available_handles=["planner", "writer", "critic", "manager"],
                )
            )

            db.refresh(conv)
            web_conv = (
                db.query(ConversationModel)
                .filter(
                    ConversationModel.chat_id == chat_id,
                    ConversationModel.platform == "web",
                )
                .order_by(ConversationModel.updated_at.desc())
                .first()
            )
            assert web_conv is not None
            messages = svc.list_messages(web_conv.id, limit=20)
            assert [msg.speaker_role for msg in messages if msg.message_type == "agent"] == [
                "planner",
                "writer",
                "critic",
                "manager",
            ]
            artifacts = svc.list_artifacts(web_conv.id, limit=10)
            assert [artifact.artifact_type for artifact in reversed(artifacts)] == [
                "brief",
                "draft",
                "review_notes",
                "final",
            ]
            assert artifacts[0].artifact_type == "final"
            assert artifacts[0].created_by_handle == "manager"
    finally:
        orchestrator._agents = original_agents
        orchestrator._loaded = original_loaded


def test_orchestrator_pauses_when_fixed_chain_member_missing(client):
    _ensure_schema()
    original_agents = orchestrator._agents
    original_loaded = orchestrator._loaded
    orchestrator._agents = {
        "planner": _FakeAgent(
            "planner",
            [_agent_result("planner", "기준을 고정합니다", "writer", done=False, artifact_type="brief", artifact_content="브리프")],
        ),
        "writer": _FakeAgent(
            "writer",
            [_agent_result("writer", "초안 작성 완료", None, done=True, artifact_type="draft", artifact_content="초안")],
        ),
    }
    orchestrator._loaded = True

    try:
        with SessionLocal() as db:
            svc = ConversationService(db)
            chat_id = f"web:{uuid.uuid4().hex}"
            conv = svc.get_or_create_conversation(
                chat_id=chat_id,
                platform="web",
                mode="autonomous-lite",
                selected_agents=["planner", "writer"],
            )
            db.commit()

            asyncio.run(
                orchestrator.process_message(
                    db=db,
                    chat_id=chat_id,
                    text="보고서 작성해줘",
                    sender_name="tester",
                    telegram_message_id=None,
                    topic_id=None,
                    inbound_identity="pm",
                    chat_type="web",
                    dispatcher_override=_FakeDispatcher(),
                    available_handles=["planner", "writer"],
                )
            )

            db.refresh(conv)
            assert conv.status == "paused"
            assert conv.needs_user_input is True
            assert conv.approved_next_agent == "critic"
    finally:
        orchestrator._agents = original_agents
        orchestrator._loaded = original_loaded
