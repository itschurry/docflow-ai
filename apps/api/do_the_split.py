#!/usr/bin/env python3
"""
Split routes_orig_test.py into the routes/ package.
Uses AST to extract each function/class with its exact source text.
"""
import ast
import os
import sys

# Use routes_orig_test.py as the source (described as routes_backup.py in task)
BACKUP = "app/api/routes_orig_test.py"
OUT = "app/routes"

with open(BACKUP) as f:
    src = f.read()

lines = src.splitlines()
tree = ast.parse(src)

# Build ordered list of top-level nodes
top_nodes = [
    n for n in ast.iter_child_nodes(tree)
    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
]


def node_start(node):
    return node.decorator_list[0].lineno if node.decorator_list else node.lineno


# Build map: name -> (dec_start_lineno, end_lineno)  [1-indexed]
func_map = {}
for node in top_nodes:
    func_map[node.name] = (node_start(node), node.end_lineno)


def get_text(name):
    if name not in func_map:
        raise ValueError(f"Function/class {name!r} not found in {BACKUP}")
    dec_start, end = func_map[name]
    return "\n".join(lines[dec_start - 1: end])


# Find router = APIRouter() line (1-indexed)
router_line_idx = None  # 0-indexed position in lines[]
for i, line in enumerate(lines):
    if line.strip() == "router = APIRouter()":
        router_line_idx = i
        break
assert router_line_idx is not None, "Could not find 'router = APIRouter()'"

# imports_text: everything before `router = APIRouter()` line
imports_text = "\n".join(lines[:router_line_idx])

# constants_text: lines after `router = APIRouter()` until first function decorator
first_func_start = node_start(top_nodes[0])  # 1-indexed
constants_text = "\n".join(lines[router_line_idx + 1: first_func_start - 1])


def write_module(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)
    try:
        ast.parse(content)
        print(f"  ✓ {path}")
    except SyntaxError as e:
        print(f"  ✗ SYNTAX ERROR in {path}: {e}")
        sys.exit(1)


def join_funcs(names):
    """Extract and join function texts with double newlines between them."""
    parts = []
    for name in names:
        parts.append(get_text(name))
    return "\n\n\n".join(parts)


print("Generating routes/ package from", BACKUP)

# --------------------------------------------------------------------------
# __init__.py
# --------------------------------------------------------------------------
init_content = '''\
from fastapi import APIRouter

from app.routes.health import router as health_router
from app.routes.projects import router as projects_router
from app.routes.files import router as files_router
from app.routes.jobs import router as jobs_router
from app.routes.web_knowledge import router as web_knowledge_router
from app.routes.ops import router as ops_router
from app.routes.telegram import router as telegram_router
from app.routes.conversations import router as conversations_router
from app.routes.web_runs import router as web_runs_router
from app.routes.web_chats import router as web_chats_router

# Re-export internal helpers used by tests
from app.routes.web_runs import (
    _build_structured_deliverable,
    _build_done_with_risks_content,
    _normalize_presentation_final_content,
    _presentation_user_visible_markdown,
    _task_execution_contract,
    _coerce_team_tasks,
)

# Re-export patching targets so monkeypatch on `routes.<name>` works
from app.routes.web_runs import (
    anthropic_skills_available,
    openai_document_generation_available,
    AnthropicSkillsDocumentGenerator,
    OpenAIDocumentIRGenerator,
)
from app.core.config import settings

router = APIRouter()
router.include_router(health_router)
router.include_router(projects_router)
router.include_router(files_router)
router.include_router(jobs_router)
router.include_router(web_knowledge_router)
router.include_router(ops_router)
router.include_router(telegram_router)
router.include_router(conversations_router)
router.include_router(web_runs_router)
router.include_router(web_chats_router)

__all__ = [
    "router",
    "_build_structured_deliverable",
    "_build_done_with_risks_content",
    "_normalize_presentation_final_content",
    "_presentation_user_visible_markdown",
    "_task_execution_contract",
    "_coerce_team_tasks",
    "anthropic_skills_available",
    "openai_document_generation_available",
    "AnthropicSkillsDocumentGenerator",
    "OpenAIDocumentIRGenerator",
]
'''
write_module(f"{OUT}/__init__.py", init_content)

