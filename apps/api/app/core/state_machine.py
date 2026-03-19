from enum import StrEnum


class JobStatus(StrEnum):
    DRAFT = "DRAFT"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


_ALLOWED_TRANSITIONS: dict[JobStatus, set[JobStatus]] = {
    JobStatus.DRAFT: {JobStatus.QUEUED, JobStatus.CANCELLED},
    JobStatus.QUEUED: {JobStatus.RUNNING, JobStatus.CANCELLED, JobStatus.FAILED},
    JobStatus.RUNNING: {
        JobStatus.REVIEW_REQUIRED,
        JobStatus.COMPLETED,
        JobStatus.FAILED,
        JobStatus.CANCELLED,
    },
    JobStatus.REVIEW_REQUIRED: {JobStatus.RUNNING, JobStatus.COMPLETED, JobStatus.CANCELLED},
    JobStatus.COMPLETED: set(),
    JobStatus.FAILED: {JobStatus.QUEUED},
    JobStatus.CANCELLED: set(),
}


def can_transition(current: JobStatus, target: JobStatus) -> bool:
    return target in _ALLOWED_TRANSITIONS[current]


def ensure_transition(current: JobStatus, target: JobStatus) -> None:
    if not can_transition(current, target):
        raise ValueError(f"Invalid transition: {current} -> {target}")
