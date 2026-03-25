"""Chunking service: 문서 텍스트를 RAG 파이프라인용 chunk로 분할한다.

청킹 전략:
- section 경계를 최대한 존중한다.
- 단일 청크 최대 크기는 CHUNK_SIZE 자.
- 연속 청크 간 CHUNK_OVERLAP 자 중첩을 둔다.
- document_ir 섹션 구조가 있으면 섹션 단위로 먼저 쪼갠다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

CHUNK_SIZE = 500
CHUNK_OVERLAP = 80


@dataclass
class Chunk:
    content: str
    section: str = ""
    chunk_index: int = 0
    page_hint: int = 0
    document_type: str = ""
    file_name: str = ""
    metadata: dict = field(default_factory=dict)


def _split_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """텍스트를 고정 크기 슬라이딩 윈도우로 분할한다."""
    if not text:
        return []
    parts: list[str] = []
    start = 0
    while start < len(text):
        end = start + size
        parts.append(text[start:end])
        if end >= len(text):
            break
        start = end - overlap
    return parts


def chunk_from_ir(ir: dict[str, Any], *, file_name: str = "") -> list[Chunk]:
    """document_ir 구조에서 Chunk 목록을 생성한다."""
    chunks: list[Chunk] = []
    document_type = str(ir.get("document_type") or "")
    idx = 0

    for section in ir.get("sections") or []:
        heading = str(section.get("heading") or "").strip()
        page_hint = int(section.get("page") or 0)

        section_text_parts: list[str] = []
        for block in section.get("blocks") or []:
            btype = str(block.get("type") or "")
            if btype == "paragraph":
                section_text_parts.append(str(block.get("text") or "").strip())
            elif btype == "table":
                for row in block.get("rows") or []:
                    section_text_parts.append(" | ".join(str(c) for c in row))
            elif btype == "bullet":
                section_text_parts.append(str(block.get("text") or "").strip())

        section_text = "\n".join(p for p in section_text_parts if p)

        for part in _split_text(section_text):
            if part.strip():
                chunks.append(Chunk(
                    content=part.strip(),
                    section=heading,
                    chunk_index=idx,
                    page_hint=page_hint,
                    document_type=document_type,
                    file_name=file_name,
                ))
                idx += 1

    return chunks


def chunk_from_text(
    text: str,
    *,
    file_name: str = "",
    document_type: str = "",
) -> list[Chunk]:
    """평문 텍스트를 직접 청킹한다 (IR 구조 없을 때 fallback)."""
    chunks: list[Chunk] = []
    for i, part in enumerate(_split_text(text)):
        if part.strip():
            chunks.append(Chunk(
                content=part.strip(),
                chunk_index=i,
                file_name=file_name,
                document_type=document_type,
            ))
    return chunks
