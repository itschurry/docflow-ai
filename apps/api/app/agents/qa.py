from app.agents.base import AgentConfig, BaseAgent


class QaAgent(BaseAgent):
    def build_prompt(self, user_request: str, context: str = "") -> str:
        parts = [f"원래 요구사항:\n{user_request}"]
        if context:
            parts.append(f"\n최종 검증할 산출물:\n{context}")
        parts.append(
            "\n위 산출물에 대해 품질 보증(QA) 테스트를 수행하세요. "
            "1. 요구사항 준수 여부: 원래 의도대로 구현/작성되었는가? "
            "2. 일관성 및 논리: 전체적인 맥락과 논리가 어긋나는 곳이 없는가? "
            "3. 결함 및 오점: 빠진 내용이나 보완이 필요한 치명적인 결함이 있는가? "
            "\n결과를 구체적인 체크리스트 형식으로 작성하고, 승인(Pass) 또는 반려(Reject) 여부를 명확히 판정하세요."
        )
        return "\n".join(parts)


def make_qa(config: AgentConfig) -> QaAgent:
    return QaAgent(config)
