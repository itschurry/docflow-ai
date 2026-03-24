from pathlib import Path

from app.services.file_generators import generate_budget_xlsx
from app.services.file_generators import generate_pptx
from app.services.file_generators import generate_report_docx


def test_upload_file_returns_document_ir_analysis(client):
    project = client.post(
        "/api/projects",
        json={"name": "Upload IR Project", "description": "analysis"},
    )
    assert project.status_code == 200
    project_id = project.json()["id"]

    samples = [
        ("sample.docx", generate_report_docx("업로드 보고서", "## 개요\n- 핵심 내용"), "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "word"),
        ("sample.xlsx", generate_budget_xlsx([{"category": "ops", "name": "cloud", "unit_cost": 100, "months": 2, "rate": 1}], 200), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "sheet"),
        ("sample.pptx", generate_pptx("업로드 발표", [{"title": "개요", "bullets": ["핵심 메시지", "다음 단계"]}]), "application/vnd.openxmlformats-officedocument.presentationml.presentation", "slides"),
    ]

    for filename, payload, mime_type, expected_type in samples:
        upload = client.post(
            f"/api/projects/{project_id}/files",
            files={"uploaded_file": (filename, payload, mime_type)},
        )
        assert upload.status_code == 200
        upload_payload = upload.json()
        assert upload_payload["document_type"] == expected_type
        assert upload_payload["document_summary"]
        assert upload_payload["document_ir"]["document_type"] == expected_type

        analysis = client.get(f"/api/files/{upload_payload['id']}/analysis")
        assert analysis.status_code == 200
        assert analysis.json()["file"]["document_type"] == expected_type
        assert analysis.json()["document_ir"]["document_type"] == expected_type


def test_full_job_flow_persists_generated_artifacts_and_logs(client):
    project = client.post(
        "/api/projects",
        json={"name": "Test Project", "description": "integration"},
    )
    assert project.status_code == 200
    project_id = project.json()["id"]

    sample_path = Path("/tmp/docflow_test_input.md")
    sample_path.write_text("reference content", encoding="utf-8")

    with sample_path.open("rb") as fh:
        upload = client.post(
            f"/api/projects/{project_id}/files",
            files={"uploaded_file": (
                "docflow_test_input.md", fh, "text/markdown")},
        )
    assert upload.status_code == 200

    job = client.post(
        f"/api/projects/{project_id}/jobs",
        json={"request": "make report excel and ppt",
              "output_types": ["report", "excel", "ppt"]},
    )
    assert job.status_code == 200

    job_payload = job.json()
    assert job_payload["status"] == "REVIEW_REQUIRED"
    job_id = job_payload["job_id"]

    detail = client.get(f"/api/jobs/{job_id}")
    assert detail.status_code == 200
    assert detail.json()["status"] == "REVIEW_REQUIRED"

    history = client.get(f"/api/projects/{project_id}/jobs")
    assert history.status_code == 200
    history_jobs = history.json()["jobs"]
    assert len(history_jobs) >= 1
    assert history_jobs[0]["id"] == job_id

    artifacts = client.get(f"/api/jobs/{job_id}/artifacts")
    assert artifacts.status_code == 200
    artifacts_payload = artifacts.json()

    generated = [a for a in artifacts_payload["artifacts"]
                 if a["source_type"] == "generated"]
    assert len(generated) >= 4

    names = [a["original_name"] for a in generated]
    assert any(n.endswith(".md") for n in names)
    assert any(n.endswith(".docx") for n in names)
    assert any(n.endswith(".xlsx") for n in names)
    assert any(n.endswith(".pptx") for n in names)
    assert any(a["document_type"] for a in generated)

    task_statuses = {t["task_type"]: t["status"]
                     for t in artifacts_payload["tasks"]}
    assert task_statuses["generate_report_draft"] == "COMPLETED"
    assert task_statuses["generate_xlsx"] == "COMPLETED"
    assert task_statuses["generate_ppt"] == "COMPLETED"

    logs = client.get(f"/api/jobs/{job_id}/prompt-logs")
    assert logs.status_code == 200
    assert len(logs.json()["logs"]) >= 1

    # Verify download endpoint returns file bytes.
    first_file_id = generated[0]["id"]
    download = client.get(f"/api/files/{first_file_id}/download")
    assert download.status_code == 200
    assert len(download.content) > 0
