from app.agents.base import AgentConfig, BaseAgent


class ManagerAgent(BaseAgent):
    def build_prompt(self, user_request: str, context: str = "") -> str:
        parts = [f"원래 요청:\n{user_request}"]
        if context:
            parts.append(f"\n에이전트들의 논의 결과:\n{context}")
        parts.append(
            "\n모든 에이전트의 의견을 종합하여 최종 결론을 내리세요. "
            "핵심 결론, 채택된 내용, 수정된 내용, 다음 액션 아이템을 명확히 정리하세요. "
            "필요한 경우 문서 artifact 생성을 지시하세요."
        )
        return "\n".join(parts)


def make_manager(config: AgentConfig) -> ManagerAgent:
    return ManagerAgent(config)
