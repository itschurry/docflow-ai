from app.models import TaskModel
from app.services.executors.context import ExecutionContext
from app.services.file_generators import generate_budget_xlsx


DEFAULT_ITEM = {
    "category": "인건비",
    "name": "연구원 A",
    "unit_cost": 5000000,
    "months": 6,
    "rate": 0.5,
}


def run_extract_budget_items(ctx: ExecutionContext) -> dict:
    payload = {"items": [DEFAULT_ITEM]}
    ctx.set_output("extract_budget_items", payload)
    return payload


def run_budget_rules(task: TaskModel, ctx: ExecutionContext) -> dict:
    items = ctx.get_output("extract_budget_items").get("items")
    if not items:
        items = task.input_payload_json.get(
            "items") if task.input_payload_json else None
    if not items:
        items = [DEFAULT_ITEM]

    total = sum(i["unit_cost"] * i["months"] * i["rate"] for i in items)
    payload = {"total": int(total), "items": items}
    ctx.set_output("run_budget_rules", payload)
    return payload


def run_generate_xlsx(ctx: ExecutionContext, persist_generated_file) -> dict:
    budget_output = ctx.get_output("run_budget_rules")
    items = budget_output.get("items") or []
    total = int(budget_output.get("total", 0))

    xlsx_bytes = generate_budget_xlsx(items=items, total=total)
    artifact = persist_generated_file(
        filename="budget.xlsx",
        content=xlsx_bytes,
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    payload = {"status": "generated",
               "artifact_file_id": str(artifact.id), "total": total}
    ctx.set_output("generate_xlsx", payload)
    return payload
