"""
مدل‌های دیتابیس. تمام منطق سلسله‌مراتب نمایندگی، قیمت‌گذاری، کیف‌پول، رفرال،
درخواست نمایندگی و سفارش‌ها این‌جا تعریف شده.
"""
from datetime import datetime, timezone
from enum import Enum
from typing import Optional
import secrets
import uuid

from sqlmodel import SQLModel, Field


def new_uuid() -> str:
    return str(uuid.uuid4())


def new_api_key() -> str:
    return "cxb_" + secrets.token_urlsafe(32)


def utcnow() -> datetime:
    """نسخه‌ی timezone-aware از utcnow — نسخه‌های جدید SQLModel/Pydantic دیگر
    datetime naive (بدون timezone) را برای ستون‌های DateTime قبول نمی‌کنند."""
    return datetime.now(timezone.utc)


class PaymentMethod(str, Enum):
    CARD_TO_CARD = "card_to_card"
    GATEWAY = "gateway"
    CRYPTO = "crypto"
    WALLET = "wallet"  # پرداخت از موجودی کیف‌پول داخلی مشتری


class OrderStatus(str, Enum):
    AWAITING_PAYMENT = "awaiting_payment"
    PAYMENT_SUBMITTED = "payment_submitted"  # رسید ارسال شد، منتظر تایید ادمین
    PAID = "paid"                              # تایید شد، منتظر ساخت/افزودن پلن
    FULFILLED = "fulfilled"                    # روی Connectix ساخته/اضافه شد
    REJECTED = "rejected"
    FAILED = "failed"


class ReceiptStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class ResellerRequestStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class CustomerWalletTxType(str, Enum):
    TOPUP = "topup"
    PURCHASE = "purchase"
    REFERRAL_BONUS = "referral_bonus"
    ADJUSTMENT = "adjustment"


