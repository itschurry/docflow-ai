from uuid import UUID

from app.core.config import settings
from app.services.job_executor import execute_job


def dispatch_job(job_id: UUID) -> str:
    job_id_str = str(job_id)

    if settings.execution_backend == "celery":
        try:
            from app.workers.tasks import execute_job_task

            execute_job_task.delay(job_id_str)
            return "queued"
        except Exception:
            # Fall back to inline execution when broker is unavailable.
            execute_job(job_id_str)
            return "inline-fallback"

    # Inline execution is the default safe mode for local development.
    execute_job(job_id_str)
    return "inline"
