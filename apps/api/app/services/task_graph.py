from collections.abc import Iterable

# Task dependency graph for phase-1 orchestration.
# Keys are task types and values are prerequisite task types.
TASK_DEPENDENCIES: dict[str, set[str]] = {
    "parse_reference_docs": set(),
    "generate_report_outline": {"parse_reference_docs"},
    "generate_report_draft": {"generate_report_outline"},
    "review_report": {"generate_report_draft"},
    "extract_budget_items": set(),
    "run_budget_rules": {"extract_budget_items"},
    "generate_xlsx": {"run_budget_rules"},
    "generate_slide_outline": set(),
    "generate_slide_body": {"generate_slide_outline"},
    "generate_ppt": {"generate_slide_body"},
}


def dependencies_for(task_type: str) -> set[str]:
    return TASK_DEPENDENCIES.get(task_type, set())


def get_ready_task_types(pending_task_types: Iterable[str], completed_task_types: set[str]) -> list[str]:
    pending = list(pending_task_types)
    ready = [
        task_type
        for task_type in pending
        if dependencies_for(task_type).issubset(completed_task_types)
    ]
    return sorted(ready)
