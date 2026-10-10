"""
درخواست نمایندگی: یک مشتری عادی می‌تواند درخواست بدهد که اجازه‌ی ساخت ربات
نمایندگی داشته باشد. اگر ادمین تایید کند، همان کاربر (حتی اگر can_create_sub_reseller
برای کل ربات خاموش باشد) اجازه‌ی ساخت یک ربات نمایندگی را پیدا می‌کند.
"""
from sqlmodel import Session, select

from backend.models import Bot, Customer, ResellerRequest, ResellerRequestStatus


def create_request(session: Session, bot: Bot, customer: Customer, note: str = "") -> ResellerRequest:
    existing = session.exec(
        select(ResellerRequest).where(
            ResellerRequest.bot_id == bot.id,
            ResellerRequest.customer_id == customer.id,
            ResellerRequest.status == ResellerRequestStatus.PENDING,
        )
    ).first()
    if existing:
        return existing
    req = ResellerRequest(bot_id=bot.id, customer_id=customer.id, telegram_id=customer.telegram_id, note=note)
    session.add(req)
    session.commit()
    session.refresh(req)
    return req


def has_permission(session: Session, bot: Bot, telegram_id: str) -> bool:
    """آیا این کاربر اجازه‌ی ساخت ربات نمایندگی زیر این ربات را دارد؟
    یا از طریق مجوز عمومی ربات (can_create_sub_reseller)، یا از طریق یک
    درخواست تاییدشده‌ی مخصوص همین کاربر."""
    if bot.can_create_sub_reseller:
        return True
    approved = session.exec(
        select(ResellerRequest).where(
            ResellerRequest.bot_id == bot.id,
            ResellerRequest.telegram_id == telegram_id,
            ResellerRequest.status == ResellerRequestStatus.APPROVED,
        )
    ).first()
    return approved is not None
