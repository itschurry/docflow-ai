from app.agents.base import AgentConfig, BaseAgent


class WriterAgent(BaseAgent):
    def build_prompt(self, user_request: str, context: str = "") -> str:
        parts = [f"작성 요청:\n{user_request}"]
        if context:
            parts.append(f"\n참고 컨텍스트 및 플래너 계획:\n{context}")
        parts.append(
            "\n위 내용을 바탕으로 완성된 초안 문서를 작성하세요. "
            "명확한 구조(제목, 소제목, 본문)를 갖추고 논리적으로 서술하세요."
        )
        return "\n".join(parts)


def make_writer(config: AgentConfig) -> WriterAgent:
    return WriterAgent(config)
