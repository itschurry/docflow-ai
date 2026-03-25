from app.core.config import settings
from app.services.executors.context import ExecutionContext
from app.services.executors.qa_executor import run_qa_report


def test_qa_flags_short_todo_and_missing_keywords(monkeypatch):
    monkeypatch.setattr(settings, "review_min_length", 120)
    monkeypatch.setattr(settings, "review_length_penalty", 20)
    monkeypatch.setattr(settings, "review_todo_penalty", 15)
    monkeypatch.setattr(
        settings, "review_required_keywords", "summary,conclusion")
    monkeypatch.setattr(settings, "review_keyword_penalty", 8)
    monkeypatch.setattr(settings, "review_required_threshold", 90)

    ctx = ExecutionContext()
    ctx.set_output("generate_report_draft", {"text": "TODO: draft soon"})

    result = run_qa_report(ctx)

    assert result["quality_score"] == 49
    assert result["recommendation"] == "REVIEW_REQUIRED"
    assert any("TODO" in item for item in result["findings"])
    assert any("핵심 키워드 누락" in item for item in result["findings"])


def test_qa_returns_ready_when_rules_pass(monkeypatch):
    monkeypatch.setattr(settings, "review_min_length", 30)
    monkeypatch.setattr(settings, "review_length_penalty", 20)
    monkeypatch.setattr(settings, "review_todo_penalty", 15)
    monkeypatch.setattr(
        settings, "review_required_keywords", "summary,conclusion")
    monkeypatch.setattr(settings, "review_keyword_penalty", 8)
    monkeypatch.setattr(settings, "review_required_threshold", 85)

    text = "summary: project status is stable. conclusion: proceed with rollout."
    ctx = ExecutionContext()
    ctx.set_output("generate_report_draft", {"text": text})

    result = run_qa_report(ctx)

    assert result["quality_score"] == 100
    assert result["recommendation"] == "READY_FOR_HUMAN_REVIEW"
