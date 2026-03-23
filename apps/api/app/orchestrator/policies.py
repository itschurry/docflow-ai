"""Agent routing policies per conversation mode."""
from __future__ import annotations

from app.agents.registry import PIPELINE_ORDER

# mode → ordered list of agent handles to execute
MODE_PIPELINES: dict[str, list[str]] = {
    "direct": [],          # filled dynamically from mention
    "pipeline": PIPELINE_ORDER,
    "debate": ["planner", "writer", "critic", "manager"],
    "artifact": ["planner", "writer", "manager"],
}


def get_pipeline(mode: str, direct_handle: str | None = None) -> list[str]:
    if mode == "direct":
        return [direct_handle] if direct_handle else []
    return MODE_PIPELINES.get(mode, PIPELINE_ORDER)


def extract_mentioned_handle(text: str, known_handles: set[str]) -> str | None:
    """Return the first agent handle mentioned with @handle syntax."""
    lower = text.lower()
    for handle in known_handles:
        if f"@{handle}" in lower:
            return handle
    return None


def detect_mode_from_command(text: str) -> str | None:
    """Parse /mode <value> command from text."""
    text = text.strip()
    if text.startswith("/mode "):
        parts = text.split(maxsplit=1)
        if len(parts) == 2:
            return parts[1].strip().lower()
    return None
