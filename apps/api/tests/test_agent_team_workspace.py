import asyncio
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
import uuid

from app.conversation_models import ConversationModel
from app.conversation_models import MessageModel
from app.conversation_models import TeamTaskModel
from app.conversations.selectors import build_context_prompt
from app.conversations.service import ConversationService
from app.core.config import settings
from app.core.database import Base, SessionLocal, engine
from app.core.time_utils import now_utc
from app.orchestrator.engine import orchestrator
from app.adapters.telegram.dispatcher import DispatchResult
from app.agents.base import AgentResult
from app.agents.base import _parse_agent_payload
from app.api.routes import _build_structured_deliverable
from app.api.routes import _build_done_with_risks_content
from app.api.routes import _normalize_presentation_final_content
from app.api.routes import _presentation_user_visible_markdown
from app.api.routes import _task_execution_contract
from app.api.routes import _coerce_team_tasks
from app.services.file_generators import generate_report_docx


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
    assert bootstrap["run"]["oversight_mode"] == "auto"
    assert bootstrap["tasks"] == []

    planned = client.post(
        f"/web/team-runs/{bootstrap['run']['id']}/requests",
        json={"text": "시장 조사 보고서를 작성해줘", "sender_name": "ceo"},
    )
    assert planned.status_code == 202
    board = planned.json()
    assert board["run"]["status"] == "done"
    assert board["run"]["oversight_mode"] == "auto"
    assert board["run"]["auto_review_max_rounds"] == 2
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
    assert board_resp.json()["sessions"]
    assert any(item["role"] == "leader" for item in board_resp.json()["sessions"])


def test_team_run_bootstrap_exposes_sessions_and_inbox(client):
    _ensure_schema()
    created = client.post(
        "/web/team-runs",
        json={
            "title": "Session Run",
            "selected_agents": ["planner", "writer", "critic", "manager"],
        },
    )
    assert created.status_code == 201
    payload = created.json()
    assert payload["sessions"]
    assert payload["messages"] == []
    assert any(item["handle"] == "planner" and item["role"] == "leader" for item in payload["sessions"])

    sessions_resp = client.get(f"/web/team-runs/{payload['run']['id']}/sessions")
    assert sessions_resp.status_code == 200
    assert len(sessions_resp.json()["items"]) >= 4


def test_team_run_uses_presentation_preset_for_slide_requests(client):
    _ensure_schema()
    created = client.post(
        "/web/team-runs",
        json={
            "title": "Presentation Run",
            "selected_agents": ["planner", "writer", "critic", "manager", "coder"],
            "oversight_mode": "auto",
        },
    )
    run_id = created.json()["run"]["id"]
    planned = client.post(
        f"/web/team-runs/{run_id}/requests",
        json={"text": "청계천의 변화와 역사에 대한 발표자료 작성", "sender_name": "ceo"},
    )
    assert planned.status_code == 202
    board = planned.json()
    assert board["run"]["workflow_preset"] == "presentation_team"
    writer_task = next(task for task in board["tasks"] if task["owner_handle"] == "writer")
    critic_task = next(task for task in board["tasks"] if task["owner_handle"] == "critic")
    manager_task = next(task for task in board["tasks"] if task["artifact_goal"] == "final")
    assert "슬라이드" in writer_task["title"]
    assert critic_task["depends_on_titles"]
    assert writer_task["title"] in critic_task["depends_on_titles"]
    assert critic_task["title"] in manager_task["depends_on_titles"]


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
    assert critic_task["review_state"] == "approved"
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
            "oversight_mode": "manual",
        },
    )
    run_id = created.json()["run"]["id"]
    planned = client.post(
        f"/web/team-runs/{run_id}/requests",
        json={"text": "시장 동향 브리프를 작성해줘", "sender_name": "ceo"},
    )
    board = planned.json()
    assert board["run"]["status"] == "awaiting_plan_approval"
    assert board["run"]["plan_status"] == "awaiting_approval"
    approved_plan = client.post(
        f"/web/team-runs/{run_id}/plan/approve",
        json={"actor_handle": "manager"},
    )
    assert approved_plan.status_code == 200
    board = approved_plan.json()
    assert board["run"]["status"] == "awaiting_review"
    assert board["deliverable"]["artifact_type"] == "draft"
    critic_task = next(task for task in board["tasks"] if task["owner_handle"] == "critic")

    approved = client.patch(
        f"/web/tasks/{critic_task['id']}",
        json={"action": "approve_review", "actor_handle": "manager"},
    )
    assert approved.status_code == 200
    approved_payload = approved.json()
    approved_task = next(item for item in approved_payload["tasks"] if item["id"] == critic_task["id"])
    assert approved_task["review_state"] == "approved"
    assert approved_payload["run"]["status"] == "done"
    assert approved_payload["deliverable"]["artifact_type"] == "final"
    assert any(event["event_type"] == "review_approved" for event in approved_payload["activity"])
    final_before = approved_payload["deliverable"]["version"]

    rejected = client.patch(
        f"/web/tasks/{critic_task['id']}",
        json={"action": "reject_review", "actor_handle": "manager"},
    )
    assert rejected.status_code == 200
    rejected_payload = rejected.json()
    rejected_task = next(item for item in rejected_payload["tasks"] if item["id"] == critic_task["id"])
    assert rejected_task["review_state"] == "reviewed"
    assert rejected_payload["run"]["status"] == "awaiting_review"
    assert any(event["event_type"] == "review_rejected" for event in rejected_payload["activity"])