# --------------------------------------------------------------------------
# _shared.py  – imports + constants + 4 shared helpers (NO router = APIRouter())
# --------------------------------------------------------------------------
shared_funcs = [
    "_ensure_web_upload_project",
    "_file_analysis_payload",
    "_required_workflow_agents",
    "_normalize_web_selected_agents",
]
shared_content = (
    imports_text
    + "\n\n"
    + constants_text
    + "\n\n\n"
    + join_funcs(shared_funcs)
    + "\n"
)
write_module(f"{OUT}/_shared.py", shared_content)

# --------------------------------------------------------------------------
# health.py
# --------------------------------------------------------------------------
health_header = """\
from fastapi import APIRouter
from fastapi.responses import RedirectResponse

router = APIRouter()
"""
write_module(
    f"{OUT}/health.py",
    health_header + "\n\n" + join_funcs(["docs_redirect", "health"]) + "\n",
)

# --------------------------------------------------------------------------
# projects.py
# --------------------------------------------------------------------------
projects_header = """\
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import JobModel, ProjectModel
from app.schemas.request_response import (
    CreateProjectRequest,
    CreateProjectResponse,
    JobHistoryItem,
    ProjectJobsResponse,
)

router = APIRouter()
"""
write_module(
    f"{OUT}/projects.py",
    projects_header + "\n\n" + join_funcs(["create_project", "list_project_jobs"]) + "\n",
)

# --------------------------------------------------------------------------
# files.py
# --------------------------------------------------------------------------
files_header = """\
from pathlib import Path
import shutil
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.models import FileModel, ProjectModel
from app.schemas.request_response import UploadFileResponse
from app.services.document_ir import extract_text_from_ir, parse_document_to_ir, summarize_document_ir
from app.services.indexing_service import index_file
from ._shared import _ensure_web_upload_project, _file_analysis_payload

router = APIRouter()
"""
write_module(
    f"{OUT}/files.py",
    files_header + "\n\n" + join_funcs([
        "upload_file",
        "upload_web_file",
        "get_file_analysis",
        "download_file",
    ]) + "\n",
)

# --------------------------------------------------------------------------
# web_knowledge.py
# --------------------------------------------------------------------------
web_knowledge_header = """\
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import DocumentChunkModel, FileModel
from ._shared import _ensure_web_upload_project

router = APIRouter()
"""
write_module(
    f"{OUT}/web_knowledge.py",
    web_knowledge_header + "\n\n" + join_funcs([
        "list_web_knowledge",
        "list_web_knowledge_chunks",
        "delete_web_knowledge_file",
    ]) + "\n",
)

# --------------------------------------------------------------------------
# jobs.py
# --------------------------------------------------------------------------
jobs_header = """\
import threading
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal, get_db
from app.core.state_machine import JobStatus
from app.core.time_utils import now_utc
from app.models import FileModel, JobModel, ProjectModel, PromptLogModel, TaskModel
from app.schemas.plan import PlanResult
from app.schemas.request_response import (
    ArtifactSummary,
    CreateJobRequest,
    CreateJobResponse,
    JobDetailResponse,
    PromptLogSummary,
    TaskSummary,
)
from app.services.job_dispatcher import dispatch_job
from app.services.llm_router import get_llm_provider
from app.services.planner_agent import PlannerAgent

router = APIRouter()
"""
write_module(
    f"{OUT}/jobs.py",
    jobs_header + "\n\n" + join_funcs([
        "create_job",
        "get_job",
        "get_job_artifacts",
        "stream_job_status",
        "retry_job",
        "get_job_prompt_logs",
    ]) + "\n",
)

# --------------------------------------------------------------------------
# ops.py  – helpers first, then route handlers
# --------------------------------------------------------------------------
ops_header = """\
import hashlib
import hmac
import json
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.time_utils import now_utc
from app.core.state_machine import JobStatus
from app.models import JobModel
from app.schemas.request_response import (
    CreateOpsApiKeyRequest,
    CreateOpsApiKeyResponse,
    DeadLetterItem,
    DeadLetterListResponse,
    DeadLetterReplayRequest,
    DeadLetterReplayResponse,
    ReplayAuditItem,
    ReplayAuditListResponse,
)
from app.services.job_dispatcher import dispatch_job

router = APIRouter()
"""
write_module(
    f"{OUT}/ops.py",
    ops_header + "\n\n" + join_funcs([
        "_secret_hash",
        "_has_active_ops_keys",
        "_authorize_ops_request",
        "_replay_marker_path",
        "_append_replay_audit",
        "list_dead_letters",
        "replay_dead_letter",
        "create_ops_api_key",
        "list_replay_audit",
    ]) + "\n",
)

