from enum import StrEnum


class ConversationStatus(StrEnum):
    IDLE = "idle"
    RECEIVED = "received"
    PLANNED = "planned"
    RUNNING = "running"
    WAITING_REVIEW = "waiting_review"
    SUMMARIZING = "summarizing"
    EXPORTING = "exporting"
    DONE = "done"
    FAILED = "failed"
    PAUSED = "paused"


_ALLOWED: dict[str, set[str]] = {
    ConversationStatus.IDLE: {
        ConversationStatus.RECEIVED,
        ConversationStatus.PAUSED,
    },
    ConversationStatus.RECEIVED: {
        ConversationStatus.PLANNED,
        ConversationStatus.RUNNING,
        ConversationStatus.FAILED,
    },
    ConversationStatus.PLANNED: {
        ConversationStatus.RUNNING,
        ConversationStatus.FAILED,
    },
    ConversationStatus.RUNNING: {
        ConversationStatus.WAITING_REVIEW,
        ConversationStatus.SUMMARIZING,
        ConversationStatus.DONE,
        ConversationStatus.FAILED,
        ConversationStatus.PAUSED,
    },
    ConversationStatus.WAITING_REVIEW: {
        ConversationStatus.RUNNING,
        ConversationStatus.SUMMARIZING,
        ConversationStatus.DONE,
    },
    ConversationStatus.SUMMARIZING: {
        ConversationStatus.EXPORTING,
        ConversationStatus.DONE,
        ConversationStatus.FAILED,
    },
    ConversationStatus.EXPORTING: {
        ConversationStatus.DONE,
        ConversationStatus.FAILED,
    },
    ConversationStatus.DONE: {ConversationStatus.IDLE},
    ConversationStatus.FAILED: {ConversationStatus.IDLE, ConversationStatus.RECEIVED},
    ConversationStatus.PAUSED: {ConversationStatus.RECEIVED, ConversationStatus.IDLE},
}


def can_transition(current: str, target: str) -> bool:
    return target in _ALLOWED.get(current, set())


def ensure_transition(current: str, target: str) -> None:
    if not can_transition(current, target):
        raise ValueError(f"Invalid conversation transition: {current} → {target}")