def test_team_run_manual_mode_waits_for_review_approval(client):
    _ensure_schema()
    created = client.post(
        "/web/team-runs",
        json={
            "title": "Manual Review Run",
            "selected_agents": ["planner", "writer", "critic", "manager"],
            "oversight_mode": "manual",
        },
    )
    run_id = created.json()["run"]["id"]
    planned = client.post(
        f"/web/team-runs/{run_id}/requests",
        json={"text": "운영 보고서를 작성해줘", "sender_name": "ceo"},
    )
    assert planned.status_code == 202
    board = planned.json()
    assert board["run"]["oversight_mode"] == "manual"
    assert board["run"]["status"] == "awaiting_plan_approval"
    assert board["run"]["plan_status"] == "awaiting_approval"
    assert board["deliverable"]["artifact_type"] == "brief"
    approved_plan = client.post(
        f"/web/team-runs/{run_id}/plan/approve",
        json={"actor_handle": "manager"},
    )
    assert approved_plan.status_code == 200
    board = approved_plan.json()
    assert board["run"]["status"] == "awaiting_review"
    assert board["deliverable"]["artifact_type"] == "draft"
    critic_task = next(task for task in board["tasks"] if task["owner_handle"] == "critic")
    manager_task = next(task for task in board["tasks"] if task["owner_handle"] == "manager")
    assert critic_task["review_state"] == "reviewed"
    assert manager_task["status"] == "todo"


def test_team_run_manual_mode_plan_reject_creates_planner_inbox_message(client):
    _ensure_schema()
    created = client.post(
        "/web/team-runs",
        json={
            "title": "Manual Plan Run",
            "selected_agents": ["planner", "writer", "critic", "manager"],
            "oversight_mode": "manual",
        },
    )
    run_id = created.json()["run"]["id"]
    planned = client.post(
        f"/web/team-runs/{run_id}/requests",
        json={"text": "운영 개요 초안을 작성해줘", "sender_name": "ceo"},
    )
    assert planned.status_code == 202
    assert planned.json()["run"]["status"] == "awaiting_plan_approval"

    rejected = client.post(
        f"/web/team-runs/{run_id}/plan/reject",
        json={"actor_handle": "manager", "reason": "구성 범위를 더 좁혀 주세요."},
    )
    assert rejected.status_code == 200
    payload = rejected.json()
    assert payload["run"]["status"] == "blocked"
    assert payload["run"]["plan_status"] == "rejected"
    assert any(item["to_handle"] == "planner" for item in payload["messages"])
    assert any(event["event_type"] == "plan_rejected" for event in payload["activity"])


def test_team_run_claim_and_release_task_updates_session_state(client):
    _ensure_schema()
    created = client.post(
        "/web/team-runs",
        json={
            "title": "Claim Run",
            "selected_agents": ["planner", "writer", "critic", "manager"],
            "oversight_mode": "manual",
        },
    )
    run_id = created.json()["run"]["id"]
    planned = client.post(
        f"/web/team-runs/{run_id}/requests",
        json={"text": "시장 브리프를 작성해줘", "sender_name": "ceo"},
    )
    assert planned.status_code == 202
    payload = planned.json()
    writer_task = next(task for task in payload["tasks"] if task["owner_handle"] == "writer")
    writer_session = next(item for item in payload["sessions"] if item["handle"] == "writer")

    claimed = client.post(
        f"/web/tasks/{writer_task['id']}/claim",
        json={"session_id": writer_session["id"]},
    )
    assert claimed.status_code == 200
    claim_payload = claimed.json()
    claimed_task = next(item for item in claim_payload["tasks"] if item["id"] == writer_task["id"])
    updated_session = next(item for item in claim_payload["sessions"] if item["id"] == writer_session["id"])
    assert claimed_task["claim_status"] == "claimed"
    assert claimed_task["claimed_by_session_id"] == writer_session["id"]
    assert updated_session["status"] == "busy"

    released = client.post(
        f"/web/tasks/{writer_task['id']}/release",
        json={},
    )
    assert released.status_code == 200
    released_payload = released.json()
    released_task = next(item for item in released_payload["tasks"] if item["id"] == writer_task["id"])
    released_session = next(item for item in released_payload["sessions"] if item["id"] == writer_session["id"])
    assert released_task["claim_status"] == "open"
    assert released_task["claimed_by_session_id"] is None
    assert released_session["status"] == "idle"