# --------------------------------------------------------------------------
# telegram.py
# --------------------------------------------------------------------------
telegram_header = """\
from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.adapters.telegram.handlers import process_update
from app.core.config import settings
from app.core.database import get_db

router = APIRouter()
"""
write_module(
    f"{OUT}/telegram.py",
    telegram_header + "\n\n" + join_funcs([
        "telegram_webhook",
        "setup_telegram_webhook",
    ]) + "\n",
)

# --------------------------------------------------------------------------
# conversations.py
# --------------------------------------------------------------------------
conversations_header = """\
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.conversations.service import ConversationService
from app.conversations.serializer import (
    serialize_agent_run,
    serialize_conversation,
    serialize_message,
    serialize_team_run,
)

router = APIRouter()
"""
write_module(
    f"{OUT}/conversations.py",
    conversations_header + "\n\n" + join_funcs([
        "get_conversation",
        "list_conversation_messages",
        "list_conversation_runs",
        "stop_conversation",
    ]) + "\n",
)

# --------------------------------------------------------------------------
# web_runs.py
# --------------------------------------------------------------------------
# All helpers from lines ~147-522 (excluding ops helpers and _shared helpers)
web_runs_helpers_block1 = [
    "_normalize_oversight_mode",
    "_normalize_output_type",
    "_infer_output_type_from_request_text",
    "_summarize_workspace_title",
    "_normalize_auto_review_max_rounds",
    "_run_auto_review_max_rounds",
    "_infer_task_kind",
    "_slugify_filename",
    "_ensure_team_export_project",
    # skip _ensure_web_upload_project -> _shared.py
    "_persist_team_export_file",
    "_ensure_files_document_columns",
    # skip _file_analysis_payload -> _shared.py
    "_collect_source_files",
    "_build_deliverable_ir",
    "_build_xlsx_ir_from_markdown",
    # skip ops helpers (_has_active_ops_keys etc.) -> ops.py
]

# Route handlers
web_runs_routes = [
    "list_web_chats",
    "list_web_agents",
    "list_web_team_runs",
    "create_web_team_run",
    "get_web_team_run",
    "delete_web_team_run",
    "get_web_team_run_board",
    "get_web_team_run_activity",
    "get_web_team_run_sessions",
    "spawn_web_team_run_session",
    "get_web_team_run_messages",
    "create_web_team_run_message",
    "claim_web_team_task",
    "release_web_team_task_claim",
    "create_web_team_task",
    "get_web_team_task_detail",
    "update_web_team_run_agents",
    "send_web_team_run_request",
    "approve_web_team_run_plan",
    "reject_web_team_run_plan",
    "update_web_team_task",
    "update_web_team_task_dependencies",
]

# Helpers from lines ~2428 to ~5338 (excluding _normalize_web_selected_agents,
# _required_workflow_agents, _build_workspace_snapshot)
web_runs_helpers_block2 = [
    "_artifact_payload",
    "_latest_active_task_artifact",
    "_extract_web_deliverable",
    "_extract_sources_from_text",
    "_build_ppt_slides_from_text",
    "_build_structured_deliverable",
    "_structured_deliverable_to_markdown",
    "_normalize_presentation_final_content",
    "_select_presentation_primary_body",
    "_presentation_user_visible_markdown",
]

