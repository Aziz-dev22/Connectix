from fastapi import Header, HTTPException, Depends
from sqlmodel import Session, select

from backend.database import get_session
from backend.models import Bot


def get_current_bot(
    x_bot_api_key: str = Header(..., alias="X-Bot-Api-Key"),
    session: Session = Depends(get_session),
) -> Bot:
    bot = session.exec(select(Bot).where(Bot.api_key == x_bot_api_key)).first()
    if not bot:
        raise HTTPException(status_code=401, detail="API key نامعتبر است.")
    if not bot.is_active:
        raise HTTPException(status_code=403, detail="این ربات غیرفعال شده است.")
    return bot


def is_admin_of_bot(bot: Bot, telegram_id: str) -> bool:
    admin_ids = {x.strip() for x in bot.telegram_admin_id.split(",") if x.strip()}
    return str(telegram_id) in admin_ids


def require_admin(bot: Bot, telegram_id: str) -> None:
    from fastapi import HTTPException as _HTTPException
    if not is_admin_of_bot(bot, telegram_id):
        raise _HTTPException(status_code=403, detail="فقط ادمین این ربات اجازه‌ی این عملیات را دارد.")