def test_team_run_rerun_hides_stale_final_until_new_final_is_published(client):
    _ensure_schema()
    created = client.post(
        "/web/team-runs",
        json={
            "title": "Stale Final Run",
            "selected_agents": ["planner", "writer", "critic", "manager"],
            "oversight_mode": "manual",
        },
    )
    run_id = created.json()["run"]["id"]
    planned = client.post(
        f"/web/team-runs/{run_id}/requests",
        json={"text": "시장 동향 브리프를 작성해줘", "sender_name": "ceo"},
    )
    approved_plan = client.post(
        f"/web/team-runs/{run_id}/plan/approve",
        json={"actor_handle": "manager"},
    )
    assert approved_plan.status_code == 200
    critic_task = next(task for task in approved_plan.json()["tasks"] if task["owner_handle"] == "critic")
    approved = client.patch(
        f"/web/tasks/{critic_task['id']}",
        json={"action": "approve_review", "actor_handle": "manager"},
    )
    assert approved.status_code == 200
    assert approved.json()["deliverable"]["artifact_type"] == "final"

    writer_task = next(task for task in approved.json()["tasks"] if task["owner_handle"] == "writer")
    rerun = client.patch(
        f"/web/tasks/{writer_task['id']}",
        json={"action": "rerun", "actor_handle": "planner"},
    )
    assert rerun.status_code == 200
    payload = rerun.json()
    assert payload["run"]["status"] == "awaiting_review"
    assert payload["deliverable"]["artifact_type"] == "draft"
    assert payload["deliverable"]["status"] == "active"


def test_team_run_auto_mode_rejects_then_approves_without_human(client):
    _ensure_schema()
    original_agents = orchestrator._agents
    original_loaded = orchestrator._loaded
    orchestrator._agents = {
        "planner": _FakeAgent("planner", []),
        "writer": _FakeAgent(
            "writer",
            [
                _agent_result("writer", "초안 1차 작성", None, done=True, artifact_type="draft", artifact_content="초안 v1"),
                _agent_result("writer", "초안 2차 작성", None, done=True, artifact_type="draft", artifact_content="초안 v2"),
            ],
        ),
        "critic": _FakeAgent(
            "critic",
            [
                _agent_result("critic", "출처 누락으로 반려 필요", None, done=True, artifact_type="review_notes", artifact_content="출처 누락으로 반려 필요"),
                _agent_result("critic", "검토 통과", None, done=True, artifact_type="review_notes", artifact_content="검토 통과"),
            ],
        ),
        "manager": _FakeAgent(
            "manager",
            [
                _agent_result("manager", "최종본 정리", None, done=True, artifact_type="final", artifact_content="최종본"),
            ],
        ),
    }
    orchestrator._loaded = True

    try:
        created = client.post(
            "/web/team-runs",
            json={
                "title": "Auto Review Run",
                "selected_agents": ["planner", "writer", "critic", "manager"],
                "oversight_mode": "auto",
            },
        )
        run_id = created.json()["run"]["id"]
        planned = client.post(
            f"/web/team-runs/{run_id}/requests",
            json={"text": "시장 분석 브리프를 작성해줘", "sender_name": "ceo"},
        )
        assert planned.status_code == 202
        board = planned.json()
        assert board["run"]["status"] == "done"
        assert board["deliverable"]["artifact_type"] == "final"
        critic_task = next(task for task in board["tasks"] if task["owner_handle"] == "critic")
        assert critic_task["review_state"] == "approved"
        assert any(event["event_type"] == "auto_review_started" for event in board["activity"])
        assert any(event["event_type"] == "review_rejected" for event in board["activity"])
        assert any(event["event_type"] == "review_approved" for event in board["activity"])
    finally:
        orchestrator._agents = original_agents
        orchestrator._loaded = original_loaded