# After export_web_team_run_deliverable route
web_runs_helpers_block3 = [
    "_compact_progress_text",
    "_looks_like_jsonish_text",
    "_normalize_text_block",
    "_provider_error_http_exception",
    "_is_status_only_artifact",
    "_best_effort_result_content",
    "_task_execution_contract",
    # skip _required_workflow_agents -> _shared.py
    # skip _normalize_web_selected_agents -> _shared.py
    "_default_session_specs",
    "_ensure_team_run_sessions",
    "_ensure_session_for_handle",
    "_create_team_inbox_message",
    "_infer_team_workflow_preset",
    "_run_workflow_preset",
    "_presentation_team_tasks",
    "_docx_team_tasks",
    "_xlsx_team_tasks",
    "_default_team_tasks",
    "_normalize_team_tasks",
    "_coerce_team_tasks",
    "_decompose_team_request",
    "_review_snapshot",
    "_is_review_gate_task",
    "_reconcile_review_gate_tasks",
    "_dependency_review_satisfied",
    "_eligible_ready_tasks",
    "_select_idle_session_for_task",
    "_claim_ready_tasks_for_idle_sessions",
    "_session_inbox_context",
    "_build_team_board_snapshot",
    "_serialize_team_task_snapshot",
    "_build_team_task_detail",
    "_fallback_task_artifact_content",
    "_latest_rework_feedback_for_task",
    "_execute_team_task",
    "_review_rejection_count",
    "_find_final_task",
    "_manager_auto_review_decision",
    "_build_done_with_risks_content",
    "_publish_done_with_risks",
    "_escalate_auto_review_to_manual",
    "_maybe_auto_review_task",
    "_bg_run_scheduler",
    "_run_team_scheduler",
    "_refresh_team_run_status",
    "_coerce_optional_uuid",
    "_coerce_uuid_list",
    "_validate_task_owner",
    "_validate_artifact_goal",
    "_validate_dependency_ids",
    "_creates_dependency_cycle",
    "_task_has_review_notes",
    "_task_is_ready",
    "_reset_team_task_branch",
    "_supersede_reopened_task_artifacts",
    "_reopen_review_branch",
    "_progress_summary_for_message",
    "_format_progress_label",
    "_build_progress_steps",
    # skip _build_workspace_snapshot -> web_chats.py
]

web_runs_header = """\
import asyncio
from pathlib import Path
import re
import sys as _sys
import uuid
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal, get_db
from app.core.time_utils import now_utc
from app import conversation_models
from app.conversations.service import ConversationService
from app.conversations.serializer import (
    serialize_agent_run,
    serialize_artifact,
    serialize_conversation,
    serialize_message,
    serialize_team_activity,
    serialize_team_dependency,
    serialize_team_message,
    serialize_team_run,
    serialize_team_session,
    serialize_team_task,
)
from app.models import DocumentChunkModel, FileModel, ProjectModel, TaskModel
from app.orchestrator.engine import orchestrator
from app.team_runtime.service import TeamRunService
from app.services.anthropic_skills import (
    AnthropicSkillsDocumentGenerator as _AnthropicSkillsDocumentGenerator_impl,
    anthropic_skills_available as _anthropic_skills_available_impl,
    default_document_provider,
)
from app.services.document_ir import (
    build_slides_ir,
    build_sheet_ir_from_outline,
    build_word_ir_from_markdown,
    extract_text_from_ir,
    parse_document_to_ir,
    render_ir_to_docx_bytes,
    render_ir_to_pptx_bytes,
    render_ir_to_xlsx_bytes,
    summarize_document_ir,
)
from app.services.llm_router import get_llm_provider
from app.services.openai_document_generator import (
    OpenAIDocumentIRGenerator as _OpenAIDocumentIRGenerator_impl,
    openai_document_generation_available as _openai_document_generation_available_impl,
)
from ._shared import (
    _file_analysis_payload,
    _normalize_web_selected_agents,
    TEAM_EXPORT_PROJECT_NAME,
    AUTO_REVIEW_MAX_ROUNDS,
    AUTO_REVIEW_MAX_ROUNDS_MIN,
    AUTO_REVIEW_MAX_ROUNDS_MAX,
    AUTO_REVIEW_REJECT_KEYWORDS,
    TEAM_ARTIFACT_STATUS_PHRASES,
    PRESENTATION_REQUEST_KEYWORDS,
    SHEET_REQUEST_KEYWORDS,
    OUTPUT_TYPE_PRESET_MAP,
    WEB_UPLOAD_PROJECT_NAME,
)


# ---------------------------------------------------------------------------
# Proxy callables so that monkeypatch on `app.routes.<name>` is forwarded
# to the live lookup used inside route handlers (testability shim).
# ---------------------------------------------------------------------------
class _RouteProxy:
    \"\"\"Callable proxy that delegates through app.routes at call time.\"\"\"

    def __init__(self, attr_name: str, default_impl):
        self._attr_name = attr_name
        self._default_impl = default_impl

    def __call__(self, *args, **kwargs):
        pkg = _sys.modules.get("app.routes")
        if pkg is not None:
            fn = pkg.__dict__.get(self._attr_name)
            if fn is not None and fn is not self:
                return fn(*args, **kwargs)
        return self._default_impl(*args, **kwargs)

    def __repr__(self):
        return f"_RouteProxy({self._attr_name!r})"


anthropic_skills_available = _RouteProxy(
    "anthropic_skills_available", _anthropic_skills_available_impl
)
openai_document_generation_available = _RouteProxy(
    "openai_document_generation_available", _openai_document_generation_available_impl
)
AnthropicSkillsDocumentGenerator = _RouteProxy(
    "AnthropicSkillsDocumentGenerator", _AnthropicSkillsDocumentGenerator_impl
)
OpenAIDocumentIRGenerator = _RouteProxy(
    "OpenAIDocumentIRGenerator", _OpenAIDocumentIRGenerator_impl
)

router = APIRouter()
"""

