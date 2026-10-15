"""
API ای که ربات‌های کلاینت (bot_client) با آن صحبت می‌کنند. احراز هویت با
هدر X-Bot-Api-Key انجام می‌شود و هر ربات فقط به داده‌های خودش دسترسی دارد.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from backend.database import get_session
from backend.models import (
    Bot, Customer, Order, OrderStatus, Plan, PlanPricing,
    PaymentMethod, PaymentMethodSetting, PaymentReceipt, ReceiptStatus,
    ResellerRequest, ResellerRequestStatus, utcnow,
)
from backend.api.deps import get_current_bot, require_admin, is_admin_of_bot
from backend.connectix_client import ConnectixClient, ConnectixError
from backend.services import pricing as pricing_service
from backend.services import hierarchy as hierarchy_service
from backend.services import orders as orders_service
from backend.services import receipts as receipts_service
from backend.services import referral as referral_service
from backend.services import reseller_requests as reseller_requests_service
from backend.services.wallet import customer_topup, customer_deduct_for_purchase, InsufficientBalanceError
from backend.services.audit import log_action
from backend.models import CustomerWalletTxType

router = APIRouter(prefix="/api/v1")


# ---------------------------------------------------------------------------
# Bot meta
# ---------------------------------------------------------------------------
@router.get("/bot/info")
def bot_info(bot: Bot = Depends(get_current_bot)):
    return {
        "bot_id": bot.id,
        "label": bot.label,
        "has_parent": bot.parent_bot_id is not None,
        "can_create_sub_reseller": bot.can_create_sub_reseller,
        "admin_ids": [x.strip() for x in bot.telegram_admin_id.split(",") if x.strip()],
        "forced_channel": bot.forced_channel,
        "forced_channel_enabled": bot.forced_channel_enabled,
        "support_contact": bot.support_contact,
        "referral_bonus_amount": bot.referral_bonus_amount,
    }


class UpdateBotSettingsRequest(BaseModel):
    requesting_telegram_id: str
    forced_channel: Optional[str] = None
    forced_channel_enabled: Optional[bool] = None
    support_contact: Optional[str] = None
    referral_bonus_amount: Optional[float] = None


@router.post("/bot/settings")
def update_bot_settings(
    body: UpdateBotSettingsRequest, bot: Bot = Depends(get_current_bot), session: Session = Depends(get_session)
):
    require_admin(bot, body.requesting_telegram_id)
    if body.forced_channel is not None:
        bot.forced_channel = body.forced_channel
    if body.forced_channel_enabled is not None:
        bot.forced_channel_enabled = body.forced_channel_enabled
    if body.support_contact is not None:
        bot.support_contact = body.support_contact
    if body.referral_bonus_amount is not None:
        bot.referral_bonus_amount = body.referral_bonus_amount
    session.add(bot)
    session.commit()
    return {"message": "تنظیمات ذخیره شد."}


def _is_admin(bot: Bot, telegram_id: str) -> bool:
    return is_admin_of_bot(bot, telegram_id)


@router.get("/bot/is-admin")
def bot_is_admin(telegram_id: str, bot: Bot = Depends(get_current_bot)):
    return {"is_admin": _is_admin(bot, telegram_id)}


# ---------------------------------------------------------------------------
# Plans
# ---------------------------------------------------------------------------
@router.get("/plans")
def list_plans(bot: Bot = Depends(get_current_bot), session: Session = Depends(get_session)):
    rows = session.exec(
        select(PlanPricing, Plan)
        .where(PlanPricing.bot_id == bot.id, PlanPricing.plan_id == Plan.id)
        .where(PlanPricing.is_offered == True)  # noqa: E712
    ).all()
    return [
        {
            "plan_id": plan.id,
            "title": plan.title,
            "full_period_text": plan.full_period_text,
            "count_of_devices": plan.count_of_devices,
            "traffic_amount": plan.traffic_amount,
            "sell_price": pp.sell_price,
        }
        for pp, plan in rows
        if not plan.is_trial  # پلن تست فقط از مسیر جداگانه‌ی «تست رایگان» قابل‌دسترسی است
    ]


class SetPriceRequest(BaseModel):
    requesting_telegram_id: str
    sell_price: float
    is_offered: bool = True


@router.post("/plans/{plan_id}/price")
def set_plan_price(
    plan_id: str, body: SetPriceRequest,
    bot: Bot = Depends(get_current_bot), session: Session = Depends(get_session),
):
    require_admin(bot, body.requesting_telegram_id)
    pp = session.exec(
        select(PlanPricing).where(PlanPricing.bot_id == bot.id, PlanPricing.plan_id == plan_id)
    ).first()
    if not pp:
        raise HTTPException(404, "این پلن برای این ربات ثبت نشده است.")
    if body.sell_price != 0 and body.sell_price < pp.cost_price:
        raise HTTPException(422, "قیمت فروش نمی‌تواند کمتر از قیمت تمام‌شده باشد (مگر برای پلن رایگان/تست).")
    pp.sell_price = body.sell_price
    pp.is_offered = body.is_offered
    session.add(pp)
    session.commit()

    # اگر این ربات خودش والدِ ربات‌های دیگری است، قیمت تمام‌شده‌ی آن‌ها باید به‌روز شود
    children = session.exec(select(Bot).where(Bot.parent_bot_id == bot.id)).all()
    for child in children:
        pricing_service.refresh_cost_prices(session, child)

    return {"message": "قیمت به‌روزرسانی شد."}


@router.get("/plans/all-for-admin")
def list_all_plans_for_admin(
    requesting_telegram_id: str,
    bot: Bot = Depends(get_current_bot), session: Session = Depends(get_session),
):
    """همه‌ی پلن‌ها (حتی غیرفعال‌ها) به‌همراه قیمت تمام‌شده، برای صفحه‌ی مدیریت قیمت‌گذاری ادمین هر ربات."""
    require_admin(bot, requesting_telegram_id)
    rows = session.exec(
        select(PlanPricing, Plan).where(PlanPricing.bot_id == bot.id, PlanPricing.plan_id == Plan.id)
    ).all()
    return [
        {
            "plan_id": plan.id,
            "title": plan.title,
            "cost_price": pp.cost_price,
            "sell_price": pp.sell_price,
            "is_offered": pp.is_offered,
        }
        for pp, plan in rows
    ]


# ---------------------------------------------------------------------------
# Payment methods
# ---------------------------------------------------------------------------
@router.get("/payment-methods")
def list_payment_methods(bot: Bot = Depends(get_current_bot), session: Session = Depends(get_session)):
    rows = session.exec(select(PaymentMethodSetting).where(PaymentMethodSetting.bot_id == bot.id)).all()
    return [{"method": r.method, "enabled": r.enabled, "details": r.details} for r in rows]


class SetPaymentMethodRequest(BaseModel):
    requesting_telegram_id: str
    enabled: bool
    details: Optional[str] = None


@router.post("/payment-methods/{method}")
def set_payment_method(
    method: PaymentMethod, body: SetPaymentMethodRequest,
    bot: Bot = Depends(get_current_bot), session: Session = Depends(get_session),
):
    require_admin(bot, body.requesting_telegram_id)
    row = session.exec(
        select(PaymentMethodSetting).where(
            PaymentMethodSetting.bot_id == bot.id, PaymentMethodSetting.method == method
        )
    ).first()
    if not row:
        row = PaymentMethodSetting(bot_id=bot.id, method=method)
    row.enabled = body.enabled
    if body.details is not None:
        row.details = body.details
    session.add(row)
    session.commit()
    return {"message": "تنظیمات درگاه ذخیره شد."}


# ---------------------------------------------------------------------------
# Customers
# ---------------------------------------------------------------------------
class UpsertCustomerRequest(BaseModel):
    telegram_id: str
    telegram_username: str = ""
    name: str = ""
    phone: str = ""
    email: str = ""
    referred_by_telegram_id: Optional[str] = None  # فقط برای اولین ساخت کاربر اثر دارد


@router.post("/customers/upsert")
def upsert_customer(
    body: UpsertCustomerRequest, bot: Bot = Depends(get_current_bot), session: Session = Depends(get_session)
):
    customer = session.exec(
        select(Customer).where(Customer.bot_id == bot.id, Customer.telegram_id == body.telegram_id)
    ).first()
    is_new = customer is None
    if not customer:
        customer = Customer(bot_id=bot.id, telegram_id=body.telegram_id)
    customer.telegram_username = body.telegram_username or customer.telegram_username
    customer.name = body.name or customer.name
    customer.phone = body.phone or customer.phone
    customer.email = body.email or customer.email

    if is_new and body.referred_by_telegram_id and body.referred_by_telegram_id != body.telegram_id:
        referrer = session.exec(
            select(Customer).where(
                Customer.bot_id == bot.id, Customer.telegram_id == body.referred_by_telegram_id
            )
        ).first()
        if referrer:
            customer.referred_by_customer_id = referrer.id

    session.add(customer)
    session.commit()
    session.refresh(customer)
    return {
        "customer_id": customer.id,
        "has_active_service": customer.connectix_client_id is not None,
        "used_trial": customer.used_trial,
        "wallet_balance": customer.wallet_balance,
        "is_blocked": customer.is_blocked,
    }


@router.get("/customers/{telegram_id}/services")
async def get_customer_services(
    telegram_id: str, bot: Bot = Depends(get_current_bot), session: Session = Depends(get_session)
):
    customer = session.exec(
        select(Customer).where(Customer.bot_id == bot.id, Customer.telegram_id == telegram_id)
    ).first()
    if not customer or not customer.connectix_client_id:
        return {"has_service": False}
    client = ConnectixClient()
    try:
        data = await client.show_user(customer.connectix_client_id)
    except ConnectixError as e:
        raise HTTPException(502, f"خطا در دریافت اطلاعات از Connectix: {e.message}")
    return {"has_service": True, "client": data}


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------
class CreateOrderRequest(BaseModel):
    telegram_id: str
    plan_id: str
    payment_method: PaymentMethod


@router.post("/orders")
def create_order(
    body: CreateOrderRequest, bot: Bot = Depends(get_current_bot), session: Session = Depends(get_session)
):
    customer = session.exec(
        select(Customer).where(Customer.bot_id == bot.id, Customer.telegram_id == body.telegram_id)
    ).first()
    if not customer:
        raise HTTPException(404, "ابتدا باید مشتری ثبت شود (customers/upsert).")
    if customer.is_blocked:
        raise HTTPException(403, "این کاربر مسدود شده است.")
    if body.payment_method == PaymentMethod.WALLET:
        raise HTTPException(422, "برای پرداخت از کیف‌پول از endpoint جداگانه‌ی orders/pay-from-wallet استفاده کنید.")

    pm_setting = session.exec(
        select(PaymentMethodSetting).where(
            PaymentMethodSetting.bot_id == bot.id, PaymentMethodSetting.method == body.payment_method
        )
    ).first()
    if not pm_setting or not pm_setting.enabled:
        raise HTTPException(422, "این روش پرداخت برای این ربات فعال نیست.")

    try:
        order = orders_service.create_order(session, bot, customer, body.plan_id, body.payment_method)
    except orders_service.OrderError as e:
        raise HTTPException(422, str(e))
    return {
        "order_id": order.id,
        "sell_price": order.sell_price,
        "status": order.status,
        "payment_details": pm_setting.details,
    }


@router.get("/orders/{order_id}")
def get_order(order_id: int, bot: Bot = Depends(get_current_bot), session: Session = Depends(get_session)):
    order = session.get(Order, order_id)
    if not order or order.bot_id != bot.id:
        raise HTTPException(404, "سفارش یافت نشد.")
    return order


class SubmitReceiptRequest(BaseModel):
    telegram_file_id: str
    note: str = ""


@router.post("/orders/{order_id}/receipt")
def submit_order_receipt(
    order_id: int, body: SubmitReceiptRequest,
    bot: Bot = Depends(get_current_bot), session: Session = Depends(get_session),
):
    order = session.get(Order, order_id)
    if not order or order.bot_id != bot.id:
        raise HTTPException(404, "سفارش یافت نشد.")
    order.status = OrderStatus.PAYMENT_SUBMITTED
    session.add(order)
    receipt = PaymentReceipt(
        bot_id=bot.id, order_id=order.id,
        telegram_file_id=body.telegram_file_id, note=body.note,
    )
    session.add(receipt)
    session.commit()
    session.refresh(receipt)
    return {"receipt_id": receipt.id, "message": "رسید ثبت شد و در انتظار تایید ادمین است."}


# ---------------------------------------------------------------------------
# Wallet (فقط برای ربات‌هایی که والد دارند معنا دارد)
# ---------------------------------------------------------------------------
@router.get("/wallet")
def get_wallet(bot: Bot = Depends(get_current_bot)):
    return {"balance": bot.wallet_balance, "has_parent": bot.parent_bot_id is not None}


class WalletTopupRequest(BaseModel):
    amount: float
    telegram_file_id: str
    note: str = ""


@router.post("/wallet/topup-request")
def request_wallet_topup(
    body: WalletTopupRequest, bot: Bot = Depends(get_current_bot), session: Session = Depends(get_session)
):
    if bot.parent_bot_id is None:
        raise HTTPException(422, "ربات ریشه کیف‌پول والد ندارد.")
    receipt = PaymentReceipt(
        bot_id=bot.parent_bot_id,  # رسید برای تایید نزد ادمینِ والد ثبت می‌شود
        wallet_topup_bot_id=bot.id,
        wallet_topup_amount=body.amount,
        telegram_file_id=body.telegram_file_id,
        note=body.note,
    )
    session.add(receipt)
    session.commit()
    session.refresh(receipt)
    return {"receipt_id": receipt.id, "message": "درخواست شارژ ثبت شد و برای تایید نزد ادمین ارسال شد."}


# ---------------------------------------------------------------------------
# Receipt review (توسط ادمینِ همان ربات که رسید برایش ثبت شده)
# ---------------------------------------------------------------------------
@router.get("/receipts/pending")
def list_pending_receipts(
    requesting_telegram_id: str, bot: Bot = Depends(get_current_bot), session: Session = Depends(get_session)
):
    require_admin(bot, requesting_telegram_id)
    rows = session.exec(
        select(PaymentReceipt).where(
            PaymentReceipt.bot_id == bot.id, PaymentReceipt.status == ReceiptStatus.PENDING
        )
    ).all()
    return rows


class ReviewReceiptRequest(BaseModel):
    requesting_telegram_id: str
    note: str = ""


@router.post("/receipts/{receipt_id}/approve")
async def approve_receipt_endpoint(
    receipt_id: int, body: ReviewReceiptRequest,
    bot: Bot = Depends(get_current_bot), session: Session = Depends(get_session),
):
    require_admin(bot, body.requesting_telegram_id)
    receipt = session.get(PaymentReceipt, receipt_id)
    if not receipt or receipt.bot_id != bot.id:
        raise HTTPException(404, "رسید یافت نشد.")
    try:
        await receipts_service.approve_receipt(session, receipt, body.requesting_telegram_id)
    except Exception as e:
        raise HTTPException(500, f"خطا در پردازش تایید: {e}")
    log_action(session, f"bot:{body.requesting_telegram_id}", "receipt_approve", bot_id=bot.id, detail=f"receipt_id={receipt_id}")
    return {"message": "تایید شد."}


@router.post("/receipts/{receipt_id}/reject")
def reject_receipt_endpoint(
    receipt_id: int, body: ReviewReceiptRequest,
    bot: Bot = Depends(get_current_bot), session: Session = Depends(get_session),
):
    require_admin(bot, body.requesting_telegram_id)
    receipt = session.get(PaymentReceipt, receipt_id)
    if not receipt or receipt.bot_id != bot.id:
        raise HTTPException(404, "رسید یافت نشد.")
    receipts_service.reject_receipt(session, receipt, body.requesting_telegram_id, body.note)
    return {"message": "رد شد."}


# ---------------------------------------------------------------------------
# Sub-reseller bot creation
# ---------------------------------------------------------------------------
class CreateSubBotRequest(BaseModel):
    requesting_telegram_id: str
    telegram_token: str
    telegram_admin_id: str
    label: str = ""


@router.post("/sub-bots")
async def create_sub_bot(
    body: CreateSubBotRequest, bot: Bot = Depends(get_current_bot), session: Session = Depends(get_session)
):
    # توجه: برخلاف بقیه‌ی عملیات ادمین، ساخت ربات نمایندگی لازم نیست حتماً
    # توسط «ادمین» این ربات باشد — هر کاربری که مجوز عمومی ربات یا یک درخواست
    # نمایندگیِ تاییدشده‌ی مخصوص خودش را داشته باشد می‌تواند این کار را بکند؛
    # آن بررسی داخل create_sub_reseller_bot انجام می‌شود.
    try:
        new_bot = hierarchy_service.create_sub_reseller_bot(
            session, bot, body.telegram_token, body.telegram_admin_id,
            body.requesting_telegram_id, body.label,
        )
    except hierarchy_service.PermissionDeniedError as e:
        raise HTTPException(403, str(e))
    except hierarchy_service.SubBotLimitReachedError as e:
        raise HTTPException(422, str(e))
    log_action(session, f"bot:{body.requesting_telegram_id}", "bot_create", bot_id=new_bot.id, detail=f"parent={bot.id}")
    # ربات جدید را بلافاصله، بدون نیاز به ری‌استارت کل سیستم، در همین پروسه راه‌اندازی کن
    from backend.main import launch_bot_runtime  # import تاخیری برای جلوگیری از چرخه
    await launch_bot_runtime(new_bot)
    return {"bot_id": new_bot.id, "api_key": new_bot.api_key}


# ---------------------------------------------------------------------------
# پلن تست / رایگان
# ---------------------------------------------------------------------------
@router.get("/plans/trial")
def get_trial_plan(telegram_id: str, bot: Bot = Depends(get_current_bot), session: Session = Depends(get_session)):
    pricing = orders_service.get_trial_pricing(session, bot)
    if not pricing:
        return {"available": False}
    plan = session.get(Plan, pricing.plan_id)
    customer = session.exec(
        select(Customer).where(Customer.bot_id == bot.id, Customer.telegram_id == telegram_id)
    ).first()
    already_used = bool(customer and customer.used_trial)
    return {
        "available": not already_used,
        "plan_id": plan.id, "title": plan.title,
        "full_period_text": plan.full_period_text, "traffic_amount": plan.traffic_amount,
    }


class TrialOrderRequest(BaseModel):
    telegram_id: str


@router.post("/orders/trial")
async def create_trial_order(
    body: TrialOrderRequest, bot: Bot = Depends(get_current_bot), session: Session = Depends(get_session)
):
    customer = session.exec(
        select(Customer).where(Customer.bot_id == bot.id, Customer.telegram_id == body.telegram_id)
    ).first()
    if not customer:
        raise HTTPException(404, "ابتدا باید مشتری ثبت شود.")
    if customer.is_blocked:
        raise HTTPException(403, "این کاربر مسدود شده است.")
    try:
        order = orders_service.create_trial_order(session, bot, customer)
        order = await orders_service.fulfill_order(session, order)
    except orders_service.OrderError as e:
        raise HTTPException(422, str(e))
    return {"order_id": order.id, "status": order.status}


# ---------------------------------------------------------------------------
# پرداخت از کیف‌پول داخلی مشتری
# ---------------------------------------------------------------------------
@router.get("/wallet/customer")
def get_customer_wallet(telegram_id: str, bot: Bot = Depends(get_current_bot), session: Session = Depends(get_session)):
    customer = session.exec(
        select(Customer).where(Customer.bot_id == bot.id, Customer.telegram_id == telegram_id)
    ).first()
    return {"balance": customer.wallet_balance if customer else 0}


class CustomerWalletTopupRequest(BaseModel):
    telegram_id: str
    amount: float
    telegram_file_id: str
    note: str = ""


@router.post("/wallet/customer/topup-request")
def request_customer_wallet_topup(
    body: CustomerWalletTopupRequest, bot: Bot = Depends(get_current_bot), session: Session = Depends(get_session)
):
    customer = session.exec(
        select(Customer).where(Customer.bot_id == bot.id, Customer.telegram_id == body.telegram_id)
    ).first()
    if not customer:
        raise HTTPException(404, "ابتدا باید مشتری ثبت شود.")
    receipt = PaymentReceipt(
        bot_id=bot.id,
        customer_topup_customer_id=customer.id,
        customer_topup_amount=body.amount,
        telegram_file_id=body.telegram_file_id,
        note=body.note,
    )
    session.add(receipt)
    session.commit()
    session.refresh(receipt)
    return {"receipt_id": receipt.id, "message": "درخواست شارژ کیف‌پول ثبت شد و در انتظار تایید ادمین است."}


class PayFromWalletRequest(BaseModel):
    telegram_id: str
    plan_id: str


@router.post("/orders/pay-from-wallet")
async def pay_from_wallet(
    body: PayFromWalletRequest, bot: Bot = Depends(get_current_bot), session: Session = Depends(get_session)
):
    customer = session.exec(
        select(Customer).where(Customer.bot_id == bot.id, Customer.telegram_id == body.telegram_id)
    ).first()
    if not customer:
        raise HTTPException(404, "ابتدا باید مشتری ثبت شود.")
    if customer.is_blocked:
        raise HTTPException(403, "این کاربر مسدود شده است.")

    try:
        order = orders_service.create_order(session, bot, customer, body.plan_id, PaymentMethod.WALLET)
    except orders_service.OrderError as e:
        raise HTTPException(422, str(e))

    try:
        customer_deduct_for_purchase(session, customer, order.sell_price, order.id, note=f"سفارش #{order.id}")
    except InsufficientBalanceError as e:
        order.status = OrderStatus.FAILED
        order.failure_reason = str(e)
        session.add(order)
        session.commit()
        raise HTTPException(422, str(e))

    order.status = OrderStatus.PAID
    session.add(order)
    session.commit()

    try:
        order = await orders_service.fulfill_order(session, order)
    except orders_service.OrderError as e:
        # بازگرداندن مبلغ به کیف‌پول مشتری چون تحویل روی Connectix ناموفق بود
        customer_topup(session, customer, order.sell_price, tx_type=CustomerWalletTxType.ADJUSTMENT, note=f"بازگشت سفارش ناموفق #{order.id}")
        raise HTTPException(422, str(e))

    return {"order_id": order.id, "status": order.status}


# ---------------------------------------------------------------------------
# رفرال
# ---------------------------------------------------------------------------
@router.get("/referral/me")
def referral_me(telegram_id: str, bot: Bot = Depends(get_current_bot), session: Session = Depends(get_session)):
    customer = session.exec(
        select(Customer).where(Customer.bot_id == bot.id, Customer.telegram_id == telegram_id)
    ).first()
    if not customer:
        raise HTTPException(404, "مشتری یافت نشد.")
    stats = referral_service.get_referral_stats(session, customer)
    return {
        "referred_count": stats["referred_count"],
        "bonus_amount": bot.referral_bonus_amount,
        "customer_telegram_id": customer.telegram_id,
    }


# ---------------------------------------------------------------------------
# درخواست نمایندگی
# ---------------------------------------------------------------------------
class CreateResellerRequestBody(BaseModel):
    telegram_id: str
    note: str = ""


@router.post("/reseller-requests")
def create_reseller_request(
    body: CreateResellerRequestBody, bot: Bot = Depends(get_current_bot), session: Session = Depends(get_session)
):
    customer = session.exec(
        select(Customer).where(Customer.bot_id == bot.id, Customer.telegram_id == body.telegram_id)
    ).first()
    if not customer:
        raise HTTPException(404, "ابتدا باید مشتری ثبت شود.")
    if reseller_requests_service.has_permission(session, bot, body.telegram_id):
        return {"message": "شما همین حالا هم اجازه‌ی ساخت ربات نمایندگی را دارید."}
    req = reseller_requests_service.create_request(session, bot, customer, body.note)
    return {"request_id": req.id, "status": req.status, "message": "درخواست شما ثبت شد و در انتظار بررسی ادمین است."}


@router.get("/reseller-requests/my-status")
def my_reseller_status(
    telegram_id: str, bot: Bot = Depends(get_current_bot), session: Session = Depends(get_session)
):
    """آیا این کاربر همین الان اجازه‌ی ساخت ربات نمایندگی دارد؟ و آیا درخواست در انتظاری دارد؟"""
    can_create = reseller_requests_service.has_permission(session, bot, telegram_id)
    pending = session.exec(
        select(ResellerRequest).where(
            ResellerRequest.bot_id == bot.id,
            ResellerRequest.telegram_id == telegram_id,
            ResellerRequest.status == ResellerRequestStatus.PENDING,
        )
    ).first()
    return {"can_create": can_create, "has_pending_request": pending is not None}


@router.get("/reseller-requests/pending")
def list_pending_reseller_requests(
    requesting_telegram_id: str, bot: Bot = Depends(get_current_bot), session: Session = Depends(get_session)
):
    require_admin(bot, requesting_telegram_id)
    rows = session.exec(
        select(ResellerRequest).where(
            ResellerRequest.bot_id == bot.id, ResellerRequest.status == ResellerRequestStatus.PENDING
        )
    ).all()
    return rows


class ReviewResellerRequestBody(BaseModel):
    requesting_telegram_id: str


@router.post("/reseller-requests/{request_id}/approve")
def approve_reseller_request(
    request_id: int, body: ReviewResellerRequestBody,
    bot: Bot = Depends(get_current_bot), session: Session = Depends(get_session),
):
    require_admin(bot, body.requesting_telegram_id)
    req = session.get(ResellerRequest, request_id)
    if not req or req.bot_id != bot.id:
        raise HTTPException(404, "درخواست یافت نشد.")
    req.status = ResellerRequestStatus.APPROVED
    req.reviewed_at = utcnow()
    session.add(req)
    session.commit()
    return {"message": "تایید شد."}


@router.post("/reseller-requests/{request_id}/reject")
def reject_reseller_request(
    request_id: int, body: ReviewResellerRequestBody,
    bot: Bot = Depends(get_current_bot), session: Session = Depends(get_session),
):
    require_admin(bot, body.requesting_telegram_id)
    req = session.get(ResellerRequest, request_id)
    if not req or req.bot_id != bot.id:
        raise HTTPException(404, "درخواست یافت نشد.")
    req.status = ResellerRequestStatus.REJECTED
    session.add(req)
    session.commit()
    return {"message": "رد شد."}


# ---------------------------------------------------------------------------
# مدیریت کاربران (مخصوص ادمین همان ربات — هم برای استفاده از داخل ربات، هم پنل وب)
# ---------------------------------------------------------------------------
@router.get("/customers")
def list_customers(
    requesting_telegram_id: str, bot: Bot = Depends(get_current_bot), session: Session = Depends(get_session)
):
    require_admin(bot, requesting_telegram_id)
    rows = session.exec(select(Customer).where(Customer.bot_id == bot.id)).all()
    return rows


class CustomerActionRequest(BaseModel):
    requesting_telegram_id: str


@router.post("/customers/{customer_id}/toggle-block")
def toggle_block_customer(
    customer_id: int, body: CustomerActionRequest,
    bot: Bot = Depends(get_current_bot), session: Session = Depends(get_session),
):
    require_admin(bot, body.requesting_telegram_id)
    customer = session.get(Customer, customer_id)
    if not customer or customer.bot_id != bot.id:
        raise HTTPException(404, "کاربر یافت نشد.")
    customer.is_blocked = not customer.is_blocked
    session.add(customer)
    session.commit()
    return {"is_blocked": customer.is_blocked}


class CustomerWalletAdjustRequest(BaseModel):
    requesting_telegram_id: str
    amount: float
    note: str = ""


@router.post("/customers/{customer_id}/wallet-adjust")
def adjust_customer_wallet(
    customer_id: int, body: CustomerWalletAdjustRequest,
    bot: Bot = Depends(get_current_bot), session: Session = Depends(get_session),
):
    require_admin(bot, body.requesting_telegram_id)
    customer = session.get(Customer, customer_id)
    if not customer or customer.bot_id != bot.id:
        raise HTTPException(404, "کاربر یافت نشد.")
    customer_topup(session, customer, body.amount, tx_type=CustomerWalletTxType.ADJUSTMENT, note=body.note or "اصلاح دستی توسط ادمین")
    return {"balance": customer.wallet_balance}
