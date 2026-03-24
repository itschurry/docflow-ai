from sqlalchemy import select

from app.models import FileModel, JobModel
from app.services.document_ir import extract_text_from_ir
from app.services.document_ir import parse_document_to_ir
from app.services.document_ir import summarize_document_ir
from app.services.executors.context import ExecutionContext

_MAX_CONTEXT_CHARS = 12_000


def run_parse_reference_docs(db, job: JobModel, ctx: ExecutionContext) -> dict:
    ref_files = db.execute(
        select(FileModel).where(FileModel.project_id == job.project_id)
    ).scalars().all()

    # Re-extract text for files that were stored with empty extracted_text
    # (e.g. HWP files uploaded before parser support was added).
    texts = []
    file_summaries = []
    for f in ref_files:
        text = f.extracted_text or ""
        if not text and f.stored_path:
            document_ir = parse_document_to_ir(f.stored_path, f.mime_type)
            text = extract_text_from_ir(document_ir)
            if document_ir:
                f.document_type = str(document_ir.get("document_type") or f.document_type or "")
                f.document_summary = summarize_document_ir(document_ir)
            if text:
                f.extracted_text = text
                db.add(f)
        texts.append(text)
        if f.document_summary:
            file_summaries.append(f"[{f.original_name}] {f.document_summary}")

    combined = "\n\n".join(t for t in texts if t)
    payload = {
        "file_count": len(ref_files),
        "text_chars": len(combined),
        "combined_text": combined[:_MAX_CONTEXT_CHARS],
        "file_summaries": file_summaries,
    }
    ctx.set_output("parse_reference_docs", payload)
    return payload
