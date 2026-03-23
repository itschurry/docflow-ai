"""Format Telegram messages for agent outputs."""
from __future__ import annotations


def agent_message(emoji: str, display_name: str, text: str) -> str:
    header = f"<b>[{emoji} {display_name}]</b>"
    escaped = _escape_html(text)
    return f"{header}\n{escaped}"


def status_message(emoji: str, display_name: str, action: str) -> str:
    return f"{emoji} <i>{display_name}가 {action} 중...</i>"


def final_summary(text: str) -> str:
    return f"🟢 <b>최종 결론</b>\n\n{_escape_html(text)}"


def error_message(display_name: str, emoji: str, error: str) -> str:
    return f"❌ <b>{emoji} {display_name}</b> 실패\n<code>{_escape_html(error)}</code>"


def agents_list(agents: list[dict]) -> str:
    if not agents:
        return "⚠️ 등록된 에이전트가 없습니다."
    lines = ["<b>🤖 사용 가능한 에이전트</b>\n"]
    for a in agents:
        lines.append(
            f"  {a['emoji']} <b>{a['display_name']}</b> "
            f"(<code>@{a['handle']}</code>) — {a['provider']}/{a['model']}"
        )
    lines.append("\n<i>/mode pipeline|debate|artifact|direct 로 모드 변경</i>")
    return "\n".join(lines)


def mode_changed(mode: str) -> str:
    labels = {
        "pipeline": "🔗 Pipeline (순차 처리)",
        "debate": "💬 Debate (토론 후 요약)",
        "artifact": "📄 Artifact (문서 생성)",
        "direct": "🎯 Direct (직접 호출)",
    }
    label = labels.get(mode, mode)
    return f"✅ 모드가 <b>{label}</b>으로 변경됐습니다."


def help_text() -> str:
    return (
        "<b>DocFlow AI 도움말</b>\n\n"
        "<b>명령어</b>\n"
        "  /agents — 에이전트 목록\n"
        "  /mode [pipeline|debate|artifact|direct] — 모드 변경\n"
        "  /status — 현재 대화 상태\n"
        "  /stop — 작업 중단\n"
        "  /final — 현재까지 결론 요약\n"
        "  /export [md|docx|xlsx|pptx] — 결과 내보내기\n\n"
        "<b>멘션 예시</b>\n"
        "  @planner 사업계획서 작업 분해해줘\n"
        "  @writer 마케팅 전략 초안 작성\n"
        "  @critic 이 주장의 허점을 찾아줘\n"
        "  @coder 이 기능 구현 방향 알려줘\n"
    )


def _escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
