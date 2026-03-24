"""Agent routing policies per conversation mode."""
from __future__ import annotations

import re

from app.agents.registry import PIPELINE_ORDER

# mode → ordered list of agent handles to execute
MODE_PIPELINES: dict[str, list[str]] = {
    "direct": [],          # filled dynamically from mention
    "guided": PIPELINE_ORDER,
    "autonomous-lite": ["planner"],
    "autonomous": ["planner"],
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
    "협업", "토론", "각자", "서로", "의견", "관점",
    "plan", "write", "review", "code", "analyze", "create", "build",
}
_COLLAB_KEYWORDS = {
    "각자", "서로", "의견", "관점", "토론", "협업", "팀", "다같이", "다 함께",
    "출석체크", "출첵", "나머지", "다들", "모두", "전원", "전체", "봇들", "멤버",
}
_SOCIAL_COLLAB_KEYWORDS = {
    "인사", "소개", "자기소개", "친해", "잡담", "얘기", "대화", "롤콜",
    "출석체크", "출첵", "나머지", "다들", "모두", "전원",
    "hello", "hi", "greet", "roll call", "반가",
}
_CONCRETE_TASK_KEYWORDS = _TASK_KEYWORDS - _COLLAB_KEYWORDS


def is_casual_message(text: str) -> bool:
    """짧은 인사나 잡담이면 True. 작업 요청이면 False."""
    stripped = text.strip()
    if is_collaboration_request(stripped):
        return False

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


def is_collaboration_request(text: str) -> bool:
    lower = text.strip().lower()
    if not lower:
        return False
    return any(kw in lower for kw in _COLLAB_KEYWORDS)


def is_social_collaboration_message(text: str) -> bool:
    """협업 요청 중에서도 잡담/인사형 팀 대화에 해당하면 True."""
    lower = text.strip().lower()
    if not lower or not is_collaboration_request(lower):
        return False
    has_social = any(kw in lower for kw in _SOCIAL_COLLAB_KEYWORDS)
    has_concrete_task = any(kw in lower for kw in _CONCRETE_TASK_KEYWORDS)
    if has_social and not has_concrete_task:
        return True
    # 매우 짧은 협업 요청은 팀 스몰톡으로 분류
    return len(lower) <= 24 and not has_concrete_task


def get_pipeline(mode: str, direct_handle: str | None = None) -> list[str]:
    if mode == "direct":
        return [direct_handle] if direct_handle else []
    if mode == "pipeline":
        mode = "autonomous-lite"
    return MODE_PIPELINES.get(mode, PIPELINE_ORDER)


def extract_mentioned_handle(
    text: str,
    known_handles: set[str],
    mention_aliases: dict[str, str] | None = None,
) -> str | None:
    """Return first target handle from @mention syntax."""
    handles = extract_mentioned_handles(text, known_handles, mention_aliases)
    return handles[0] if handles else None


def extract_mentioned_handles(
    text: str,
    known_handles: set[str],
    mention_aliases: dict[str, str] | None = None,
) -> list[str]:
    """Return ordered unique target handles from @mention syntax."""
    aliases = {k.lower().lstrip("@"): v for k, v in (mention_aliases or {}).items()}
    mentions = re.findall(r"@([a-zA-Z0-9_]+)", text.lower())
    found: list[str] = []
    seen: set[str] = set()
    for token in mentions:
        handle: str | None = None
        if token in known_handles:
            handle = token
        else:
            mapped = aliases.get(token)
            if mapped and mapped in known_handles:
                handle = mapped
        if handle and handle not in seen:
            found.append(handle)
            seen.add(handle)
    # Fallback for legacy '@handle' substring behavior.
    lower = text.lower()
    for handle in sorted(known_handles):
        if f"@{handle}" in lower and handle not in seen:
            found.append(handle)
            seen.add(handle)
    return found


def detect_mode_from_command(text: str) -> str | None:
    """Parse /mode <value> command from text."""
    text = text.strip()
    if text.startswith("/mode "):
        parts = text.split(maxsplit=1)
        if len(parts) == 2:
            mode = parts[1].strip().lower()
            if mode == "pipeline":
                return "autonomous-lite"
            return mode
    return None
