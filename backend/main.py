"""
نقطه‌ی ورود بک‌اند مرکزی. این برنامه هم پنل مدیریت تحت وب (/admin) و هم API
ربات‌های کلاینت (/api/v1) را سرو می‌کند، و ربات‌های با hosting_mode=central را
در همین پروسه (با asyncio) اجرا می‌کند.
"""
import asyncio
import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from sqlmodel import Session, select

from backend.config import settings
from backend.database import init_db, get_session_direct
from backend.models import Bot, ScheduledBroadcast, ScheduledBroadcastStatus, utcnow
from backend.admin.routes import router as admin_router
from backend.admin.auth import ensure_default_admin
from backend.api.routes_bot import router as api_router
from backend.services.broadcast import send_broadcast
from bot_client.bot import build_application, start_polling_in_background, stop_application

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("backend.main")

app = FastAPI(title="Connectix VPN Bot — Reseller Platform")
app.add_middleware(SessionMiddleware, secret_key=settings.SESSION_SECRET_KEY)
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")
app.include_router(admin_router)
app.include_router(api_router)

# نگه‌داری نمونه‌ی در حال اجرای هر ربات مرکزی: bot_id -> telegram.ext.Application
running_bots: dict[int, object] = {}


@app.get("/")
def root():
    from starlette.responses import RedirectResponse
    return RedirectResponse("/admin")


async def launch_bot_runtime(bot: Bot) -> None:
    """ربات را (اگر مرکزی و فعال است) در همین پروسه راه‌اندازی می‌کند، بدون
    نیاز به ری‌استارت کل سرویس. برای ربات‌هایی که روی سرور شخصی نماینده اجرا
    می‌شوند (hosting_mode=external) کاری انجام نمی‌دهد — آن‌ها را خود نماینده
    با bot_client روی سرور خودش اجرا می‌کند."""
    if bot.hosting_mode != "central" or not bot.is_active:
        return
    if bot.id in running_bots:
        return
    backend_url = f"http://127.0.0.1:{settings.INTERNAL_PORT}"
    application = build_application(bot.telegram_token, bot.api_key, backend_url)
    try:
        await start_polling_in_background(application)
    except Exception:
        logger.exception("راه‌اندازی ربات #%s شکست خورد (توکن نامعتبر؟)", bot.id)
        return
    running_bots[bot.id] = application
    logger.info("ربات #%s (%s) راه‌اندازی شد.", bot.id, bot.label)


async def stop_bot_runtime(bot_id: int) -> None:
    application = running_bots.pop(bot_id, None)
    if application:
        await stop_application(application)


_scheduled_broadcast_task = None


async def scheduled_broadcast_worker() -> None:
    """هر ۳۰ ثانیه، پیام‌های همگانیِ زمان‌بندی‌شده‌ای که زمانشان رسیده را ارسال می‌کند."""
    while True:
        try:
            session: Session = get_session_direct()
            try:
                due = session.exec(
                    select(ScheduledBroadcast).where(
                        ScheduledBroadcast.status == ScheduledBroadcastStatus.PENDING,
                        ScheduledBroadcast.send_at <= utcnow(),
                    )
                ).all()
                for sb in due:
                    bot = session.get(Bot, sb.bot_id)
                    if not bot:
                        sb.status = ScheduledBroadcastStatus.FAILED
                        session.add(sb)
                        continue
                    try:
                        result = await send_broadcast(session, bot, sb.message)
                        sb.status = ScheduledBroadcastStatus.SENT
                        sb.sent_count = result["sent"]
                        sb.failed_count = result["failed"]
                    except Exception:
                        logger.exception("ارسال پیام زمان‌بندی‌شده #%s شکست خورد", sb.id)
                        sb.status = ScheduledBroadcastStatus.FAILED
                    sb.sent_at = utcnow()
                    session.add(sb)
                if due:
                    session.commit()
            finally:
                session.close()
        except Exception:
            logger.exception("خطا در وُرکر پیام‌های زمان‌بندی‌شده")
        await asyncio.sleep(30)


@app.on_event("startup")
async def on_startup():
    init_db()
    session: Session = get_session_direct()
    try:
        ensure_default_admin(session)
        bots = session.exec(
            select(Bot).where(Bot.is_active == True, Bot.hosting_mode == "central")  # noqa: E712
        ).all()
    finally:
        session.close()
    for bot in bots:
        await launch_bot_runtime(bot)

    global _scheduled_broadcast_task
    _scheduled_broadcast_task = asyncio.create_task(scheduled_broadcast_worker())


@app.on_event("shutdown")
async def on_shutdown():
    for bot_id in list(running_bots.keys()):
        await stop_bot_runtime(bot_id)
    if _scheduled_broadcast_task:
        _scheduled_broadcast_task.cancel()
