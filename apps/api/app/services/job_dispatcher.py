from uuid import UUID

from app.services.job_executor import execute_job


def dispatch_job(job_id: UUID) -> str:
    execute_job(str(job_id))
    return "inline"
