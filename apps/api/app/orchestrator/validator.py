"""Validation layer for agent-suggested dynamic handoff."""
from __future__ import annotations

from dataclasses import dataclass


PM_HANDLE = "planner"


@dataclass
class HandoffValidation:
    approved_next_agent: str | None
    fallback_applied: bool
    validation_reason: str
    terminate: bool
    termination_reason: str | None


def detect_progress(previous_task_status: str | None, current_task_status: str | None, done: bool) -> bool:
    if done:
        return True
    prev = (previous_task_status or "").strip()
    cur = (current_task_status or "").strip()
    return bool(cur and cur != prev)


def validate_dynamic_handoff(
    *,
    mode: str,
    known_handles: set[str],
    current_agent: str,
    suggested_next_agent: str | None,
    expected_next_handle: str | None,
    required_artifact_types: set[str] | None,
    produced_artifact_type: str | None,
    done: bool,
    needs_user_input: bool,
    turn_index: int,
    max_turns: int,
    history_agents: list[str],
    same_agent_streak_limit: int,
    recent_pattern_repeat_limit: int,
    no_progress_streak: int,
    max_no_progress_handoffs: int,
) -> HandoffValidation:
    if mode == "guided":
        mode = "autonomous-lite"
    candidate = (suggested_next_agent or "").strip() or None

    if required_artifact_types and produced_artifact_type not in required_artifact_types:
        return HandoffValidation(
            approved_next_agent=None,
            fallback_applied=False,
            validation_reason="missing_required_artifact",
            terminate=True,
            termination_reason="missing_required_artifact",
        )

    if needs_user_input:
        return HandoffValidation(
            approved_next_agent=None,
            fallback_applied=False,
            validation_reason="needs_user_input",
            terminate=True,
            termination_reason="needs_user_input",
        )

    if expected_next_handle:
        if expected_next_handle not in known_handles:
            return HandoffValidation(
                approved_next_agent=None,
                fallback_applied=False,
                validation_reason="missing_required_team_member",
                terminate=True,
                termination_reason="missing_required_team_member",
            )
        return HandoffValidation(
            approved_next_agent=expected_next_handle,
            fallback_applied=candidate != expected_next_handle,
            validation_reason="fixed_chain",
            terminate=False,
            termination_reason=None,
        )

    if done:
        if mode == "autonomous-lite" and current_agent != PM_HANDLE:
            return HandoffValidation(
                approved_next_agent=PM_HANDLE,
                fallback_applied=True,
                validation_reason="done_requires_pm_in_lite",
                terminate=False,
                termination_reason=None,
            )
        return HandoffValidation(
            approved_next_agent=None,
            fallback_applied=False,
            validation_reason="done",
            terminate=True,
            termination_reason="done",
        )

    if turn_index + 1 >= max_turns:
        return HandoffValidation(
            approved_next_agent=None,
            fallback_applied=False,
            validation_reason="max_turns",
            terminate=True,
            termination_reason="max_turns",
        )

    fallback_applied = False
    reason = "accepted"

    if candidate and candidate not in known_handles:
        candidate = None
        fallback_applied = True
        reason = "unknown_agent_fallback_pm"

    if candidate is None:
        candidate = PM_HANDLE
        fallback_applied = True
        reason = "empty_suggestion_fallback_pm"

    streak = _tail_streak(history_agents, current_agent)
    if candidate == current_agent and same_agent_streak_limit >= 0 and streak > same_agent_streak_limit:
        candidate = PM_HANDLE
        fallback_applied = True
        reason = "same_agent_streak_limit_fallback_pm"

    if (
        candidate is not None
        and recent_pattern_repeat_limit > 0
        and _would_repeat_recent_pattern(history_agents, candidate)
    ):
        candidate = PM_HANDLE
        fallback_applied = True
        reason = "pattern_repeat_fallback_pm"

    if no_progress_streak > max_no_progress_handoffs and current_agent != PM_HANDLE:
        candidate = PM_HANDLE
        fallback_applied = True
        reason = "no_progress_fallback_pm"

    return HandoffValidation(
        approved_next_agent=candidate,
        fallback_applied=fallback_applied,
        validation_reason=reason,
        terminate=False,
        termination_reason=None,
    )


def _tail_streak(history_agents: list[str], handle: str) -> int:
    count = 0
    for item in reversed(history_agents):
        if item != handle:
            break
        count += 1
    return count


def _would_repeat_recent_pattern(history_agents: list[str], candidate: str) -> bool:
    seq = history_agents + [candidate]
    if len(seq) < 4:
        return False
    return seq[-4:-2] == seq[-2:]
