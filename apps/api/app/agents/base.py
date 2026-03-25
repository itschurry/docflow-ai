from abc import ABC, abstractmethod
from dataclasses import dataclass
import json
import re
from typing import Any

from app.services.llm_provider import (
    AnthropicProvider,
    LLMProvider,
    OpenAIProvider,
    StubLLMProvider,
)
from app.core.config import settings


@dataclass
class AgentConfig:
    handle: str
    display_name: str
    emoji: str
    provider: str
    model: str
    max_tokens: int
    system_prompt: str
    enabled: bool = True


@dataclass
class AgentResult:
    handle: str
    display_name: str
    emoji: str
    text: str
    provider: str
    model: str
    visible_message: str
    suggested_next_agent: str | None
    handoff_reason: str
    task_status: str
    done: bool
    needs_user_input: bool
    confidence: float | None = None
    alternative_next_agents: list[str] | None = None
    missing_information: list[str] | None = None
    recommended_mode: str | None = None
    artifact_update: dict[str, Any] | None = None
    fallback_used: bool = False
    fallback_reason: str | None = None


def build_provider(provider: str, model: str) -> LLMProvider:
    # LLM_PROVIDER=stub 환경변수로 전역 오버라이드 가능 (테스트용)
    import os
    if os.environ.get("LLM_PROVIDER", "").lower() == "stub":
        return StubLLMProvider()
    if provider == "openai" and settings.openai_api_key:
        return OpenAIProvider(api_key=settings.openai_api_key, model=model)
    if provider == "anthropic" and settings.anthropic_api_key:
        return AnthropicProvider(api_key=settings.anthropic_api_key, model=model)
    return StubLLMProvider()


class BaseAgent(ABC):
    def __init__(self, config: AgentConfig):
        self.config = config
        self._provider = build_provider(config.provider, config.model)
        self._fallback_provider: LLMProvider | None = None
        self._fallback_provider_name: str | None = None
        self._fallback_provider_model: str | None = None
        if config.provider == "anthropic" and settings.openai_api_key:
            self._fallback_provider = OpenAIProvider(
                api_key=settings.openai_api_key,
                model=settings.openai_model,
            )
            self._fallback_provider_name = "openai"
            self._fallback_provider_model = settings.openai_model

    @property
    def handle(self) -> str:
        return self.config.handle

    @property
    def display_name(self) -> str:
        return self.config.display_name

    @property
    def emoji(self) -> str:
        return self.config.emoji

    @abstractmethod
    def build_prompt(self, user_request: str, context: str = "") -> str:
        """Build the full prompt for this agent turn."""

    async def run(self, user_request: str, context: str = "") -> AgentResult:
        prompt = self.build_prompt(user_request, context)
        role_rules = _role_contract_rules(self.config.handle)
        full_prompt = (
            f"{self.config.system_prompt}\n\n"
            f"{prompt}\n\n"
            "반드시 아래 JSON 객체 하나만 출력하세요 (설명 금지):\n"
            "{\n"
            '  "visible_message": "텔레그램에 바로 게시 가능한 자연스러운 발화",\n'
            '  "suggested_next_agent": "planner|writer|critic|qa|manager 또는 null",\n'
            '  "handoff_reason": "다음 에이전트를 제안한 이유",\n'
            '  "task_status": "현재 작업 상태 문자열",\n'
            '  "done": false,\n'
            '  "needs_user_input": false,\n'
            '  "confidence": 0.0,\n'
            '  "artifact_update": {\n'
            '    "type": "brief|draft|review_notes|decision|final 또는 null",\n'
            '    "content": "공유 작업공간에 저장할 본문",\n'
            '    "replace_latest": true\n'
            "  }\n"
            "}\n"
            "규칙:\n"
            "- 단순 인사/잡담이면 done=false, needs_user_input=false, suggested_next_agent='planner' 권장\n"
            "- 현재 에이전트가 완료를 선언해도 autonomous-lite에서는 PM(planner) 정리 단계가 있을 수 있음\n"
            "- 불필요한 핑퐁을 만들지 말고, 근거 없는 handoff 제안 금지\n"
            "- 문서 초안/검토/결정/최종본을 만들었다면 artifact_update를 함께 채우고, 단순 대화면 null로 두세요\n"
            f"{role_rules}"
        )
        provider_used = self.config.provider
        model_used = self.config.model
        fallback_used = False
        fallback_reason: str | None = None
        try:
            text = (await self._provider.generate_text(full_prompt)).strip()
        except Exception as exc:
            allow_provider_fallback = (
                _is_retryable_provider_error(exc)
                or (self.config.provider == "anthropic" and _is_anthropic_provider_error(exc))
            )
            if self._fallback_provider and allow_provider_fallback:
                text = (await self._fallback_provider.generate_text(full_prompt)).strip()
                provider_used = self._fallback_provider_name or "openai"
                model_used = self._fallback_provider_model or settings.openai_model
                fallback_used = True
                fallback_reason = f"{type(exc).__name__}: {str(exc)[:220]}"
            else:
                raise
        parsed = _parse_agent_payload(text)
        suggested = parsed.get("suggested_next_agent")
        handoff_reason = str(parsed.get("handoff_reason") or "").strip()
        task_status = str(parsed.get("task_status") or "in_progress").strip()
        done = bool(parsed.get("done", False))
        needs_user_input = bool(parsed.get("needs_user_input", False))
        confidence = _to_float(parsed.get("confidence"))
        alternatives = _to_str_list(parsed.get("alternative_next_agents"))
        missing = _to_str_list(parsed.get("missing_information"))
        recommended_mode = parsed.get("recommended_mode")
        recommended_mode = str(
            recommended_mode).strip() if recommended_mode else None
        artifact_update = _parse_artifact_update(parsed.get("artifact_update"))
        visible = _normalize_visible_message(
            handle=self.config.handle,
            visible=str(parsed.get("visible_message") or text).strip(),
            fallback_text=text,
            task_status=task_status,
        )
        return AgentResult(
            handle=self.config.handle,
            display_name=self.config.display_name,
            emoji=self.config.emoji,
            text=text,
            visible_message=visible,
            suggested_next_agent=str(suggested).strip() if suggested else None,
            handoff_reason=handoff_reason,
            task_status=task_status,
            done=done,
            needs_user_input=needs_user_input,
            confidence=confidence,
            alternative_next_agents=alternatives,
            missing_information=missing,
            recommended_mode=recommended_mode,
            artifact_update=artifact_update,
            provider=provider_used,
            model=model_used,
            fallback_used=fallback_used,
            fallback_reason=fallback_reason,
        )


