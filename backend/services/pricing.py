"""
منطق قیمت‌گذاری پلکانی: هر ربات قیمت تمام‌شده‌اش را از والدش می‌گیرد (یا از
Connectix اگر ربات ریشه است) و قیمت فروش را خودش تعیین می‌کند.
"""
from typing import Optional

from sqlmodel import Session, select

from backend.models import Bot, Plan, PlanPricing, utcnow
from backend.connectix_client import ConnectixClient


async def sync_plans_from_connectix(session: Session) -> list[Plan]:
    """پلن‌های فعال Connectix را می‌خواند و کش محلی (جدول Plan) را به‌روز می‌کند.
    این فقط قیمت پایه‌ی خود Connectix را ذخیره می‌کند؛ قیمت‌گذاری هر ربات جدا است."""
    client = ConnectixClient()
    plans_raw = await client.get_enabled_plans()
    result: list[Plan] = []
    for p in plans_raw:
        existing = session.get(Plan, p["id"])
        traffic = str(p.get("traffic_amount", ""))
        if existing:
            existing.title = p.get("title", existing.title)
            existing.full_period_text = p.get("full_period_text", "")
            existing.full_period_days = p.get("full_period_days", 0)
            existing.period = p.get("period", 0)
            existing.period_unit = p.get("period_unit", "")
            existing.count_of_devices = p.get("count_of_devices", 0)
            existing.traffic_amount = traffic
            existing.plan_type = p.get("type", "")
            existing.connectix_sell_price = p.get("sell_price", 0)
            existing.is_enabled_upstream = True
            existing.last_synced_at = utcnow()
            session.add(existing)
            result.append(existing)
        else:
            plan = Plan(
                id=p["id"],
                title=p.get("title", ""),
                full_period_text=p.get("full_period_text", ""),
                full_period_days=p.get("full_period_days", 0),
                period=p.get("period", 0),
                period_unit=p.get("period_unit", ""),
                count_of_devices=p.get("count_of_devices", 0),
                traffic_amount=traffic,
                plan_type=p.get("type", ""),
                connectix_sell_price=p.get("sell_price", 0),
            )
            session.add(plan)
            result.append(plan)

    # پلن‌هایی که دیگر در لیست Connectix نیستند را غیرفعال کن (حذف نکن، چون سفارش قدیمی به آن اشاره دارد)
    seen_ids = {p["id"] for p in plans_raw}
    all_cached = session.exec(select(Plan)).all()
    for plan in all_cached:
        if plan.id not in seen_ids:
            plan.is_enabled_upstream = False
            session.add(plan)

    session.commit()
    return result


def get_cost_price(session: Session, bot: Bot, plan_id: str) -> float:
    """قیمت تمام‌شده‌ی یک پلن برای یک ربات مشخص: اگر ریشه است، قیمت فروش خود
    Connectix؛ در غیر این صورت قیمت فروشی که والدش برای همین پلن تعیین کرده —
    که بعد از آن، درصد تخفیف سطح نمایندگیِ همین ربات (tier_discount_percent)
    رویش اعمال می‌شود (قیمت خرید اختصاصی هر نماینده بر اساس سطحش)."""
    if bot.parent_bot_id is None:
        plan = session.get(Plan, plan_id)
        base = plan.connectix_sell_price if plan else 0
    else:
        parent_pricing = session.exec(
            select(PlanPricing).where(
                PlanPricing.bot_id == bot.parent_bot_id, PlanPricing.plan_id == plan_id
            )
        ).first()
        base = parent_pricing.sell_price if parent_pricing else 0

    discount = max(0, min(100, bot.tier_discount_percent or 0))
    return base * (1 - discount / 100)


def ensure_plan_pricing_rows(session: Session, bot: Bot) -> None:
    """برای هر پلن فعال Connectix که هنوز برای این ربات رکورد قیمت‌گذاری ندارد،
    یک ردیف می‌سازد: پلن به‌صورت پیش‌فرض فعال/عرضه‌شده است (is_offered=True) و
    با ۱۰۰٪ سود روی قیمت تمام‌شده (sell = cost × 2) قیمت‌گذاری می‌شود — به‌جز
    پلن‌های نوع «Free» (تست/رایگان) که رایگان می‌مانند. ادمین هر ربات می‌تواند
    بعداً از منوی قیمت‌گذاری این‌ها را تغییر دهد یا خاموش کند."""
    plans = session.exec(select(Plan).where(Plan.is_enabled_upstream == True)).all()  # noqa: E712
    for plan in plans:
        existing = session.exec(
            select(PlanPricing).where(
                PlanPricing.bot_id == bot.id, PlanPricing.plan_id == plan.id
            )
        ).first()
        if existing:
            continue
        cost = get_cost_price(session, bot, plan.id)
        sell = 0 if plan.is_trial else cost * 2
        session.add(
            PlanPricing(
                bot_id=bot.id,
                plan_id=plan.id,
                cost_price=cost,
                sell_price=sell,
                is_offered=True,
            )
        )
    session.commit()


def refresh_cost_prices(session: Session, bot: Bot) -> None:
    """وقتی والد یک ربات قیمت فروشش را تغییر می‌دهد، قیمت تمام‌شده‌ی این ربات
    باید به‌روز شود (صدا زدن این تابع بعد از هر تغییر قیمت فروش والد لازم است)."""
    rows = session.exec(select(PlanPricing).where(PlanPricing.bot_id == bot.id)).all()
    for row in rows:
        row.cost_price = get_cost_price(session, bot, row.plan_id)
        session.add(row)
    session.commit()
