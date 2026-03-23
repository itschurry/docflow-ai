"""
Telegram Long-Polling runner — 멀티봇 인바운드 지원.

모든 봇(PM/Writer/Critic/Coder)이 동시에 폴링하며,
어느 봇으로 메시지가 와도 오케스트레이터 파이프라인이 실행됩니다.

사용법:
  cd apps/api
  python3 -m scripts.polling
"""

import asyncio
import logging
import sys
import os
from pathlib import Path

# .env 로드 (인라인 주석 제거, 항상 .env 값으로 덮어쓰기)
env_file = Path(__file__).parent.parent / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            v = v.split("#")[0].strip()  # 인라인 주석 제거
            os.environ[k.strip()] = v

sys.path.insert(0, str(Path(__file__).parent.parent))

import httpx
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal, Base, engine
from app import models, conversation_models  # noqa: F401
from app.adapters.telegram.handlers import process_update
from app.adapters.telegram.registry import BotRegistry, BotIdentity

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("polling")


async def get_updates(
    client: httpx.AsyncClient,
    bot: BotIdentity,
    offset: int,
) -> list[dict]:
    url = f"https://api.telegram.org/bot{bot.token}/getUpdates"
    try:
        resp = await client.get(
            url,
            params={"timeout": 30, "offset": offset},
            timeout=35.0,
        )
        if resp.status_code == 401:
            raise SystemExit(
                f"❌ @{bot.username} — 401 Unauthorized. "
                f"TELEGRAM_{bot.key.upper()}_BOT_TOKEN 토큰을 확인하세요."
            )
        data = resp.json()
        if data.get("ok"):
            return data.get("result", [])
        logger.warning("[%s] getUpdates not ok: %s", bot.username, data.get("description"))
    except SystemExit:
        raise
    except Exception as e:
        logger.warning("[%s] getUpdates error: %s", bot.username, e)
        await asyncio.sleep(3)
    return []


async def poll_bot(bot: BotIdentity) -> None:
    """단일 봇의 폴링 루프."""
    async with httpx.AsyncClient() as client:
        # 봇 연결 확인
        try:
            resp = await client.get(f"https://api.telegram.org/bot{bot.token}/getMe")
            me = resp.json().get("result", {})
            print(f"  ✅ @{me.get('username')} ({me.get('first_name')}) [{bot.key}]")
        except Exception as e:
            print(f"  ❌ {bot.key} 연결 실패: {e}")
            return

        offset = 0
        while True:
            updates = await get_updates(client, bot, offset)
            for update in updates:
                offset = update["update_id"] + 1
                logger.info("[%s] update_id=%s", bot.username, update["update_id"])
                db: Session = SessionLocal()
                try:
                    await process_update(update, db)
                    db.commit()
                except Exception as e:
                    logger.exception("[%s] 처리 오류: %s", bot.username, e)
                    db.rollback()
                finally:
                    db.close()


async def run_polling() -> None:
    # BotRegistry 로드
    reg = BotRegistry()
    reg.load(settings.agent_config_path)
    bots = reg.all()

    if not bots:
        # fallback: 단일 TELEGRAM_BOT_TOKEN
        token = settings.telegram_bot_token
        if not token:
            print("❌ 봇 토큰이 없습니다. .env 에 TELEGRAM_PM_BOT_TOKEN 을 설정하세요.")
            return
        from app.adapters.telegram.registry import BotIdentity
        bots = [BotIdentity(key="pm", token=token, username="bot",
                            display_name="Bot", emoji="🤖")]

    # DB 테이블 자동 생성
    Base.metadata.create_all(bind=engine)

    print(f"\n📡 {len(bots)}개 봇 폴링 시작:")
    for b in bots:
        print(f"   • {b.emoji} {b.display_name} (@{b.username})")
    print()

    # 모든 봇 동시 폴링
    await asyncio.gather(*[poll_bot(b) for b in bots])


if __name__ == "__main__":
    try:
        asyncio.run(run_polling())
    except KeyboardInterrupt:
        print("\n👋 폴링 종료")
