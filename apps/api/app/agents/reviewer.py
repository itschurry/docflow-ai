from app.agents.base import AgentConfig, BaseAgent


class ReviewerAgent(BaseAgent):
    def build_prompt(self, user_request: str, context: str = "") -> str:
        parts = [f"원래 요구사항:\n{user_request}"]
        if context:
            parts.append(f"\n검수할 산출물:\n{context}")
        parts.append(
            "\n위 산출물이 원래 요구사항을 충족하는지 검수하세요. "
            "통과 항목과 보완 필요 항목을 명확히 구분하고, "
            "최종 품질 수준을 0~100 점수로 평가하세요."
        )
        return "\n".join(parts)


def make_reviewer(config: AgentConfig) -> ReviewerAgent:
    return ReviewerAgent(config)
