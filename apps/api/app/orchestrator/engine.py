"""Central orchestration engine (dynamic handoff edition)."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
import re

from sqlalchemy.orm import Session

from app.agents.base import AgentResult, BaseAgent
from app.agents.registry import load_agent_registry
from app.conversations.selectors import build_context_prompt
from app.conversations.service import ConversationService
from app.core.config import settings
from app.orchestrator.router import route
from app.orchestrator.state_machine import ConversationStatus
from app.orchestrator.validator import detect_progress, validate_dynamic_handoff

logger = logging.getLogger(__name__)

_DYNAMIC_MODES = {"autonomous-lite", "autonomous"}
_SUGGESTION_ALIASES = {
    "pm": "planner",
}
_IDENTITY_TO_HANDLE = {
    "pm": "planner",
    "writer": "writer",
    "critic": "critic",
    "coder": "coder",
}
_ROLE_TO_HANDLE_ALIASES = {
    "pm": "planner",
}
_FIXED_CHAIN = ("planner", "writer", "critic", "manager")
_REQUIRED_ARTIFACT_TYPES = {
    "planner": {"brief", "decision"},
    "writer": {"draft"},
    "critic": {"review_notes"},
    "manager": {"final"},
}
_SELF_HANDOFF_PATTERNS = (
    "다음 이어서 진행해줘",
    "다음 이어서 진행",
    "다음 진행해줘",
    "다음 진행",
)
_STOP_TERMS = (
    "i'm sorry, i can't assist with that",
    "i cannot assist with that",
    "can't assist with that",
    "죄송하지만 도와드릴 수 없습니다",
    "도와드릴 수 없습니다",
)


@dataclass
class TurnExecution:
    run_id: object
    handle: str
    identity: str
    agent: BaseAgent
    context_snapshot: str
    result: AgentResult | None
    error: str | None


class OrchestratorEngine:
    def __init__(self):
        self._agents: dict[str, BaseAgent] = {}
        self._loaded = False
        self._dispatcher = None

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self._agents = load_agent_registry(settings.agent_config_path)
            self._loaded = True

    def reload_agents(self) -> None:
        self._agents = load_agent_registry(settings.agent_config_path)
        self._loaded = True
        self._dispatcher = None

    def _get_dispatcher(self):
        if self._dispatcher is None:
            from app.adapters.telegram.dispatcher import BotDispatcher
            from app.adapters.telegram.outbound import MultiBotOutbound
            from app.adapters.telegram.registry import BotRegistry

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

    async def process_message(
        self,
        db: Session,
        chat_id: str,
        text: str,
        sender_name: str,
        telegram_message_id: int | None,
        send_fn=None,
        topic_id: str | None = None,
        inbound_identity: str | None = None,
        chat_type: str | None = None,
        dispatcher_override=None,
        available_handles: list[str] | None = None,
    ) -> None:
        self._ensure_loaded()
        svc = ConversationService(db)
        dispatcher = dispatcher_override or self._get_dispatcher()
        platform = "web" if chat_type == "web" else "telegram"
        allowed_handles = self._normalize_allowed_handles(available_handles)

        conv = svc.get_or_create_conversation(
            chat_id=chat_id,
            topic_id=topic_id,
            title=text[:100],
            mode=settings.orchestrator_default_mode,
            platform=platform,
            selected_agents=sorted(allowed_handles),
        )
        conv_id = conv.id

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
        svc.update_runtime_state(
            conv_id,
            anchor_message_id=telegram_message_id,
            last_message_id=telegram_message_id,
            is_waiting_user=False,
            done=False,
            needs_user_input=False,
            last_user_goal_snapshot=text[:1000],
        )
        db.commit()

        forced_direct_handle = None
        if chat_type == "private":
            forced_direct_handle = self._resolve_handle_from_identity(inbound_identity)
        mention_aliases = self._build_mention_aliases(dispatcher)
        decision = route(
            text,
            conv.mode,
            allowed_handles,
            forced_direct_handle=forced_direct_handle,
            mention_aliases=mention_aliases,
        )
        if decision.new_mode:
            svc.set_conversation_mode(conv_id, decision.new_mode)
            db.commit()

        if not decision.pipeline:
            await _fallback_send(
                dispatcher,
                chat_id,
                "⚠️ 실행할 에이전트가 없습니다. <code>/agents</code>로 목록을 확인하세요.",
            )
            return

        svc.update_conversation_status(conv_id, ConversationStatus.RECEIVED)
        db.commit()
        svc.update_conversation_status(conv_id, ConversationStatus.RUNNING)
        db.commit()

        if decision.mode in _DYNAMIC_MODES:
            await self._run_dynamic(
                db=db,
                svc=svc,
                dispatcher=dispatcher,
                conversation_id=conv_id,
                user_message_id=user_msg.id,
                chat_id=chat_id,
                request_text=text,
                start_agent=decision.pipeline[0],
                anchor_message_id=telegram_message_id,
                mode=decision.mode,
                requested_handles=decision.mentioned_handles,
                social_collaboration=decision.social_collaboration,
                mention_aliases=mention_aliases,
                allowed_handles=allowed_handles,
            )
        else:
            await self._run_guided(
                db=db,
                svc=svc,
                dispatcher=dispatcher,
                conversation_id=conv_id,
                user_message_id=user_msg.id,
                chat_id=chat_id,
                request_text=text,
                pipeline=decision.pipeline,
                anchor_message_id=telegram_message_id,
                allowed_handles=allowed_handles,
            )

    async def _run_guided(
        self,
        db: Session,
        svc: ConversationService,
        dispatcher,
        conversation_id,
        user_message_id,
        chat_id: str,
        request_text: str,
        pipeline: list[str],
        anchor_message_id: int | None,
        allowed_handles: set[str],
    ) -> None:
        previous_output = ""
        last_msg_id = anchor_message_id
        last_task_status: str | None = None
        history_agents: list[str] = []
        failed = False

        effective_pipeline = [handle for handle in pipeline if handle in allowed_handles]
        for idx, handle in enumerate(effective_pipeline):
            execution = await self._invoke_agent_turn(
                db=db,
                svc=svc,
                dispatcher=dispatcher,
                conversation_id=conversation_id,
                user_message_id=user_message_id,
                chat_id=chat_id,
                request_text=request_text,
                handle=handle,
                previous_output=previous_output,
                turn_index=idx,
                history_agents=history_agents,
                last_task_status=last_task_status,
            )
            if execution.error or not execution.result:
                failed = True
                await _fallback_send(
                    dispatcher,
                    chat_id,
                    f"❌ {handle} 실행 중 오류가 발생했습니다: {execution.error or 'unknown'}",
                )
                break

            next_handle = effective_pipeline[idx + 1] if idx + 1 < len(effective_pipeline) else None
            dispatch_result = await dispatcher.dispatch(
                role=handle,
                chat_id=chat_id,
                body=execution.result.visible_message,
                next_role=next_handle,
                reply_to_message_id=last_msg_id,
            )
            progress = detect_progress(last_task_status, execution.result.task_status, execution.result.done)
            await self._persist_turn_success(
                db=db,
                svc=svc,
                dispatcher=dispatcher,
                conversation_id=conversation_id,
                execution=execution,
                dispatch_result=dispatch_result,
                request_text=request_text,
                suggested_next_agent=execution.result.suggested_next_agent,
                approved_next_agent=next_handle,
                fallback_applied=False,
                validation_reason="guided_order",
                progress_detected=progress,
                termination_reason="guided_end" if next_handle is None else None,
            )
            history_agents.append(handle)
            previous_output = execution.result.visible_message
            last_task_status = execution.result.task_status or last_task_status
            if dispatch_result.telegram_message_id:
                last_msg_id = dispatch_result.telegram_message_id
            await asyncio.sleep(0.2)

        if failed:
            svc.update_runtime_state(
                conversation_id,
                is_waiting_user=True,
                needs_user_input=True,
                done=False,
            )
            svc.update_conversation_status(conversation_id, ConversationStatus.PAUSED)
        else:
            svc.update_runtime_state(
                conversation_id,
                is_waiting_user=False,
                needs_user_input=False,
                done=True,
                last_message_id=last_msg_id,
            )
            svc.update_conversation_status(conversation_id, ConversationStatus.DONE)
            svc.update_conversation_status(conversation_id, ConversationStatus.IDLE)
        db.commit()

    async def _run_dynamic(
        self,
        db: Session,
        svc: ConversationService,
        dispatcher,
        conversation_id,
        user_message_id,
        chat_id: str,
        request_text: str,
        start_agent: str,
        anchor_message_id: int | None,
        mode: str,
        requested_handles: list[str] | None = None,
        social_collaboration: bool = False,
        mention_aliases: dict[str, str] | None = None,
        allowed_handles: set[str] | None = None,
    ) -> None:
        conv = svc.get_conversation(conversation_id)
        team_handles = self._normalize_allowed_handles(list(allowed_handles or []))
        fixed_chain_enabled = self._should_use_fixed_chain(mode, social_collaboration)
        missing_chain_handles = self._missing_fixed_chain_handles(team_handles) if fixed_chain_enabled else []
        turn_limit = conv.turn_limit if conv else settings.orchestrator_max_turns
        max_turns = min(max(1, turn_limit), max(1, settings.orchestrator_max_turns))

        previous_output = ""
        current_handle = start_agent if start_agent in team_handles else self._default_handle(team_handles)
        history_agents: list[str] = list((conv.last_n_agents if conv else []) or [])
        no_progress_streak = int((conv.loop_guard_counter if conv else 0) or 0)
        last_task_status: str | None = conv.task_status if conv else None
        last_msg_id = (conv.last_message_id if conv else None) or anchor_message_id
        termination_reason: str | None = None
        fallback_message: str | None = None

        if missing_chain_handles:
            missing = ", ".join(missing_chain_handles)
            await dispatcher.dispatch(
                role="planner",
                chat_id=chat_id,
                body=f"이 팀 구성으로는 문서 협업 체인을 시작할 수 없어요. 필요한 역할: {missing}",
                next_role=None,
                reply_to_message_id=last_msg_id,
            )
            svc.update_runtime_state(
                conversation_id,
                is_waiting_user=True,
                needs_user_input=True,
                done=False,
                approved_next_agent=missing_chain_handles[0],
            )
            svc.update_conversation_status(conversation_id, ConversationStatus.PAUSED)
            db.commit()
            return

        for turn_index in range(max_turns):
            execution = await self._invoke_agent_turn(
                db=db,
                svc=svc,
                dispatcher=dispatcher,
                conversation_id=conversation_id,
                user_message_id=user_message_id,
                chat_id=chat_id,
                request_text=request_text,
                handle=current_handle,
                previous_output=previous_output,
                turn_index=turn_index,
                history_agents=history_agents,
                last_task_status=last_task_status,
            )
            if execution.error or not execution.result:
                termination_reason = "agent_error"
                fallback_message = f"❌ {current_handle} 실행 중 오류가 발생했습니다: {execution.error or 'unknown'}"
                break

            suggested = self._normalize_suggestion(
                execution.result.suggested_next_agent,
                allowed_handles=team_handles,
                visible_message=execution.result.visible_message,
                mention_aliases=mention_aliases,
            )
            execution.result.suggested_next_agent = suggested
            artifact_type = self._artifact_type_of(execution.result)
            required_artifact_types = self._required_artifact_types_for(
                current_handle,
                fixed_chain_enabled,
            )
            expected_next_handle = self._expected_fixed_chain_next(
                current_handle,
                fixed_chain_enabled,
            )
            if fixed_chain_enabled and current_handle != "manager":
                execution.result.done = False
            if fixed_chain_enabled and current_handle == "manager" and artifact_type == "final":
                execution.result.done = True
            progress = detect_progress(last_task_status, execution.result.task_status, execution.result.done)
            no_progress_streak = 0 if progress else (no_progress_streak + 1)

            validation = validate_dynamic_handoff(
                mode=mode,
                known_handles=team_handles,
                current_agent=current_handle,
                suggested_next_agent=suggested,
                expected_next_handle=expected_next_handle,
                required_artifact_types=required_artifact_types,
                produced_artifact_type=artifact_type,
                done=execution.result.done,
                needs_user_input=execution.result.needs_user_input,
                turn_index=turn_index,
                max_turns=max_turns,
                history_agents=history_agents + [current_handle],
                same_agent_streak_limit=settings.orchestrator_same_agent_streak_limit,
                recent_pattern_repeat_limit=settings.orchestrator_recent_pattern_repeat_limit,
                no_progress_streak=no_progress_streak,
                max_no_progress_handoffs=settings.orchestrator_max_no_progress_handoffs,
            )
            if self._should_stop_early(execution.result):
                validation.terminate = True
                validation.termination_reason = "unsafe_or_refusal"
                validation.approved_next_agent = None
                validation.validation_reason = "refusal_guard"
            if (
                not fixed_chain_enabled
                and
                mode in ("autonomous-lite", "autonomous")
                and requested_handles
                and validation.approved_next_agent == "planner"
            ):
                seen = set(history_agents + [current_handle])
                pending = [h for h in requested_handles if h in team_handles and h not in seen]
                if pending and not execution.result.done and not execution.result.needs_user_input:
                    validation.approved_next_agent = pending[0]
                    validation.fallback_applied = True
                    validation.validation_reason = "requested_handle_priority"
            if (
                not fixed_chain_enabled
                and
                mode in ("autonomous-lite", "autonomous")
                and social_collaboration
                and requested_handles
                and not execution.result.needs_user_input
            ):
                ordered = [
                    h for h in requested_handles if h in team_handles and h != "planner"
                ] or [h for h in requested_handles if h in team_handles]
                seen = set(history_agents + [current_handle])
                pending = [h for h in ordered if h not in seen]
                if pending and not execution.result.done:
                    validation.approved_next_agent = pending[0]
                    validation.fallback_applied = True
                    validation.validation_reason = "social_round_robin"
                else:
                    validation.terminate = True
                    validation.termination_reason = "done"
                    validation.approved_next_agent = None
                    validation.validation_reason = "social_round_complete"
                    validation.fallback_applied = False
            if social_collaboration and execution.result.done:
                validation.terminate = True
                validation.termination_reason = "done"
                validation.approved_next_agent = None
                validation.validation_reason = "social_done"
                validation.fallback_applied = False

            next_role = None if validation.terminate else validation.approved_next_agent
            dispatch_result = await dispatcher.dispatch(
                role=current_handle,
                chat_id=chat_id,
                body=execution.result.visible_message,
                next_role=next_role,
                reply_to_message_id=last_msg_id,
                include_handoff_hint=not social_collaboration,
            )
            await self._persist_turn_success(
                db=db,
                svc=svc,
                dispatcher=dispatcher,
                conversation_id=conversation_id,
                execution=execution,
                dispatch_result=dispatch_result,
                request_text=request_text,
                suggested_next_agent=suggested,
                approved_next_agent=validation.approved_next_agent,
                fallback_applied=validation.fallback_applied,
                validation_reason=validation.validation_reason,
                progress_detected=progress,
                termination_reason=validation.termination_reason,
            )

            history_agents.append(current_handle)
            history_agents = history_agents[-8:]
            previous_output = execution.result.visible_message
            last_task_status = execution.result.task_status or last_task_status
            if dispatch_result.telegram_message_id:
                last_msg_id = dispatch_result.telegram_message_id

            conv_now = svc.get_conversation(conversation_id)
            conf_trend = list((conv_now.confidence_trend if conv_now else []) or [])
            if execution.result.confidence is not None:
                conf_trend.append(execution.result.confidence)
                conf_trend = conf_trend[-10:]
            svc.update_runtime_state(
                conversation_id,
                current_agent=current_handle,
                current_identity=dispatch_result.identity,
                suggested_next_agent=suggested,
                approved_next_agent=validation.approved_next_agent,
                last_handoff_reason=execution.result.handoff_reason,
                task_status=execution.result.task_status,
                done=execution.result.done,
                needs_user_input=execution.result.needs_user_input,
                loop_guard_counter=no_progress_streak,
                last_n_agents=history_agents,
                last_message_id=last_msg_id,
                completion_score=int((execution.result.confidence or 0.0) * 100)
                if execution.result.confidence is not None
                else None,
                confidence_trend=conf_trend,
                is_waiting_user=execution.result.needs_user_input,
            )
            db.commit()

            if validation.terminate:
                termination_reason = validation.termination_reason or "terminated"
                break

            next_candidate = validation.approved_next_agent or self._default_handle(team_handles)
            if next_candidate == current_handle and self._looks_like_self_handoff(execution.result.visible_message):
                next_candidate = self._default_handle(team_handles)
            current_handle = next_candidate
            await asyncio.sleep(0.2)

        if not termination_reason:
            termination_reason = "max_turns"

        if fallback_message:
            await _fallback_send(dispatcher, chat_id, fallback_message)

        if termination_reason == "done":
            svc.update_runtime_state(
                conversation_id,
                is_waiting_user=False,
                needs_user_input=False,
                done=True,
            )
            svc.update_conversation_status(conversation_id, ConversationStatus.DONE)
            svc.update_conversation_status(conversation_id, ConversationStatus.IDLE)
        else:
            if termination_reason == "needs_user_input":
                await dispatcher.dispatch(
                    role="planner",
                    chat_id=chat_id,
                    body="다음 진행을 위해 사용자 입력이 필요합니다. 필요한 정보나 선택지를 알려주세요.",
                    next_role=None,
                    reply_to_message_id=last_msg_id,
                )
            elif termination_reason == "unsafe_or_refusal":
                await dispatcher.dispatch(
                    role="planner",
                    chat_id=chat_id,
                    body="요청이 안전 정책 또는 해석 문제로 중단됐어요. 표현을 바꿔 다시 요청해주면 팀이 이어서 협업할게요.",
                    next_role=None,
                    reply_to_message_id=last_msg_id,
                )
            elif termination_reason == "max_turns":
                await dispatcher.dispatch(
                    role="planner",
                    chat_id=chat_id,
                    body="안정성 보호를 위해 현재 턴에서 멈췄어요. 방향을 지정해주면 이어서 진행할게요.",
                    next_role=None,
                    reply_to_message_id=last_msg_id,
                )
            elif termination_reason == "missing_required_artifact":
                await dispatcher.dispatch(
                    role="planner",
                    chat_id=chat_id,
                    body="팀 협업 단계에 필요한 결과물이 누락돼서 멈췄어요. 역할별 산출물을 다시 확인해야 합니다.",
                    next_role=None,
                    reply_to_message_id=last_msg_id,
                )
            elif termination_reason == "missing_required_team_member":
                await dispatcher.dispatch(
                    role="planner",
                    chat_id=chat_id,
                    body="현재 채팅방에 writer/critic/manager 역할이 모두 있어야 팀 협업을 진행할 수 있어요.",
                    next_role=None,
                    reply_to_message_id=last_msg_id,
                )
            svc.update_runtime_state(
                conversation_id,
                is_waiting_user=True,
                needs_user_input=True,
                done=False,
            )
            svc.update_conversation_status(conversation_id, ConversationStatus.PAUSED)
        db.commit()

    async def _invoke_agent_turn(
        self,
        db: Session,
        svc: ConversationService,
        dispatcher,
        conversation_id,
        user_message_id,
        chat_id: str,
        request_text: str,
        handle: str,
        previous_output: str,
        turn_index: int,
        history_agents: list[str],
        last_task_status: str | None,
    ) -> TurnExecution:
        agent = self._agents.get(handle)
        if not agent:
            return TurnExecution(
                run_id=None,
                handle=handle,
                identity="pm",
                agent=self._agents.get("planner") or list(self._agents.values())[0],
                context_snapshot="",
                result=None,
                error=f"unknown agent: {handle}",
            )

        context = self._build_context(
            svc=svc,
            db=db,
            conversation_id=conversation_id,
            previous_output=previous_output,
            turn_index=turn_index,
            history_agents=history_agents,
            last_task_status=last_task_status,
        )
        identity = dispatcher.resolve_identity(handle)
        run = svc.create_agent_run(
            conversation_id=conversation_id,
            agent_handle=handle,
            trigger_message_id=user_message_id,
            provider=agent.config.provider,
            model=agent.config.model,
            speaker_identity=identity,
            input_context_snapshot=context[:2000],
        )
        db.commit()
        svc.start_agent_run(run.id)
        db.commit()

        try:
            result = await agent.run(request_text, context)
            return TurnExecution(
                run_id=run.id,
                handle=handle,
                identity=identity,
                agent=agent,
                context_snapshot=context[:2000],
                result=result,
                error=None,
            )
        except Exception as exc:
            err = str(exc)
            logger.exception("Agent '%s' failed: %s", handle, exc)
            svc.finish_agent_run(
                run_id=run.id,
                output="",
                input_snapshot=f"request={request_text[:300]}",
                input_context_snapshot=context[:2000],
                error=err,
            )
            db.commit()
            return TurnExecution(
                run_id=run.id,
                handle=handle,
                identity=identity,
                agent=agent,
                context_snapshot=context[:2000],
                result=None,
                error=err,
            )

    async def _persist_turn_success(
        self,
        db: Session,
        svc: ConversationService,
        dispatcher,
        conversation_id,
        execution: TurnExecution,
        dispatch_result,
        request_text: str,
        suggested_next_agent: str | None,
        approved_next_agent: str | None,
        fallback_applied: bool,
        validation_reason: str,
        progress_detected: bool,
        termination_reason: str | None,
    ) -> None:
        result = execution.result
        if not result:
            return

        svc.finish_agent_run(
            run_id=execution.run_id,
            output=result.text,
            input_snapshot=f"request={request_text[:300]}",
            input_context_snapshot=execution.context_snapshot,
            output_message_id=dispatch_result.telegram_message_id,
            suggested_next_agent=suggested_next_agent,
            approved_next_agent=approved_next_agent,
            handoff_reason=result.handoff_reason,
            validation_result={"reason": validation_reason},
            fallback_applied=fallback_applied,
            progress_detected=progress_detected,
            termination_reason=termination_reason,
        )

        agent_p = svc.get_or_create_participant(
            conversation_id=conversation_id,
            handle=execution.handle,
            type="agent",
            display_name=execution.agent.display_name,
            provider=execution.agent.config.provider,
            model=execution.agent.config.model,
        )
        bot_info = dispatcher._registry.get(execution.identity)
        svc.create_message(
            conversation_id=conversation_id,
            raw_text=result.text,
            rendered_text=dispatch_result.rendered_text,
            message_type="agent",
            participant_id=agent_p.id,
            telegram_message_id=dispatch_result.telegram_message_id,
            speaker_role=execution.handle,
            speaker_identity=execution.identity,
            speaker_bot_username=bot_info.username if bot_info else None,
            visible_message=result.visible_message,
            suggested_next_agent=suggested_next_agent,
            approved_next_agent=approved_next_agent,
            handoff_reason=result.handoff_reason,
            task_status=result.task_status,
            done=result.done,
            needs_user_input=result.needs_user_input,
            is_progress_turn=progress_detected,
            is_agent_message=True,
        )
        artifact_update = result.artifact_update or {}
        artifact_type = str(artifact_update.get("type") or "").strip().lower()
        artifact_content = str(artifact_update.get("content") or "").strip()
        if artifact_type and artifact_content:
            svc.create_or_replace_artifact(
                conversation_id=conversation_id,
                artifact_type=artifact_type,
                content=artifact_content,
                created_by_handle=execution.handle,
                source_run_id=execution.run_id,
                replace_latest=bool(artifact_update.get("replace_latest", True)),
            )
        conv_obj = svc.get_conversation(conversation_id)
        if conv_obj and artifact_type:
            conv_obj.artifact_requested = True
            if artifact_type == "final":
                conv_obj.export_ready = True
        if conv_obj and dispatch_result.telegram_message_id:
            ids = dict(conv_obj.last_message_ids or {})
            ids[execution.identity] = dispatch_result.telegram_message_id
            conv_obj.last_message_ids = ids
        db.commit()

    def _build_context(
        self,
        svc: ConversationService,
        db: Session,
        conversation_id,
        previous_output: str,
        turn_index: int,
        history_agents: list[str],
        last_task_status: str | None,
    ) -> str:
        ctx_parts: list[str] = []
        conv = svc.get_conversation(conversation_id)
        if conv and conv.last_user_goal_snapshot:
            ctx_parts.append(f"최신 사용자 목표:\n{conv.last_user_goal_snapshot[:1200]}")
        if previous_output:
            trimmed = previous_output[:1200] + "…" if len(previous_output) > 1200 else previous_output
            ctx_parts.append(f"직전 에이전트 발화:\n{trimmed}")
        latest_by_type: dict[str, str] = {}
        for artifact in svc.list_artifacts(conversation_id, limit=12):
            if artifact.artifact_type in latest_by_type:
                continue
            body = artifact.content[:1500] + "…" if len(artifact.content) > 1500 else artifact.content
            latest_by_type[artifact.artifact_type] = body
        artifact_lines = [
            f"[{artifact_type}] {latest_by_type[artifact_type]}"
            for artifact_type in ("brief", "draft", "review_notes", "decision", "final")
            if artifact_type in latest_by_type
        ]
        if artifact_lines:
            ctx_parts.append("공유 작업공간:\n" + "\n\n".join(artifact_lines))
        history = build_context_prompt(db, conversation_id, message_limit=10, max_chars=2500)
        if history:
            ctx_parts.append(f"대화 이력:\n{history}")
        runtime_hint = (
            f"런타임 상태:\n"
            f"- turn_index: {turn_index}\n"
            f"- last_task_status: {last_task_status or 'none'}\n"
            f"- recent_agents: {', '.join(history_agents[-4:]) if history_agents else 'none'}"
        )
        ctx_parts.append(runtime_hint)
        return "\n\n".join(ctx_parts)

    def _normalize_suggestion(
        self,
        suggested: str | None,
        allowed_handles: set[str],
        visible_message: str | None = None,
        mention_aliases: dict[str, str] | None = None,
    ) -> str | None:
        if not suggested:
            return self._infer_handle_from_text(visible_message or "", mention_aliases, allowed_handles)
        candidate = suggested.strip().lower().lstrip("@")
        candidate = _SUGGESTION_ALIASES.get(candidate, candidate)
        if candidate in allowed_handles:
            return candidate
        for handle in allowed_handles:
            if candidate.startswith(handle):
                return handle
        # username mention fallback: @IdocFlowWriterBot -> writer
        aliases = mention_aliases or self._build_mention_aliases(self._get_dispatcher())
        mapped = aliases.get(candidate)
        if mapped and mapped in allowed_handles:
            return mapped
        return self._infer_handle_from_text(visible_message or "", aliases, allowed_handles)

    def _resolve_handle_from_identity(self, inbound_identity: str | None) -> str:
        identity = (inbound_identity or "pm").strip().lower()
        handle = _IDENTITY_TO_HANDLE.get(identity, "planner")
        return handle if handle in self.agent_handles else "planner"

    def _build_mention_aliases(self, dispatcher) -> dict[str, str]:
        aliases: dict[str, str] = {}
        for handle in self.agent_handles:
            aliases[handle] = handle
            identity = dispatcher.resolve_identity(handle)
            bot = dispatcher._registry.get(identity)
            if bot and bot.username:
                aliases[bot.username.lower()] = handle
        # keep role-style aliases too
        for k, v in _ROLE_TO_HANDLE_ALIASES.items():
            aliases[k] = v
        return aliases

    def _infer_handle_from_text(
        self,
        text: str,
        mention_aliases: dict[str, str] | None,
        allowed_handles: set[str],
    ) -> str | None:
        if not text:
            return None
        aliases = mention_aliases or self._build_mention_aliases(self._get_dispatcher())
        tokens = [t.lower() for t in re.findall(r"@([a-zA-Z0-9_]+)", text)]
        for token in tokens:
            mapped = aliases.get(token)
            if mapped and mapped in allowed_handles:
                return mapped
        return None

    def _should_use_fixed_chain(self, mode: str, social_collaboration: bool) -> bool:
        return mode in ("autonomous-lite", "autonomous") and not social_collaboration

    def _missing_fixed_chain_handles(self, allowed_handles: set[str]) -> list[str]:
        return [handle for handle in _FIXED_CHAIN if handle not in allowed_handles]

    def _required_artifact_types_for(
        self,
        current_handle: str,
        fixed_chain_enabled: bool,
    ) -> set[str] | None:
        if not fixed_chain_enabled:
            return None
        return _REQUIRED_ARTIFACT_TYPES.get(current_handle)

    def _expected_fixed_chain_next(
        self,
        current_handle: str,
        fixed_chain_enabled: bool,
    ) -> str | None:
        if not fixed_chain_enabled or current_handle not in _FIXED_CHAIN:
            return None
        idx = _FIXED_CHAIN.index(current_handle)
        if idx + 1 >= len(_FIXED_CHAIN):
            return None
        return _FIXED_CHAIN[idx + 1]

    def _artifact_type_of(self, result: AgentResult | None) -> str | None:
        if not result:
            return None
        artifact_update = result.artifact_update or {}
        artifact_type = str(artifact_update.get("type") or "").strip().lower()
        return artifact_type or None

    def _normalize_allowed_handles(self, available_handles: list[str] | None) -> set[str]:
        handles = {handle for handle in (available_handles or []) if handle in self.agent_handles}
        if not handles:
            handles = set(self.agent_handles)
        if "planner" in self.agent_handles:
            handles.add("planner")
        return handles

    def _default_handle(self, allowed_handles: set[str]) -> str:
        if "planner" in allowed_handles:
            return "planner"
        ordered = sorted(allowed_handles)
        return ordered[0] if ordered else "planner"

    def _looks_like_self_handoff(self, visible_message: str) -> bool:
        lowered = (visible_message or "").lower()
        if not lowered:
            return False
        return any(pat in lowered for pat in _SELF_HANDOFF_PATTERNS)

    def _should_stop_early(self, result: AgentResult) -> bool:
        text = f"{result.visible_message}\n{result.text}".lower()
        return any(term in text for term in _STOP_TERMS)

    def list_agents_info(self) -> list[dict]:
        self._ensure_loaded()
        dispatcher = self._get_dispatcher()
        infos = []
        for a in self._agents.values():
            identity = dispatcher.resolve_identity(a.handle)
            bot = dispatcher._registry.get(identity)
            infos.append(
                {
                    "handle": a.handle,
                    "display_name": a.display_name,
                    "emoji": a.emoji,
                    "identity": identity,
                    "bot_username": bot.username if bot else None,
                    "provider": a.config.provider,
                    "model": a.config.model,
                }
            )
        return infos


async def _fallback_send(dispatcher, chat_id: str, text: str) -> None:
    inbound_id = dispatcher._registry.inbound_identity
    await dispatcher.dispatch_status(inbound_id, chat_id, text)


orchestrator = OrchestratorEngine()
