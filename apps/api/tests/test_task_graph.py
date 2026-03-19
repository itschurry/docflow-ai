from app.services.task_graph import get_ready_task_types


def test_ready_task_types_respects_dependencies():
    pending0 = [
        "generate_report_outline",
        "parse_reference_docs",
        "generate_report_draft",
        "review_report",
    ]

    ready0 = get_ready_task_types(pending0, completed_task_types=set())
    assert ready0 == ["parse_reference_docs"]

    pending1 = [
        "generate_report_outline",
        "generate_report_draft",
        "review_report",
    ]
    ready1 = get_ready_task_types(pending1, completed_task_types={
                                  "parse_reference_docs"})
    assert ready1 == ["generate_report_outline"]

    ready2 = get_ready_task_types(
        ["generate_report_draft", "review_report"],
        completed_task_types={
            "parse_reference_docs", "generate_report_outline"},
    )
    assert ready2 == ["generate_report_draft"]
