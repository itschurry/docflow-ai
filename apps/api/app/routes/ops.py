import hashlib
import hmac
import json
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.time_utils import now_utc
from app.core.state_machine import JobStatus
from app.models import JobModel
from app.schemas.request_response import (
    CreateOpsApiKeyRequest,
    CreateOpsApiKeyResponse,
    DeadLetterItem,
    DeadLetterListResponse,
    DeadLetterReplayRequest,
    DeadLetterReplayResponse,
    ReplayAuditItem,
    ReplayAuditListResponse,
)
from app.services.job_dispatcher import dispatch_job

router = APIRouter()


def _secret_hash(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _has_active_ops_keys(db: Session) -> bool:
    from app.models import OpsApiKeyModel

    row = db.execute(
        select(OpsApiKeyModel.id).where(
            OpsApiKeyModel.is_active.is_(True)).limit(1)
    ).first()
    return row is not None


def _authorize_ops_request(
    db: Session,
    x_ops_token: str | None,
    x_ops_key_id: str | None,
    x_ops_key_secret: str | None,
) -> str:
    from app.models import OpsApiKeyModel

    expected = settings.ops_api_token.strip()
    has_keys = _has_active_ops_keys(db)

    if expected and x_ops_token and hmac.compare_digest(x_ops_token, expected):
        return "legacy-token"

    if x_ops_key_id and x_ops_key_secret:
        key = db.execute(
            select(OpsApiKeyModel).where(
                OpsApiKeyModel.key_id == x_ops_key_id,
                OpsApiKeyModel.is_active.is_(True),
            )
        ).scalar_one_or_none()
        if key and hmac.compare_digest(key.secret_hash, _secret_hash(x_ops_key_secret)):
            key.last_used_at = now_utc()
            db.add(key)
            db.commit()
            return f"apikey:{key.key_id}"

    if not expected and not has_keys:
        return "anonymous"

    raise HTTPException(status_code=401, detail="Invalid ops credentials")


def _replay_marker_path(file_name: str) -> Path:
    return Path(settings.dead_letter_dir) / "replayed" / f"{file_name}.done.json"


def _append_replay_audit(
    *,
    action: str,
    file_name: str,
    job_id: str,
    actor: str,
    result: str,
) -> None:
    dead_letter_dir = Path(settings.dead_letter_dir)
    dead_letter_dir.mkdir(parents=True, exist_ok=True)
    audit_path = dead_letter_dir / "replay_audit.jsonl"
    record = {
        "at": now_utc().isoformat(),
        "action": action,
        "file_name": file_name,
        "job_id": job_id,
        "actor": actor,
        "result": result,
    }
    with audit_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=True) + "\n")


@router.get("/api/ops/dead-letters", response_model=DeadLetterListResponse)
def list_dead_letters(
    limit: int = 20,
    db: Session = Depends(get_db),
    x_ops_token: str | None = Header(default=None, alias="X-Ops-Token"),
    x_ops_key_id: str | None = Header(default=None, alias="X-Ops-Key-Id"),
    x_ops_key_secret: str | None = Header(
        default=None, alias="X-Ops-Key-Secret"),
) -> DeadLetterListResponse:
    _authorize_ops_request(db, x_ops_token, x_ops_key_id, x_ops_key_secret)

    dead_letter_dir = Path(settings.dead_letter_dir)
    if not dead_letter_dir.exists():
        return DeadLetterListResponse(items=[])

    files = sorted(dead_letter_dir.glob("job_*.json"), reverse=True)
    items: list[DeadLetterItem] = []
    safe_limit = max(1, min(limit, 200))

    for file_path in files[:safe_limit]:
        try:
            payload = json.loads(file_path.read_text(encoding="utf-8"))
            items.append(
                DeadLetterItem(
                    file_name=file_path.name,
                    path=str(file_path),
                    job_id=str(payload.get("job_id", "")),
                    reason=str(payload.get("reason", "")),
                    retries=int(payload.get("retries", 0)),
                    created_at=str(payload.get("created_at", "")),
                )
            )
        except Exception:
            continue

    return DeadLetterListResponse(items=items)


