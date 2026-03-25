"""Style retriever: section별 style pattern을 검색해 context로 반환한다.

style_mode:
  default      – style injection 없음
  company      – 프로젝트 내 저장된 company style 패턴 사용
  strong       – 강도 높은 패턴 적용

style_strength: weak | medium | strong
  각 강도에 따라 반환 패턴 수와 프롬프트 지시 강도가 달라진다.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import StylePatternModel

_TOP_K = {"weak": 2, "medium": 4, "strong": 6}
_STRENGTH_INSTRUCTION = {
    "weak": "아래 문체를 참고해 자연스럽게 반영하세요.",
    "medium": "아래 문체 패턴을 적극적으로 따르되, 원문 복제는 금지합니다.",
    "strong": "아래 문체를 최대한 충실하게 반영하세요. 단, 사실 내용의 왜곡은 금지합니다.",
}


def build_style_context(
    *,
    section: str,
    project_id: uuid.UUID,
    db: Session,
    style_mode: str = "default",
    strength: str = "medium",
) -> str:
    """style_mode와 strength에 따라 프롬프트에 삽입할 style context 문자열 반환.

    Returns:
        빈 문자열이면 style injection 없음.
    """
    if style_mode == "default":
        return ""

    top_k = _TOP_K.get(strength, 4)
    instruction = _STRENGTH_INSTRUCTION.get(strength, _STRENGTH_INSTRUCTION["medium"])

    stmt = (
        select(StylePatternModel)
        .where(StylePatternModel.project_id == project_id)
        .order_by(StylePatternModel.created_at.desc())
        .limit(top_k * 3)
    )
    rows = db.execute(stmt).scalars().all()
    if not rows:
        return ""

    # section 매칭 우선, 나머지 보충
    section_lower = section.lower()
    matched = [r for r in rows if section_lower in (r.section or "").lower()]
    others = [r for r in rows if r not in matched]
    selected = (matched + others)[:top_k]

    if not selected:
        return ""

    pattern_lines = "\n".join(
        f"- [{r.pattern_type}] {r.content}" for r in selected
    )
    return f"{instruction}\n{pattern_lines}"