def test_auto_review_rejection_feedback_is_injected_into_rerun_context(client):
    _ensure_schema()
    original_agents = orchestrator._agents
    original_loaded = orchestrator._loaded
    writer = _RecordingAgent(
        "writer",
        [
            _agent_result("writer", "초안 1차 작성", None, done=True, artifact_type="draft", artifact_content="초안 v1"),
            _agent_result("writer", "초안 2차 작성", None, done=True, artifact_type="draft", artifact_content="초안 v2"),
        ],
    )
    orchestrator._agents = {
        "planner": _FakeAgent("planner", []),
        "writer": writer,
        "critic": _FakeAgent(
            "critic",
            [
                _agent_result("critic", "출처 보강 필요", None, done=True, artifact_type="review_notes", artifact_content="출처가 부족하니 보강이 필요합니다."),
                _agent_result("critic", "검토 통과", None, done=True, artifact_type="review_notes", artifact_content="검토 통과"),
            ],
        ),
        "manager": _FakeAgent(
            "manager",
            [
                _agent_result("manager", "최종본 정리", None, done=True, artifact_type="final", artifact_content="최종본"),
            ],
        ),
    }
    orchestrator._loaded = True

    try:
        created = client.post(
            "/web/team-runs",
            json={
                "title": "Auto Feedback Run",
                "selected_agents": ["planner", "writer", "critic", "manager"],
                "oversight_mode": "auto",
            },
        )
        run_id = created.json()["run"]["id"]
        planned = client.post(
            f"/web/team-runs/{run_id}/requests",
            json={"text": "시장 분석 브리프를 작성해줘", "sender_name": "ceo"},
        )
        assert planned.status_code == 202
        assert len(writer.calls) >= 2
        assert "최근 자동 반려 피드백" in writer.calls[1]["user_request"]
        assert "출처가 부족하니 보강이 필요합니다." in writer.calls[1]["context"]
    finally:
        orchestrator._agents = original_agents
        orchestrator._loaded = original_loaded


def test_session_inbox_assignment_is_injected_and_consumed(client):
    _ensure_schema()
    original_agents = orchestrator._agents
    original_loaded = orchestrator._loaded
    writer = _RecordingAgent(
        "writer",
        [
            _agent_result("writer", "초안 작성 완료", None, done=True, artifact_type="draft", artifact_content="초안 v1"),
        ],
    )
    orchestrator._agents = {
        "planner": _FakeAgent("planner", []),
        "writer": writer,
        "critic": _FakeAgent(
            "critic",
            [
                _agent_result("critic", "검토 완료", None, done=True, artifact_type="review_notes", artifact_content="검토 완료"),
            ],
        ),
        "manager": _FakeAgent(
            "manager",
            [
                _agent_result("manager", "최종본 정리", None, done=True, artifact_type="final", artifact_content="최종본"),
            ],
        ),
    }
    orchestrator._loaded = True

    try:
        created = client.post(
            "/web/team-runs",
            json={
                "title": "Inbox Context Run",
                "selected_agents": ["planner", "writer", "critic", "manager"],
                "oversight_mode": "auto",
            },
        )
        run_id = created.json()["run"]["id"]
        planned = client.post(
            f"/web/team-runs/{run_id}/requests",
            json={"text": "서비스 소개 브리프를 작성해줘", "sender_name": "ceo"},
        )
        assert planned.status_code == 202
        board = planned.json()
        assert writer.calls
        assert "세션 inbox:" in writer.calls[0]["context"]
        assert "새 작업이 배정되었습니다." in writer.calls[0]["context"]
        writer_session = next(item for item in board["sessions"] if item["handle"] == "writer")
        assert "초안 작성" in writer_session["context_window_summary"] or "draft" in writer_session["context_window_summary"]
        writer_messages = [item for item in board["messages"] if item["to_handle"] == "writer"]
        assert writer_messages
        assert any(item["status"] == "read" for item in writer_messages)
    finally:
        orchestrator._agents = original_agents
        orchestrator._loaded = original_loaded


