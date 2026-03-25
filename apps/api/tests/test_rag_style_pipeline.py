"""TASK_02–04 RAG integration, retrieval policy, style layer 검증."""
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.database import Base
from app.models import DocumentChunkModel, ProjectModel, StylePatternModel
from app.services.chunking_service import Chunk, chunk_from_ir, chunk_from_text
from app.services.retriever import (
    RagResult,
    RetrievalStatus,
    build_rag_context,
)
from app.services.style_extractor import extract_and_store_style_patterns
from app.services.style_retriever import build_style_context
from app.services.vector_store import SQLiteKeywordStore
from app.orchestrator.retrieval_policy import (
    MAX_RETRIEVAL_RETRIES,
    apply_retrieval_policy,
)


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture()
def project(db):
    proj = ProjectModel(name="test-project", description="")
    db.add(proj)
    db.flush()
    return proj


# ── TASK_02: Chunking ──────────────────────────────────────────────────────

def test_chunk_from_text_splits_long_content():
    long = "가 " * 300
    chunks = chunk_from_text(long, file_name="doc.txt")
    assert len(chunks) > 1
    assert all(c.file_name == "doc.txt" for c in chunks)


def test_chunk_from_text_empty_input():
    assert chunk_from_text("") == []


def test_chunk_from_ir_uses_section_heading():
    ir = {
        "document_type": "word",
        "sections": [
            {
                "heading": "1장 개요",
                "page": 1,
                "blocks": [{"type": "paragraph", "text": "본 사업의 목적은 다음과 같습니다. " * 5}],
            }
        ],
    }
    chunks = chunk_from_ir(ir, file_name="plan.docx")
    assert chunks
    assert chunks[0].section == "1장 개요"


# ── TASK_02: VectorStore & Retriever ──────────────────────────────────────

def test_sqlite_store_add_and_search(db, project):
    chunks = chunk_from_text(
        "summary: 프로젝트 개요입니다. conclusion: 최종 결론입니다.",
        file_name="test.txt",
    )
    file_id = uuid.uuid4()
    store = SQLiteKeywordStore()
    count = store.add(chunks, file_id=file_id, project_id=project.id, db=db)
    assert count == len(chunks)

    hits = store.search("summary conclusion", project_id=project.id, source_file_ids=None, top_k=3, db=db)
    assert len(hits) > 0
    assert hits[0].score > 0


def test_source_file_filter_excludes_other_files(db, project):
    file_a = uuid.uuid4()
    file_b = uuid.uuid4()
    store = SQLiteKeywordStore()
    store.add(
        chunk_from_text("summary 내용 A", file_name="a.txt"),
        file_id=file_a,
        project_id=project.id,
        db=db,
    )
    store.add(
        chunk_from_text("conclusion 내용 B", file_name="b.txt"),
        file_id=file_b,
        project_id=project.id,
        db=db,
    )
    db.flush()

    hits = store.search("summary", project_id=project.id, source_file_ids=[file_a], top_k=5, db=db)
    assert all(str(h.chunk.file_id) == str(file_a) for h in hits)


def test_build_rag_context_empty_returns_empty_status(db, project):
    result = build_rag_context("아무것도 없음", project_id=project.id, db=db, top_k=3)
    assert result.retrieval_status == RetrievalStatus.EMPTY
    assert result.chunk_count == 0
    assert result.context_text == ""


def test_build_rag_context_ok_with_sufficient_hits(db, project):
    store = SQLiteKeywordStore()
    text = "summary conclusion overview 한국 사업 계획 구체적 목표"
    for i in range(4):
        store.add(
            chunk_from_text(text + f" 섹션{i}번", file_name=f"doc{i}.txt"),
            file_id=uuid.uuid4(),
            project_id=project.id,
            db=db,
        )
    db.flush()
    result = build_rag_context("summary conclusion overview", project_id=project.id, db=db, top_k=3)
    assert result.chunk_count > 0
    assert result.retrieval_status in (RetrievalStatus.OK, RetrievalStatus.WEAK)


