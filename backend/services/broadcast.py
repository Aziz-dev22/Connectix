"""
منطق مشترک ارسال پیام همگانی — هم برای ارسال فوری از پنل، هم برای
پیام‌های زمان‌بندی‌شده که توسط وُرکر پس‌زمینه ارسال می‌شوند.
"""
import asyncio

from sqlmodel import Session, select

from backend.models import Bot, Customer


async def send_broadcast(session: Session, bot: Bot, message: str) -> dict:
    """پیام را برای تمام مشتریان این ربات ارسال می‌کند. {sent, failed, total} برمی‌گرداند."""
    from telegram import Bot as TgBot
    from telegram.error import TelegramError

    customers = session.exec(select(Customer).where(Customer.bot_id == bot.id)).all()
    sent, failed = 0, 0
    tg = TgBot(token=bot.telegram_token)
    for c in customers:
        try:
            await tg.send_message(chat_id=int(c.telegram_id), text=message)
            sent += 1
        except (TelegramError, ValueError):
            failed += 1
        await asyncio.sleep(0.05)  # رعایت محدودیت نرخ تلگرام (~20 پیام بر ثانیه)
    return {"sent": sent, "failed": failed, "total": len(customers)}
