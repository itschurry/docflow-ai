from __future__ import annotations

import json
from typing import Any

from openai import OpenAI

from app.core.config import settings


def openai_document_generation_available() -> bool:
    return bool(settings.openai_api_key and settings.openai_model)


class OpenAIDocumentIRGenerator:
    def __init__(self, api_key: str | None = None, model: str | None = None):
        self.client = OpenAI(api_key=api_key or settings.openai_api_key)
        self.model = model or settings.openai_model

    def generate_ir(
        self,
        *,
        output_type: str,
        title: str,
        request_text: str,
        content: str,
        source_ir_summary: str = "",
    ) -> dict[str, Any]:
        schema = _schema_for_output_type(output_type)
        prompt = _build_prompt(
            output_type=output_type,
            title=title,
            request_text=request_text,
            content=content,
            source_ir_summary=source_ir_summary,
        )
        response = self.client.responses.create(
            model=self.model,
            input=[{"role": "user", "content": prompt}],
            text={
                "format": {
                    "type": "json_schema",
                    "name": "document_ir",
                    "schema": schema,
                    "strict": True,
                }
            },
        )
        payload_text = (response.output_text or "").strip()
        if not payload_text:
            raise RuntimeError("OpenAI document generation returned empty output")
        parsed = json.loads(payload_text)
        if not isinstance(parsed, dict):
            raise RuntimeError("OpenAI document generation returned non-object JSON")
        return parsed


def _build_prompt(
    *,
    output_type: str,
    title: str,
    request_text: str,
    content: str,
    source_ir_summary: str,
) -> str:
    sections = [
        f"Generate a structured IR for an editable {output_type.upper()} document.",
        "Output must strictly match the given JSON schema.",
        "Do not include markdown fences.",
        "",
        f"Title: {title or '문서'}",
        f"User request: {request_text or title or '사용자 요청에 맞는 문서를 작성하세요.'}",
    ]
    if source_ir_summary.strip():
        sections.extend(["", "[Source Summary]", source_ir_summary.strip()])
    sections.extend(["", "[Draft Content]", content.strip() or "내용 없음"])
    return "\n".join(sections).strip()


def _schema_for_output_type(output_type: str) -> dict[str, Any]:
    if output_type == "pptx":
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "document_type": {"type": "string", "const": "slides"},
                "title": {"type": "string"},
                "slides": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "index": {"type": "integer"},
                            "title": {"type": "string"},
                            "bullets": {"type": "array", "items": {"type": "string"}},
                            "speaker_notes": {"type": "string"},
                        },
                        "required": ["index", "title", "bullets", "speaker_notes"],
                    },
                },
                "sources": {"type": "array", "items": {"type": "string"}},
                "notes": {"type": "array", "items": {"type": "string"}},
                "metadata": {"type": "object"},
            },
            "required": ["document_type", "title", "slides", "sources", "notes", "metadata"],
        }
    if output_type == "xlsx":
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "document_type": {"type": "string", "const": "sheet"},
                "title": {"type": "string"},
                "sheets": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "name": {"type": "string"},
                            "rows": {
                                "type": "array",
                                "items": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                            },
                        },
                        "required": ["name", "rows"],
                    },
                },
                "tables": {"type": "array", "items": {"type": "object"}},
                "sources": {"type": "array", "items": {"type": "string"}},
                "notes": {"type": "array", "items": {"type": "string"}},
                "metadata": {"type": "object"},
            },
            "required": ["document_type", "title", "sheets", "tables", "sources", "notes", "metadata"],
        }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "document_type": {"type": "string", "const": "word"},
            "title": {"type": "string"},
            "sections": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "heading": {"type": "string"},
                        "level": {"type": "integer"},
                        "blocks": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "properties": {
                                    "type": {"type": "string"},
                                    "text": {"type": "string"},
                                },
                                "required": ["type", "text"],
                            },
                        },
                    },
                    "required": ["heading", "level", "blocks"],
                },
            },
            "tables": {"type": "array", "items": {"type": "object"}},
            "sources": {"type": "array", "items": {"type": "string"}},
            "notes": {"type": "array", "items": {"type": "string"}},
            "metadata": {"type": "object"},
        },
        "required": ["document_type", "title", "sections", "tables", "sources", "notes", "metadata"],
    }
