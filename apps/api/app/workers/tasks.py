from app.core.celery_app import celery_app
from app.core.config import settings
from app.services.dead_letter import write_dead_letter
from app.services.job_executor import RetryableJobError, execute_job, mark_job_failed


def run_job_with_retry_handling(job_id: str, retries: int, max_retries: int) -> None:
    try:
        execute_job(job_id)
    except RetryableJobError as exc:
        if retries >= max_retries:
            write_dead_letter(job_id=job_id, reason=str(exc), retries=retries)
            mark_job_failed(job_id, str(exc))
        raise


@celery_app.task(
    bind=True,
    name="docflow.execute_job",
    autoretry_for=(RetryableJobError,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=settings.queue_max_retries,
)
def execute_job_task(self, job_id: str) -> None:
    retries = int(getattr(self.request, "retries", 0))
    run_job_with_retry_handling(job_id, retries, settings.queue_max_retries)