@router.post("/api/ops/dead-letters/replay", response_model=DeadLetterReplayResponse)
def replay_dead_letter(
    payload: DeadLetterReplayRequest,
    db: Session = Depends(get_db),
    x_ops_token: str | None = Header(default=None, alias="X-Ops-Token"),
    x_ops_key_id: str | None = Header(default=None, alias="X-Ops-Key-Id"),
    x_ops_key_secret: str | None = Header(
        default=None, alias="X-Ops-Key-Secret"),
) -> DeadLetterReplayResponse:
    actor = _authorize_ops_request(
        db, x_ops_token, x_ops_key_id, x_ops_key_secret)

    # Prevent path traversal by accepting only plain filenames.
    file_name = Path(payload.file_name).name
    file_path = Path(settings.dead_letter_dir) / file_name

    if not file_path.exists():
        raise HTTPException(
            status_code=404, detail="Dead letter file not found")

    data = json.loads(file_path.read_text(encoding="utf-8"))
    job_id = str(data.get("job_id", ""))
    replay_marker = _replay_marker_path(file_name)
    replayed_before = replay_marker.exists()

    if not payload.requeue:
        preview_message = "Set requeue=true to replay this dead-letter job."
        if replayed_before:
            preview_message = "This dead-letter file was already replayed once. Set force_requeue=true to replay again."

        _append_replay_audit(
            action="preview",
            file_name=file_name,
            job_id=job_id,
            actor=actor,
            result="ok",
        )
        return DeadLetterReplayResponse(
            file_name=file_name,
            job_id=job_id,
            requeued=False,
            deleted=False,
            status="PREVIEW",
            message=preview_message,
        )

    if replayed_before and not payload.force_requeue:
        _append_replay_audit(
            action="requeue",
            file_name=file_name,
            job_id=job_id,
            actor=actor,
            result="blocked_duplicate",
        )
        raise HTTPException(
            status_code=409,
            detail="Dead letter already replayed; set force_requeue=true to override",
        )

    try:
        job_uuid = UUID(job_id)
    except Exception as exc:
        raise HTTPException(
            status_code=400, detail="Invalid job_id in dead letter") from exc

    job = db.get(JobModel, job_uuid)
    if not job:
        raise HTTPException(status_code=404, detail="Target job not found")

    job.status = JobStatus.QUEUED
    job.updated_at = now_utc()
    db.add(job)
    db.commit()

    dispatch_job(job_uuid)
    db.refresh(job)

    replay_marker.parent.mkdir(parents=True, exist_ok=True)
    replay_marker.write_text(
        json.dumps(
            {
                "file_name": file_name,
                "job_id": job_id,
                "replayed_at": now_utc().isoformat(),
                "actor": actor,
            },
            ensure_ascii=True,
        ),
        encoding="utf-8",
    )

    _append_replay_audit(
        action="requeue",
        file_name=file_name,
        job_id=job_id,
        actor=actor,
        result="dispatched",
    )

    deleted = False
    if payload.delete_on_success:
        file_path.unlink(missing_ok=True)
        deleted = True

    return DeadLetterReplayResponse(
        file_name=file_name,
        job_id=job_id,
        requeued=True,
        deleted=deleted,
        status=job.status,
        message="Dead-letter replay dispatched.",
    )


@router.post("/api/ops/api-keys", response_model=CreateOpsApiKeyResponse)
def create_ops_api_key(
    payload: CreateOpsApiKeyRequest,
    db: Session = Depends(get_db),
    x_ops_token: str | None = Header(default=None, alias="X-Ops-Token"),
    x_ops_key_id: str | None = Header(default=None, alias="X-Ops-Key-Id"),
    x_ops_key_secret: str | None = Header(
        default=None, alias="X-Ops-Key-Secret"),
) -> CreateOpsApiKeyResponse:
    from app.models import OpsApiKeyModel

    actor = _authorize_ops_request(
        db, x_ops_token, x_ops_key_id, x_ops_key_secret)
    if actor == "anonymous":
        raise HTTPException(
            status_code=403,
            detail="Configure OPS_API_TOKEN first to bootstrap API keys",
        )

    existing = db.execute(
        select(OpsApiKeyModel).where(OpsApiKeyModel.key_id == payload.key_id)
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="key_id already exists")

    row = OpsApiKeyModel(
        key_id=payload.key_id,
        secret_hash=_secret_hash(payload.key_secret),
        role=payload.role,
        is_active=True,
    )
    db.add(row)
    db.commit()

    return CreateOpsApiKeyResponse(
        key_id=row.key_id,
        role=row.role,
        is_active=row.is_active,
    )


@router.get("/api/ops/replay-audit", response_model=ReplayAuditListResponse)
def list_replay_audit(
    limit: int = 50,
    db: Session = Depends(get_db),
    x_ops_token: str | None = Header(default=None, alias="X-Ops-Token"),
    x_ops_key_id: str | None = Header(default=None, alias="X-Ops-Key-Id"),
    x_ops_key_secret: str | None = Header(
        default=None, alias="X-Ops-Key-Secret"),
) -> ReplayAuditListResponse:
    _authorize_ops_request(db, x_ops_token, x_ops_key_id, x_ops_key_secret)

    audit_path = Path(settings.dead_letter_dir) / "replay_audit.jsonl"
    if not audit_path.exists():
        return ReplayAuditListResponse(items=[])

    safe_limit = max(1, min(limit, 500))
    lines = audit_path.read_text(encoding="utf-8").splitlines()
    selected = lines[-safe_limit:]

    items: list[ReplayAuditItem] = []
    for line in reversed(selected):
        try:
            payload = json.loads(line)
            items.append(
                ReplayAuditItem(
                    at=str(payload.get("at", "")),
                    action=str(payload.get("action", "")),
                    file_name=str(payload.get("file_name", "")),
                    job_id=str(payload.get("job_id", "")),
                    actor=str(payload.get("actor", "")),
                    result=str(payload.get("result", "")),
                )
            )
        except Exception:
            continue

    return ReplayAuditListResponse(items=items)
