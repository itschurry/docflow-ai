from app.core.time_utils import now_utc
from app.models import JobModel, PromptLogModel, TaskModel
from app.services.executors.context import ExecutionContext
from app.services.file_generators import generate_report_docx


import re as _re


def _extract_section(text: str, *keywords: str, max_chars: int = 600) -> str:
    """텍스트에서 키워드 주변 내용을 최대 max_chars 자 추출."""
    for kw in keywords:
        idx = text.find(kw)
        if idx != -1:
            start = max(0, idx - 30)
            snippet = text[start: start + max_chars].strip()
            # 다음 섹션 구분자 앞에서 자르기
            cut = _re.search(r"□|■|○ [가-힣]", snippet[50:])
            if cut:
                snippet = snippet[: 50 + cut.start()]
            return snippet
    return ""


def _build_stub_draft_from_text(
    request: str, ref_text: str, file_count: int
) -> str:
    """ref_text(추출된 참고문서 전문)를 분석해 구조화된 초안 마크다운 반환."""
    t = ref_text  # 편의상 단축

    # 과제명/사업명 추출
    project_name = ""
    m = _re.search(r"연구개발\s*과제명[>〉\]]*\s*[<〈\[]*([^\n<>〈〉\[\]]{6,80})", t)
    if m:
        project_name = m.group(1).strip()
    if not project_name:
        m = _re.search(r"과제명[>\]]*\s*[:：]?\s*([^\n]{6,60})", t)
        if m:
            project_name = m.group(1).strip()
    if not project_name:
        project_name = "(문서에서 과제명을 자동 추출하지 못했습니다. 직접 기재 필요)"

    # 사업유형/지역 추출
    biz_type = _extract_section(t, "사업유형", "사업명", max_chars=120)
    region = ""
    m = _re.search(r"지역명[>\]]*\s*[<\[]*([가-힣A-Za-z]+)", t)
    if m:
        region = m.group(1).strip()

    # 개념/정의 섹션 추출
    concept = _extract_section(t, "개념 및 정의", "개념", "□ 개념", max_chars=600)
    # 산업동향 추출
    trend = _extract_section(t, "산업동향", "시장 동향", "글로벌", max_chars=600)
    # 기술개발 필요성
    need = _extract_section(t, "기술개발 필요성", "필요성", "문제점", max_chars=500)

    loc_info = f" ({region})" if region else ""
    src_note = f"업로드 파일 {file_count}건 / 추출 텍스트 {len(ref_text)}자"

    lines = [
        "# 사업계획서 초안 (DocFlow AI 자동생성)",
        f"> 입력자료: {src_note}",
        "",
        "## 1. 과제 개요",
        f"- **과제명**: {project_name}",
        f"- **사업유형**: {biz_type or '(문서 참조)'}",
        f"- **추진지역**: {region or '(문서 참조)'}{loc_info}",
        "",
        "## 2. 추진 배경 및 필요성",
    ]

    if need:
        lines += [f"> (RFP 원문 발췌)\n> {need[:400]}", ""]
    else:
        lines += [
            "- 제조·물류·서비스 현장의 자동화 수요 증가로 핵심 기술 국산화 필요성 대두",
            "- 글로벌 선도기업 대비 기술 격차 해소 및 지역 산업 생태계 연계 강화 필요",
        ]

    lines += ["", "## 3. 산업 동향"]
    if trend:
        lines += [f"> (RFP 원문 발췌)\n> {trend[:500]}", ""]
    else:
        lines += ["- (RFP 원문의 산업동향 섹션 내용 미추출 — 직접 기재 필요)", ""]

    if concept:
        lines += [
            "## 4. 핵심 개념 및 기술 범위",
            f"> (RFP 원문 발췌)\n> {concept[:500]}",
            "",
        ]

    lines += [
        "## 5. 목표 및 세부 개발 내용",
        "### 5.1 최종 목표",
        "- (RFP의 개발목표 항목을 기반으로 작성할 것)",
        "",
        "### 5.2 세부 개발 항목 (Work Package)",
        "- WP1. 핵심 기술 설계 및 시제품 개발",
        "- WP2. 통합·제어 시스템 구현 및 고도화",
        "- WP3. 현장 실증 및 성능 검증",
        "- WP4. 사업화 패키지 정립 및 인증 취득",
        "",
        "## 6. 추진 전략",
        "- 지역 산학연 컨소시엄 기반 단계적 설계-제작-검증-사업화 추진",
        "- 공통 플랫폼화 전략으로 파생 모델 확장성 확보",
        "- 표준화·특허·인증을 병행하여 시장 진입 장벽 선점",
        "",
        "## 7. 연차별 추진계획(안)",
        "| 연차 | 주요 내용 | 핵심 산출물 |",
        "|---|---|---|",
        "| 1차년도 | 요구사항 분석, 핵심 모듈 설계, 기반 구축 | 설계서, 시제품 v0.1 |",
        "| 2차년도 | 통합 고도화, 성능·안전성 검증 | 성능검증 보고서, 시제품 v1.0 |",
        "| 3차년도 | 현장 실증, 양산 전환 검토, 사업화·수출 연계 | 실증결과, 사업화계획서 |",
        "",
        "## 8. 정량 목표 (KPI)",
        "| 구분 | 지표명 | 목표치 | 측정방법 |",
        "|---|---|---|---|",
        "| 기술 | 핵심 성능 지표 | TBD | 시험성적서 |",
        "| 지식재산 | 특허 출원 | TBD건 | 출원서 |",
        "| 인증 | 표준/인증 취득 | TBD건 | 인증서 |",
        "| 사업화 | 매출/수출 목표 | TBD억 | 증빙 매출 |",
        "",
        "## 9. 기대효과",
        "- **기술**: 핵심 역량 내재화 및 기술 자립 기반 구축",
        "- **산업**: 지역 생태계 고도화 및 공급망 활성화",
        "- **경제**: 수입대체·수출 기반 확보, 지역 일자리 창출",
        "",
        "## 10. 리스크 및 대응",
        "| 리스크 유형 | 내용 | 대응방안 |",
        "|---|---|---|",
        "| 기술 | 핵심 기술 개발 지연 | 단계별 마일스톤 검증 및 외부 전문가 자문 |",
        "| 사업 | 시장 요구 변동 | 모듈형 제품전략 및 다산업 실증으로 유연 대응 |",
        "| 인력 | 핵심인력 이탈 | 역할 이중화 및 기술 문서화 강화 |",
        "",
        "---",
        f"**사용자 요청**: {request[:300]}{'...' if len(request) > 300 else ''}",
        "",
        "*본 문서는 DocFlow AI 파이프라인(StubLLM 모드)으로 자동 생성된 초안입니다.*",
        "*실제 LLM(OpenAI/Anthropic) 연동 시 RFP 전문 분석 기반 고품질 초안이 생성됩니다.*",
        "*제출 전 컨소시엄 역할·예산·세부지표 수치 보정이 필요합니다.*",
    ]

    return "\n".join(lines)


