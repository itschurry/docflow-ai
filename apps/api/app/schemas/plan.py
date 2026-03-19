from pydantic import BaseModel


class PlanResult(BaseModel):
    job_type: str
    tasks: list[str]
