"""
تایید/رد رسیدهای کارت‌به‌کارت — هم برای سفارش مشتری، هم برای شارژ کیف‌پول نماینده.
"""

from sqlmodel import Session

from backend.models import (
    Bot, Customer, Order, OrderStatus, PaymentReceipt, ReceiptStatus, utcnow,
    CustomerWalletTxType,
)
from backend.services.wallet import topup, customer_topup
from backend.services.orders import fulfill_order, OrderError


async def approve_receipt(session: Session, receipt: PaymentReceipt, reviewer_admin_id: str) -> PaymentReceipt:
    receipt.status = ReceiptStatus.APPROVED
    receipt.reviewed_at = utcnow()
    receipt.reviewed_by_admin_id = reviewer_admin_id
    session.add(receipt)
    session.commit()

    if receipt.order_id:
        order = session.get(Order, receipt.order_id)
        order.status = OrderStatus.PAID
        session.add(order)
        session.commit()
        try:
            await fulfill_order(session, order)
        except OrderError:
            # fulfill_order خودش وضعیت سفارش را روی FAILED می‌گذارد؛ خطا را اینجا
            # فرو می‌بلعیم تا تایید رسید fail نشود، ادمین از گزارش سفارش‌ها متوجه می‌شود.
            pass
    elif receipt.wallet_topup_bot_id and receipt.wallet_topup_amount:
        bot = session.get(Bot, receipt.wallet_topup_bot_id)
        topup(session, bot, receipt.wallet_topup_amount, note=f"تایید رسید #{receipt.id}")
    elif receipt.customer_topup_customer_id and receipt.customer_topup_amount:
        customer = session.get(Customer, receipt.customer_topup_customer_id)
        customer_topup(
            session, customer, receipt.customer_topup_amount,
            tx_type=CustomerWalletTxType.TOPUP, note=f"تایید رسید #{receipt.id}",
        )

    session.refresh(receipt)
    return receipt


def reject_receipt(session: Session, receipt: PaymentReceipt, reviewer_admin_id: str, note: str = "") -> PaymentReceipt:
    receipt.status = ReceiptStatus.REJECTED
    receipt.reviewed_at = utcnow()
    receipt.reviewed_by_admin_id = reviewer_admin_id
    if note:
        receipt.note = (receipt.note + " | " if receipt.note else "") + f"رد شد: {note}"
    session.add(receipt)
    if receipt.order_id:
        order = session.get(Order, receipt.order_id)
        order.status = OrderStatus.REJECTED
        session.add(order)
    session.commit()
    session.refresh(receipt)
    return receipt
