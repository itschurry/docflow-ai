import sys
import types
from uuid import uuid4

from app.core.config import settings
from app.services import job_dispatcher


class _FakeTask:
    def __init__(self, should_fail: bool = False):
        self.should_fail = should_fail
        self.called_with: list[str] = []

    def delay(self, job_id: str) -> None:
        if self.should_fail:
            raise RuntimeError("broker unavailable")
        self.called_with.append(job_id)


def test_dispatch_job_uses_celery_when_available(monkeypatch):
    monkeypatch.setattr(settings, "execution_backend", "celery")

    fake_task = _FakeTask(should_fail=False)
    fake_module = types.ModuleType("app.workers.tasks")
    fake_module.execute_job_task = fake_task
    monkeypatch.setitem(sys.modules, "app.workers.tasks", fake_module)

    executed_inline: list[str] = []
    monkeypatch.setattr(job_dispatcher, "execute_job",
                        lambda job_id: executed_inline.append(job_id))

    job_id = uuid4()
    result = job_dispatcher.dispatch_job(job_id)

    assert result == "queued"
    assert fake_task.called_with == [str(job_id)]
    assert executed_inline == []


def test_dispatch_job_falls_back_to_inline_when_celery_fails(monkeypatch):
    monkeypatch.setattr(settings, "execution_backend", "celery")

    fake_task = _FakeTask(should_fail=True)
    fake_module = types.ModuleType("app.workers.tasks")
    fake_module.execute_job_task = fake_task
    monkeypatch.setitem(sys.modules, "app.workers.tasks", fake_module)

    executed_inline: list[str] = []
    monkeypatch.setattr(job_dispatcher, "execute_job",
                        lambda job_id: executed_inline.append(job_id))

    job_id = uuid4()
    result = job_dispatcher.dispatch_job(job_id)

    assert result == "inline-fallback"
    assert executed_inline == [str(job_id)]