def test_team_run_retries_placeholder_artifact_and_uses_substantive_retry(client):
    _ensure_schema()
    original_agents = orchestrator._agents
    original_loaded = orchestrator._loaded
    orchestrator._agents = {
        "planner": _FakeAgent("planner", []),
        "writer": _FakeAgent(
            "writer",
            [
                _agent_result("writer", "Writer가 초안을 작성 중입니다.", None, done=True),
                _agent_result(
                    "writer",
                    "서울 야간관광 발표 초안 작성 완료",
                    None,
                    done=True,
                    artifact_type="draft",
                    artifact_content="## 서울 야간관광 활성화 전략\n- 핵심 제안 1\n- 핵심 제안 2\n- 실행 포인트 3",
                ),
            ],
        ),
        "critic": _FakeAgent(
            "critic",
            [
                _agent_result(
                    "critic",
                    "검토 완료",
                    None,
                    done=True,
                    artifact_type="review_notes",
                    artifact_content="좋은 점 1개\n문제점 2개\n개선 제안 1개",
                ),
            ],
        ),
        "manager": _FakeAgent(
            "manager",
            [
                _agent_result(
                    "manager",
                    "최종본 정리",
                    None,
                    done=True,
                    artifact_type="final",
                    artifact_content="서울 야간관광 발표 최종본",
                ),
            ],
        ),
    }
    orchestrator._loaded = True

    try:
        created = client.post(
            "/web/team-runs",
            json={
                "title": "Retry Placeholder Run",
                "selected_agents": ["planner", "writer", "critic", "manager"],
                "oversight_mode": "auto",
            },
        )
        run_id = created.json()["run"]["id"]
        planned = client.post(
            f"/web/team-runs/{run_id}/requests",
            json={"text": "서울 야간관광 활성화 전략 발표자료를 작성해줘", "sender_name": "ceo"},
        )
        assert planned.status_code == 202
        board = planned.json()
        assert board["run"]["status"] == "done"
        draft_artifacts = [item for item in board["artifacts"] if item["artifact_type"] == "draft"]
        assert any("서울 야간관광 활성화 전략" in item["content"] for item in draft_artifacts)
        assert all("작성 중입니다" not in item["content"] for item in draft_artifacts)
    finally:
        orchestrator._agents = original_agents
        orchestrator._loaded = original_loaded


def test_coerce_team_tasks_normalizes_manager_final_to_review_tail():
    tasks = _coerce_team_tasks(
        [
            {
                "title": "시장 리서치",
                "description": "기초 자료를 수집한다.",
                "owner_handle": "writer",
                "artifact_goal": "draft",
                "depends_on_titles": [],
            },
            {
                "title": "전략 설계",
                "description": "전략 초안을 정리한다.",
                "owner_handle": "manager",
                "artifact_goal": "",
                "depends_on_titles": ["시장 리서치"],
            },
            {
                "title": "크리틱",
                "description": "초안을 검토한다.",
                "owner_handle": "critic",
                "artifact_goal": "",
                "depends_on_titles": [],
            },
        ],
        ["planner", "writer", "critic", "manager"],
    )
    strategy = next(task for task in tasks if task["title"] == "전략 설계")
    critic = next(task for task in tasks if task["title"] == "크리틱")
    final_task = next(task for task in tasks if task["artifact_goal"] == "final")

    assert strategy["artifact_goal"] == "decision"
    assert "시장 리서치" in critic["depends_on_titles"]
    assert "전략 설계" not in critic["depends_on_titles"]
    assert "크리틱" in final_task["depends_on_titles"]


def test_parse_agent_payload_recovers_malformed_json_like_artifact_content():
    raw = """```json
{
  "visible_message": "서울 야간관광 발표 초안을 완성했습니다!",
  "suggested_next_agent": "critic",
  "handoff_reason": "초안 검토가 필요합니다.",
  "task_status": "draft_completed",
  "done": false,
  "needs_user_input": false,
  "confidence": 0.92,
  "artifact_update": {
    "type": "draft",
    "content": "# 서울 야간관광 활성화 전략\n\n## 슬라이드 1\n- 제목: "야간관광은 운영 시스템입니다"\n- 핵심 메시지 정리",
    "replace_latest": true
  }
}
```"""
    parsed = _parse_agent_payload(raw)
    assert parsed["visible_message"] == "서울 야간관광 발표 초안을 완성했습니다!"
    assert parsed["artifact_update"]["type"] == "draft"
    assert "서울 야간관광 활성화 전략" in parsed["artifact_update"]["content"]
    assert '"야간관광은 운영 시스템입니다"' in parsed["artifact_update"]["content"]


def test_parse_agent_payload_recovers_truncated_artifact_content():
    raw = """```json
{
  "visible_message": "초안 작성 완료",
  "suggested_next_agent": "critic",
  "handoff_reason": "검토 필요",
  "task_status": "draft 완료",
  "done": false,
  "needs_user_input": false,
  "artifact_update": {
    "type": "draft",
    "content": "# 서울 야간관광 활성화 전략\\n\\n## 슬라이드 1\\n- 핵심 메시지\\n- 발표 대본 일부"""
    parsed = _parse_agent_payload(raw)
    assert parsed["artifact_update"]["type"] == "draft"
    assert "서울 야간관광 활성화 전략" in parsed["artifact_update"]["content"]
    assert "발표 대본 일부" in parsed["artifact_update"]["content"]


