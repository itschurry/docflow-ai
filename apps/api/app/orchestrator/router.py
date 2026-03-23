"""Route incoming messages to the appropriate execution path."""
from __future__ import annotations

from dataclasses import dataclass

from app.orchestrator.policies import (
    detect_mode_from_command,
    extract_mentioned_handle,
    get_pipeline,
    is_casual_message,
)


@dataclass
class RoutingDecision:
    mode: str
    pipeline: list[str]
    direct_handle: str | None
    new_mode: str | None  # non-None means mode was changed by this message


def route(
    text: str,
    current_mode: str,
    known_handles: set[str],
) -> RoutingDecision:
    # Check if this message changes the mode
    new_mode = detect_mode_from_command(text)
    effective_mode = new_mode if new_mode else current_mode

    # Check for direct mention (@handle)
    direct_handle = extract_mentioned_handle(text, known_handles)
    if direct_handle:
        effective_mode = "direct"
        return RoutingDecision(
            mode=effective_mode,
            pipeline=get_pipeline(effective_mode, direct_handle),
            direct_handle=direct_handle,
            new_mode=new_mode,
        )

    # 인사/잡담이면 PM(planner)만 응답
    if is_casual_message(text):
        return RoutingDecision(
            mode="direct",
            pipeline=["planner"],
            direct_handle="planner",
            new_mode=new_mode,
        )

    # 작업 요청 → 전체 파이프라인
    pipeline = get_pipeline(effective_mode, direct_handle)

    return RoutingDecision(
        mode=effective_mode,
        pipeline=pipeline,
        direct_handle=direct_handle,
        new_mode=new_mode,
    )
