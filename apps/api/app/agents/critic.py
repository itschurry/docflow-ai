from app.agents.base import AgentConfig, BaseAgent


class CriticAgent(BaseAgent):
    def build_prompt(self, user_request: str, context: str = "") -> str:
        parts = [f"원래 요청:\n{user_request}"]
        if context:
            parts.append(f"\n검토할 초안:\n{context}")
        parts.append(
            "\n위 초안의 논리적 결함, 근거 부족, 빠진 전제, 과장된 표현을 찾아 구체적으로 지적하세요. "
            "각 항목에 개선 제안도 함께 제시하세요."
        )
        return "\n".join(parts)


def make_critic(config: AgentConfig) -> CriticAgent:
    return CriticAgent(config)