# ---------------------------------------------------------------------------
# Bot — هر رکورد یک ربات تلگرامی (اصلی یا نماینده در هر سطح) است
# ---------------------------------------------------------------------------
class Bot(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    parent_bot_id: Optional[int] = Field(default=None, foreign_key="bot.id", index=True)

    label: str = Field(default="")  # نام نمایشی برای ادمین اصلی در پنل
    telegram_token: str = Field(index=True)
    telegram_admin_id: str  # آیدی عددی تلگرام ادمین این ربات (می‌تواند چندتایی با , جدا شود)

    api_key: str = Field(default_factory=new_api_key, index=True, unique=True)

    is_active: bool = Field(default=True)
    hosting_mode: str = Field(default="central")  # "central" | "external"

    # آیا این ربات اجازه دارد زیرنماینده (ربات فرزند) بسازد؟ (مجوز عمومی — برای همه‌ی کاربرانش)
    can_create_sub_reseller: bool = Field(default=False)

    # موجودی پیش‌پرداختِ این ربات نزد والدش (برای ربات ریشه بی‌معنی/استفاده‌نشده است)
    wallet_balance: float = Field(default=0)

    # عضویت اجباری در کانال
    forced_channel: str = Field(default="")  # مثل @mychannel
    forced_channel_enabled: bool = Field(default=False)

    # رفرال
    referral_bonus_amount: float = Field(default=0)  # مبلغ پاداش به معرف، به ازای اولین خرید موفق زیرمجموعه

    # پشتیبانی
    support_contact: str = Field(default="")  # مثلاً @AdminSupport یا شماره تماس (نمایشی)

    # سطح نمایندگی (برچسب تجاری) + درصد تخفیف این سطح روی قیمت تمام‌شده
    tier: str = Field(default="normal")  # normal | special | professional | enterprise
    tier_discount_percent: float = Field(default=0)  # ۰ تا ۱۰۰؛ روی قیمت فروش والد اعمال می‌شود

    # سقف تعداد زیرنماینده‌ای که این ربات مجاز به ساختن آن است (خالی/None = نامحدود)
    max_sub_bots: Optional[int] = Field(default=None)

    # سقف اعتبار (بدهی مجاز): موجودی کیف‌پول این ربات می‌تواند تا این مقدار منفی شود
    credit_limit: float = Field(default=0)

    created_at: datetime = Field(default_factory=utcnow)


class BotTier(str, Enum):
    NORMAL = "normal"
    SPECIAL = "special"
    PROFESSIONAL = "professional"
    ENTERPRISE = "enterprise"


TIER_LABELS_FA = {
    "normal": "عادی",
    "special": "ویژه",
    "professional": "حرفه‌ای",
    "enterprise": "سازمانی",
}


# ---------------------------------------------------------------------------
# Plan — کش محلی پلن‌های Connectix (از clients/get-enabled-plans)
# ---------------------------------------------------------------------------
class Plan(SQLModel, table=True):
    id: str = Field(primary_key=True)  # همان UUID پلن در Connectix
    title: str
    full_period_text: str = ""
    full_period_days: int = 0
    period: int = 0
    period_unit: str = ""
    count_of_devices: int = 0
    traffic_amount: str = ""  # عدد یا "Unlimited" — به صورت متن ذخیره می‌شود
    plan_type: str = ""  # Connectix این را "Free" برای پلن‌های تست/رایگان می‌فرستد
    connectix_sell_price: float = 0  # قیمتی که خود Connectix برای این پلن اعلام کرده
    is_enabled_upstream: bool = Field(default=True)
    last_synced_at: datetime = Field(default_factory=utcnow)

    @property
    def is_trial(self) -> bool:
        return self.plan_type.strip().lower() == "free"


# ---------------------------------------------------------------------------
# PlanPricing — قیمت هر پلن به ازای هر ربات (هم قیمت تمام‌شده، هم قیمت فروش)
# ---------------------------------------------------------------------------
class PlanPricing(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    bot_id: int = Field(foreign_key="bot.id", index=True)
    plan_id: str = Field(foreign_key="plan.id", index=True)

    # قیمتی که این ربات برای تهیه‌ی این پلن از والدش (یا از Connectix اگر ریشه است) می‌پردازد
    cost_price: float = Field(default=0)

    # قیمتی که این ربات به مشتریان خودش می‌فروشد (توسط ادمین همان ربات تعیین می‌شود)
    sell_price: float = Field(default=0)

    is_offered: bool = Field(default=True)  # آیا این ربات این پلن را عرضه می‌کند؟


# ---------------------------------------------------------------------------
# PaymentMethodSetting — روشن/خاموش بودن هر روش پرداخت به ازای هر ربات
# ---------------------------------------------------------------------------
class PaymentMethodSetting(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    bot_id: int = Field(foreign_key="bot.id", index=True)
    method: PaymentMethod
    enabled: bool = Field(default=False)
    # برای کارت‌به‌کارت: شماره کارت/نام صاحب حساب. برای کریپتو: آدرس کیف‌پول.
    details: str = Field(default="")


# ---------------------------------------------------------------------------
# Customer — کاربر نهایی تلگرام، همیشه زیرمجموعه‌ی یک ربات مشخص
# ---------------------------------------------------------------------------
class Customer(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    bot_id: int = Field(foreign_key="bot.id", index=True)
    telegram_id: str = Field(index=True)
    telegram_username: str = Field(default="")
    name: str = Field(default="")
    phone: str = Field(default="")
    email: str = Field(default="")

    # اطلاعات کلاینت متناظر در Connectix (بعد از اولین خرید پر می‌شود)
    connectix_client_id: Optional[str] = Field(default=None, index=True)
    connectix_username: Optional[str] = None
    connectix_password: Optional[str] = None

    # کیف‌پول داخلی مشتری (برای پرداخت سریع بدون نیاز به ارسال رسید هر بار)
    wallet_balance: float = Field(default=0)

    # پلن تست/رایگان فقط یک‌بار قابل استفاده است
    used_trial: bool = Field(default=False)

    is_blocked: bool = Field(default=False)

    # رفرال: کدام مشتری (در همین ربات) این کاربر را معرفی کرده
    referred_by_customer_id: Optional[int] = Field(default=None, foreign_key="customer.id")

    created_at: datetime = Field(default_factory=utcnow)


# ---------------------------------------------------------------------------
# Order — یک سفارش خرید/تمدید پلن
# ---------------------------------------------------------------------------
class Order(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    bot_id: int = Field(foreign_key="bot.id", index=True)
    customer_id: int = Field(foreign_key="customer.id", index=True)
    plan_id: str = Field(foreign_key="plan.id")

    cost_price: float = 0
    sell_price: float = 0
    payment_method: Optional[PaymentMethod] = None
    status: OrderStatus = Field(default=OrderStatus.AWAITING_PAYMENT)

    is_renewal: bool = Field(default=False)  # افزودن پلن به کاربر موجود در برابر ساخت کاربر جدید
    is_trial: bool = Field(default=False)

    created_at: datetime = Field(default_factory=utcnow)
    fulfilled_at: Optional[datetime] = None
    failure_reason: str = Field(default="")


# ---------------------------------------------------------------------------
# PaymentReceipt — رسید کارت‌به‌کارت: برای سفارش مشتری، شارژ کیف‌پول نماینده،
# یا شارژ کیف‌پول داخلی مشتری
# ---------------------------------------------------------------------------
class PaymentReceipt(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    bot_id: int = Field(foreign_key="bot.id", index=True)

    # دقیقاً یکی از این سه‌تا پر می‌شود:
    order_id: Optional[int] = Field(default=None, foreign_key="order.id")
    wallet_topup_bot_id: Optional[int] = Field(default=None, foreign_key="bot.id")
    wallet_topup_amount: Optional[float] = None
    customer_topup_customer_id: Optional[int] = Field(default=None, foreign_key="customer.id")
    customer_topup_amount: Optional[float] = None

    telegram_file_id: str = Field(default="")  # عکس رسید در تلگرام
    note: str = Field(default="")
    status: ReceiptStatus = Field(default=ReceiptStatus.PENDING)
    created_at: datetime = Field(default_factory=utcnow)
    reviewed_at: Optional[datetime] = None
    reviewed_by_admin_id: str = Field(default="")


# ---------------------------------------------------------------------------
# WalletTransaction — دفترکل تغییرات موجودی کیف‌پول هر ربات (برای گزارش/شفافیت)
# ---------------------------------------------------------------------------
class WalletTransactionType(str, Enum):
    TOPUP = "topup"
    ORDER_DEDUCTION = "order_deduction"
    ADJUSTMENT = "adjustment"


class WalletTransaction(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    bot_id: int = Field(foreign_key="bot.id", index=True)
    amount: float  # مثبت = افزایش، منفی = کاهش
    type: WalletTransactionType
    related_order_id: Optional[int] = Field(default=None, foreign_key="order.id")
    note: str = Field(default="")
    created_at: datetime = Field(default_factory=utcnow)


# ---------------------------------------------------------------------------
# CustomerWalletTransaction — دفترکل کیف‌پول داخلی مشتری
# ---------------------------------------------------------------------------
class CustomerWalletTransaction(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    customer_id: int = Field(foreign_key="customer.id", index=True)
    amount: float
    type: CustomerWalletTxType
    related_order_id: Optional[int] = Field(default=None, foreign_key="order.id")
    note: str = Field(default="")
    created_at: datetime = Field(default_factory=utcnow)


# ---------------------------------------------------------------------------
# ResellerRequest — درخواست یک مشتری عادی برای تبدیل‌شدن به نماینده
# ---------------------------------------------------------------------------
class ResellerRequest(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    bot_id: int = Field(foreign_key="bot.id", index=True)
    customer_id: int = Field(foreign_key="customer.id", index=True)
    telegram_id: str = Field(index=True)
    note: str = Field(default="")
    status: ResellerRequestStatus = Field(default=ResellerRequestStatus.PENDING)
    created_at: datetime = Field(default_factory=utcnow)
    reviewed_at: Optional[datetime] = None


# ---------------------------------------------------------------------------
# AdminUser — حساب‌های ورود به پنل تحت وب (چندکاربره، با نقش)
# ---------------------------------------------------------------------------
class AdminRole(str, Enum):
    SUPER_ADMIN = "super_admin"    # دسترسی کامل، از جمله مالی و ساخت ربات
    FINANCE = "finance"            # کیف‌پول، تایید رسید، گزارش مالی
    SUPPORT = "support"            # مشاهده‌ی کاربران/سفارش‌ها، بدون تغییرات مالی
    VIEWER = "viewer"              # فقط مشاهده


class AdminUser(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(index=True, unique=True)
    password_hash: str
    role: AdminRole = Field(default=AdminRole.SUPER_ADMIN)
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=utcnow)


# ---------------------------------------------------------------------------
# AdminActionLog — لاگ حسابرسیِ عملیات مهم مدیریتی/مالی (قابل‌حذف نیست)
# ---------------------------------------------------------------------------
class AdminActionLog(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    actor: str  # مثل "web:aziz" یا "bot:123456789"
    action: str  # مثل "wallet_adjust", "bot_create", "receipt_approve"
    bot_id: Optional[int] = Field(default=None, foreign_key="bot.id")
    detail: str = Field(default="")
    created_at: datetime = Field(default_factory=utcnow)


# ---------------------------------------------------------------------------
# ScheduledBroadcast — پیام همگانیِ زمان‌بندی‌شده برای ارسال در آینده
# ---------------------------------------------------------------------------
class ScheduledBroadcastStatus(str, Enum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ScheduledBroadcast(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    bot_id: int = Field(foreign_key="bot.id", index=True)
    message: str
    send_at: datetime = Field(index=True)  # زمان ارسال، به وقت UTC
    status: ScheduledBroadcastStatus = Field(default=ScheduledBroadcastStatus.PENDING)
    created_by: str = Field(default="")
    created_at: datetime = Field(default_factory=utcnow)
    sent_at: Optional[datetime] = None
    sent_count: int = Field(default=0)
    failed_count: int = Field(default=0)
