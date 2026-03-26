from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.adapters.telegram.handlers import process_update
from app.core.config import settings
from app.core.database import get_db

router = APIRouter()


@router.post("/telegram/webhook", status_code=200)
async def telegram_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
):
    """Receive Telegram webhook updates."""
    secret = settings.telegram_webhook_secret
    if secret and x_telegram_bot_api_secret_token != secret:
        raise HTTPException(status_code=403, detail="Invalid webhook secret")

    update = await request.json()
    background_tasks.add_task(process_update, update, db)
    return {"ok": True}


@router.post("/telegram/setup-webhook", status_code=200)
async def setup_telegram_webhook(
    _: str = Depends(lambda: None),
    db: Session = Depends(get_db),
):
    """Register the webhook URL with Telegram (call once after deploy)."""
    from app.adapters.telegram.bot import bot as tg_bot
    if not settings.telegram_webhook_url:
        raise HTTPException(status_code=400, detail="TELEGRAM_WEBHOOK_URL not configured")
    ok = await tg_bot.set_webhook(
        settings.telegram_webhook_url,
        settings.telegram_webhook_secret,
    )
    return {"ok": ok, "webhook_url": settings.telegram_webhook_url}
