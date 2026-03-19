"""DocFlow AI Streamlit UI."""
from __future__ import annotations

import os
import time
import zipfile
from io import BytesIO
from pathlib import Path

import requests
import streamlit as st

API_BASE = os.environ.get("API_BASE", "http://localhost:8000").rstrip("/")
POLL_INTERVAL = 2
MAX_POLL_SECONDS = 300

OUTPUT_TYPE_LABELS = {
    "report": "연구보고서 (MD + DOCX)",
    "budget": "예산표 (XLSX)",
    "slide": "슬라이드 (PPTX)",
}

_EXT_MIME = {
    ".hwp": "application/x-hwp",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".json": "application/json",
    ".csv": "text/csv",
}


def _api(method: str, path: str, timeout: float | tuple[float, float] = 60, **kwargs):
    url = f"{API_BASE}{path}"
    try:
        resp = requests.request(method=method.upper(),
                                url=url, timeout=timeout, **kwargs)
        resp.raise_for_status()
        return resp
    except requests.Timeout:
        st.error(
            f"API 타임아웃 ({method.upper()} {path}): 서버에서 작업이 아직 처리 중일 수 있습니다. "
            "잠시 후 작업 목록에서 상태를 확인하세요."
        )
        return None
    except requests.RequestException as exc:
        st.error(f"API 오류 ({method.upper()} {path}): {exc}")
        return None


def _api_with_retry(
    method: str,
    path: str,
    *,
    attempts: int = 3,
    delay_seconds: float = 1.0,
    timeout: float | tuple[float, float] = 60,
    **kwargs,
):
    last_resp = None
    for i in range(1, attempts + 1):
        last_resp = _api(method, path, timeout=timeout, **kwargs)
        if last_resp is not None:
            return last_resp
        if i < attempts:
            st.warning(f"일시 지연 감지: {i}/{attempts} 재시도 중...")
            time.sleep(delay_seconds)
    return last_resp


def _recover_job_id(project_id: str) -> str | None:
    """create_job 호출이 타임아웃된 경우 최근 job을 찾아 복구한다."""
    resp = _api("GET", f"/api/projects/{project_id}/jobs", timeout=20)
    if resp is None:
        return None

    jobs = resp.json().get("jobs", [])
    if not jobs:
        return None

    active_statuses = {"QUEUED", "RUNNING", "REVIEW_REQUIRED", "COMPLETED"}
    for job in jobs:
        status = str(job.get("status", "")).upper()
        if status in active_statuses and job.get("id"):
            return str(job["id"])

    return str(jobs[0].get("id")) if jobs[0].get("id") else None


def _wait_for_job(job_id: str, status_placeholder):
    elapsed = 0
    while elapsed < MAX_POLL_SECONDS:
        resp = _api("GET", f"/api/jobs/{job_id}", timeout=15)
        if resp is None:
            return None

        data = resp.json()
        status = str(data.get("status", "unknown")).upper()

        progress = {
            "QUEUED": 0.1,
            "RUNNING": 0.6,
            "REVIEW_REQUIRED": 1.0,
            "COMPLETED": 1.0,
            "FAILED": 1.0,
            "CANCELLED": 1.0,
        }.get(status, 0.2)

        icon = {
            "QUEUED": "⏳",
            "RUNNING": "🔄",
            "REVIEW_REQUIRED": "✅",
            "COMPLETED": "✅",
            "FAILED": "❌",
            "CANCELLED": "⚪",
        }.get(status, "❓")

        status_placeholder.progress(
            progress, text=f"{icon} 상태: {status} (경과 {elapsed}초)")

        if status in {"REVIEW_REQUIRED", "COMPLETED"}:
            return data
        if status in {"FAILED", "CANCELLED"}:
            st.error(f"작업 실패/중단: {status}")
            return None

        time.sleep(POLL_INTERVAL)
        elapsed += POLL_INTERVAL

    st.error("제한 시간 내 완료되지 않았습니다. 작업 목록 탭에서 계속 확인해주세요.")
    return None


def _build_zip(artifacts: list[dict]) -> bytes:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for art in artifacts:
            dl = _api("GET", f"/api/files/{art['id']}/download", timeout=120)
            if dl is not None:
                zf.writestr(art["original_name"], dl.content)
    buf.seek(0)
    return buf.read()


st.set_page_config(page_title="DocFlow AI", page_icon="📝", layout="wide")

with st.sidebar:
    st.title("DocFlow AI")
    st.caption("자동 문서 생성 플랫폼")

    health = _api("GET", "/health", timeout=5)
    if health is None:
        st.error("API 서버에 연결할 수 없습니다.")
        st.info(f"API_BASE={API_BASE}")
        st.stop()
    st.success("API 서버 연결됨")

    st.divider()
    st.markdown("출력 형식")
    st.markdown("- report: 연구보고서")
    st.markdown("- budget: 예산표")
    st.markdown("- slide: 슬라이드")


tab_generate, tab_jobs = st.tabs(["문서 생성", "작업 목록"])

