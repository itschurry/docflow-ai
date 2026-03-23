from app.agents.base import AgentConfig, BaseAgent


class CoderAgent(BaseAgent):
    def build_prompt(self, user_request: str, context: str = "") -> str:
        parts = [f"기술 요청:\n{user_request}"]
        if context:
            parts.append(f"\n관련 컨텍스트:\n{context}")
        parts.append(
            "\n위 요청에 대한 기술 구현 방향을 제시하세요. "
            "코드 구조, 리팩터링 방향, 구현 TODO, 주요 설계 결정을 구체적으로 작성하고 "
            "필요한 경우 코드 예시를 포함하세요."
        )
        return "\n".join(parts)


def make_coder(config: AgentConfig) -> CoderAgent:
    return CoderAgent(config)
