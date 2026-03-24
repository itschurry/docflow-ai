from collections.abc import Sequence

from app.schemas.plan import PlanResult


def infer_job_type(output_types: Sequence[str]) -> str:
    if not output_types:
        return "report"
    return "_and_".join(sorted(output_types))


def build_plan(output_types: Sequence[str]) -> PlanResult:
    tasks: list[str] = ["parse_reference_docs"]

    if "report" in output_types or not output_types:
        tasks.extend(["generate_report_outline",
                     "generate_report_draft", "review_report"])
    if any(t in output_types for t in ("excel", "budget", "xlsx")):
        tasks.extend(
            ["extract_budget_items", "run_budget_rules", "generate_xlsx"])
    if any(t in output_types for t in ("ppt", "pptx", "slide")):
        tasks.extend(["generate_slide_outline",
                     "generate_slide_body", "generate_ppt"])

    return PlanResult(job_type=infer_job_type(output_types), tasks=tasks)
