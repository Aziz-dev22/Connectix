"""
ساخت ربات‌های جدید (ریشه یا نماینده) و اعمال سلسله‌مراتب.
"""
from typing import Optional

from sqlmodel import Session

from backend.models import Bot, PaymentMethod, PaymentMethodSetting
from backend.services.pricing import ensure_plan_pricing_rows


class PermissionDeniedError(Exception):
    pass


def create_bot(
    session: Session,
    telegram_token: str,
    telegram_admin_id: str,
    label: str = "",
    parent_bot: Optional[Bot] = None,
    hosting_mode: str = "central",
) -> Bot:
    """یک ربات جدید می‌سازد. اگر parent_bot داده شود، باید can_create_sub_reseller
    آن روشن باشد (این چک را فراخواننده قبل از صدا زدن این تابع انجام می‌دهد یا
    می‌تواند از create_sub_reseller_bot زیر استفاده کند که خودش چک می‌کند)."""
    bot = Bot(
        telegram_token=telegram_token,
        telegram_admin_id=telegram_admin_id,
        label=label or telegram_admin_id,
        parent_bot_id=parent_bot.id if parent_bot else None,
        hosting_mode=hosting_mode,
        # زیرنماینده‌ها به‌صورت پیش‌فرض اجازه‌ی ساخت زیرنماینده‌ی خودشان را ندارند؛
        # ادمین اصلی باید صریحاً از پنل روشنش کند.
        can_create_sub_reseller=False,
    )
    session.add(bot)
    session.commit()
    session.refresh(bot)

    # درگاه‌های پرداخت پیش‌فرض: همه خاموش تا ادمین همان ربات آگاهانه روشن کند.
    # WALLET (پرداخت از کیف‌پول داخلی مشتری) جزو این تنظیمات نیست — همیشه در
    # دسترس است (در صورت کافی بودن موجودی)، نیازی به روشن/خاموش کردن ندارد.
    for method in PaymentMethod:
        if method == PaymentMethod.WALLET:
            continue
        session.add(PaymentMethodSetting(bot_id=bot.id, method=method, enabled=False))
    session.commit()

    # ردیف‌های قیمت‌گذاری اولیه برای همه‌ی پلن‌های فعلی
    ensure_plan_pricing_rows(session, bot)

    return bot


def create_sub_reseller_bot(
    session: Session, parent_bot: Bot, telegram_token: str, telegram_admin_id: str,
    requesting_telegram_id: str, label: str = "",
) -> Bot:
    """درخواست ساخت زیرنماینده. مجاز است اگر: (۱) مجوز عمومی ربات روشن باشد،
    یا (۲) خودِ این کاربر یک درخواست نمایندگی تاییدشده داشته باشد."""
    from backend.services.reseller_requests import has_permission  # جلوگیری از import چرخه‌ای
    if not has_permission(session, parent_bot, requesting_telegram_id):
        raise PermissionDeniedError(
            "شما اجازه‌ی ساخت ربات نمایندگی ندارید. ابتدا باید از منوی «نمایندگی» درخواست بدهید و تایید شود."
        )
    return create_bot(
        session,
        telegram_token=telegram_token,
        telegram_admin_id=telegram_admin_id,
        label=label,
        parent_bot=parent_bot,
        hosting_mode="central",
    )
