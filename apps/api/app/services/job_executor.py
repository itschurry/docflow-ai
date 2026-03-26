import asyncio
from pathlib import Path
import threading
from uuid import UUID

from sqlalchemy import select

from app.core.database import SessionLocal
from app.core.state_machine import JobStatus
from app.core.config import settings
from app.core.time_utils import now_utc
from app.models import FileModel, JobModel, PromptLogModel, TaskModel
from app.services.document_ir import parse_document_to_ir
from app.services.document_ir import summarize_document_ir
from app.services.executors.context import ExecutionContext
from app.services.executors.parser_executor import run_parse_reference_docs
from app.services.executors.qa_executor import run_qa_report
from app.services.executors.slide_executor import run_generate_ppt, run_slide_text_task
from app.services.executors.spreadsheet_executor import (
    run_budget_rules,
    run_extract_budget_items,
    run_generate_xlsx,
)
from app.services.task_graph import get_ready_task_types
from app.services.executors.writer_executor import run_writer_task
from app.services.llm_router import get_llm_provider
from app.services.retriever import build_rag_context


class RetryableJobError(RuntimeError):
    pass


def _provider_info(provider) -> tuple[str, str]:
    provider_name = provider.__class__.__name__.replace("Provider", "").lower()
    model_name = getattr(provider, "model", "stub")
    return provider_name, str(model_name)


def _run_generate_text(provider, prompt: str) -> str:
    result: dict[str, str] = {}
    error: dict[str, Exception] = {}

    def _target() -> None:
        try:
            result["value"] = asyncio.run(provider.generate_text(prompt))
        except Exception as exc:
            error["value"] = exc

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    thread.join()

    if "value" in error:
        raise error["value"]
    return result.get("value", "")


def _persist_generated_file(
    db,
    *,
    job: JobModel,
    task: TaskModel,
    filename: str,
    content: bytes,
    mime_type: str,
    extracted_text: str = "",
) -> FileModel:
    output_dir = Path(settings.upload_dir) / \
        str(job.project_id) / "generated" / str(job.id)
    output_dir.mkdir(parents=True, exist_ok=True)

    stored_path = output_dir / filename
    stored_path.write_bytes(content)
    document_ir = parse_document_to_ir(str(stored_path), mime_type)
    document_type = str(document_ir.get("document_type") or "")
    document_summary = summarize_document_ir(document_ir)

    file_row = FileModel(
        project_id=job.project_id,
        job_id=job.id,
        original_name=filename,
        stored_path=str(stored_path),
        mime_type=mime_type,
        size=stored_path.stat().st_size,
        source_type="generated",
        extracted_text=extracted_text,
        document_type=document_type,
        document_summary=document_summary,
        created_at=now_utc(),
    )
    db.add(file_row)
    db.flush()
    return file_row


