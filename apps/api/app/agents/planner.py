from app.agents.base import AgentConfig, BaseAgent


class PlannerAgent(BaseAgent):
    def build_prompt(self, user_request: str, context: str = "") -> str:
        parts = [f"사용자 요청:\n{user_request}"]
        if context:
            parts.append(f"\n이전 대화 컨텍스트:\n{context}")
        parts.append(
            "\n위 요청을 분석하고 작업을 분해하세요. "
            "목표, 입력자료, 작업단계, 담당자 제안, 예상 리스크를 명확히 출력하세요."
        )
        return "\n".join(parts)


def make_planner(config: AgentConfig) -> PlannerAgent:
    return PlannerAgent(config)