# ── TASK_03: Retrieval Policy ─────────────────────────────────────────────

def test_policy_empty_routes_to_planner():
    r = apply_retrieval_policy(
        current_handle="writer",
        approved_next_agent="critic",
        visible_message="⚠️ 검색 결과가 없습니다. 참고 자료 없이 작성 중임을 명시하세요.",
        retrieval_status="EMPTY",
        retrieval_retry_count=0,
        fixed_chain_enabled=True,
    )
    assert r.override_next_agent == "planner"
    assert r.retrieval_retry_count == 1


def test_policy_weak_routes_to_planner():
    r = apply_retrieval_policy(
        current_handle="writer",
        approved_next_agent="critic",
        visible_message="",
        retrieval_status="WEAK",
        retrieval_retry_count=0,
        fixed_chain_enabled=True,
    )
    assert r.override_next_agent == "planner"


def test_policy_conflict_routes_to_critic_in_fixed_chain():
    r = apply_retrieval_policy(
        current_handle="writer",
        approved_next_agent="critic",
        visible_message="",
        retrieval_status="CONFLICT",
        retrieval_retry_count=0,
        fixed_chain_enabled=True,
    )
    assert r.override_next_agent == "critic"


def test_policy_ok_no_override():
    r = apply_retrieval_policy(
        current_handle="writer",
        approved_next_agent="critic",
        visible_message="",
        retrieval_status="OK",
        retrieval_retry_count=0,
        fixed_chain_enabled=True,
    )
    assert r.override_next_agent is None


def test_policy_retry_limit_stops_override():
    r = apply_retrieval_policy(
        current_handle="writer",
        approved_next_agent="critic",
        visible_message="",
        retrieval_status="EMPTY",
        retrieval_retry_count=MAX_RETRIEVAL_RETRIES,
        fixed_chain_enabled=True,
    )
    assert r.override_next_agent is None


def test_policy_ignores_non_writer():
    r = apply_retrieval_policy(
        current_handle="planner",
        approved_next_agent="writer",
        visible_message="",
        retrieval_status="EMPTY",
        retrieval_retry_count=0,
        fixed_chain_enabled=True,
    )
    assert r.override_next_agent is None


# ── TASK_04: Style Layer ──────────────────────────────────────────────────

def test_style_extraction_finds_patterns(db, project):
    text = (
        "따라서 본 사업계획서는 다음과 같은 목적을 가집니다. "
        "구체적으로 1차년도에는 핵심 기술을 개발하였습니다. "
        "결론적으로 이 사업은 지역 경제에 기여합니다. "
        "반면에 경쟁 업체들은 이 분야에 진입하지 않았습니다. "
        "이에 따라 시장 선점 기회가 존재합니다."
    )
    chunks = chunk_from_text(text, file_name="plan.docx")
    n = extract_and_store_style_patterns(chunks, project_id=project.id, source_file_id=None, db=db)
    assert n > 0


def test_style_context_default_mode_returns_empty(db, project):
    ctx = build_style_context(
        section="generate_report_draft",
        project_id=project.id,
        db=db,
        style_mode="default",
    )
    assert ctx == ""


def test_style_context_company_mode_returns_patterns(db, project):
    text = (
        "따라서 본 사업계획서는 다음과 같은 목적을 가집니다. "
        "구체적으로 1차년도에는 핵심 기술을 개발하였습니다. "
        "결론적으로 이 사업은 지역 경제에 기여합니다."
    )
    chunks = chunk_from_text(text, file_name="ref.docx")
    extract_and_store_style_patterns(chunks, project_id=project.id, source_file_id=None, db=db)
    db.flush()

    ctx = build_style_context(
        section="generate_report_draft",
        project_id=project.id,
        db=db,
        style_mode="company",
        strength="medium",
    )
    assert ctx != ""
    assert "[" in ctx  # 패턴 타입 표시 포함