def execute_job(job_id: str) -> None:
    db = SessionLocal()
    try:
        job_uuid = UUID(job_id)
        job = db.get(JobModel, job_uuid)
        if not job:
            return

        job.status = JobStatus.RUNNING
        job.progress = 0
        job.updated_at = now_utc()
        db.add(job)
        db.commit()

        tasks = list(db.execute(
            select(TaskModel).where(TaskModel.job_id ==
                                    job_uuid).order_by(TaskModel.id)
        ).scalars().all())

        total_tasks = len(tasks)
        completed_count = 0
        pending_tasks = {task.id: task for task in tasks}
        completed_task_types: set[str] = set()
        ctx = ExecutionContext()

        provider = get_llm_provider()
        provider_name, model_name = _provider_info(provider)

        while pending_tasks:
            ready_types = get_ready_task_types(
                (task.task_type for task in pending_tasks.values()),
                completed_task_types,
            )

            ready_tasks = [
                task
                for task in pending_tasks.values()
                if task.task_type in ready_types
            ]

            if not ready_tasks:
                job.status = JobStatus.FAILED
                job.updated_at = now_utc()
                db.add(job)
                db.commit()
                raise RuntimeError(
                    "Task graph stalled: unresolved dependencies")

            ready_tasks.sort(key=lambda t: (t.task_type, str(t.id)))
            for task in ready_tasks:
                task.status = "RUNNING"
                task.started_at = now_utc()
                db.add(task)
                db.commit()

                try:
                    if task.task_type == "parse_reference_docs":
                        task.output_payload_json = run_parse_reference_docs(
                            db, job, ctx)

                    elif task.task_type in {
                        "generate_report_outline",
                        "generate_report_draft",
                    }:
                        # RAG context 빌드
                        rag_result = build_rag_context(
                            job.request_text,
                            project_id=job.project_id,
                            db=db,
                            top_k=5,
                        )
                        # style 옵션은 job input_payload에서 읽음
                        job_payload = task.input_payload_json or {}
                        style_mode = job_payload.get("style_mode", "default")
                        style_strength = job_payload.get("style_strength", "medium")

                        task.output_payload_json = run_writer_task(
                            db,
                            job=job,
                            task=task,
                            ctx=ctx,
                            run_generate_text=lambda prompt: _run_generate_text(
                                provider, prompt),
                            persist_generated_file=lambda **kwargs: _persist_generated_file(
                                db,
                                job=job,
                                task=task,
                                **kwargs,
                            ),
                            provider_name=provider_name,
                            model_name=model_name,
                            rag_result=rag_result,
                            style_mode=style_mode,
                            style_strength=style_strength,
                        )
                        # retrieval status를 context에 기록 (orchestrator 참조용)
                        ctx.set_output("retrieval_status", {
                            "status": rag_result.retrieval_status.value,
                            "chunk_count": rag_result.chunk_count,
                        })

                    elif task.task_type == "review_report":
                        task.output_payload_json = run_qa_report(ctx)

                    elif task.task_type in {"generate_slide_outline", "generate_slide_body"}:
                        task.output_payload_json = run_slide_text_task(
                            task_type=task.task_type,
                            run_generate_text=lambda prompt: _run_generate_text(
                                provider, prompt),
                            request_text=job.request_text,
                            ctx=ctx,
                        )
                        db.add(
                            PromptLogModel(
                                task_id=task.id,
                                provider=provider_name,
                                model=model_name,
                                prompt_text=f"task={task.task_type} request={job.request_text}",
                                response_text=task.output_payload_json.get(
                                    "text", ""),
                                created_at=now_utc(),
                            )
                        )

                    elif task.task_type == "extract_budget_items":
                        task.output_payload_json = run_extract_budget_items(
                            ctx)

                    elif task.task_type == "run_budget_rules":
                        task.output_payload_json = run_budget_rules(task, ctx)

                    elif task.task_type == "generate_xlsx":
                        task.output_payload_json = run_generate_xlsx(
                            ctx,
                            persist_generated_file=lambda **kwargs: _persist_generated_file(
                                db,
                                job=job,
                                task=task,
                                **kwargs,
                            ),
                        )

                    elif task.task_type == "generate_ppt":
                        task.output_payload_json = run_generate_ppt(
                            ctx,
                            persist_generated_file=lambda **kwargs: _persist_generated_file(
                                db,
                                job=job,
                                task=task,
                                **kwargs,
                            ),
                        )

                    else:
                        task.output_payload_json = {"status": "noop"}

                    task.status = "COMPLETED"
                    task.finished_at = now_utc()
                    db.add(task)
                    completed_count += 1
                    job.progress = int(completed_count / total_tasks * 100) if total_tasks else 100
                    job.updated_at = now_utc()
                    db.add(job)
                    db.commit()
                    completed_task_types.add(task.task_type)
                    pending_tasks.pop(task.id, None)

                except Exception as task_exc:
                    task.status = "FAILED"
                    task.error_message = str(task_exc)
                    task.finished_at = now_utc()
                    db.add(task)

                    job.status = JobStatus.FAILED
                    job.updated_at = now_utc()
                    db.add(job)
                    db.commit()
                    return

        job.status = JobStatus.REVIEW_REQUIRED
        job.progress = 100
        job.updated_at = now_utc()
        db.add(job)
        db.commit()

    except Exception:
        db.rollback()
        raise RetryableJobError(f"job execution failed for {job_id}")
    finally:
        db.close()


def mark_job_failed(job_id: str, reason: str) -> None:
    db = SessionLocal()
    try:
        job = db.get(JobModel, UUID(job_id))
        if not job:
            return
        job.status = JobStatus.FAILED
        job.updated_at = now_utc()
        db.add(job)
        db.commit()
    finally:
        db.close()
