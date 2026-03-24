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
            f"⚠️ 서버 응답 지연: 서버가 아직 작업 중일 수 있습니다. "
            "잠시 후 「작업 목록」에서 상태를 확인해 주세요."
        )
        return None
    except requests.RequestException as exc:
        st.error(f"⚠️ 연결 오류: {exc}")
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
            st.warning(f"⏳ 서버 응답 지연 — 재시도 중 ({i}/{attempts})...")
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
    running_elapsed = 0
    while elapsed < MAX_POLL_SECONDS:
        resp = _api("GET", f"/api/jobs/{job_id}", timeout=15)
        if resp is None:
            return None

        data = resp.json()
        status = str(data.get("status", "unknown")).upper()

        if status == "RUNNING":
            running_elapsed += POLL_INTERVAL
            run_progress = min(0.55 + (running_elapsed / MAX_POLL_SECONDS) * 0.35, 0.90)
        else:
            running_elapsed = 0

        progress = {
            "QUEUED": 0.08,
            "COMPLETED": 1.0,
            "REVIEW_REQUIRED": 1.0,
            "FAILED": 1.0,
            "CANCELLED": 1.0,
        }.get(status, run_progress if status == "RUNNING" else 0.2)

        icon = {
            "QUEUED": "⏳",
            "RUNNING": "🔄",
            "REVIEW_REQUIRED": "✅",
            "COMPLETED": "✅",
            "FAILED": "❌",
            "CANCELLED": "⚪",
        }.get(status, "❓")

        task_hints = {
            "QUEUED": "잠시 기다려 주세요",
            "RUNNING": _running_hint(running_elapsed),
            "REVIEW_REQUIRED": "작성 완료, 검토 준비됨",
            "COMPLETED": "생성 완료",
            "FAILED": "생성 실패",
            "CANCELLED": "작업이 취소되었습니다",
        }

        status_label = {
            "QUEUED": "대기 중 — 곧 시작됩니다",
            "RUNNING": f"AI가 문서를 작성하고 있습니다 ({task_hints['RUNNING']})",
            "REVIEW_REQUIRED": "완료 — 검토 준비됨",
            "COMPLETED": "생성 완료",
            "FAILED": "생성 실패",
            "CANCELLED": "작업이 취소되었습니다",
        }.get(status, "처리 중")

        status_placeholder.progress(
            progress, text=f"{icon} {status_label} ({elapsed}초 경과)")

        if status in {"REVIEW_REQUIRED", "COMPLETED"}:
            return data
        if status in {"FAILED", "CANCELLED"}:
            st.error(f"❌ 문서 생성에 실패했습니다 (상태: {status}). 잠시 후 다시 시도해 주세요.")
            return None

        time.sleep(POLL_INTERVAL)
        elapsed += POLL_INTERVAL

    st.error("⚠️ 시간이 너무 많이 걸립니다. 「작업 목록」 탭에서 나중에 상태를 확인해 주세요.")
    return None


def _running_hint(elapsed_seconds: int) -> str:
    """Return a contextual hint based on how long the job has been running."""
    if elapsed_seconds < 15:
        return "문서 구조 분석 중"
    if elapsed_seconds < 35:
        return "초안 작성 중"
    if elapsed_seconds < 60:
        return "내용 검토 및 보완 중"
    return "마무리 중"


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

