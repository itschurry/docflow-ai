"""Telegram command handlers (/agents, /mode, /status, /stop, /final, /export)."""
from __future__ import annotations

import logging
import uuid

from sqlalchemy.orm import Session
import yaml

from app.adapters.telegram.bot import bot
from app.adapters.telegram.formatter import (
    agents_list,
    help_text,
    mode_changed,
)
from app.conversations.service import ConversationService
from app.conversations.serializer import serialize_conversation
from app.core.config import settings
from app.orchestrator.engine import orchestrator

logger = logging.getLogger(__name__)


async def handle_agents(chat_id: str, db: Session, **_) -> None:
    info = orchestrator.list_agents_info()
    await bot.send_message(chat_id, agents_list(info))


async def handle_mode(chat_id: str, text: str, db: Session, **_) -> None:
    parts = text.strip().split(maxsplit=1)
    mode = parts[1].strip().lower() if len(parts) > 1 else ""
    alias = {"pipeline": "guided"}
    mode = alias.get(mode, mode)
    valid = {"guided", "autonomous-lite", "autonomous", "debate", "artifact", "direct"}
    if mode not in valid:
        await bot.send_message(
            chat_id,
            f"⚠️ 유효하지 않은 모드입니다. 사용 가능: {', '.join(sorted(valid))}",
        )
        return
    svc = ConversationService(db)
    conv = svc.get_or_create_conversation(chat_id=chat_id)
    svc.set_conversation_mode(conv.id, mode)
    db.commit()
    await bot.send_message(chat_id, mode_changed(mode))


async def handle_status(chat_id: str, db: Session, **_) -> None:
    svc = ConversationService(db)
    conv = svc.get_or_create_conversation(chat_id=chat_id)
    info = serialize_conversation(conv)
    text = (
        f"📊 <b>대화 상태</b>\n"
        f"모드: <code>{info['mode']}</code>\n"
        f"상태: <code>{info['status']}</code>\n"
        f"업데이트: {info['updated_at'][:19]}"
    )
    await bot.send_message(chat_id, text)


async def handle_stop(chat_id: str, db: Session, **_) -> None:
    svc = ConversationService(db)
    conv = svc.get_or_create_conversation(chat_id=chat_id)
    svc.update_conversation_status(conv.id, "idle")
    db.commit()
    await bot.send_message(chat_id, "⏹ 작업이 중단됐습니다.")


async def handle_help(chat_id: str, **_) -> None:
    await bot.send_message(chat_id, help_text(_load_help_mentions()))


async def handle_final(chat_id: str, db: Session, **_) -> None:
    from app.conversations.selectors import get_recent_agent_output_since_last_user
    svc = ConversationService(db)
    conv = svc.get_or_create_conversation(chat_id=chat_id)
    output = get_recent_agent_output_since_last_user(db, conv.id, "manager")
    if output:
        await bot.send_message(chat_id, f"🎯 <b>최종 결론</b>\n\n{output}")
    else:
        await bot.send_message(chat_id, "최근 요청 기준으로 완료된 manager 출력이 없습니다.")


async def handle_export(chat_id: str, text: str, db: Session, **_) -> None:
    parts = text.strip().split(maxsplit=1)
    fmt = parts[1].strip().lower() if len(parts) > 1 else "md"
    valid = {"md", "docx", "xlsx", "pptx"}
    if fmt not in valid:
        await bot.send_message(
            chat_id,
            f"⚠️ 지원 형식: {', '.join(sorted(valid))}",
        )
        return
    await bot.send_message(
        chat_id,
        f"📤 <code>{fmt}</code> 내보내기는 아직 구현 중입니다. (Phase 3에서 artifact pipeline 연동 예정)",
    )


async def handle_new(chat_id: str, db: Session, **_) -> None:
    svc = ConversationService(db)
    closed = svc.close_active_conversations(chat_id=chat_id)
    db.commit()
    await bot.send_message(chat_id, f"🧹 새 대화를 시작합니다. 이전 활성 대화 {closed}개를 종료했어요.")


_COMMAND_HANDLERS = {
    "/agents": handle_agents,
    "/mode": handle_mode,
    "/status": handle_status,
    "/stop": handle_stop,
    "/help": handle_help,
    "/start": handle_help,
    "/final": handle_final,
    "/export": handle_export,
    "/new": handle_new,
}


def _load_help_mentions() -> dict[str, str]:
    """Build mention examples from configured telegram usernames."""
    handles = ("planner", "writer", "critic", "coder")
    dispatcher = orchestrator._get_dispatcher()
    mapping: dict[str, str] = {}

    try:
        with open(settings.agent_config_path, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        agent_cfg = raw.get("agents", {}) or {}
        bots_cfg = (raw.get("telegram", {}) or {}).get("bots", {}) or {}

        for handle in handles:
            identity = (
                (agent_cfg.get(handle, {}) or {}).get("identity")
                or dispatcher.resolve_identity(handle)
            )
            username = (bots_cfg.get(identity, {}) or {}).get("username")
            if username:
                mapping[handle] = f"@{str(username).lstrip('@')}"
    except Exception as exc:
        logger.debug("Failed to load help mentions from config: %s", exc)

    # Fallback to runtime registry values if not in YAML-derived mapping.
    for handle in handles:
        if handle in mapping:
            continue
        identity = dispatcher.resolve_identity(handle)
        bot_info = dispatcher._registry.get(identity)
        if bot_info and bot_info.username:
            mapping[handle] = f"@{bot_info.username.lstrip('@')}"

    return mapping


async def dispatch_command(
    chat_id: str,
    text: str,
    db: Session,
) -> bool:
    """Returns True if the text was handled as a command."""
    cmd = text.strip().split()[0].lower() if text.strip() else ""
    # Strip bot username suffix e.g. /agents@mybot
    if "@" in cmd:
        cmd = cmd.split("@")[0]
    handler = _COMMAND_HANDLERS.get(cmd)
    if handler:
        await handler(chat_id=chat_id, text=text, db=db)
        return True
    return False