def test_build_structured_deliverable_extracts_slides_and_sources():
    structured = _build_structured_deliverable(
        "서울 야간관광 발표",
        """# 서울 야간관광 발표

## 슬라이드 1
- 왜 지금 야간관광인가
- 체류시간 연장 전략

## 슬라이드 2
- 청계천, DDP, 한강을 연결한 동선
- 운영 시간과 안전 관리

## 참고 출처
- 서울관광재단 2025 https://example.com/source1
- 서울시 열린데이터광장 https://example.com/source2
""",
    )
    assert structured["title"] == "서울 야간관광 발표"
    assert len(structured["slide_outline"]) >= 2
    assert structured["slide_outline"][0]["title"] == "슬라이드 1"
    assert any("서울관광재단" in item for item in structured["sources"])


def test_build_structured_deliverable_extracts_speaker_notes_and_skips_meta_sections():
    structured = _build_structured_deliverable(
        "정책 발표",
        """# 정책 발표

## 슬라이드 1
- 정책 배경
- 실행 우선순위
- 발표 포인트: 숫자보다 실행 순서를 강조

## 참고 출처
- 서울시 2025 https://example.com/source

## 검토 반영 메모
- 이 섹션은 슬라이드로 들어가면 안 됨
""",
    )
    assert len(structured["slide_outline"]) == 1
    assert structured["slide_outline"][0]["speaker_notes"] == "숫자보다 실행 순서를 강조"
    assert any("서울시 2025" in item for item in structured["sources"])


def test_build_structured_deliverable_filters_internal_meta_bullets():
    structured = _build_structured_deliverable(
        "정책 발표",
        """# 정책 발표

## 슬라이드 1
- 정책 배경
- 검증 메모: 수치 근거 보강 필요
- 출처: 서울시 2025 https://example.com/source
- 발표 포인트: 실행 순서 중심으로 설명
""",
    )
    assert structured["slide_outline"][0]["bullets"] == ["정책 배경"]
    assert structured["slide_outline"][0]["speaker_notes"] == "실행 순서 중심으로 설명"
    assert any("서울시 2025" in item for item in structured["sources"])


def test_presentation_task_execution_contracts_are_role_specific():
    run = ConversationModel(title="발표 런", chat_id="x", platform="web")
    run.request_text = "서울 야간관광 활성화 전략 발표자료 작성"
    writer_task = TeamTaskModel(title="writer task", team_run_id=uuid.uuid4(), owner_handle="writer", artifact_goal="draft")
    coder_task = TeamTaskModel(title="coder task", team_run_id=uuid.uuid4(), owner_handle="coder", artifact_goal="draft")
    critic_task = TeamTaskModel(title="critic task", team_run_id=uuid.uuid4(), owner_handle="critic", artifact_goal="review_notes")
    manager_task = TeamTaskModel(title="manager task", team_run_id=uuid.uuid4(), owner_handle="manager", artifact_goal="final")

    writer_contract = _task_execution_contract(run=run, task=writer_task)
    coder_contract = _task_execution_contract(run=run, task=coder_task)
    critic_contract = _task_execution_contract(run=run, task=critic_task)
    manager_contract = _task_execution_contract(run=run, task=manager_task)

    assert "정확히 5~6개 슬라이드" in writer_contract
    assert "출처 확인 필요" in writer_contract
    assert "검증 메모" in coder_contract
    assert "출처:" in coder_contract
    assert "## 좋은 점" in critic_contract
    assert "정확히 5~6개 슬라이드" in manager_contract
    assert "raw critique를 그대로 복붙하지 말고" in manager_contract


def test_normalize_presentation_final_content_keeps_review_as_summary_not_slide():
    run = ConversationModel(title="발표 런", chat_id="x", platform="web")
    run.request_text = "서울 야간관광 활성화 전략 발표자료 작성"
    normalized = _normalize_presentation_final_content(
        run=run,
        content="""## 슬라이드 1
- 정책 배경
- 발표 포인트: 정책 순서를 강조

## 슬라이드 2
- 실행 구조
- 발표 포인트: 시범-확대 구조 설명

## 참고 출처
- 서울시 2025 https://example.com/source
""",
        review_bodies=["문제점 1\n문제점 2\n개선 제안 1"],
    )
    assert "## Slide Outline" in normalized
    assert normalized.count("# 발표 런") == 1
    assert "## 검토 메모" in normalized
    assert "발표자용 검토 반영 요약:" not in normalized


