"""Orchestrator retrieval policy (TASK_03).

orchestrator가 retrieval_status 기반으로 next-agent를 결정하는 정책 계층.

정책:
  OK      → 정상 체인 진행
  WEAK    → planner 재정의 (최대 MAX_RETRIEVAL_RETRIES 회)
  EMPTY   → planner 복귀 후 source selection 안내
  CONFLICT → critic 또는 planner로 근거 충돌 정리

무한 루프 방지: retrieval_retry_count >= MAX_RETRIEVAL_RETRIES 시 정상 체인 진행.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

MAX_RETRIEVAL_RETRIES = 2

# 메시지에서 retrieval 상태를 감지하는 키워드
_WEAK_SIGNALS = ("⚠️ 검색 결과가 약합니다", "검증 필요", "근거 부족")
_EMPTY_SIGNALS = ("⚠️ 검색 결과가 없습니다", "참고 자료 없이")
_CONFLICT_SIGNALS = ("⚠️ 출처 간 내용 충돌",)


def _detect_status_from_message(text: str) -> str | None:
    """작성된 텍스트에서 retrieval status 신호를 추출한다."""
    if any(s in text for s in _EMPTY_SIGNALS):
        return "EMPTY"
    if any(s in text for s in _CONFLICT_SIGNALS):
        return "CONFLICT"
    if any(s in text for s in _WEAK_SIGNALS):
        return "WEAK"
    return None


@dataclass
class RetrievalPolicyResult:
    override_next_agent: str | None
    retrieval_retry_count: int
    reason: str


def apply_retrieval_policy(
    *,
    current_handle: str,
    approved_next_agent: str | None,
    visible_message: str,
    retrieval_status: str | None,
    retrieval_retry_count: int,
    fixed_chain_enabled: bool,
) -> RetrievalPolicyResult:
    """writer 턴 완료 후 retrieval 상태 기반 next-agent를 보정한다.

    Args:
        current_handle: 현재 실행 에이전트 핸들
        approved_next_agent: orchestrator가 결정한 next agent
        visible_message: 에이전트 출력 텍스트
        retrieval_status: RAG 결과 상태값 (OK|WEAK|EMPTY|CONFLICT|None)
        retrieval_retry_count: 현재까지 retrieval 재시도 횟수
        fixed_chain_enabled: 고정 체인 모드 여부

    Returns:
        RetrievalPolicyResult (override_next_agent이 None이면 원래 결정 유지)
    """
    # writer 턴이 아니면 정책 적용 안 함
    if current_handle != "writer":
        return RetrievalPolicyResult(
            override_next_agent=None,
            retrieval_retry_count=retrieval_retry_count,
            reason="not_writer",
        )

    # 최대 재시도 초과 — 정상 진행
    if retrieval_retry_count >= MAX_RETRIEVAL_RETRIES:
        logger.info(
            "Retrieval retry limit reached (%d). Proceeding with normal chain.",
            retrieval_retry_count,
        )
        return RetrievalPolicyResult(
            override_next_agent=None,
            retrieval_retry_count=retrieval_retry_count,
            reason="retry_limit_reached",
        )

    # retrieval_status 우선, 없으면 visible_message에서 감지
    status = retrieval_status or _detect_status_from_message(visible_message or "")

    if status == "EMPTY":
        logger.info("Retrieval EMPTY — routing back to planner for source refinement.")
        return RetrievalPolicyResult(
            override_next_agent="planner",
            retrieval_retry_count=retrieval_retry_count + 1,
            reason="retrieval_empty_planner_fallback",
        )

    if status == "WEAK":
        logger.info("Retrieval WEAK — routing back to planner for query refinement.")
        return RetrievalPolicyResult(
            override_next_agent="planner",
            retrieval_retry_count=retrieval_retry_count + 1,
            reason="retrieval_weak_planner_retry",
        )

    if status == "CONFLICT":
        # 충돌 시 critic 또는 planner로 라우팅
        next_agent = "critic" if fixed_chain_enabled else "planner"
        logger.info("Retrieval CONFLICT — routing to %s for reconciliation.", next_agent)
        return RetrievalPolicyResult(
            override_next_agent=next_agent,
            retrieval_retry_count=retrieval_retry_count + 1,
            reason="retrieval_conflict_reconciliation",
        )

    # OK 또는 감지 못 함 — 원래 결정 유지
    return RetrievalPolicyResult(
        override_next_agent=None,
        retrieval_retry_count=retrieval_retry_count,
        reason="retrieval_ok",
    )