def _is_retryable_provider_error(exc: Exception) -> bool:
    status_code = getattr(exc, "status_code", None)
    if isinstance(status_code, int):
        if status_code in {408, 409, 425, 429, 500, 502, 503, 504, 529}:
            return True
    lowered = str(exc).lower()
    retryable_tokens = (
        "rate limit",
        "too many requests",
        "quota",
        "insufficient quota",
        "insufficient_quota",
        "credit balance",
        "billing",
        "usage limit",
        "resource exhausted",
        "overloaded",
        "temporar",
        "timeout",
        "timed out",
        "connection reset",
        "service unavailable",
        "529",
        "429",
    )
    return any(token in lowered for token in retryable_tokens)


def _is_anthropic_provider_error(exc: Exception) -> bool:
    module_name = str(getattr(exc.__class__, "__module__", "")).lower()
    class_name = str(getattr(exc.__class__, "__name__", "")).lower()
    if "anthropic" in module_name:
        return True
    if "anthropic" in class_name:
        return True
    lowered = str(exc).lower()
    return "anthropic" in lowered or "claude" in lowered


def _parse_agent_payload(text: str) -> dict[str, Any]:
    """Best-effort parse for agent JSON output with safe fallback."""
    if not text:
        return {}
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else {}
    except json.JSONDecodeError:
        pass

    # Extract first JSON object block when model adds explanatory wrappers.
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return _recover_json_like_payload(text)
    try:
        obj = json.loads(match.group(0))
        return obj if isinstance(obj, dict) else {}
    except json.JSONDecodeError:
        return _recover_json_like_payload(match.group(0))


def _recover_json_like_payload(text: str) -> dict[str, Any]:
    cleaned = (text or "").strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[len("```json"):].strip()
    if cleaned.startswith("```"):
        cleaned = cleaned[len("```"):].strip()
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3].strip()

    recovered: dict[str, Any] = {}
    for field in ("visible_message", "suggested_next_agent", "handoff_reason", "task_status", "recommended_mode"):
        value = _extract_json_like_string(cleaned, field)
        if value is not None:
            recovered[field] = value
    for field in ("done", "needs_user_input", "replace_latest"):
        value = _extract_json_like_bool(cleaned, field)
        if value is not None:
            recovered[field] = value
    confidence = _extract_json_like_number(cleaned, "confidence")
    if confidence is not None:
        recovered["confidence"] = confidence

    artifact_type = _extract_json_like_string(cleaned, "type")
    artifact_content = _extract_artifact_content(cleaned)
    replace_latest = _extract_json_like_bool(cleaned, "replace_latest")
    if artifact_type or artifact_content:
        recovered["artifact_update"] = {
            "type": artifact_type or "",
            "content": artifact_content or "",
            "replace_latest": True if replace_latest is None else replace_latest,
        }
    return recovered


