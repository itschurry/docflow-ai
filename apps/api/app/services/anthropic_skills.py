from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from anthropic import Anthropic

from app.core.config import settings

SKILLS_BETAS = [
    "code-execution-2025-08-25",
    "skills-2025-10-02",
]
FILES_BETA = "files-api-2025-04-14"
SKILL_IDS = {
    "docx": "docx",
    "xlsx": "xlsx",
    "pptx": "pptx",
}
MIME_TYPES = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}


def anthropic_skills_available() -> bool:
    return bool(
        settings.anthropic_skills_enabled
        and settings.anthropic_api_key
        and settings.anthropic_model
    )


def default_document_provider() -> str:
    if settings.anthropic_skills_default_provider and anthropic_skills_available():
        return "claude_skills"
    return "internal_fallback"


def build_skill_prompt(
    *,
    output_type: str,
    title: str,
    request_text: str,
    content: str,
    structured_ir: dict[str, Any] | None = None,
    source_ir_summary: str = "",
) -> str:
    structured_ir = structured_ir or {}
    prompt = [
        f"Create an editable {output_type.upper()} document.",
        f"Document title: {title or '문서'}",
        "The document must be professional, clean, and directly usable.",
        "Do not include internal review notes or meta commentary unless explicitly requested.",
        "",
        "[User Request]",
        request_text.strip() or title.strip() or "사용자 요청에 맞는 문서를 작성하세요.",
    ]
    if source_ir_summary.strip():
        prompt.extend(["", "[Source Materials Summary]", source_ir_summary.strip()])
    if structured_ir:
        prompt.extend(["", "[Structured Target]", str(structured_ir)])
    prompt.extend(["", "[Content Draft]", content.strip()])

    if output_type == "pptx":
        prompt.extend(
            [
                "",
                "Build a concise slide deck.",
                "Use the slide structure, keep speaker notes in notes, and keep slides presentation-ready.",
            ]
        )
    elif output_type == "xlsx":
        prompt.extend(
            [
                "",
                "Build a workbook with clear sheet names, headers, and editable tabular layout.",
                "Avoid decorative content and prefer structured tables.",
            ]
        )
    else:
        prompt.extend(
            [
                "",
                "Build a readable business document with headings, bullets, and tables where useful.",
            ]
        )
    return "\n".join(part for part in prompt if part is not None).strip()


def _extract_file_ids(response: Any) -> list[str]:
    file_ids: list[str] = []
    for item in getattr(response, "content", []) or []:
        if getattr(item, "type", None) != "bash_code_execution_tool_result":
            continue
        content_item = getattr(item, "content", None)
        if getattr(content_item, "type", None) != "bash_code_execution_result":
            continue
        for generated in getattr(content_item, "content", []) or []:
            file_id = getattr(generated, "file_id", None)
            if file_id:
                file_ids.append(str(file_id))
    return file_ids


class AnthropicSkillsDocumentGenerator:
    def __init__(self, api_key: str | None = None, model: str | None = None):
        self.client = Anthropic(api_key=api_key or settings.anthropic_api_key)
        self.model = model or settings.anthropic_model

    def generate(
        self,
        *,
        output_type: str,
        title: str,
        request_text: str,
        content: str,
        structured_ir: dict[str, Any] | None = None,
        source_ir_summary: str = "",
    ) -> dict[str, Any]:
        if output_type not in SKILL_IDS:
            raise ValueError("unsupported output_type")

        prompt = build_skill_prompt(
            output_type=output_type,
            title=title,
            request_text=request_text,
            content=content,
            structured_ir=structured_ir,
            source_ir_summary=source_ir_summary,
        )
        response = self.client.beta.messages.create(
            model=self.model,
            max_tokens=4096,
            betas=SKILLS_BETAS,
            container={
                "skills": [{"type": "anthropic", "skill_id": SKILL_IDS[output_type], "version": "latest"}]
            },
            messages=[{"role": "user", "content": prompt}],
            tools=[{"type": "code_execution_20250825", "name": "code_execution"}],
        )

        messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
        retries = 0
        while getattr(response, "stop_reason", None) == "pause_turn" and retries < 6:
            messages.append({"role": "assistant", "content": response.content})
            response = self.client.beta.messages.create(
                model=self.model,
                max_tokens=4096,
                betas=SKILLS_BETAS,
                container={
                    "id": getattr(getattr(response, "container", None), "id", None),
                    "skills": [{"type": "anthropic", "skill_id": SKILL_IDS[output_type], "version": "latest"}],
                },
                messages=messages,
                tools=[{"type": "code_execution_20250825", "name": "code_execution"}],
            )
            retries += 1

        file_ids = _extract_file_ids(response)
        if not file_ids:
            raise RuntimeError("Claude Skills did not return a generated file")

        file_id = file_ids[-1]
        metadata = self.client.beta.files.retrieve_metadata(file_id=file_id, betas=[FILES_BETA])
        downloaded = self.client.beta.files.download(file_id=file_id, betas=[FILES_BETA])

        with tempfile.TemporaryDirectory(prefix="docflow_skills_") as tmpdir:
            filename = str(getattr(metadata, "filename", "") or f"{title}.{output_type}")
            target = Path(tmpdir) / filename
            downloaded.write_to_file(target)
            file_bytes = target.read_bytes()

        return {
            "provider": "claude_skills",
            "file_id": file_id,
            "filename": str(getattr(metadata, "filename", "") or f"{title}.{output_type}"),
            "mime_type": MIME_TYPES[output_type],
            "content": file_bytes,
        }
