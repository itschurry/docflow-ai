"""
Telegram Long-Polling runner (로컬 개발/테스트용)
웹훅 없이 봇 토큰만으로 메시지 수신 및 에이전트 실행.

사용법:
  cd apps/api
  python3 -m scripts.polling

멀티봇 모드:
  .env 에 TELEGRAM_PM_BOT_TOKEN (및 WRITER/CRITIC/CODER 선택) 설정 시
  에이전트별 봇 계정으로 분리 발송됩니다.
  TELEGRAM_PM_BOT_TOKEN 미설정 시 TELEGRAM_BOT_TOKEN 으로 fallback.
"""

import asyncio
import logging
import sys
import os
from pathlib import Path

# .env 로드 (항상 .env 값으로 덮어쓰기)
env_file = Path(__file__).parent.parent / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ[k.strip()] = v.strip()

sys.path.insert(0, str(Path(__file__).parent.parent))

import httpx
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal, Base, engine
from app import models, conversation_models  # noqa: F401
from app.adapters.telegram.handlers import process_update
from app.adapters.telegram.registry import BotRegistry

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("polling")


def _get_inbound_token() -> tuple[str, str]:
    """
    Returns (token, description).
    Prefers TELEGRAM_PM_BOT_TOKEN (multi-bot mode), falls back to TELEGRAM_BOT_TOKEN.
    """
    reg = BotRegistry()
    reg.load(settings.agent_config_path)
    inbound = reg.get_inbound()
    if inbound and inbound.token:
        return inbound.token, f"@{inbound.username} (multi-bot PM)"
    fallback = settings.telegram_bot_token or ""
    return fallback, "single-bot fallback"


async def get_updates(client: httpx.AsyncClient, base_url: str, offset: int) -> list[dict]:
    try:
        resp = await client.get(
            f"{base_url}/getUpdates",
            params={"timeout": 30, "offset": offset},
            timeout=35.0,
        )
        data = resp.json()
        if data.get("ok"):
            return data.get("result", [])
    except Exception as e:
        logger.warning("getUpdates error: %s", e)
    return []


async def run_polling():
    token, desc = _get_inbound_token()
    if not token:
        print("❌ 봇 토큰이 설정되지 않았습니다.")
        print("   .env 에 TELEGRAM_PM_BOT_TOKEN 또는 TELEGRAM_BOT_TOKEN 을 추가하세요.")
        return

    base_url = f"https://api.telegram.org/bot{token}"

    # DB 테이블 자동 생성
    Base.metadata.create_all(bind=engine)

    async with httpx.AsyncClient() as client:
        me = (await client.get(f"{base_url}/getMe")).json().get("result", {})
        print(f"✅ 봇 연결 성공: @{me.get('username')} ({me.get('first_name')}) [{desc}]")
        print("📡 메시지 수신 대기 중... (Ctrl+C 로 종료)\n")

        offset = 0
        while True:
            updates = await get_updates(client, base_url, offset)
            for update in updates:
                offset = update["update_id"] + 1
                logger.info("Update received: update_id=%s", update["update_id"])
                db: Session = SessionLocal()
                try:
                    await process_update(update, db)
                    db.commit()
                except Exception as e:
                    logger.exception("처리 오류: %s", e)
                    db.rollback()
                finally:
                    db.close()


if __name__ == "__main__":
    try:
        asyncio.run(run_polling())
    except KeyboardInterrupt:
        print("\n👋 폴링 종료")