def _extract_json_like_string(text: str, field: str) -> str | None:
    match = re.search(
        rf'"{re.escape(field)}"\s*:\s*"((?:[^"\\]|\\.|"(?!\s*,\s*"[A-Za-z_]))*)"', text, re.DOTALL)
    if not match:
        return None
    return _decode_json_like_string(match.group(1))


def _extract_json_like_bool(text: str, field: str) -> bool | None:
    match = re.search(
        rf'"{re.escape(field)}"\s*:\s*(true|false)', text, re.IGNORECASE)
    if not match:
        return None
    return match.group(1).lower() == "true"


def _extract_json_like_number(text: str, field: str) -> float | None:
    match = re.search(rf'"{re.escape(field)}"\s*:\s*(-?\d+(?:\.\d+)?)', text)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _extract_artifact_content(text: str) -> str | None:
    match = re.search(
        r'"content"\s*:\s*"(?P<content>.*?)(?="\s*,\s*"replace_latest"|\s*}\s*}\s*$)',
        text,
        re.DOTALL,
    )
    if match:
        return _decode_json_like_string(match.group("content"))
    fallback = re.search(r'"content"\s*:\s*"(?P<content>.*)$', text, re.DOTALL)
    if not fallback:
        return None
    tail = fallback.group("content")
    tail = re.sub(r'"\s*,?\s*```?\s*$', "", tail, flags=re.DOTALL).strip()
    return _decode_json_like_string(tail)


def _decode_json_like_string(value: str) -> str:
    text = value.strip()
    if not text:
        return ""
    text = text.replace('\\"', '"')
    text = text.replace("\\n", "\n")
    text = text.replace("\\t", "\t")
    text = text.replace("\\r", "\r")
    text = text.replace("\\/", "/")
    return text.strip()


def _to_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_str_list(value: Any) -> list[str] | None:
    if not isinstance(value, list):
        return None
    out = [str(item).strip() for item in value if str(item).strip()]
    return out or None


def _parse_artifact_update(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    artifact_type = str(value.get("type") or "").strip().lower()
    content = str(value.get("content") or "").strip()
    replace_latest = bool(value.get("replace_latest", True))
    allowed_types = {"brief", "draft", "review_notes", "decision", "final"}
    placeholder_contents = {
        "공유 작업공간에 저장할 본문",
        "본문",
        "content",
    }
    if artifact_type in {"null", "none"}:
        artifact_type = ""
    if artifact_type not in allowed_types:
        return None
    if not content or content in placeholder_contents:
        return None
    return {
        "type": artifact_type,
        "content": content,
        "replace_latest": replace_latest,
    }


def _role_contract_rules(handle: str) -> str:
    rules = {
        "planner": "- planner는 작업 가능 상태가 되면 artifact_update.type을 brief 또는 decision으로 남기고 writer를 제안하세요\n",
        "writer": "- writer는 반드시 draft를 남기고 critic을 제안하세요\n",
        "critic": "- critic은 반드시 review_notes를 남기고 qa를 제안하세요\n",
        "qa": "- qa는 반드시 review_notes를 남기고 manager를 제안하세요\n",
        "manager": "- manager는 반드시 final을 남기고 done=true로 종료하세요\n",
    }
    return rules.get(handle, "")


def _normalize_visible_message(
    *,
    handle: str,
    visible: str,
    fallback_text: str,
    task_status: str | None,
) -> str:
    """Prevent schema-template placeholder leakage to Telegram."""
    placeholders = {
        "텔레그램에 바로 게시 가능한 자연스러운 발화",
        "자연스러운 발화",
        "visible_message",
    }
    compact = visible.strip().strip('"').strip("'")
    if compact in placeholders or compact.lower() in placeholders or _looks_like_json_blob(compact):
        return _fallback_status_message(handle, task_status)
    line = _first_non_json_line(fallback_text)
    if not line or _looks_like_json_blob(line):
        return _fallback_status_message(handle, task_status)
    return visible


def _first_non_json_line(text: str) -> str:
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("{") or s.startswith("}") or s.startswith('"'):
            continue
        return s
    return text.strip()


def _looks_like_json_blob(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    if stripped.startswith("```json") or stripped.startswith("{") or stripped.startswith("["):
        return True
    return stripped.count('":') >= 2 and "{" in stripped and "}" in stripped


def _fallback_status_message(handle: str, task_status: str | None) -> str:
    status = (task_status or "").strip()
    if status and status not in {"in_progress", "done"} and not _looks_like_json_blob(status):
        return status
    labels = {
        "planner": "기획 리드가 작업 기준을 정리 중입니다.",
        "writer": "작성 담당이 초안을 작성 중입니다.",
        "critic": "검토 담당이 검토 중입니다.",
        "qa": "품질 보증 담당이 최종 검증 중입니다.",
        "manager": "최종 승인이 마감 중입니다.",
    }
    return labels.get(handle, "에이전트가 작업 중입니다.")