with tab_generate:
    st.header("문서 생성")

    c1, c2 = st.columns(2)
    with c1:
        project_name = st.text_input("프로젝트명", placeholder="예: 울산 R&D 사업계획서")
        project_desc = st.text_area("설명(선택)", placeholder="프로젝트 목적/배경")
        uploaded_files = st.file_uploader(
            "참고문서 업로드",
            type=["hwp", "pdf", "docx", "txt", "md", "json", "csv"],
            accept_multiple_files=True,
        )

    with c2:
        request_text = st.text_area(
            "요청 내용", height=180, placeholder="사업계획서 초안 작성")
        output_types: list[str] = []
        if st.checkbox(OUTPUT_TYPE_LABELS["report"], value=True):
            output_types.append("report")
        if st.checkbox(OUTPUT_TYPE_LABELS["budget"], value=False):
            output_types.append("budget")
        if st.checkbox(OUTPUT_TYPE_LABELS["slide"], value=False):
            output_types.append("slide")

        run = st.button("문서 생성 시작", type="primary", use_container_width=True)

    if run:
        if not project_name.strip() or not request_text.strip() or not output_types:
            st.warning("프로젝트명, 요청 내용, 출력 유형을 확인해주세요.")
            st.stop()

        status_area = st.empty()

        with st.spinner("프로젝트 생성 중..."):
            p = _api_with_retry(
                "POST",
                "/api/projects",
                attempts=3,
                delay_seconds=1.0,
                timeout=(10, 30),
                json={"name": project_name.strip(
                ), "description": project_desc.strip()},
            )
            if p is None:
                st.stop()
            project_id = p.json()["id"]
            st.write(f"프로젝트 생성 완료: {project_id}")

        if uploaded_files:
            with st.spinner(f"파일 업로드 중 ({len(uploaded_files)}건)..."):
                for uf in uploaded_files:
                    ext = Path(uf.name).suffix.lower()
                    mime = _EXT_MIME.get(ext, "application/octet-stream")
                    u = _api_with_retry(
                        "POST",
                        f"/api/projects/{project_id}/files",
                        attempts=2,
                        delay_seconds=0.7,
                        timeout=(10, 180),
                        files={"uploaded_file": (
                            uf.name, uf.getvalue(), mime)},
                    )
                    if u is None:
                        st.stop()
                    st.write(f"업로드 완료: {uf.name}")

        with st.spinner("작업 생성 중..."):
            # async_dispatch=true: inline 백엔드에서도 즉시 응답을 받아 UI 타임아웃 방지
            j = _api_with_retry(
                "POST",
                f"/api/projects/{project_id}/jobs?async_dispatch=true",
                attempts=2,
                delay_seconds=1.0,
                timeout=(10, 30),
                json={"request": request_text.strip(
                ), "output_types": output_types},
            )
            if j is not None:
                job_id = j.json()["job_id"]
            else:
                recovered = _recover_job_id(project_id)
                if not recovered:
                    st.error("작업 생성 응답을 받지 못했고 복구도 실패했습니다. 작업 목록에서 확인해주세요.")
                    st.stop()
                job_id = recovered
                st.info(f"작업 복구 성공: {job_id}")

            st.write(f"작업 생성 완료: {job_id}")

        st.write("생성 진행 중...")
        job_data = _wait_for_job(job_id, status_area)
        if job_data is None:
            st.stop()

        art_resp = _api("GET", f"/api/jobs/{job_id}/artifacts", timeout=30)
        if art_resp is None:
            st.stop()

        artifacts = art_resp.json().get("artifacts", [])
        if not artifacts:
            st.warning("생성된 산출물이 없습니다.")
            st.stop()

        st.subheader("결과 다운로드")
        for art in artifacts:
            dl = _api("GET", f"/api/files/{art['id']}/download", timeout=120)
            if dl is None:
                continue
            st.download_button(
                label=f"다운로드: {art['original_name']}",
                data=dl.content,
                file_name=art["original_name"],
                mime=art.get("mime_type", "application/octet-stream"),
                key=f"dl_{art['id']}",
            )

        if len(artifacts) > 1:
            zip_bytes = _build_zip(artifacts)
            st.download_button(
                label="전체 ZIP 다운로드",
                data=zip_bytes,
                file_name=f"docflow_{job_id[:8]}.zip",
                mime="application/zip",
                use_container_width=True,
            )

        md_art = next(
            (a for a in artifacts if a["original_name"].endswith(".md")), None)
        if md_art:
            md_dl = _api(
                "GET", f"/api/files/{md_art['id']}/download", timeout=120)
            if md_dl is not None:
                with st.expander("보고서 미리보기", expanded=True):
                    st.markdown(md_dl.content.decode("utf-8", errors="ignore"))

with tab_jobs:
    st.header("작업 목록")
    project_id = st.text_input("프로젝트 ID")
    if project_id.strip():
        h = _api("GET", f"/api/projects/{project_id.strip()}/jobs", timeout=15)
        if h is not None:
            jobs = h.json().get("jobs", [])
            if not jobs:
                st.info("작업이 없습니다.")
            for job in jobs:
                st.json(job)
