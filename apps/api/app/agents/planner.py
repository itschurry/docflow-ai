from app.agents.base import AgentConfig, BaseAgent


class PlannerAgent(BaseAgent):
    def build_prompt(self, user_request: str, context: str = "") -> str:
        parts = [f"사용자 요청:\n{user_request}"]
        if context:
            parts.append(f"\n이전 대화 컨텍스트:\n{context}")
        parts.append(
            "\n위 요청을 분석하여 Pipeline Contract를 생성하세요.\n\n"
            "반드시 다음 순서로 출력하세요:\n"
            "1. Objective — 이 작업의 최종 목적\n"
            "2. Pipeline Definition — stages: [plan, draft, critique, validate, finalize]\n"
            "3. Data Contract — 아래 스키마를 엄격히 따른 JSON 블록:\n"
            "   {\n"
            '     "objective": "...",\n'
            '     "constraints": ["..."],\n'
            '     "sections": ["..."],\n'
            '     "evidence_required": true,\n'
            '     "tone": "report",\n'
            '     "risk_points": ["..."]\n'
            "   }\n"
            "4. Task Breakdown — 각 stage별 실행 지침\n"
            "5. Failure Strategy — 정보/근거 부족 시 처리 방법\n\n"
            "JSON 블록 누락 시 파이프라인이 실패합니다. 반드시 포함하세요."
        )
        return "\n".join(parts)


def make_planner(config: AgentConfig) -> PlannerAgent:
    return PlannerAgent(config)
