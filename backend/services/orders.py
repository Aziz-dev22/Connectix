"""
جریان کامل یک سفارش: ساخت سفارش → تایید پرداخت → تحویل روی Connectix (ساخت
کاربر جدید یا افزودن پلن به کاربر موجود) → کسر از کیف‌پول ربات (اگر نماینده باشد).
"""
import secrets
from typing import Optional

from sqlmodel import Session, select

from backend.models import (
    Bot, Customer, Order, OrderStatus, Plan, PlanPricing, PaymentMethod, utcnow,
)
from backend.connectix_client import ConnectixClient, ConnectixError
from backend.services.wallet import deduct_for_order, InsufficientBalanceError
from backend.services import referral as referral_service


class OrderError(Exception):
    pass


def get_trial_pricing(session: Session, bot: Bot) -> Optional[PlanPricing]:
    """ردیف قیمت‌گذاریِ پلن تست/رایگان (نوع Free در Connectix) برای این ربات، اگر فعال باشد."""
    rows = session.exec(
        select(PlanPricing, Plan).where(
            PlanPricing.bot_id == bot.id, PlanPricing.plan_id == Plan.id, PlanPricing.is_offered == True  # noqa: E712
        )
    ).all()
    for pp, plan in rows:
        if plan.is_trial:
            return pp
    return None


def create_trial_order(session: Session, bot: Bot, customer: Customer) -> Order:
    if customer.used_trial:
        raise OrderError("شما قبلاً از پلن تست استفاده کرده‌اید.")
    pricing = get_trial_pricing(session, bot)
    if not pricing:
        raise OrderError("پلن تست برای این ربات فعال نیست.")
    order = Order(
        bot_id=bot.id, customer_id=customer.id, plan_id=pricing.plan_id,
        cost_price=pricing.cost_price, sell_price=0,
        status=OrderStatus.PAID, is_renewal=customer.connectix_client_id is not None, is_trial=True,
    )
    session.add(order)
    session.commit()
    session.refresh(order)
    return order


def create_order(
    session: Session, bot: Bot, customer: Customer, plan_id: str,
    payment_method: Optional[PaymentMethod] = None,
) -> Order:
    pricing = session.exec(
        select(PlanPricing).where(
            PlanPricing.bot_id == bot.id, PlanPricing.plan_id == plan_id
        )
    ).first()
    if not pricing or not pricing.is_offered:
        raise OrderError("این پلن برای این ربات فعال نیست.")

    order = Order(
        bot_id=bot.id,
        customer_id=customer.id,
        plan_id=plan_id,
        cost_price=pricing.cost_price,
        sell_price=pricing.sell_price,
        payment_method=payment_method,
        status=OrderStatus.AWAITING_PAYMENT,
        is_renewal=customer.connectix_client_id is not None,
    )
    session.add(order)
    session.commit()
    session.refresh(order)
    return order


async def fulfill_order(session: Session, order: Order) -> Order:
    """بعد از تایید پرداخت صدا زده می‌شود. اول کیف‌پول ربات را (اگر نماینده
    است) بررسی/کسر می‌کند، بعد روی Connectix پلن را فعال می‌کند."""
    bot = session.get(Bot, order.bot_id)
    customer = session.get(Customer, order.customer_id)
    plan = session.get(Plan, order.plan_id)

    try:
        deduct_for_order(
            session, bot, order.cost_price, order.id,
            note=f"سفارش #{order.id} - {plan.title if plan else order.plan_id}",
        )
    except InsufficientBalanceError as e:
        order.status = OrderStatus.FAILED
        order.failure_reason = str(e)
        session.add(order)
        session.commit()
        raise OrderError(str(e))

    client = ConnectixClient()
    try:
        if customer.connectix_client_id:
            await client.add_plan(customer.connectix_client_id, order.plan_id)
        else:
            password = secrets.token_urlsafe(4)[:8].ljust(5, "x")
            result = await client.create_user(
                password=password,
                plan_id=order.plan_id,
                telegram_id=customer.telegram_id,
                name=customer.name or None,
                phone=customer.phone or None,
                email=customer.email or None,
            )
            customer.connectix_client_id = result.get("client_id")
            # username از text_to_copy استخراج می‌شود؛ برای سادگی خودمان هم پسورد را داریم
            customer.connectix_password = password
            session.add(customer)
    except ConnectixError as e:
        order.status = OrderStatus.FAILED
        order.failure_reason = e.message
        session.add(order)
        session.commit()
        # در این نسخه، در صورت شکست Connectix بعد از کسر کیف‌پول، مبلغ به‌صورت
        # خودکار بازگردانده نمی‌شود — پیشنهاد می‌شود ادمین به‌صورت دستی از پنل
        # گزارش سفارش‌های ناموفق را بررسی و اصلاح (refund دستی) کند.
        raise OrderError(f"خطا در Connectix: {e.message}")

    order.status = OrderStatus.FULFILLED
    order.fulfilled_at = utcnow()
    session.add(order)

    if order.is_trial:
        customer.used_trial = True
        session.add(customer)

    session.commit()
    session.refresh(order)

    if not order.is_trial:
        referral_service.maybe_grant_referral_bonus(session, bot, customer, order)

    return order