def test_done_with_risks_presentation_prefers_slide_draft_and_keeps_meta_out_of_slides():
    run = ConversationModel(title="리스크 발표 런", chat_id="x", platform="web")
    run.request_text = "서울 야간관광 활성화 전략 발표자료 작성"

    content = _build_done_with_risks_content(
        run=run,
        draft_bodies=[
            """## 슬라이드 1
- 정책 배경
- 발표 포인트: 정책 순서를 강조

## 슬라이드 2
- 실행 구조
- 발표 포인트: 시범-확대 구조 설명
""",
            """## 슬라이드 1
- 검증 메모: 수치 근거 보강 필요
- 출처: 서울시 2025 https://example.com/source
""",
        ],
        review_bodies=["문제점 1\n문제점 2\n개선 제안 1"],
        summary="근거 출처가 부족합니다.",
        risk_summary="질의응답에서 신뢰도 하락 위험이 있습니다.",
        rounds_used=2,
    )

    assert "## 작성 메모" in content
    assert "상태: done_with_risks" in content
    assert "## 검토 메모" in content
    structured = _build_structured_deliverable(run.title or "최종 발표자료", content)
    assert len(structured["slide_outline"]) == 2
    assert structured["slide_outline"][0]["title"] == "슬라이드 1"
    assert structured["slide_outline"][0]["speaker_notes"] == "정책 순서를 강조"
    assert not any("done_with_risks" in " ".join(slide["bullets"]) for slide in structured["slide_outline"])


def test_presentation_user_visible_markdown_strips_appendix_sections():
    cleaned = _presentation_user_visible_markdown(
        "발표 런",
        """# 발표 런

## 요청
서울 야간관광 활성화 전략 발표자료 작성

## Slide Outline

## 슬라이드 1. 정책 배경
- 야간 체류시간 확대
- 발표 포인트: 실행 순서 중심 설명

## 작성 메모
- 상태: done_with_risks
- 현재 판단: 근거 보강 필요

## 검토 메모
- 출처 보강 필요

## 참고 출처
- 서울시 2025 https://example.com/source
""",
    )
    assert "## 작성 메모" not in cleaned
    assert "## 검토 메모" not in cleaned
    assert "## 요청" not in cleaned
    assert "## Slide Outline" in cleaned
    assert "## 참고 출처" in cleaned


def test_presentation_final_is_normalized_into_slide_markdown(client):
    _ensure_schema()
    original_agents = orchestrator._agents
    original_loaded = orchestrator._loaded
    orchestrator._agents = {
        "planner": _FakeAgent("planner", []),
        "writer": _FakeAgent(
            "writer",
            [
                _agent_result(
                    "writer",
                    "슬라이드 초안 작성 완료",
                    None,
                    done=True,
                    artifact_type="draft",
                    artifact_content="## 슬라이드 1\n- 청계천 복원 배경\n- 도시재생 의미",
                ),
            ],
        ),
        "coder": _FakeAgent(
            "coder",
            [
                _agent_result(
                    "coder",
                    "출처 보강 완료",
                    None,
                    done=True,
                    artifact_type="draft",
                    artifact_content="## 참고 출처\n- 서울시 2025 https://example.com/source",
                ),
            ],
        ),
        "critic": _FakeAgent(
            "critic",
            [
                _agent_result(
                    "critic",
                    "검토 완료",
                    None,
                    done=True,
                    artifact_type="review_notes",
                    artifact_content="구성은 적절하나 출처를 마지막에 유지하세요.",
                ),
            ],
        ),
        "manager": _FakeAgent(
            "manager",
            [
                _agent_result(
                    "manager",
                    "최종 발표자료 정리",
                    None,
                    done=True,
                    artifact_type="final",
                    artifact_content="청계천 발표자료\n슬라이드 1: 복원 배경\n슬라이드 2: 현재 활용\n출처: 서울시 2025 https://example.com/source",
                ),
            ],
        ),
    }
    orchestrator._loaded = True

    try:
        created = client.post(
            "/web/team-runs",
            json={
                "title": "Presentation Final Run",
                "selected_agents": ["planner", "writer", "critic", "manager", "coder"],
                "oversight_mode": "manual",
            },
        )
        run_id = created.json()["run"]["id"]
        planned = client.post(
            f"/web/team-runs/{run_id}/requests",
            json={"text": "청계천의 변화와 역사에 대한 발표자료 작성", "sender_name": "ceo"},
        )
        assert planned.status_code == 202
        approved_plan = client.post(
            f"/web/team-runs/{run_id}/plan/approve",
            json={"actor_handle": "manager"},
        )
        assert approved_plan.status_code == 200
        critic_task = next(task for task in approved_plan.json()["tasks"] if task["owner_handle"] == "critic")
        approved = client.patch(
            f"/web/tasks/{critic_task['id']}",
            json={"action": "approve_review", "actor_handle": "manager"},
        )
        assert approved.status_code == 200
        deliverable = approved.json()["deliverable"]["content"]
        assert "## Slide Outline" in deliverable
        assert "## 참고 출처" in deliverable
        assert "청계천" in deliverable
    finally:
        orchestrator._agents = original_agents
        orchestrator._loaded = original_loaded


