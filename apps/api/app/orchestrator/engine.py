"""Central orchestration engine (multi-bot edition).

Receives a parsed Telegram message, manages conversation state,
runs agent pipeline, and dispatches each turn via the correct bot identity.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy.orm import Session

from app.agents.base import AgentResult, BaseAgent
from app.agents.registry import load_agent_registry
from app.conversations.selectors import build_context_prompt
from app.conversations.service import ConversationService
from app.core.config import settings
from app.core.time_utils import now_utc
from app.orchestrator.router import route
from app.orchestrator.state_machine import ConversationStatus

logger = logging.getLogger(__name__)


class OrchestratorEngine:
    def __init__(self):
        self._agents: dict[str, BaseAgent] = {}
        self._loaded = False
        self._dispatcher = None  # set lazily via _ensure_dispatcher

    # ── Setup ─────────────────────────────────────────────────────────────

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self._agents = load_agent_registry(settings.agent_config_path)
            self._loaded = True

    def reload_agents(self) -> None:
        self._agents = load_agent_registry(settings.agent_config_path)
        self._loaded = True
        self._dispatcher = None  # force dispatcher reload too

    def _get_dispatcher(self):
        """Lazily initialize and return the BotDispatcher."""
        if self._dispatcher is None:
            from app.adapters.telegram.registry import BotRegistry
            from app.adapters.telegram.outbound import MultiBotOutbound
            from app.adapters.telegram.dispatcher import BotDispatcher
            reg = BotRegistry()
            reg.load(settings.agent_config_path)
            outbound = MultiBotOutbound(reg)
            disp = BotDispatcher(reg, outbound)
            disp.load(settings.agent_config_path)
            self._dispatcher = disp
        return self._dispatcher

    @property
    def agent_handles(self) -> set[str]:
        self._ensure_loaded()
        return set(self._agents.keys())

    # ── Main entry point ──────────────────────────────────────────────────

    async def process_message(
        self,
        db: Session,
        chat_id: str,
        text: str,
        sender_name: str,
        telegram_message_id: int | None,
        # send_fn kept for compatibility but dispatcher is preferred
        send_fn=None,
        topic_id: str | None = None,
    ) -> None:
        self._ensure_loaded()
        svc = ConversationService(db)
        dispatcher = self._get_dispatcher()

        # ── Conversation ─────────────────────────────────────────────────
        conv = svc.get_or_create_conversation(
            chat_id=chat_id,
            topic_id=topic_id,
            title=text[:100],
            mode=settings.orchestrator_default_mode,
        )
        conv_id = conv.id

        # ── User participant + message ────────────────────────────────────
        user_p = svc.get_or_create_participant(
            conversation_id=conv_id,
            handle=sender_name,
            type="user",
            display_name=sender_name,
        )
        user_msg = svc.create_message(
            conversation_id=conv_id,
            raw_text=text,
            message_type="user",
            participant_id=user_p.id,
            telegram_message_id=telegram_message_id,
            is_agent_message=False,
        )
        db.commit()

        # Track anchor message id for reply chain
        anchor_msg_id = telegram_message_id

        # ── Route ─────────────────────────────────────────────────────────
        decision = route(text, conv.mode, self.agent_handles)
        if decision.new_mode:
            svc.set_conversation_mode(conv_id, decision.new_mode)
            db.commit()

        if not decision.pipeline:
            await _fallback_send(dispatcher, chat_id, anchor_msg_id,
                                 "⚠️ 실행할 에이전트가 없습니다. <code>/agents</code>로 목록을 확인하세요.")
            return

        # ── State transition ──────────────────────────────────────────────
        svc.update_conversation_status(conv_id, ConversationStatus.RECEIVED)
        db.commit()
        svc.update_conversation_status(conv_id, ConversationStatus.RUNNING)
        db.commit()

        # ── Pipeline execution ────────────────────────────────────────────
        previous_output = ""
        # reply chain: each bot replies to the previous bot's message
        last_msg_id: int | None = anchor_msg_id

        for idx, handle in enumerate(decision.pipeline):
            agent = self._agents.get(handle)
            if not agent:
                logger.warning("Agent '%s' not found, skipping", handle)
                continue

            # Determine next role for mention (skip if last)
            is_last = idx == len(decision.pipeline) - 1
            next_handle = decision.pipeline[idx + 1] if not is_last else None

            # Status notification via inbound identity (pm)
            inbound_id = dispatcher._registry.inbound_identity
            status_text = f"🟡 {agent.emoji} {agent.display_name}가 작업 중..."
            await dispatcher.dispatch_status(inbound_id, chat_id, status_text)

            # Build context (토큰 절약: 이전 출력 1000자, 히스토리 5개 메시지)
            ctx_parts = []
            if previous_output:
                trimmed = previous_output[:1000] + "…" if len(previous_output) > 1000 else previous_output
                ctx_parts.append(f"이전 에이전트 출력:\n{trimmed}")
            history = build_context_prompt(db, conv_id, message_limit=5, max_chars=2000)
            if history:
                ctx_parts.append(f"대화 이력:\n{history}")
            context = "\n\n".join(ctx_parts)

            # Agent run record
            identity = dispatcher.resolve_identity(handle)
            run = svc.create_agent_run(
                conversation_id=conv_id,
                agent_handle=handle,
                trigger_message_id=user_msg.id,
                provider=agent.config.provider,
                model=agent.config.model,
                speaker_identity=identity,
            )
            db.commit()
            svc.start_agent_run(run.id)
            db.commit()

            # Execute agent LLM call
            result: AgentResult | None = None
            error: str | None = None
            try:
                result = await agent.run(text, context)
                previous_output = result.text
            except Exception as exc:
                error = str(exc)
                logger.exception("Agent '%s' failed: %s", handle, exc)

            # Dispatch via correct bot identity
            tg_sent_id: int | None = None
            if result:
                dispatch_result = await dispatcher.dispatch(
                    role=handle,
                    chat_id=chat_id,
                    body=result.text,
                    next_role=next_handle,
                    reply_to_message_id=last_msg_id,
                )
                tg_sent_id = dispatch_result.telegram_message_id
                last_msg_id = tg_sent_id or last_msg_id
                rendered = dispatch_result.rendered_text
            else:
                rendered = f"❌ {agent.emoji} {agent.display_name} 실패: {error}"
                await _fallback_send(dispatcher, chat_id, last_msg_id, rendered)

            # Finish agent run
            svc.finish_agent_run(
                run_id=run.id,
                output=result.text if result else "",
                input_snapshot=f"request={text[:300]}\nctx={context[:500]}",
                error=error,
                output_message_id=tg_sent_id,
            )

            # Save agent message with identity fields
            agent_p = svc.get_or_create_participant(
                conversation_id=conv_id,
                handle=handle,
                type="agent",
                display_name=agent.display_name,
                provider=agent.config.provider,
                model=agent.config.model,
            )
            bot_info = dispatcher._registry.get(identity)
            svc.create_message(
                conversation_id=conv_id,
                raw_text=result.text if result else "",
                rendered_text=rendered,
                message_type="agent",
                participant_id=agent_p.id,
                telegram_message_id=tg_sent_id,
                speaker_role=handle,
                speaker_identity=identity,
                speaker_bot_username=bot_info.username if bot_info else None,
                is_agent_message=True,
            )

            # Persist reply chain state
            conv_obj = svc.get_conversation(conv_id)
            if conv_obj and tg_sent_id:
                ids = dict(conv_obj.last_message_ids or {})
                ids[identity] = tg_sent_id
                conv_obj.last_message_ids = ids
            db.commit()

            await asyncio.sleep(0.3)

        # ── Wrap up ───────────────────────────────────────────────────────
        svc.update_conversation_status(conv_id, ConversationStatus.DONE)
        svc.update_conversation_status(conv_id, ConversationStatus.IDLE)
        db.commit()

    def list_agents_info(self) -> list[dict]:
        self._ensure_loaded()
        dispatcher = self._get_dispatcher()
        infos = []
        for a in self._agents.values():
            identity = dispatcher.resolve_identity(a.handle)
            bot = dispatcher._registry.get(identity)
            infos.append({
                "handle": a.handle,
                "display_name": a.display_name,
                "emoji": a.emoji,
                "identity": identity,
                "bot_username": bot.username if bot else None,
                "provider": a.config.provider,
                "model": a.config.model,
            })
        return infos


async def _fallback_send(dispatcher, chat_id: str, reply_to: int | None, text: str) -> None:
    """Send via inbound bot as fallback."""
    inbound_id = dispatcher._registry.inbound_identity
    await dispatcher.dispatch_status(inbound_id, chat_id, text)


# Singleton
orchestrator = OrchestratorEngine()
