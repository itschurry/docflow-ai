from app.core.config import settings
from app.services.executors.context import ExecutionContext


def run_review_report(ctx: ExecutionContext) -> dict:
    draft_text = (ctx.get_output("generate_report_draft")
                  or {}).get("text", "")
    normalized_text = draft_text.strip()

    findings: list[str] = []
    score = 100

    if len(normalized_text) < settings.review_min_length:
        findings.append("보고서 본문 길이가 짧아 내용 보강이 필요합니다.")
        score -= settings.review_length_penalty

    if "TODO" in draft_text.upper():
        findings.append("미완성 표시(TODO)가 포함되어 있습니다.")
        score -= settings.review_todo_penalty

    required_keywords = [
        token.strip() for token in settings.review_required_keywords.split(",") if token.strip()
    ]
    missing_keywords = [
        keyword for keyword in required_keywords if keyword.lower() not in normalized_text.lower()
    ]
    if missing_keywords:
        findings.append(
            f"핵심 키워드 누락: {', '.join(missing_keywords)}"
        )
        score -= settings.review_keyword_penalty * len(missing_keywords)

    if not findings:
        findings.append("구조/분량 관점에서 치명적인 누락은 발견되지 않았습니다.")

    final_score = max(score, 0)
    recommendation = "REVIEW_REQUIRED" if final_score < settings.review_required_threshold else "READY_FOR_HUMAN_REVIEW"
    payload = {
        "quality_score": final_score,
        "findings": findings,
        "recommendation": recommendation,
        "rules": {
            "min_length": settings.review_min_length,
            "required_keywords": required_keywords,
            "required_threshold": settings.review_required_threshold,
        },
    }
    ctx.set_output("review_report", payload)
    return payload