def test_team_run_can_export_docx_xlsx_and_pptx(client):
    _ensure_schema()
    created = client.post(
        "/web/team-runs",
        json={
            "title": "Export Run",
            "selected_agents": ["planner", "writer", "critic", "manager"],
            "oversight_mode": "auto",
        },
    )
    run_id = created.json()["run"]["id"]
    planned = client.post(
        f"/web/team-runs/{run_id}/requests",
        json={"text": "서울 역사 산책 발표자료를 작성해줘", "sender_name": "ceo"},
    )
    assert planned.status_code == 202

    docx_export = client.post(
        f"/web/team-runs/{run_id}/exports",
        json={"format": "docx"},
    )
    assert docx_export.status_code == 200
    docx_payload = docx_export.json()
    assert docx_payload["file"]["original_name"].endswith(".docx")

    docx_download = client.get(docx_payload["download_path"])
    assert docx_download.status_code == 200
    assert len(docx_download.content) > 0

    xlsx_export = client.post(
        f"/web/team-runs/{run_id}/exports",
        json={"format": "xlsx"},
    )
    assert xlsx_export.status_code == 200
    xlsx_payload = xlsx_export.json()
    assert xlsx_payload["file"]["original_name"].endswith(".xlsx")

    xlsx_download = client.get(xlsx_payload["download_path"])
    assert xlsx_download.status_code == 200
    assert len(xlsx_download.content) > 0

    pptx_export = client.post(
        f"/web/team-runs/{run_id}/exports",
        json={"format": "pptx"},
    )
    assert pptx_export.status_code == 200
    pptx_payload = pptx_export.json()
    assert pptx_payload["file"]["original_name"].endswith(".pptx")

    pptx_download = client.get(pptx_payload["download_path"])
    assert pptx_download.status_code == 200
    assert len(pptx_download.content) > 0


def test_team_run_request_accepts_source_files_and_exposes_source_ir_summary(client):
    _ensure_schema()
    project = client.post(
        "/api/projects",
        json={"name": "Source Files", "description": "team run"},
    )
    assert project.status_code == 200
    project_id = project.json()["id"]

    upload = client.post(
        f"/api/projects/{project_id}/files",
        files={
            "uploaded_file": (
                "source.docx",
                generate_report_docx("참고 문서", "## 배경\n- 청계천 역사\n## 현재 활용\n- 관광과 보행"),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )
    assert upload.status_code == 200
    file_id = upload.json()["id"]

    created = client.post(
        "/web/team-runs",
        json={
            "title": "Source IR Run",
            "selected_agents": ["planner", "writer", "critic", "manager"],
            "oversight_mode": "auto",
            "source_file_ids": [file_id],
        },
    )
    assert created.status_code == 201
    run_id = created.json()["run"]["id"]
    assert created.json()["run"]["source_file_ids"] == [file_id]
    assert created.json()["run"]["source_ir_summary"]

    planned = client.post(
        f"/web/team-runs/{run_id}/requests",
        json={
            "text": "이 파일을 참고해서 청계천 브리핑 문서를 재구성해줘",
            "sender_name": "ceo",
            "source_file_ids": [file_id],
        },
    )
    assert planned.status_code == 202
    payload = planned.json()
    assert payload["run"]["source_file_ids"] == [file_id]
    assert payload["run"]["source_ir_summary"]
    assert payload["source_files"][0]["document_type"] == "word"
    assert payload["source_files"][0]["document_ir"]["document_type"] == "word"


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


class _RecordingAgent(_FakeAgent):
    def __init__(self, handle: str, results: list[AgentResult]):
        super().__init__(handle, results)
        self.calls: list[dict[str, str]] = []

    async def run(self, user_request: str, context: str = "") -> AgentResult:
        self.calls.append({"user_request": user_request, "context": context})
        return await super().run(user_request, context)


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