# Build web_runs.py content: helpers_block1, then routes (including
# export_web_team_run_deliverable interleaved), then helpers_block2/3
# We follow the order from routes_backup.py:
# helpers_block1, routes (list_web_chats...update_web_team_task_dependencies),
# helpers_block2, export_web_team_run_deliverable, helpers_block3

web_runs_sections = []
web_runs_sections.append(join_funcs(web_runs_helpers_block1))
web_runs_sections.append(join_funcs(web_runs_routes))
web_runs_sections.append(join_funcs(web_runs_helpers_block2))
web_runs_sections.append(get_text("export_web_team_run_deliverable"))
web_runs_sections.append(join_funcs(web_runs_helpers_block3))

web_runs_content = web_runs_header + "\n\n" + "\n\n\n".join(web_runs_sections) + "\n"
write_module(f"{OUT}/web_runs.py", web_runs_content)

# --------------------------------------------------------------------------
# web_chats.py
# --------------------------------------------------------------------------
web_chats_header = """\
import uuid
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.time_utils import now_utc
from app import conversation_models
from app.adapters.telegram.dispatcher import DispatchResult
from app.conversations.service import ConversationService
from app.conversations.serializer import (
    serialize_artifact,
    serialize_conversation,
    serialize_message,
)
from app.orchestrator.engine import orchestrator
from app.team_runtime.service import TeamRunService
from ._shared import (
    _normalize_web_selected_agents,
    WEB_UPLOAD_PROJECT_NAME,
)
from .web_runs import _build_progress_steps, _extract_web_deliverable

router = APIRouter()
"""
web_chats_content = (
    web_chats_header
    + "\n\n"
    + join_funcs([
        "_WebDispatcher",
        "_build_workspace_snapshot",
        "create_web_chat",
        "update_web_chat_agents",
        "get_web_chat_deliverable",
        "get_web_chat_workspace",
        "send_web_chat_message",
        "list_agents",
        "reload_agent_config",
    ])
    + "\n"
)
write_module(f"{OUT}/web_chats.py", web_chats_content)

print("\nAll modules written successfully.")

# Final route count check
total_routes = 0
import importlib.util

for fname, rname in [
    ("health.py", "health"),
    ("projects.py", "projects"),
    ("files.py", "files"),
    ("jobs.py", "jobs"),
    ("web_knowledge.py", "web_knowledge"),
    ("ops.py", "ops"),
    ("telegram.py", "telegram"),
    ("conversations.py", "conversations"),
    ("web_runs.py", "web_runs"),
    ("web_chats.py", "web_chats"),
]:
    with open(f"{OUT}/{fname}") as f:
        module_src = f.read()
    module_tree = ast.parse(module_src)
    count = 0
    for node in ast.walk(module_tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                if (isinstance(dec, ast.Call)
                        and isinstance(dec.func, ast.Attribute)
                        and isinstance(dec.func.value, ast.Name)
                        and dec.func.value.id == "router"):
                    count += 1
    total_routes += count
    print(f"  {fname}: {count} routes")

print(f"\nTotal routes: {total_routes} (expected 57)")
if total_routes != 57:
    print("ERROR: route count mismatch!", file=sys.stderr)
    sys.exit(1)
