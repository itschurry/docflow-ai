import pytest

from app.services.job_executor import RetryableJobError
from app.workers import tasks


def test_retry_handler_writes_dead_letter_on_final_retry(monkeypatch):
    events: list[tuple[str, str]] = []

    def fake_execute_job(job_id: str) -> None:
        raise RetryableJobError("boom")

    def fake_write_dead_letter(job_id: str, reason: str, retries: int) -> str:
        events.append(("dead", f"{job_id}:{retries}:{reason}"))
        return "/tmp/dead.json"

    def fake_mark_job_failed(job_id: str, reason: str) -> None:
        events.append(("failed", f"{job_id}:{reason}"))

    monkeypatch.setattr(tasks, "execute_job", fake_execute_job)
    monkeypatch.setattr(tasks, "write_dead_letter", fake_write_dead_letter)
    monkeypatch.setattr(tasks, "mark_job_failed", fake_mark_job_failed)

    with pytest.raises(RetryableJobError):
        tasks.run_job_with_retry_handling("job-1", retries=3, max_retries=3)

    assert events[0][0] == "dead"
    assert events[1][0] == "failed"


def test_retry_handler_skips_dead_letter_before_max_retry(monkeypatch):
    events: list[str] = []

    def fake_execute_job(job_id: str) -> None:
        raise RetryableJobError("boom")

    monkeypatch.setattr(tasks, "execute_job", fake_execute_job)
    monkeypatch.setattr(tasks, "write_dead_letter",
                        lambda *args, **kwargs: events.append("dead"))
    monkeypatch.setattr(tasks, "mark_job_failed", lambda *args,
                        **kwargs: events.append("failed"))

    with pytest.raises(RetryableJobError):
        tasks.run_job_with_retry_handling("job-2", retries=1, max_retries=3)

    assert events == []
