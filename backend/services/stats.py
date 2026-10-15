"""
آمار تجمیعی برای داشبورد پنل مدیریت: درآمد، سود، تعداد کاربران/نماینده‌ها،
سری روزانه برای نمودار، و هشدارهای مهم (رسید/درخواست در انتظار).
"""
from datetime import timedelta
from sqlmodel import Session, select

from backend.models import (
    Bot, Order, OrderStatus, Customer, PaymentReceipt, ReceiptStatus,
    ResellerRequest, ResellerRequestStatus, utcnow,
)


def get_dashboard_stats(session: Session) -> dict:
    now = utcnow()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=today_start.weekday())
    month_start = today_start.replace(day=1)

    all_bots = session.exec(select(Bot)).all()
    total_bots = len(all_bots)
    total_resellers = len([b for b in all_bots if b.parent_bot_id is not None])
    active_bots = len([b for b in all_bots if b.is_active])

    total_customers = len(session.exec(select(Customer)).all())

    fulfilled_orders = session.exec(
        select(Order).where(Order.status == OrderStatus.FULFILLED)
    ).all()

    def sum_in_range(start):
        revenue = sum(o.sell_price for o in fulfilled_orders if o.fulfilled_at and o.fulfilled_at >= start)
        cost = sum(o.cost_price for o in fulfilled_orders if o.fulfilled_at and o.fulfilled_at >= start)
        return revenue, cost

    rev_today, cost_today = sum_in_range(today_start)
    rev_week, cost_week = sum_in_range(week_start)
    rev_month, cost_month = sum_in_range(month_start)

    # سری ۱۴ روز اخیر برای نمودار
    chart_labels = []
    chart_revenue = []
    chart_orders = []
    for i in range(13, -1, -1):
        day_start = today_start - timedelta(days=i)
        day_end = day_start + timedelta(days=1)
        day_orders = [o for o in fulfilled_orders if o.fulfilled_at and day_start <= o.fulfilled_at < day_end]
        chart_labels.append(day_start.strftime("%m-%d"))
        chart_revenue.append(sum(o.sell_price for o in day_orders))
        chart_orders.append(len(day_orders))

    pending_receipts = len(session.exec(
        select(PaymentReceipt).where(PaymentReceipt.status == ReceiptStatus.PENDING)
    ).all())
    pending_reseller_requests = len(session.exec(
        select(ResellerRequest).where(ResellerRequest.status == ResellerRequestStatus.PENDING)
    ).all())

    recent_orders = session.exec(
        select(Order).order_by(Order.created_at.desc()).limit(8)
    ).all()

    return {
        "total_bots": total_bots,
        "active_bots": active_bots,
        "total_resellers": total_resellers,
        "total_customers": total_customers,
        "revenue_today": rev_today, "profit_today": rev_today - cost_today,
        "revenue_week": rev_week, "profit_week": rev_week - cost_week,
        "revenue_month": rev_month, "profit_month": rev_month - cost_month,
        "pending_receipts": pending_receipts,
        "pending_reseller_requests": pending_reseller_requests,
        "recent_orders": recent_orders,
        "chart_labels": chart_labels,
        "chart_revenue": chart_revenue,
        "chart_orders": chart_orders,
        "total_wallet_balance": sum(b.wallet_balance for b in all_bots if b.parent_bot_id is not None),
    }
