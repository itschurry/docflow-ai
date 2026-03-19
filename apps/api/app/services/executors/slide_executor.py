from app.services.executors.context import ExecutionContext
from app.services.file_generators import generate_pptx


def run_slide_text_task(task_type: str, run_generate_text, request_text: str, ctx: ExecutionContext) -> dict:
    prompt = f"task={task_type} request={request_text}"
    text = run_generate_text(prompt)
    payload = {"text": text}
    ctx.set_output(task_type, payload)
    return payload


def run_generate_ppt(ctx: ExecutionContext, persist_generated_file) -> dict:
    outline_text = ctx.get_output(
        "generate_slide_outline").get("text", "발표 개요")
    body_text = ctx.get_output("generate_slide_body").get("text", "발표 본문")

    slides = [
        {"title": "과제 개요", "bullets": [outline_text[:120], body_text[:120]]},
        {"title": "핵심 추진 내용", "bullets": [
            body_text[120:240] or body_text[:120]]},
    ]

    pptx_bytes = generate_pptx("발표자료 초안", slides)
    artifact = persist_generated_file(
        filename="slides.pptx",
        content=pptx_bytes,
        mime_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
    )

    payload = {"status": "generated", "artifact_file_id": str(
        artifact.id), "slide_count": len(slides) + 1}
    ctx.set_output("generate_ppt", payload)
    return payload
