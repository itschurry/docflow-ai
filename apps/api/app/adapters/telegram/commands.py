"""Telegram command handlers (/agents, /mode, /status, /stop, /final, /export)."""
from __future__ import annotations

import logging
import uuid

from sqlalchemy.orm import Session

from app.adapters.telegram.bot import bot
from app.adapters.telegram.formatter import (
    agents_list,
    help_text,
    mode_changed,
)
from app.conversations.service import ConversationService
from app.conversations.serializer import serialize_conversation
from app.orchestrator.engine import orchestrator

logger = logging.getLogger(__name__)


async def handle_agents(chat_id: str, db: Session, **_) -> None:
    info = orchestrator.list_agents_info()
    await bot.send_message(chat_id, agents_list(info))


async def handle_mode(chat_id: str, text: str, db: Session, **_) -> None:
    parts = text.strip().split(maxsplit=1)
    mode = parts[1].strip().lower() if len(parts) > 1 else ""
    valid = {"pipeline", "debate", "artifact", "direct"}
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
    await bot.send_message(chat_id, help_text())


async def handle_final(chat_id: str, db: Session, **_) -> None:
    from app.conversations.selectors import get_recent_agent_output
    svc = ConversationService(db)
    conv = svc.get_or_create_conversation(chat_id=chat_id)
    output = get_recent_agent_output(db, conv.id, "manager")
    if output:
        await bot.send_message(chat_id, f"🎯 <b>최종 결론</b>\n\n{output}")
    else:
        await bot.send_message(chat_id, "아직 완료된 manager 출력이 없습니다.")


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


_COMMAND_HANDLERS = {
    "/agents": handle_agents,
    "/mode": handle_mode,
    "/status": handle_status,
    "/stop": handle_stop,
    "/help": handle_help,
    "/start": handle_help,
    "/final": handle_final,
    "/export": handle_export,
}


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