# ── 사무직 친화 커스텀 CSS ─────────────────────────────────────────────
st.markdown("""
<style>
/* 전체 배경 */
.stApp { background-color: #f0f4ff; }

/* 탭 */
.stTabs [data-baseweb="tab-list"] { gap: 6px; }
.stTabs [data-baseweb="tab"] {
    font-size: 15px !important;
    font-weight: 600 !important;
    padding: 10px 22px !important;
    border-radius: 10px 10px 0 0 !important;
}
/* 기본 버튼 */
.stButton > button {
    border-radius: 10px !important;
    font-size: 15px !important;
    font-weight: 600 !important;
    padding: 0.55rem 1.4rem !important;
    transition: all 0.2s !important;
}
/* 다운로드 버튼 */
.stDownloadButton > button {
    border-radius: 10px !important;
    font-size: 14px !important;
    font-weight: 600 !important;
}
/* 입력 필드 */
.stTextInput > label, .stTextArea > label, .stFileUploader > label,
.stCheckbox > label { font-size: 15px !important; font-weight: 600 !important; }
/* 진행 표시줄 */
.stProgress > div > div { border-radius: 999px !important; }
/* 알림 카드 */
[data-testid="stAlert"] { border-radius: 12px !important; font-size: 14px !important; }
/* 익스팬더 */
.streamlit-expanderHeader { font-size: 15px !important; font-weight: 600 !important; }
/* 구분선 */
hr { border-color: #dde4f0 !important; }
/* 사이드바 */
[data-testid="stSidebarContent"] {
    background: linear-gradient(175deg, #1e3a5f 0%, #0d1f35 100%) !important;
}
[data-testid="stSidebarContent"] p,
[data-testid="stSidebarContent"] span,
[data-testid="stSidebarContent"] div,
[data-testid="stSidebarContent"] li { color: #dbe8fc; }
[data-testid="stSidebarContent"] strong,
[data-testid="stSidebarContent"] b { color: #ffffff !important; }
[data-testid="stSidebarContent"] h1,
[data-testid="stSidebarContent"] h2,
[data-testid="stSidebarContent"] h3 { color: #ffffff !important; }
[data-testid="stSidebarContent"] hr { border-color: rgba(255,255,255,0.15) !important; }
</style>
""", unsafe_allow_html=True)

with st.sidebar:
    st.markdown("""
<div style="text-align:center; padding:12px 0 18px 0;">
  <div style="font-size:42px; margin-bottom:6px;">📝</div>
  <div style="font-size:20px; font-weight:700; color:#ffffff; letter-spacing:0.02em;">DocFlow AI</div>
  <div style="font-size:13px; color:#94b4d4; margin-top:4px;">자동 문서 생성 플랫폼</div>
</div>
""", unsafe_allow_html=True)

    health = _api("GET", "/health", timeout=5)
    if health is None:
        st.error("⚠️ 서버에 연결할 수 없습니다.")
        st.info(f"API_BASE={API_BASE}")
        st.stop()

    st.markdown("""
<div style="background:rgba(22,163,74,0.18); border:1px solid rgba(22,163,74,0.35);
     border-radius:10px; padding:8px 14px; font-size:13px; color:#a7f3d0;
     display:flex; align-items:center; gap:8px; margin-bottom:4px;">
  <span style="font-size:10px;">●</span> 서버 정상 연결됨
</div>
""", unsafe_allow_html=True)

    st.divider()

    st.markdown("""
**📋 사용 방법**

**1단계** 프로젝트명을 입력하세요.

**2단계** 참고할 문서를 업로드하세요. *(선택)*

**3단계** AI에게 원하는 내용을 입력하세요.

**4단계** 출력 형식을 선택하세요.

**5단계** 「문서 생성 시작」을 클릭하면 완료! 📥
""")

    st.divider()

    st.markdown("""
**📄 지원 출력 형식**

📝 **연구보고서** — DOCX 파일

📊 **예산표** — 엑셀(XLSX) 파일

📑 **슬라이드** — 파워포인트(PPTX) 파일
""")


tab_generate, tab_jobs = st.tabs(["📝 문서 생성", "📋 작업 목록"])

