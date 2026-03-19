from sqlalchemy import select

from app.models import FileModel, JobModel
from app.services.document_parser import extract_text
from app.services.executors.context import ExecutionContext

_MAX_CONTEXT_CHARS = 12_000


def run_parse_reference_docs(db, job: JobModel, ctx: ExecutionContext) -> dict:
    ref_files = db.execute(
        select(FileModel).where(FileModel.project_id == job.project_id)
    ).scalars().all()

    # Re-extract text for files that were stored with empty extracted_text
    # (e.g. HWP files uploaded before parser support was added).
    texts = []
    for f in ref_files:
        text = f.extracted_text or ""
        if not text and f.stored_path:
            text = extract_text(f.stored_path, f.mime_type)
            if text:
                f.extracted_text = text
                db.add(f)
        texts.append(text)

    combined = "\n\n".join(t for t in texts if t)
    payload = {
        "file_count": len(ref_files),
        "text_chars": len(combined),
        "combined_text": combined[:_MAX_CONTEXT_CHARS],
    }
    ctx.set_output("parse_reference_docs", payload)
    return payload
