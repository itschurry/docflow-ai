from pathlib import Path

from app.core.config import settings
from app.services.dead_letter import write_dead_letter


def test_write_dead_letter_creates_json_file(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "dead_letter_dir", str(tmp_path))

    file_path = write_dead_letter(
        job_id="job-123", reason="failure", retries=3)

    p = Path(file_path)
    assert p.exists()
    text = p.read_text(encoding="utf-8")
    assert '"job_id": "job-123"' in text
    assert '"reason": "failure"' in text


def test_list_dead_letter_endpoint(client, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "dead_letter_dir", str(tmp_path))
    _ = write_dead_letter(
        job_id="job-999", reason="retry exhausted", retries=3)

    res = client.get("/api/ops/dead-letters")
    assert res.status_code == 200
    payload = res.json()
    assert len(payload["items"]) >= 1
    assert payload["items"][0]["job_id"] == "job-999"


def test_dead_letter_replay_preview_and_requeue(client, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "dead_letter_dir", str(tmp_path))

    project = client.post(
        "/api/projects",
        json={"name": "DL Replay", "description": "ops"},
    )
    assert project.status_code == 200
    project_id = project.json()["id"]

    job = client.post(
        f"/api/projects/{project_id}/jobs",
        json={"request": "make report", "output_types": ["report"]},
    )
    assert job.status_code == 200
    job_id = job.json()["job_id"]

    dead_file_path = write_dead_letter(
        job_id=job_id, reason="forced", retries=3)
    dead_file_name = Path(dead_file_path).name

    preview = client.post(
        "/api/ops/dead-letters/replay",
        json={"file_name": dead_file_name, "requeue": False},
    )
    assert preview.status_code == 200
    assert preview.json()["status"] == "PREVIEW"

    replay = client.post(
        "/api/ops/dead-letters/replay",
        json={"file_name": dead_file_name,
              "requeue": True, "delete_on_success": True},
    )
    assert replay.status_code == 200
    assert replay.json()["requeued"] is True
    assert replay.json()["deleted"] is True


def test_dead_letter_replay_blocks_duplicate_without_force(client, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "dead_letter_dir", str(tmp_path))

    project = client.post(
        "/api/projects",
        json={"name": "DL Dup", "description": "ops"},
    )
    project_id = project.json()["id"]

    job = client.post(
        f"/api/projects/{project_id}/jobs",
        json={"request": "make report", "output_types": ["report"]},
    )
    job_id = job.json()["job_id"]

    dead_file_path = write_dead_letter(
        job_id=job_id, reason="forced", retries=3)
    dead_file_name = Path(dead_file_path).name

    first = client.post(
        "/api/ops/dead-letters/replay",
        json={"file_name": dead_file_name, "requeue": True},
    )
    assert first.status_code == 200

    second = client.post(
        "/api/ops/dead-letters/replay",
        json={"file_name": dead_file_name, "requeue": True},
    )
    assert second.status_code == 409

    forced = client.post(
        "/api/ops/dead-letters/replay",
        json={"file_name": dead_file_name,
              "requeue": True, "force_requeue": True},
    )
    assert forced.status_code == 200


def test_dead_letter_replay_requires_ops_token_when_configured(client, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "dead_letter_dir", str(tmp_path))
    monkeypatch.setattr(settings, "ops_api_token", "secret-token")

    project = client.post(
        "/api/projects",
        json={"name": "DL Auth", "description": "ops"},
    )
    project_id = project.json()["id"]

    job = client.post(
        f"/api/projects/{project_id}/jobs",
        json={"request": "make report", "output_types": ["report"]},
    )
    job_id = job.json()["job_id"]

    dead_file_path = write_dead_letter(
        job_id=job_id, reason="forced", retries=3)
    dead_file_name = Path(dead_file_path).name

    no_token = client.post(
        "/api/ops/dead-letters/replay",
        json={"file_name": dead_file_name, "requeue": True},
    )
    assert no_token.status_code == 401

    bad_token = client.post(
        "/api/ops/dead-letters/replay",
        json={"file_name": dead_file_name, "requeue": True},
        headers={"X-Ops-Token": "wrong"},
    )
    assert bad_token.status_code == 401

    ok = client.post(
        "/api/ops/dead-letters/replay",
        json={"file_name": dead_file_name, "requeue": True},
        headers={"X-Ops-Token": "secret-token"},
    )
    assert ok.status_code == 200


def test_ops_api_key_bootstrap_and_audit_read(client, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "dead_letter_dir", str(tmp_path))
    monkeypatch.setattr(settings, "ops_api_token", "bootstrap-token")

    project = client.post(
        "/api/projects",
        json={"name": "DL API Key", "description": "ops"},
    )
    project_id = project.json()["id"]

    job = client.post(
        f"/api/projects/{project_id}/jobs",
        json={"request": "make report", "output_types": ["report"]},
    )
    job_id = job.json()["job_id"]

    create_key = client.post(
        "/api/ops/api-keys",
        json={"key_id": "ops-main", "key_secret": "top-secret", "role": "ops"},
        headers={"X-Ops-Token": "bootstrap-token"},
    )
    assert create_key.status_code == 200

    dead_file_path = write_dead_letter(
        job_id=job_id, reason="forced", retries=3)
    dead_file_name = Path(dead_file_path).name

    replay = client.post(
        "/api/ops/dead-letters/replay",
        json={"file_name": dead_file_name, "requeue": True},
        headers={
            "X-Ops-Key-Id": "ops-main",
            "X-Ops-Key-Secret": "top-secret",
        },
    )
    assert replay.status_code == 200

    audit = client.get(
        "/api/ops/replay-audit",
        headers={
            "X-Ops-Key-Id": "ops-main",
            "X-Ops-Key-Secret": "top-secret",
        },
    )
    assert audit.status_code == 200
    assert len(audit.json()["items"]) >= 1


def test_ops_api_key_creation_without_credentials_is_rejected(client, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "dead_letter_dir", str(tmp_path))
    monkeypatch.setattr(settings, "ops_api_token", "")

    res = client.post(
        "/api/ops/api-keys",
        json={"key_id": "ops-open", "key_secret": "secret", "role": "ops"},
    )
    assert res.status_code in (401, 403)