with tab_generate:
    st.markdown("### 어떤 문서가 필요하신가요?")
    st.caption("아래 양식을 작성하고 **문서 생성 시작**을 클릭하면 AI가 자동으로 문서를 만들어 드립니다.")
    st.markdown("")

    c1, c2 = st.columns(2)
    with c1:
        project_name = st.text_input(
            "📁 프로젝트명 *",
            placeholder="예: 2025 울산 R&D 사업계획서",
            help="생성할 문서 묶음의 이름입니다. 나중에 찾기 쉬운 이름으로 입력하세요.",
        )
        project_desc = st.text_area(
            "📌 프로젝트 설명 (선택)",
            placeholder="예: 중소기업 R&D 지원 사업 신청을 위한 계획서 작성",
            help="문서의 목적이나 배경을 간략히 적어주세요. 비워도 됩니다.",
        )
        uploaded_files = st.file_uploader(
            "📎 참고 문서 업로드 (선택)",
            type=["hwp", "pdf", "docx", "txt", "md", "json", "csv"],
            accept_multiple_files=True,
            help="기존 문서, 데이터, 보고서 등을 첨부하면 AI가 내용을 참고해 더 정확한 문서를 만듭니다.",
        )

    with c2:
        request_text = st.text_area(
            "✏️ 요청 내용 *",
            height=190,
            placeholder="예: 2025년도 R&D 지원 사업 신청서 초안을 작성해 주세요. 연구 목적, 예상 성과, 소요 예산을 포함해 주세요.",
            help="AI에게 원하는 내용을 자세히 설명할수록 더 좋은 결과물이 나옵니다.",
        )
        st.markdown("**📂 출력 형식 선택** (하나 이상 선택하세요)")
        output_types: list[str] = []
        if st.checkbox("📝 " + OUTPUT_TYPE_LABELS["report"], value=True):
            output_types.append("report")
        if st.checkbox("📊 " + OUTPUT_TYPE_LABELS["budget"], value=False):
            output_types.append("budget")
        if st.checkbox("📑 " + OUTPUT_TYPE_LABELS["slide"], value=False):
            output_types.append("slide")

        st.markdown("")
        run = st.button("🚀 문서 생성 시작", type="primary", use_container_width=True)

    if run:
        if not project_name.strip() or not request_text.strip() or not output_types:
            st.warning("⚠️ 프로젝트명, 요청 내용, 출력 형식을 모두 입력해 주세요.")
            st.stop()

        status_area = st.empty()

        with st.spinner("프로젝트를 준비하는 중입니다..."):
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
            st.success("✅ 프로젝트 준비 완료")

        if uploaded_files:
            with st.spinner(f"참고 문서를 업로드하는 중입니다 ({len(uploaded_files)}개)..."):
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
                    st.caption(f"📎 업로드 완료: {uf.name}")

        with st.spinner("AI 작업을 시작하는 중입니다..."):
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
                st.info(f"✔️ 진행 중인 작업을 발견했습니다. 계속 진행합니다.")

            st.success("✅ AI 작업 시작됨 — 잠시 기다려 주세요.")

        st.markdown("---")
        st.markdown("**⏳ 문서 생성 진행 중...**  \nAI가 열심히 작성하고 있습니다. 창을 닫지 말아 주세요.")
        job_data = _wait_for_job(job_id, status_area)
        if job_data is None:
            st.stop()

        art_resp = _api("GET", f"/api/jobs/{job_id}/artifacts", timeout=30)
        if art_resp is None:
            st.stop()

        artifacts = art_resp.json().get("artifacts", [])
        if not artifacts:
            st.warning("⚠️ 생성된 파일이 없습니다. 다시 시도해 주세요.")
            st.stop()

        st.markdown("---")
        st.markdown("### 🎉 문서가 완성되었습니다!")
        st.caption("아래 버튼을 클릭하면 파일을 바로 저장할 수 있습니다.")
        for art in artifacts:
            dl = _api("GET", f"/api/files/{art['id']}/download", timeout=120)
            if dl is None:
                continue
            st.download_button(
                label=f"⬇️ 다운로드: {art['original_name']}",
                data=dl.content,
                file_name=art["original_name"],
                mime=art.get("mime_type", "application/octet-stream"),
                key=f"dl_{art['id']}",
                use_container_width=True,
            )

        if len(artifacts) > 1:
            zip_bytes = _build_zip(artifacts)
            st.download_button(
                label="📦 전체 파일 ZIP으로 한 번에 받기",
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
                with st.expander("📄 보고서 미리보기 (클릭해서 열기)", expanded=True):
                    st.markdown(md_dl.content.decode("utf-8", errors="ignore"))

with tab_jobs:
    st.markdown("### 📋 작업 목록")
    st.caption("프로젝트 ID를 입력하면 해당 프로젝트의 생성 작업 목록과 상태를 확인할 수 있습니다.")
    project_id = st.text_input(
        "🔍 프로젝트 ID",
        placeholder="예: a1b2c3d4-...",
        help="문서 생성 시 화면에 표시된 프로젝트 ID를 입력하세요.",
    )
    if project_id.strip():
        h = _api("GET", f"/api/projects/{project_id.strip()}/jobs", timeout=15)
        if h is not None:
            jobs = h.json().get("jobs", [])
            if not jobs:
                st.info("ℹ️ 해당 프로젝트에 작업이 없습니다.")
            for job in jobs:
                st.json(job)
