from collections.abc import Sequence

from app.schemas.plan import PlanResult
from app.services.llm_provider import LLMProvider
from app.services.planner import build_plan


class PlannerAgent:
    def __init__(self, provider: LLMProvider | None = None):
        self.provider = provider

    async def plan(self, user_request: str, output_types: Sequence[str]) -> PlanResult:
        # Phase 1: deterministic planning is the source of truth.
        base_plan = build_plan(output_types)
        if not self.provider:
            return base_plan

        # Optional provider hook for future richer decomposition.
        _ = await self.provider.generate_structured(
            prompt=f"Plan tasks for request: {user_request}",
            schema={"type": "object", "properties": {
                "tasks": {"type": "array"}}},
        )
        return base_plan
