"""
منطق رفرال: وقتی اولین سفارشِ یک مشتریِ معرفی‌شده تحویل داده می‌شود، مبلغ
پاداش (اگر ادمین همان ربات تنظیم کرده باشد) به کیف‌پول معرف اضافه می‌شود.
"""
from sqlmodel import Session, select

from backend.models import Bot, Customer, Order, OrderStatus, CustomerWalletTxType
from backend.services.wallet import customer_topup


def maybe_grant_referral_bonus(session: Session, bot: Bot, customer: Customer, order: Order) -> None:
    if not customer.referred_by_customer_id or bot.referral_bonus_amount <= 0:
        return
    # فقط برای اولین سفارش موفق (غیر تست) این مشتری پاداش داده می‌شود
    prior_fulfilled = session.exec(
        select(Order).where(
            Order.customer_id == customer.id,
            Order.status == OrderStatus.FULFILLED,
            Order.is_trial == False,  # noqa: E712
            Order.id != order.id,
        )
    ).first()
    if prior_fulfilled:
        return
    referrer = session.get(Customer, customer.referred_by_customer_id)
    if not referrer:
        return
    customer_topup(
        session, referrer, bot.referral_bonus_amount,
        tx_type=CustomerWalletTxType.REFERRAL_BONUS,
        note=f"پاداش معرفی کاربر #{customer.id}",
    )


def get_referral_stats(session: Session, customer: Customer) -> dict:
    referred = session.exec(
        select(Customer).where(Customer.referred_by_customer_id == customer.id)
    ).all()
    return {"referred_count": len(referred)}
