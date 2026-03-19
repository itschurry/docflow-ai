import json
from pathlib import Path

from app.core.config import settings
from app.core.time_utils import now_utc


def write_dead_letter(job_id: str, reason: str, retries: int) -> str:
    dead_letter_dir = Path(settings.dead_letter_dir)
    dead_letter_dir.mkdir(parents=True, exist_ok=True)

    stamp = now_utc().strftime("%Y%m%dT%H%M%S%fZ")
    file_path = dead_letter_dir / f"job_{job_id}_{stamp}.json"

    payload = {
        "job_id": job_id,
        "reason": reason,
        "retries": retries,
        "created_at": now_utc().isoformat(),
    }
    file_path.write_text(json.dumps(
        payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(file_path)
