"""Route incoming messages to the appropriate execution path."""
from __future__ import annotations

from dataclasses import dataclass

from app.orchestrator.policies import (
    detect_mode_from_command,
    extract_mentioned_handle,
    extract_mentioned_handles,
    get_pipeline,
    is_collaboration_request,
    is_casual_message,
    is_social_collaboration_message,
)


@dataclass
class RoutingDecision:
    mode: str
    pipeline: list[str]
    direct_handle: str | None
    mentioned_handles: list[str]
    social_collaboration: bool
    new_mode: str | None  # non-None means mode was changed by this message


def route(
    text: str,
    current_mode: str,
    known_handles: set[str],
    forced_direct_handle: str | None = None,
    mention_aliases: dict[str, str] | None = None,
) -> RoutingDecision:
    # Check if this message changes the mode
    new_mode = detect_mode_from_command(text)
    effective_mode = new_mode if new_mode else current_mode
    if effective_mode == "pipeline":
        effective_mode = "autonomous-lite"

    # Explicit direct override (e.g., private chat with specific bot identity)
    if forced_direct_handle and forced_direct_handle in known_handles:
        return RoutingDecision(
            mode="direct",
            pipeline=get_pipeline("direct", forced_direct_handle),
            direct_handle=forced_direct_handle,
            mentioned_handles=[forced_direct_handle],
            social_collaboration=False,
            new_mode=new_mode,
        )

    # Mentions
    mentioned_handles = extract_mentioned_handles(
        text,
        known_handles,
        mention_aliases=mention_aliases,
    )

    # If multiple mentions or explicit collaboration intent, run team flow.
    if len(mentioned_handles) > 1 or is_collaboration_request(text):
        targets = mentioned_handles or [
            h for h in ("planner", "writer", "critic", "manager") if h in known_handles
        ]
        social_team = is_social_collaboration_message(text)
        if not social_team and "planner" in known_handles and "planner" not in targets:
            targets = ["planner", *targets]
        team_mode = effective_mode if effective_mode in ("autonomous-lite", "autonomous") else "autonomous-lite"
        return RoutingDecision(
            mode=team_mode,
            pipeline=[targets[0]] if targets else ["planner"],
            direct_handle=None,
            mentioned_handles=targets,
            social_collaboration=social_team,
            new_mode=new_mode,
        )

    # Single mention -> direct call
    direct_handle = extract_mentioned_handle(text, known_handles, mention_aliases=mention_aliases)
    if direct_handle:
        return RoutingDecision(
            mode="direct",
            pipeline=get_pipeline("direct", direct_handle),
            direct_handle=direct_handle,
            mentioned_handles=mentioned_handles,
            social_collaboration=False,
            new_mode=new_mode,
        )

    # 인사/잡담이면 PM(planner)만 응답
    if is_casual_message(text):
        return RoutingDecision(
            mode="direct",
            pipeline=["planner"],
            direct_handle="planner",
            mentioned_handles=[],
            social_collaboration=False,
            new_mode=new_mode,
        )

    # 작업 요청 → 전체 파이프라인
    pipeline = get_pipeline(effective_mode, None)
    if mentioned_handles and effective_mode in ("autonomous-lite", "autonomous"):
        if "planner" not in mentioned_handles:
            pipeline = ["planner", *mentioned_handles]
        else:
            pipeline = mentioned_handles

    return RoutingDecision(
        mode=effective_mode,
        pipeline=pipeline,
        direct_handle=direct_handle,
        mentioned_handles=mentioned_handles,
        social_collaboration=False,
        new_mode=new_mode,
    )
