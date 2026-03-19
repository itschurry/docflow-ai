from celery import Celery

from app.core.config import settings

celery_app = Celery("docflow", broker=settings.redis_url,
                    backend=settings.redis_url)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_default_retry_delay=settings.queue_retry_delay_seconds,
)
