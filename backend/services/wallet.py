"""
مدیریت کیف‌پول پیش‌پرداخت هر ربات نزد والدش.
ربات ریشه کیف‌پول را مصرف نمی‌کند (چون هزینه‌ی واقعی مستقیماً به حساب Connectix خودش است).
"""
from typing import Optional

from sqlmodel import Session

from backend.models import (
    Bot, Customer, WalletTransaction, WalletTransactionType,
    CustomerWalletTransaction, CustomerWalletTxType,
)


class InsufficientBalanceError(Exception):
    pass


def topup(session: Session, bot: Bot, amount: float, note: str = "") -> WalletTransaction:
    bot.wallet_balance += amount
    session.add(bot)
    tx = WalletTransaction(
        bot_id=bot.id, amount=amount, type=WalletTransactionType.TOPUP, note=note
    )
    session.add(tx)
    session.commit()
    session.refresh(tx)
    return tx


def deduct_for_order(
    session: Session, bot: Bot, amount: float, order_id: Optional[int], note: str = ""
) -> Optional[WalletTransaction]:
    """هزینه‌ی تمام‌شده‌ی یک سفارش را از کیف‌پول ربات کم می‌کند. برای ربات ریشه
    (که والد ندارد) هیچ کاری نمی‌کند، چون هزینه مستقیماً از حساب خودش کسر می‌شود."""
    if bot.parent_bot_id is None:
        return None
    if bot.wallet_balance < amount:
        raise InsufficientBalanceError(
            f"موجودی کافی نیست: موجودی {bot.wallet_balance}, هزینه‌ی لازم {amount}"
        )
    bot.wallet_balance -= amount
    session.add(bot)
    tx = WalletTransaction(
        bot_id=bot.id,
        amount=-amount,
        type=WalletTransactionType.ORDER_DEDUCTION,
        related_order_id=order_id,
        note=note,
    )
    session.add(tx)
    session.commit()
    session.refresh(tx)
    return tx


# ---------------------------------------------------------------------------
# کیف‌پول داخلی مشتری (برای پرداخت سریع بدون نیاز به ارسال رسید هر بار)
# ---------------------------------------------------------------------------
def customer_topup(
    session: Session, customer: Customer, amount: float,
    tx_type: CustomerWalletTxType = CustomerWalletTxType.TOPUP, note: str = "",
) -> CustomerWalletTransaction:
    customer.wallet_balance += amount
    session.add(customer)
    tx = CustomerWalletTransaction(
        customer_id=customer.id, amount=amount, type=tx_type, note=note
    )
    session.add(tx)
    session.commit()
    session.refresh(tx)
    return tx


def customer_deduct_for_purchase(
    session: Session, customer: Customer, amount: float, order_id: Optional[int], note: str = ""
) -> CustomerWalletTransaction:
    if customer.wallet_balance < amount:
        raise InsufficientBalanceError(
            f"موجودی کیف‌پول کافی نیست: موجودی {customer.wallet_balance}, مبلغ لازم {amount}"
        )
    customer.wallet_balance -= amount
    session.add(customer)
    tx = CustomerWalletTransaction(
        customer_id=customer.id, amount=-amount, type=CustomerWalletTxType.PURCHASE,
        related_order_id=order_id, note=note,
    )
    session.add(tx)
    session.commit()
    session.refresh(tx)
    return tx
