"""Central orchestration engine.

Receives a parsed Telegram message, manages conversation state,
runs agent pipeline, and calls back to Telegram to post results.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Callable, Awaitable

from sqlalchemy.orm import Session

from app.agents.base import AgentResult, BaseAgent
from app.agents.registry import load_agent_registry
from app.conversations.selectors import build_context_prompt, get_recent_agent_output
from app.conversations.service import ConversationService
from app.core.config import settings
from app.core.time_utils import now_utc
from app.orchestrator.router import route
from app.orchestrator.state_machine import ConversationStatus

logger = logging.getLogger(__name__)

SendFn = Callable[[str, str, int | None], Awaitable[int | None]]
"""send_fn(chat_id, text, reply_to_message_id) → telegram_message_id"""


class OrchestratorEngine:
    def __init__(self):
        self._agents: dict[str, BaseAgent] = {}
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self._agents = load_agent_registry(settings.agent_config_path)
            self._loaded = True

    def reload_agents(self) -> None:
        self._agents = load_agent_registry(settings.agent_config_path)
        self._loaded = True

    @property
    def agent_handles(self) -> set[str]:
        self._ensure_loaded()
        return set(self._agents.keys())

    async def process_message(
        self,
        db: Session,
        chat_id: str,
        text: str,
        sender_name: str,
        telegram_message_id: int | None,
        send_fn: SendFn,
        topic_id: str | None = None,
    ) -> None:
        self._ensure_loaded()
        svc = ConversationService(db)

        # ── Get or create conversation ────────────────────────────────────
        conv = svc.get_or_create_conversation(
            chat_id=chat_id,
            topic_id=topic_id,
            title=text[:100],
            mode=settings.orchestrator_default_mode,
        )
        conv_id = conv.id

        # Register user participant
        user_p = svc.get_or_create_participant(
            conversation_id=conv_id,
            handle=sender_name,
            type="user",
            display_name=sender_name,
        )

        # Save the incoming user message
        user_msg = svc.create_message(
            conversation_id=conv_id,
            raw_text=text,
            message_type="user",
            participant_id=user_p.id,
            telegram_message_id=telegram_message_id,
        )
        db.commit()

        # ── Route ─────────────────────────────────────────────────────────
        decision = route(text, conv.mode, self.agent_handles)
        if decision.new_mode:
            svc.set_conversation_mode(conv_id, decision.new_mode)
            db.commit()

        if not decision.pipeline:
            await send_fn(
                chat_id,
                "⚠️ 실행할 에이전트가 없습니다. `/agents`로 사용 가능한 에이전트를 확인하세요.",
                telegram_message_id,
            )
            return

        # ── Update status ─────────────────────────────────────────────────
        svc.update_conversation_status(conv_id, ConversationStatus.RECEIVED)
        db.commit()

        # ── Execute pipeline ──────────────────────────────────────────────
        svc.update_conversation_status(conv_id, ConversationStatus.RUNNING)
        db.commit()

        previous_output = ""
        all_outputs: list[tuple[str, str]] = []  # (handle, output)

        for handle in decision.pipeline:
            agent = self._agents.get(handle)
            if not agent:
                logger.warning("Agent '%s' not found, skipping", handle)
                continue

            # Status notification
            status_text = f"🟡 {agent.emoji} {agent.display_name}가 작업 중..."
            await send_fn(chat_id, status_text, None)

            # Build context from previous agent outputs + conversation history
            ctx_parts = []
            if previous_output:
                ctx_parts.append(f"이전 에이전트 출력:\n{previous_output}")
            history = build_context_prompt(db, conv_id, message_limit=15)
            if history:
                ctx_parts.append(f"대화 이력:\n{history}")
            context = "\n\n".join(ctx_parts)

            # Create run record
            run = svc.create_agent_run(
                conversation_id=conv_id,
                agent_handle=handle,
                trigger_message_id=user_msg.id,
                provider=agent.config.provider,
                model=agent.config.model,
            )
            db.commit()
            svc.start_agent_run(run.id)
            db.commit()

            # Execute agent
            result: AgentResult | None = None
            error: str | None = None
            try:
                result = await agent.run(text, context)
                previous_output = result.text
                all_outputs.append((handle, result.text))
            except Exception as exc:
                error = str(exc)
                logger.exception("Agent '%s' failed: %s", handle, exc)

            # Finish run record
            svc.finish_agent_run(
                run_id=run.id,
                output=result.text if result else "",
                input_snapshot=f"request={text[:500]}\ncontext={context[:1000]}",
                error=error,
            )

            if result:
                # Register agent as participant
                agent_p = svc.get_or_create_participant(
                    conversation_id=conv_id,
                    handle=handle,
                    type="agent",
                    display_name=agent.display_name,
                    provider=agent.config.provider,
                    model=agent.config.model,
                )

                # Format and save agent message
                rendered = self._render_agent_message(agent, result.text)
                svc.create_message(
                    conversation_id=conv_id,
                    raw_text=result.text,
                    rendered_text=rendered,
                    message_type="agent",
                    participant_id=agent_p.id,
                )
                db.commit()

                # Send to Telegram
                await send_fn(chat_id, rendered, None)
            else:
                err_msg = f"❌ {agent.emoji} {agent.display_name} 실행 실패: {error}"
                await send_fn(chat_id, err_msg, None)

            # Small delay between agents for UX
            await asyncio.sleep(0.3)

        # ── Final summary (pipeline/debate mode) ─────────────────────────
        if settings.orchestrator_auto_summary and len(all_outputs) > 1:
            svc.update_conversation_status(conv_id, ConversationStatus.SUMMARIZING)
            db.commit()

        svc.update_conversation_status(conv_id, ConversationStatus.DONE)
        # Reset to idle so new messages can trigger a new run
        svc.update_conversation_status(conv_id, ConversationStatus.IDLE)
        db.commit()

    def _render_agent_message(self, agent: BaseAgent, text: str) -> str:
        header = f"[{agent.emoji} {agent.display_name}]"
        return f"{header}\n{text}"

    def list_agents_info(self) -> list[dict]:
        self._ensure_loaded()
        return [
            {
                "handle": a.handle,
                "display_name": a.display_name,
                "emoji": a.emoji,
                "provider": a.config.provider,
                "model": a.config.model,
            }
            for a in self._agents.values()
        ]


# Singleton instance
orchestrator = OrchestratorEngine()
