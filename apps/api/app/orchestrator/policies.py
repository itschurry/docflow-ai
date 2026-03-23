"""Agent routing policies per conversation mode."""
from __future__ import annotations

from app.agents.registry import PIPELINE_ORDER

# mode → ordered list of agent handles to execute
MODE_PIPELINES: dict[str, list[str]] = {
    "direct": [],          # filled dynamically from mention
    "pipeline": PIPELINE_ORDER,
    "debate": ["planner", "writer", "critic", "manager"],
    "artifact": ["planner", "writer", "manager"],
}

# 인사/잡담으로 분류되는 패턴
_CASUAL_KEYWORDS = {
    "안녕", "하이", "헬로", "hi", "hello", "hey", "ㅎㅇ", "ㅋㅋ", "ㅎㅎ",
    "반가워", "반갑습니다", "잘부탁", "잘 부탁", "부탁드립니다", "부탁해요",
    "고마워", "감사", "수고", "bye", "바이", "잘가", "굿", "good",
    "ㅇㅋ", "ok", "넵", "네", "응", "오케이", "알겠어", "알겠습니다",
}

# 작업 요청으로 분류되는 패턴 (이 중 하나라도 있으면 pipeline)
_TASK_KEYWORDS = {
    "계획", "기획", "설계", "개발", "구현", "작성", "만들어", "만들줘",
    "분석", "검토", "리뷰", "요약", "정리", "도와줘", "도움말",
    "해줘", "해주세요", "작업", "진행", "방안", "방법", "전략", "제안",
    "코드", "함수", "클래스", "api", "기능", "문서", "보고서", "초안",
    "plan", "write", "review", "code", "analyze", "create", "build",
}


def is_casual_message(text: str) -> bool:
    """짧은 인사나 잡담이면 True. 작업 요청이면 False."""
    stripped = text.strip()

    # 20자 이하이고 작업 키워드 없으면 casual
    if len(stripped) <= 20:
        lower = stripped.lower()
        if not any(kw in lower for kw in _TASK_KEYWORDS):
            return True

    # 명시적 인사 키워드 포함 + 작업 키워드 없으면 casual
    lower = stripped.lower()
    has_casual = any(kw in lower for kw in _CASUAL_KEYWORDS)
    has_task = any(kw in lower for kw in _TASK_KEYWORDS)
    if has_casual and not has_task:
        return True

    return False


def get_pipeline(mode: str, direct_handle: str | None = None) -> list[str]:
    if mode == "direct":
        return [direct_handle] if direct_handle else []
    return MODE_PIPELINES.get(mode, PIPELINE_ORDER)


def extract_mentioned_handle(text: str, known_handles: set[str]) -> str | None:
    """Return the first agent handle mentioned with @handle syntax."""
    lower = text.lower()
    for handle in known_handles:
        if f"@{handle}" in lower:
            return handle
    return None


def detect_mode_from_command(text: str) -> str | None:
    """Parse /mode <value> command from text."""
    text = text.strip()
    if text.startswith("/mode "):
        parts = text.split(maxsplit=1)
        if len(parts) == 2:
            return parts[1].strip().lower()
    return None