def _build_stub_business_plan_draft(job: JobModel, ctx: ExecutionContext) -> str:
    parse_payload = ctx.get_output("parse_reference_docs") or {}
    file_count = int(parse_payload.get("file_count", 0))
    ref_text = parse_payload.get("combined_text", "")
    request = job.request_text.strip()

    if ref_text:
        return _build_stub_draft_from_text(request, ref_text, file_count)

    # ref_text 없음 — request_text에서 참고문맥 추출 시도
    ctx_marker = "참고문맥:"
    idx = request.find(ctx_marker)
    if idx != -1:
        fallback_text = request[idx + len(ctx_marker):]
        return _build_stub_draft_from_text(request, fallback_text, file_count)

    # 완전 fallback
    return "\n".join([
        "# 사업계획서 초안 (DocFlow AI 자동생성)",
        "",
        "> 참고문서 텍스트가 추출되지 않아 템플릿 기반으로 생성되었습니다.",
        "> HWP/PDF/DOCX 파일 업로드 시 자동으로 내용이 반영됩니다.",
        "",
        f"**요청 내용**: {request[:500]}",
        "",
        "## 1. 과제 개요",
        "- 과제명: (요청 내용 기반 기재 필요)",
        "",
        "## 2~10. (RFP 문서 업로드 후 재생성 권장)",
        "",
        "---",
        "*본 문서는 DocFlow AI 파이프라인으로 자동 생성된 초안입니다.*",
    ])


def run_writer_task(
    db,
    *,
    job: JobModel,
    task: TaskModel,
    ctx: ExecutionContext,
    run_generate_text,
    persist_generated_file,
    provider_name: str,
    model_name: str,
) -> dict:
    prompt = f"task={task.task_type} request={job.request_text}"
    response_text = run_generate_text(prompt)

    if task.task_type == "generate_report_draft" and provider_name == "stubllm":
        response_text = _build_stub_business_plan_draft(job, ctx)

    payload = {"text": response_text}

    if task.task_type == "generate_report_draft":
        md_artifact = persist_generated_file(
            filename=f"report_draft_{task.id}.md",
            content=response_text.encode("utf-8"),
            mime_type="text/markdown",
            extracted_text=response_text,
        )
        docx_bytes = generate_report_docx(
            title="연구 결과 보고서 초안",
            body_text=response_text,
        )
        docx_artifact = persist_generated_file(
            filename=f"report_draft_{task.id}.docx",
            content=docx_bytes,
            mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            extracted_text=response_text,
        )
        payload["artifact_file_ids"] = [
            str(md_artifact.id), str(docx_artifact.id)]

    db.add(
        PromptLogModel(
            task_id=task.id,
            provider=provider_name,
            model=model_name,
            prompt_text=prompt,
            response_text=response_text,
            created_at=now_utc(),
        )
    )
    ctx.set_output(task.task_type, payload)
    return payload
