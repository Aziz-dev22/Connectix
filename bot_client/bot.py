"""
ربات تلگرامی کلاینت. این فایل تنها از طریق BackendApiClient با دنیای بیرون
(دیتابیس، Connectix، کیف‌پول) صحبت می‌کند — هیچ منطق تجاری این‌جا نیست.
همین فایل، چه روی سرور مرکزی و چه روی سرور شخصی یک نماینده اجرا شود، یکسان است.
"""
import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes,
)

from bot_client.api_client import BackendApiClient, BackendApiError

logger = logging.getLogger(__name__)

PAYMENT_METHOD_LABELS = {
    "card_to_card": "💳 کارت به کارت",
    "gateway": "🌐 درگاه پرداخت آنلاین",
    "crypto": "₿ ارز دیجیتال",
}

BACK_ROW = [InlineKeyboardButton("⬅️ بازگشت به منو", callback_data="menu")]
CANCEL_ROW = [InlineKeyboardButton("❌ انصراف", callback_data="cancel_flow")]


def kb(rows: list, back: bool = True) -> InlineKeyboardMarkup:
    """ساخت کیبورد inline با افزودن خودکار ردیف «بازگشت» به انتها."""
    all_rows = list(rows)
    if back:
        all_rows.append(BACK_ROW)
    return InlineKeyboardMarkup(all_rows)


def get_api(context: ContextTypes.DEFAULT_TYPE) -> BackendApiClient:
    return context.application.bot_data["api"]


# ---------------------------------------------------------------------------
# عضویت اجباری در کانال
# ---------------------------------------------------------------------------
async def is_channel_member(context: ContextTypes.DEFAULT_TYPE, channel: str, user_id: int) -> bool:
    try:
        member = await context.bot.get_chat_member(chat_id=channel, user_id=user_id)
        return member.status not in ("left", "kicked")
    except TelegramError:
        # اگر ربات توی کانال ادمین نباشد یا کانال نامعتبر باشد، برای جلوگیری از
        # قفل‌شدن کامل ربات، عضویت را «تایید‌شده» در نظر می‌گیریم.
        return True


async def check_forced_channel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """اگر عضویت اجباری فعال است و کاربر عضو نیست، پیام عضویت را نشان می‌دهد و False برمی‌گرداند."""
    api = get_api(context)
    info = await api.bot_info()
    if not info.get("forced_channel_enabled") or not info.get("forced_channel"):
        return True
    channel = info["forced_channel"]
    user_id = update.effective_user.id
    if await is_channel_member(context, channel, user_id):
        return True
    channel_username = channel.lstrip("@")
    buttons = [
        [InlineKeyboardButton("📡 عضویت در کانال", url=f"https://t.me/{channel_username}")],
        [InlineKeyboardButton("✅ عضو شدم، بررسی مجدد", callback_data="check_join")],
    ]
    text = "برای استفاده از ربات، ابتدا باید در کانال ما عضو شوید."
    q = update.callback_query
    if q:
        await q.answer()
        await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons))
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons))
    return False


async def cb_check_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await check_forced_channel(update, context):
        await show_main_menu(update, context)


# ---------------------------------------------------------------------------
# منوی اصلی
# ---------------------------------------------------------------------------
def main_menu_keyboard(is_admin: bool) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("🧪 تست رایگان", callback_data="trial"),
         InlineKeyboardButton("🛒 خرید سرویس", callback_data="buy")],
        [InlineKeyboardButton("📦 سرویس‌های من", callback_data="myservices"),
         InlineKeyboardButton("💰 کیف‌پول", callback_data="wallet")],
        [InlineKeyboardButton("🔗 رفرال", callback_data="referral"),
         InlineKeyboardButton("🤝 نمایندگی", callback_data="reseller_menu")],
        [InlineKeyboardButton("🆘 پشتیبانی", callback_data="support")],
    ]
    if is_admin:
        rows.append([InlineKeyboardButton("⚙️ پنل مدیریت", callback_data="admin_menu")])
    return InlineKeyboardMarkup(rows)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    api = get_api(context)
    user = update.effective_user
    context.user_data["flow"] = None

    referred_by = None
    if context.args and context.args[0].startswith("ref_"):
        referred_by = context.args[0][len("ref_"):]

    await api.upsert_customer(
        telegram_id=str(user.id), telegram_username=user.username or "", name=user.full_name or "",
        referred_by_telegram_id=referred_by,
    )

    if not await check_forced_channel(update, context):
        return

    info = await api.bot_info()
    is_admin = await api.is_admin(str(user.id))
    await update.message.reply_text(
        f"سلام {user.first_name}! 👋\nبه ربات فروش VPN «{info['label']}» خوش آمدید.",
        reply_markup=main_menu_keyboard(is_admin),
    )


async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    api = get_api(context)
    user = update.effective_user
    is_admin = await api.is_admin(str(user.id))
    text = "منوی اصلی:"
    markup = main_menu_keyboard(is_admin)
    q = update.callback_query
    if q:
        await q.edit_message_text(text, reply_markup=markup)
    else:
        await update.message.reply_text(text, reply_markup=markup)


async def cb_back_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["flow"] = None
    await update.callback_query.answer()
    await show_main_menu(update, context)


async def cb_cancel_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["flow"] = None
    await update.callback_query.answer("لغو شد.")
    await show_main_menu(update, context)


# ---------------------------------------------------------------------------
# تست رایگان
# ---------------------------------------------------------------------------
async def cb_trial(update: Update, context: ContextTypes.DEFAULT_TYPE):
    api = get_api(context)
    q = update.callback_query
    await q.answer()
    if not await check_forced_channel(update, context):
        return
    user = update.effective_user
    data = await api.get_trial_plan(str(user.id))
    if not data.get("available"):
        reason = "پلن تست برای این ربات فعال نیست." if "plan_id" not in data else "شما قبلاً از پلن تست استفاده کرده‌اید."
        await q.edit_message_text(reason, reply_markup=kb([]))
        return
    text = (
        f"پلن تست: {data['title']}\n"
        f"مدت: {data.get('full_period_text', '—')}\n"
        f"حجم: {data.get('traffic_amount', '—')}\n\n"
        "این پلن فقط یک‌بار برای هر کاربر قابل‌استفاده است. فعال شود؟"
    )
    await q.edit_message_text(text, reply_markup=kb([[InlineKeyboardButton("✅ فعال‌سازی", callback_data="trial_confirm")]]))


async def cb_trial_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    api = get_api(context)
    q = update.callback_query
    await q.answer()
    user = update.effective_user
    try:
        await api.create_trial_order(str(user.id))
    except BackendApiError as e:
        await q.edit_message_text(f"خطا: {e.detail}", reply_markup=kb([]))
        return
    await q.edit_message_text(
        "✅ پلن تست فعال شد! برای دیدن اطلاعات اتصال، «سرویس‌های من» را بزنید.",
        reply_markup=kb([[InlineKeyboardButton("📦 سرویس‌های من", callback_data="myservices")]]),
    )


# ---------------------------------------------------------------------------
# خرید سرویس
# ---------------------------------------------------------------------------
async def cb_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    api = get_api(context)
    q = update.callback_query
    await q.answer()
    if not await check_forced_channel(update, context):
        return
    plans = await api.list_plans()
    if not plans:
        await q.edit_message_text("در حال حاضر پلنی برای فروش فعال نیست.", reply_markup=kb([]))
        return
    rows = [
        [InlineKeyboardButton(
            f"{p['title']} — {p['sell_price']:,.0f} تومان",
            callback_data=f"plan:{p['plan_id']}",
        )]
        for p in plans
    ]
    await q.edit_message_text("یک پلن را انتخاب کنید:", reply_markup=kb(rows))


async def cb_select_plan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    api = get_api(context)
    q = update.callback_query
    await q.answer()
    plan_id = q.data.split(":", 1)[1]
    context.user_data["selected_plan_id"] = plan_id
    user = update.effective_user

    rows = []
    wallet = await api.get_customer_wallet(str(user.id))
    plans = await api.list_plans()
    plan = next((p for p in plans if p["plan_id"] == plan_id), None)
    if plan and wallet["balance"] >= plan["sell_price"]:
        rows.append([InlineKeyboardButton(
            f"💰 پرداخت از کیف‌پول (موجودی: {wallet['balance']:,.0f})", callback_data="pay:wallet"
        )])

    methods = await api.list_payment_methods()
    enabled = [m for m in methods if m["enabled"]]
    for m in enabled:
        rows.append([InlineKeyboardButton(PAYMENT_METHOD_LABELS.get(m["method"], m["method"]), callback_data=f"pay:{m['method']}")])

    if not rows:
        await q.edit_message_text("هیچ روش پرداختی فعال نیست. لطفاً با پشتیبانی تماس بگیرید.", reply_markup=kb([]))
        return
    await q.edit_message_text("روش پرداخت را انتخاب کنید:", reply_markup=kb(rows))


async def cb_select_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    api = get_api(context)
    q = update.callback_query
    await q.answer()
    method = q.data.split(":", 1)[1]
    plan_id = context.user_data.get("selected_plan_id")
    user = update.effective_user

    if method == "wallet":
        try:
            await api.pay_from_wallet(str(user.id), plan_id)
        except BackendApiError as e:
            await q.edit_message_text(f"خطا: {e.detail}", reply_markup=kb([]))
            return
        await q.edit_message_text(
            "✅ از کیف‌پول پرداخت شد و سرویس فعال شد.",
            reply_markup=kb([[InlineKeyboardButton("📦 سرویس‌های من", callback_data="myservices")]]),
        )
        return

    try:
        order = await api.create_order(str(user.id), plan_id, method)
    except BackendApiError as e:
        await q.edit_message_text(f"خطا در ثبت سفارش: {e.detail}", reply_markup=kb([]))
        return

    context.user_data["flow"] = f"await_receipt_photo:{order['order_id']}"
    price_text = f"{order['sell_price']:,.0f} تومان"
    if method == "gateway":
        await q.edit_message_text(
            f"مبلغ قابل پرداخت: {price_text}\n\n"
            "پرداخت آنلاین هنوز توسط ادمین این ربات نهایی نشده. لطفاً از روش‌های دیگر استفاده کنید یا با پشتیبانی تماس بگیرید.",
            reply_markup=kb([], back=True),
        )
        return

    details = order.get("payment_details") or "— (ادمین هنوز جزئیات واریز را وارد نکرده)"
    label = "شماره کارت" if method == "card_to_card" else "آدرس کیف‌پول"
    await q.edit_message_text(
        f"مبلغ قابل پرداخت: {price_text}\n{label}: `{details}`\n\n"
        "پس از پرداخت، عکس رسید را همین‌جا ارسال کنید.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([CANCEL_ROW]),
    )


# ---------------------------------------------------------------------------
# سرویس‌های من
# ---------------------------------------------------------------------------
async def cb_my_services(update: Update, context: ContextTypes.DEFAULT_TYPE):
    api = get_api(context)
    q = update.callback_query
    await q.answer()
    user = update.effective_user
    data = await api.get_customer_services(str(user.id))
    if not data.get("has_service"):
        await q.edit_message_text(
            "هنوز سرویسی خریداری نکرده‌اید.",
            reply_markup=kb([[InlineKeyboardButton("🛒 خرید سرویس", callback_data="buy")]]),
        )
        return
    c = data["client"]
    plan = c.get("active_plan") or {}
    text = (
        f"نام کاربری: `{c.get('username')}`\n"
        f"رمز عبور: `{c.get('password')}`\n"
        f"پلن فعال: {plan.get('title', '—')}\n"
        f"حجم مصرفی: {plan.get('used_traffic', 0)} / {plan.get('traffic_amount', '—')}\n"
        f"تاریخ انقضا: {plan.get('expire_date', '—')}\n\n"
        f"لینک اتصال:\n`{c.get('subscription_link')}`"
    )
    await q.edit_message_text(text, parse_mode="Markdown", reply_markup=kb([]))


# ---------------------------------------------------------------------------
# کیف‌پول مشتری
# ---------------------------------------------------------------------------
async def cb_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    api = get_api(context)
    q = update.callback_query
    await q.answer()
    user = update.effective_user
    data = await api.get_customer_wallet(str(user.id))
    text = f"موجودی فعلی کیف‌پول شما: {data['balance']:,.0f} تومان"
    await q.edit_message_text(text, reply_markup=kb([[InlineKeyboardButton("💳 شارژ کیف‌پول", callback_data="wallet_topup")]]))


async def cb_wallet_topup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    context.user_data["flow"] = "await_customer_topup_amount"
    await q.edit_message_text("مبلغی که می‌خواهید شارژ کنید را به تومان وارد کنید (فقط عدد):", reply_markup=InlineKeyboardMarkup([CANCEL_ROW]))


# ---------------------------------------------------------------------------
# رفرال
# ---------------------------------------------------------------------------
async def cb_referral(update: Update, context: ContextTypes.DEFAULT_TYPE):
    api = get_api(context)
    q = update.callback_query
    await q.answer()
    user = update.effective_user
    data = await api.referral_me(str(user.id))
    me = await context.bot.get_me()
    link = f"https://t.me/{me.username}?start=ref_{user.id}"
    text = (
        f"لینک دعوت اختصاصی شما:\n{link}\n\n"
        f"تعداد افرادی که با این لینک آمده‌اند: {data['referred_count']}\n"
        f"پاداش شما به ازای اولین خرید هرکدام: {data['bonus_amount']:,.0f} تومان"
    )
    await q.edit_message_text(text, reply_markup=kb([]))


# ---------------------------------------------------------------------------
# نمایندگی (درخواست / ساخت ربات نمایندگی)
# ---------------------------------------------------------------------------
async def cb_reseller_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    api = get_api(context)
    q = update.callback_query
    await q.answer()
    user = update.effective_user
    status = await api.my_reseller_status(str(user.id))

    rows = []
    if status["can_create"]:
        rows.append([InlineKeyboardButton("🛠 ساخت ربات نمایندگی", callback_data="admin_create_subbot")])
        text = "شما اجازه‌ی ساخت ربات نمایندگی را دارید:"
    elif status["has_pending_request"]:
        text = "درخواست نمایندگی شما ثبت شده و در انتظار بررسی ادمین است."
    else:
        rows.append([InlineKeyboardButton("📝 درخواست نمایندگی", callback_data="reseller_request")])
        text = "برای ساخت ربات نمایندگی باید مجوز داشته باشید. برای درخواست مجوز، دکمه‌ی زیر را بزنید:"

    await q.edit_message_text(text, reply_markup=kb(rows))


async def cb_reseller_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    api = get_api(context)
    q = update.callback_query
    await q.answer()
    user = update.effective_user
    try:
        result = await api.create_reseller_request(str(user.id))
    except BackendApiError as e:
        await q.edit_message_text(f"خطا: {e.detail}", reply_markup=kb([]))
        return
    await q.edit_message_text(result.get("message", "درخواست ثبت شد."), reply_markup=kb([]))


# ---------------------------------------------------------------------------
# پشتیبانی
# ---------------------------------------------------------------------------
async def cb_support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    api = get_api(context)
    q = update.callback_query
    await q.answer()
    info = await api.bot_info()
    context.user_data["flow"] = "support_chat"
    contact_line = f"\nراه ارتباطی مستقیم: {info['support_contact']}" if info.get("support_contact") else ""
    await q.edit_message_text(
        f"پیام خود را همین‌جا بنویسید؛ مستقیم برای ادمین ارسال می‌شود.{contact_line}",
        reply_markup=InlineKeyboardMarkup([CANCEL_ROW]),
    )


async def cmd_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دستور ادمین برای پاسخ به پیام پشتیبانی: /reply <telegram_id> <متن>"""
    api = get_api(context)
    user = update.effective_user
    if not await api.is_admin(str(user.id)):
        return
    parts = (update.message.text or "").split(maxsplit=2)
    if len(parts) < 3:
        await update.message.reply_text("فرمت درست: /reply <آیدی_تلگرام> <پیام>")
        return
    target_id, text = parts[1], parts[2]
    try:
        await context.bot.send_message(chat_id=int(target_id), text=f"📩 پاسخ پشتیبانی:\n{text}")
        await update.message.reply_text("ارسال شد ✅")
    except (TelegramError, ValueError) as e:
        await update.message.reply_text(f"ارسال نشد: {e}")


# ---------------------------------------------------------------------------
# ورودی‌های عکس (رسید پرداخت سفارش، شارژ کیف‌پول نماینده، شارژ کیف‌پول مشتری، پشتیبانی)
# ---------------------------------------------------------------------------
async def notify_admins_of_receipt(context: ContextTypes.DEFAULT_TYPE, receipt_id, caption: str, file_id: str):
    """بلافاصله بعد از ثبت هر رسید، عکس آن را با دکمه‌ی تایید/رد مستقیم برای همه‌ی
    ادمین‌های این ربات می‌فرستد — به‌جای این‌که منتظر بمانند تا خودشان منوی
    «رسیدهای در انتظار» را باز کنند."""
    api = get_api(context)
    info = await api.bot_info()
    kb_markup = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ تایید", callback_data=f"rcpt_ok:{receipt_id}"),
        InlineKeyboardButton("❌ رد", callback_data=f"rcpt_no:{receipt_id}"),
    ]])
    for admin_id in info["admin_ids"]:
        try:
            await context.bot.send_photo(chat_id=int(admin_id), photo=file_id, caption=caption, reply_markup=kb_markup)
        except (TelegramError, ValueError):
            pass


async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    flow = context.user_data.get("flow")
    api = get_api(context)
    file_id = update.message.photo[-1].file_id
    user = update.effective_user

    if not flow:
        return

    if flow.startswith("await_receipt_photo:"):
        order_id = int(flow.split(":", 1)[1])
        result = await api.submit_order_receipt(order_id, file_id)
        context.user_data["flow"] = None
        await update.message.reply_text("رسید دریافت شد ✅ در انتظار تایید ادمین هستید.", reply_markup=kb([]))
        await notify_admins_of_receipt(
            context, result["receipt_id"],
            f"🧾 رسید جدید برای سفارش #{order_id}\nاز: {user.full_name} (ID: {user.id})",
            file_id,
        )
        return

    if flow.startswith("await_wallet_topup_photo:"):
        amount = float(flow.split(":", 1)[1])
        result = await api.request_wallet_topup(amount, file_id)
        context.user_data["flow"] = None
        await update.message.reply_text("درخواست شارژ کیف‌پول ثبت شد ✅ در انتظار تایید است.", reply_markup=kb([]))
        await notify_admins_of_receipt(
            context, result["receipt_id"],
            f"🧾 درخواست شارژ کیف‌پول نمایندگی به مبلغ {amount:,.0f}\nاز: {user.full_name} (ID: {user.id})",
            file_id,
        )
        return

    if flow.startswith("await_customer_topup_photo:"):
        amount = float(flow.split(":", 1)[1])
        result = await api.request_customer_wallet_topup(str(user.id), amount, file_id)
        context.user_data["flow"] = None
        await update.message.reply_text("درخواست شارژ کیف‌پول ثبت شد ✅ در انتظار تایید ادمین است.", reply_markup=kb([]))
        await notify_admins_of_receipt(
            context, result["receipt_id"],
            f"🧾 درخواست شارژ کیف‌پول مشتری به مبلغ {amount:,.0f}\nاز: {user.full_name} (ID: {user.id})",
            file_id,
        )
        return

    if flow == "support_chat":
        user = update.effective_user
        info = await api.bot_info()
        caption = f"🆘 پیام پشتیبانی (عکس) از {user.full_name} (ID: {user.id})"
        for admin_id in info["admin_ids"]:
            try:
                await context.bot.send_photo(chat_id=int(admin_id), photo=file_id, caption=caption)
            except (TelegramError, ValueError):
                pass
        await update.message.reply_text("ارسال شد. منتظر پاسخ ادمین باشید.", reply_markup=kb([]))
        return


# ---------------------------------------------------------------------------
# پنل مدیریت (فقط ادمین همین ربات)
# ---------------------------------------------------------------------------
async def cb_admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    api = get_api(context)
    q = update.callback_query
    await q.answer()
    user = update.effective_user
    if not await api.is_admin(str(user.id)):
        await q.edit_message_text("⛔️ این بخش فقط برای ادمین است.", reply_markup=kb([]))
        return
    info = await api.bot_info()
    rows = [
        [InlineKeyboardButton("💰 قیمت‌گذاری پلن‌ها", callback_data="admin_prices")],
        [InlineKeyboardButton("🏦 درگاه‌های پرداخت", callback_data="admin_payments")],
        [InlineKeyboardButton("🧾 رسیدهای در انتظار تایید", callback_data="admin_receipts")],
        [InlineKeyboardButton("👥 مدیریت کاربران", callback_data="admin_customers")],
        [InlineKeyboardButton("📋 درخواست‌های نمایندگی", callback_data="admin_reseller_requests")],
        [InlineKeyboardButton("📡 عضویت اجباری کانال", callback_data="admin_channel")],
        [InlineKeyboardButton("🎁 پاداش رفرال", callback_data="admin_referral_bonus")],
        [InlineKeyboardButton("☎️ تنظیم پشتیبانی", callback_data="admin_support_contact")],
    ]
    if info["has_parent"]:
        rows.append([InlineKeyboardButton("💳 شارژ کیف‌پول من", callback_data="admin_wallet_topup")])
    my_status = await api.my_reseller_status(str(user.id))
    if my_status["can_create"]:
        rows.append([InlineKeyboardButton("🤝 ساخت ربات نمایندگی", callback_data="admin_create_subbot")])
    await q.edit_message_text("پنل مدیریت:", reply_markup=kb(rows))


async def cb_admin_prices(update: Update, context: ContextTypes.DEFAULT_TYPE):
    api = get_api(context)
    q = update.callback_query
    await q.answer()
    user = update.effective_user
    plans = await api.list_all_plans_for_admin(str(user.id))
    rows = []
    for p in plans:
        mark = "✅ فعال" if p["is_offered"] else "❌ غیرفعال"
        rows.append([InlineKeyboardButton(
            f"{p['title']} ({p['cost_price']:,.0f} ← {p['sell_price']:,.0f})", callback_data="noop"
        )])
        rows.append([
            InlineKeyboardButton(mark, callback_data=f"adm_toggleoffer2:{p['plan_id']}"),
            InlineKeyboardButton("✏️ تغییر قیمت", callback_data=f"adm_setprice2:{p['plan_id']}"),
        ])
    await q.edit_message_text(
        "مدیریت پلن‌ها (تمام‌شده ← فروش):",
        reply_markup=InlineKeyboardMarkup(rows + [[InlineKeyboardButton("⬅️ بازگشت", callback_data="admin_menu")]]),
    )


async def cb_noop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()


async def cb_admin_ask_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    plan_id = q.data.split(":", 1)[1]
    context.user_data["flow"] = f"await_set_price:{plan_id}"
    await q.edit_message_text(
        "قیمت فروش جدید را به تومان وارد کنید (فقط عدد؛ عدد ۰ یعنی رایگان):",
        reply_markup=InlineKeyboardMarkup([CANCEL_ROW]),
    )


async def cb_admin_toggle_offer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    api = get_api(context)
    q = update.callback_query
    await q.answer()
    user = update.effective_user
    plan_id = q.data.split(":", 1)[1]
    plans = await api.list_all_plans_for_admin(str(user.id))
    current = next((p for p in plans if p["plan_id"] == plan_id), None)
    if not current:
        await q.edit_message_text("پلن یافت نشد.", reply_markup=kb([]))
        return
    new_state = not current["is_offered"]
    try:
        await api.set_plan_price(plan_id, str(user.id), current["sell_price"], new_state)
    except BackendApiError as e:
        await q.edit_message_text(f"خطا: {e.detail}", reply_markup=kb([]))
        return
    await cb_admin_prices(update, context)


async def cb_admin_payments(update: Update, context: ContextTypes.DEFAULT_TYPE):
    api = get_api(context)
    q = update.callback_query
    await q.answer()
    methods = await api.list_payment_methods()
    rows = []
    for m in methods:
        mark = "✅" if m["enabled"] else "❌"
        rows.append([InlineKeyboardButton(
            f"{mark} {PAYMENT_METHOD_LABELS.get(m['method'], m['method'])}",
            callback_data=f"adm_pm:{m['method']}",
        )])
    await q.edit_message_text(
        "روی یک روش پرداخت بزنید تا روشن/خاموش کنید:",
        reply_markup=InlineKeyboardMarkup(rows + [[InlineKeyboardButton("⬅️ بازگشت", callback_data="admin_menu")]]),
    )


async def cb_admin_toggle_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    api = get_api(context)
    q = update.callback_query
    await q.answer()
    user = update.effective_user
    method = q.data.split(":", 1)[1]
    methods = await api.list_payment_methods()
    current = next((m for m in methods if m["method"] == method), {"enabled": False})
    new_state = not current["enabled"]
    if new_state and method in ("card_to_card", "crypto"):
        context.user_data["flow"] = f"await_payment_details:{method}"
        label = "شماره کارت و نام صاحب حساب" if method == "card_to_card" else "آدرس کیف‌پول"
        await q.edit_message_text(f"{label} را وارد کنید:", reply_markup=InlineKeyboardMarkup([CANCEL_ROW]))
        return
    try:
        await api.set_payment_method(method, str(user.id), new_state)
    except BackendApiError as e:
        await q.edit_message_text(f"خطا: {e.detail}", reply_markup=kb([]))
        return
    await cb_admin_payments(update, context)


async def cb_admin_receipts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    api = get_api(context)
    q = update.callback_query
    await q.answer()
    user = update.effective_user
    receipts = await api.list_pending_receipts(str(user.id))
    if not receipts:
        await q.edit_message_text(
            "رسید در انتظاری نیست.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ بازگشت", callback_data="admin_menu")]]),
        )
        return
    r = receipts[0]
    caption = f"رسید #{r['id']}\n"
    if r.get("order_id"):
        caption += f"برای سفارش #{r['order_id']}"
    elif r.get("wallet_topup_amount"):
        caption += f"شارژ کیف‌پول نمایندگی به مبلغ {r['wallet_topup_amount']:,.0f}"
    elif r.get("customer_topup_amount"):
        caption += f"شارژ کیف‌پول مشتری به مبلغ {r['customer_topup_amount']:,.0f}"
    kb_markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ تایید", callback_data=f"rcpt_ok:{r['id']}"),
            InlineKeyboardButton("❌ رد", callback_data=f"rcpt_no:{r['id']}"),
        ],
        [InlineKeyboardButton("⬅️ بازگشت", callback_data="admin_menu")],
    ])
    await q.message.reply_photo(photo=r["telegram_file_id"], caption=caption, reply_markup=kb_markup)
    await q.delete_message()


async def cb_receipt_review(update: Update, context: ContextTypes.DEFAULT_TYPE):
    api = get_api(context)
    q = update.callback_query
    await q.answer()
    user = update.effective_user
    action, receipt_id_str = q.data.split(":", 1)
    receipt_id = int(receipt_id_str)
    try:
        if action == "rcpt_ok":
            await api.approve_receipt(receipt_id, str(user.id))
            await q.edit_message_caption("✅ تایید شد.")
        else:
            await api.reject_receipt(receipt_id, str(user.id))
            await q.edit_message_caption("❌ رد شد.")
    except BackendApiError as e:
        await q.edit_message_caption(f"خطا: {e.detail}")


async def cb_admin_wallet_topup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    context.user_data["flow"] = "await_wallet_topup_amount"
    await q.edit_message_text("مبلغی که واریز کرده‌اید را به تومان وارد کنید (فقط عدد):", reply_markup=InlineKeyboardMarkup([CANCEL_ROW]))


async def cb_admin_create_subbot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    context.user_data["flow"] = "await_subbot_token"
    await q.edit_message_text(
        "برای ساخت ربات نمایندگی، ابتدا توکن ربات تلگرام خودتان را از @BotFather بگیرید و همین‌جا ارسال کنید:",
        reply_markup=InlineKeyboardMarkup([CANCEL_ROW]),
    )


# ---- مدیریت کاربران ----
async def cb_admin_customers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    api = get_api(context)
    q = update.callback_query
    await q.answer()
    user = update.effective_user
    customers = await api.list_customers(str(user.id))
    if not customers:
        await q.edit_message_text("هنوز کاربری ثبت نشده.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ بازگشت", callback_data="admin_menu")]]))
        return
    rows = []
    for c in customers[:30]:
        label = c.get("name") or c.get("telegram_username") or c["telegram_id"]
        mark = "🚫" if c.get("is_blocked") else "👤"
        rows.append([InlineKeyboardButton(f"{mark} {label}", callback_data=f"adm_cust:{c['id']}")])
    await q.edit_message_text(
        f"کاربران این ربات ({len(customers)} نفر؛ ۳۰ تای اول نمایش داده می‌شود):",
        reply_markup=InlineKeyboardMarkup(rows + [[InlineKeyboardButton("⬅️ بازگشت", callback_data="admin_menu")]]),
    )


async def cb_admin_customer_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    api = get_api(context)
    q = update.callback_query
    await q.answer()
    user = update.effective_user
    customer_id = int(q.data.split(":", 1)[1])
    context.user_data["admin_customer_id"] = customer_id
    customers = await api.list_customers(str(user.id))
    c = next((x for x in customers if x["id"] == customer_id), None)
    if not c:
        await q.edit_message_text("کاربر یافت نشد.", reply_markup=kb([]))
        return
    text = (
        f"نام: {c.get('name') or '—'}\n"
        f"یوزرنیم تلگرام: @{c.get('telegram_username') or '—'}\n"
        f"آیدی تلگرام: {c['telegram_id']}\n"
        f"موجودی کیف‌پول: {c.get('wallet_balance', 0):,.0f}\n"
        f"پلن تست استفاده‌شده: {'بله' if c.get('used_trial') else 'خیر'}\n"
        f"وضعیت: {'مسدود' if c.get('is_blocked') else 'فعال'}"
    )
    rows = [
        [InlineKeyboardButton("💰 افزودن/کسر از کیف‌پول", callback_data="adm_cust_wallet")],
        [InlineKeyboardButton("🚫 مسدود/رفع مسدودی", callback_data="adm_cust_block")],
        [InlineKeyboardButton("⬅️ بازگشت", callback_data="admin_customers")],
    ]
    await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(rows))


async def cb_admin_customer_toggle_block(update: Update, context: ContextTypes.DEFAULT_TYPE):
    api = get_api(context)
    q = update.callback_query
    await q.answer()
    user = update.effective_user
    customer_id = context.user_data.get("admin_customer_id")
    try:
        await api.toggle_block_customer(customer_id, str(user.id))
    except BackendApiError as e:
        await q.edit_message_text(f"خطا: {e.detail}", reply_markup=kb([]))
        return
    await q.edit_message_text("وضعیت به‌روزرسانی شد.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ بازگشت", callback_data="admin_customers")]]))


async def cb_admin_customer_ask_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    context.user_data["flow"] = "await_customer_wallet_adjust"
    await q.edit_message_text(
        "مبلغ را وارد کنید (مثبت = افزودن، منفی = کسر):",
        reply_markup=InlineKeyboardMarkup([CANCEL_ROW]),
    )


# ---- درخواست‌های نمایندگی ----
async def cb_admin_reseller_requests(update: Update, context: ContextTypes.DEFAULT_TYPE):
    api = get_api(context)
    q = update.callback_query
    await q.answer()
    user = update.effective_user
    requests = await api.list_pending_reseller_requests(str(user.id))
    if not requests:
        await q.edit_message_text("درخواست در انتظاری نیست.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ بازگشت", callback_data="admin_menu")]]))
        return
    r = requests[0]
    text = f"درخواست نمایندگی #{r['id']}\nآیدی تلگرام: {r['telegram_id']}"
    rows = [
        [InlineKeyboardButton("✅ تایید", callback_data=f"rr_ok:{r['id']}"), InlineKeyboardButton("❌ رد", callback_data=f"rr_no:{r['id']}")],
        [InlineKeyboardButton("⬅️ بازگشت", callback_data="admin_menu")],
    ]
    await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(rows))


async def cb_reseller_request_review(update: Update, context: ContextTypes.DEFAULT_TYPE):
    api = get_api(context)
    q = update.callback_query
    await q.answer()
    user = update.effective_user
    action, rid_str = q.data.split(":", 1)
    rid = int(rid_str)
    try:
        if action == "rr_ok":
            await api.approve_reseller_request(rid, str(user.id))
            await q.edit_message_text("✅ تایید شد.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ بازگشت", callback_data="admin_menu")]]))
        else:
            await api.reject_reseller_request(rid, str(user.id))
            await q.edit_message_text("❌ رد شد.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ بازگشت", callback_data="admin_menu")]]))
    except BackendApiError as e:
        await q.edit_message_text(f"خطا: {e.detail}", reply_markup=kb([]))


# ---- تنظیمات: کانال اجباری، پاداش رفرال، پشتیبانی ----
async def cb_admin_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    api = get_api(context)
    q = update.callback_query
    await q.answer()
    info = await api.bot_info()
    status = "روشن" if info.get("forced_channel_enabled") else "خاموش"
    text = f"عضویت اجباری در کانال: {status}\nکانال فعلی: {info.get('forced_channel') or '—'}"
    rows = [
        [InlineKeyboardButton("🔁 روشن/خاموش کردن", callback_data="adm_toggle_channel")],
        [InlineKeyboardButton("✏️ تغییر آیدی کانال", callback_data="adm_set_channel")],
        [InlineKeyboardButton("⬅️ بازگشت", callback_data="admin_menu")],
    ]
    await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(rows))


async def cb_admin_toggle_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    api = get_api(context)
    q = update.callback_query
    await q.answer()
    user = update.effective_user
    info = await api.bot_info()
    try:
        await api.update_bot_settings(str(user.id), forced_channel_enabled=not info.get("forced_channel_enabled"))
    except BackendApiError as e:
        await q.edit_message_text(f"خطا: {e.detail}", reply_markup=kb([]))
        return
    await cb_admin_channel(update, context)


async def cb_admin_set_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    context.user_data["flow"] = "await_forced_channel"
    await q.edit_message_text("آیدی کانال را وارد کنید (مثل @mychannel):", reply_markup=InlineKeyboardMarkup([CANCEL_ROW]))


async def cb_admin_referral_bonus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    context.user_data["flow"] = "await_referral_bonus"
    await q.edit_message_text("مبلغ پاداش رفرال به ازای هر خرید موفق را به تومان وارد کنید (۰ یعنی خاموش):", reply_markup=InlineKeyboardMarkup([CANCEL_ROW]))


async def cb_admin_support_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    context.user_data["flow"] = "await_support_contact"
    await q.edit_message_text("متن تماس پشتیبانی را وارد کنید (مثل @AdminSupport):", reply_markup=InlineKeyboardMarkup([CANCEL_ROW]))


# ---------------------------------------------------------------------------
# ورودی‌های متنی (ادامه‌ی گفتگوهای چندمرحله‌ای بر اساس context.user_data['flow'])
# ---------------------------------------------------------------------------
async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    flow = context.user_data.get("flow")
    if not flow:
        return
    api = get_api(context)
    user = update.effective_user
    text = update.message.text.strip()

    def is_number(s: str) -> bool:
        try:
            float(s)
            return True
        except ValueError:
            return False

    if flow.startswith("await_set_price:"):
        plan_id = flow.split(":", 1)[1]
        if not is_number(text):
            await update.message.reply_text("لطفاً فقط عدد وارد کنید.")
            return
        try:
            await api.set_plan_price(plan_id, str(user.id), float(text), True)
        except BackendApiError as e:
            await update.message.reply_text(f"خطا: {e.detail}")
            return
        context.user_data["flow"] = None
        await update.message.reply_text("قیمت به‌روزرسانی شد و پلن عرضه شد.", reply_markup=kb([]))
        return

    if flow.startswith("await_payment_details:"):
        method = flow.split(":", 1)[1]
        try:
            await api.set_payment_method(method, str(user.id), True, text)
        except BackendApiError as e:
            await update.message.reply_text(f"خطا: {e.detail}")
            return
        context.user_data["flow"] = None
        await update.message.reply_text("روش پرداخت روشن شد.", reply_markup=kb([]))
        return

    if flow == "await_wallet_topup_amount":
        if not is_number(text):
            await update.message.reply_text("لطفاً فقط عدد وارد کنید.")
            return
        context.user_data["flow"] = f"await_wallet_topup_photo:{text}"
        await update.message.reply_text("حالا عکس رسید واریزی را ارسال کنید.")
        return

    if flow == "await_customer_topup_amount":
        if not is_number(text):
            await update.message.reply_text("لطفاً فقط عدد وارد کنید.")
            return
        context.user_data["flow"] = f"await_customer_topup_photo:{text}"
        await update.message.reply_text("حالا عکس رسید واریزی را ارسال کنید.")
        return

    if flow == "await_customer_wallet_adjust":
        if not is_number(text):
            await update.message.reply_text("لطفاً فقط عدد وارد کنید.")
            return
        customer_id = context.user_data.get("admin_customer_id")
        try:
            await api.adjust_customer_wallet(customer_id, str(user.id), float(text))
        except BackendApiError as e:
            await update.message.reply_text(f"خطا: {e.detail}")
            return
        context.user_data["flow"] = None
        await update.message.reply_text("کیف‌پول به‌روزرسانی شد.", reply_markup=kb([]))
        return

    if flow == "await_forced_channel":
        try:
            await api.update_bot_settings(str(user.id), forced_channel=text)
        except BackendApiError as e:
            await update.message.reply_text(f"خطا: {e.detail}")
            return
        context.user_data["flow"] = None
        await update.message.reply_text(
            "ذخیره شد. توجه: ربات باید ادمین همان کانال باشد تا بتواند عضویت را بررسی کند.",
            reply_markup=kb([]),
        )
        return

    if flow == "await_referral_bonus":
        if not is_number(text):
            await update.message.reply_text("لطفاً فقط عدد وارد کنید.")
            return
        try:
            await api.update_bot_settings(str(user.id), referral_bonus_amount=float(text))
        except BackendApiError as e:
            await update.message.reply_text(f"خطا: {e.detail}")
            return
        context.user_data["flow"] = None
        await update.message.reply_text("ذخیره شد.", reply_markup=kb([]))
        return

    if flow == "await_support_contact":
        try:
            await api.update_bot_settings(str(user.id), support_contact=text)
        except BackendApiError as e:
            await update.message.reply_text(f"خطا: {e.detail}")
            return
        context.user_data["flow"] = None
        await update.message.reply_text("ذخیره شد.", reply_markup=kb([]))
        return

    if flow == "await_subbot_token":
        context.user_data["subbot_token"] = text
        context.user_data["flow"] = "await_subbot_admin"
        await update.message.reply_text("آیدی عددی تلگرام ادمین ربات جدید را وارد کنید:")
        return

    if flow == "await_subbot_admin":
        context.user_data["subbot_admin"] = text
        context.user_data["flow"] = "await_subbot_label"
        await update.message.reply_text("یک نام برای این ربات نمایندگی بنویسید:")
        return

    if flow == "await_subbot_label":
        token = context.user_data.get("subbot_token")
        admin_id = context.user_data.get("subbot_admin")
        try:
            result = await api.create_sub_bot(str(user.id), token, admin_id, text)
        except BackendApiError as e:
            await update.message.reply_text(f"خطا: {e.detail}")
            context.user_data["flow"] = None
            return
        context.user_data["flow"] = None
        await update.message.reply_text(
            "ربات نمایندگی ساخته شد ✅\n"
            f"API Key (در صورت نیاز به اجرای ربات روی سرور شخصی خودتان، این را نگه دارید):\n"
            f"`{result['api_key']}`",
            parse_mode="Markdown",
            reply_markup=kb([]),
        )
        return

    if flow == "support_chat":
        info = await api.bot_info()
        caption = f"🆘 پیام پشتیبانی از {user.full_name} (ID: {user.id}):\n\n{text}"
        for admin_id in info["admin_ids"]:
            try:
                await context.bot.send_message(chat_id=int(admin_id), text=caption)
            except (TelegramError, ValueError):
                pass
        await update.message.reply_text(
            f"ارسال شد. منتظر پاسخ ادمین باشید.\n(ادمین با دستور /reply {user.id} ... پاسخ می‌دهد)",
            reply_markup=kb([]),
        )
        return


def build_application(telegram_token: str, api_key: str, backend_url: str) -> Application:
    application = Application.builder().token(telegram_token).build()
    application.bot_data["api"] = BackendApiClient(backend_url, api_key)

    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("reply", cmd_reply))

    application.add_handler(CallbackQueryHandler(cb_back_to_menu, pattern="^menu$"))
    application.add_handler(CallbackQueryHandler(cb_cancel_flow, pattern="^cancel_flow$"))
    application.add_handler(CallbackQueryHandler(cb_check_join, pattern="^check_join$"))

    application.add_handler(CallbackQueryHandler(cb_trial, pattern="^trial$"))
    application.add_handler(CallbackQueryHandler(cb_trial_confirm, pattern="^trial_confirm$"))
    application.add_handler(CallbackQueryHandler(cb_buy, pattern="^buy$"))
    application.add_handler(CallbackQueryHandler(cb_select_plan, pattern="^plan:"))
    application.add_handler(CallbackQueryHandler(cb_select_payment, pattern="^pay:"))
    application.add_handler(CallbackQueryHandler(cb_my_services, pattern="^myservices$"))
    application.add_handler(CallbackQueryHandler(cb_wallet, pattern="^wallet$"))
    application.add_handler(CallbackQueryHandler(cb_wallet_topup, pattern="^wallet_topup$"))
    application.add_handler(CallbackQueryHandler(cb_referral, pattern="^referral$"))
    application.add_handler(CallbackQueryHandler(cb_reseller_menu, pattern="^reseller_menu$"))
    application.add_handler(CallbackQueryHandler(cb_reseller_request, pattern="^reseller_request$"))
    application.add_handler(CallbackQueryHandler(cb_support, pattern="^support$"))

    application.add_handler(CallbackQueryHandler(cb_admin_menu, pattern="^admin_menu$"))
    application.add_handler(CallbackQueryHandler(cb_admin_prices, pattern="^admin_prices$"))
    application.add_handler(CallbackQueryHandler(cb_noop, pattern="^noop$"))
    application.add_handler(CallbackQueryHandler(cb_admin_ask_price, pattern="^adm_setprice2:"))
    application.add_handler(CallbackQueryHandler(cb_admin_toggle_offer, pattern="^adm_toggleoffer2:"))
    application.add_handler(CallbackQueryHandler(cb_admin_payments, pattern="^admin_payments$"))
    application.add_handler(CallbackQueryHandler(cb_admin_toggle_payment, pattern="^adm_pm:"))
    application.add_handler(CallbackQueryHandler(cb_admin_receipts, pattern="^admin_receipts$"))
    application.add_handler(CallbackQueryHandler(cb_receipt_review, pattern="^rcpt_(ok|no):"))
    application.add_handler(CallbackQueryHandler(cb_admin_wallet_topup, pattern="^admin_wallet_topup$"))
    application.add_handler(CallbackQueryHandler(cb_admin_create_subbot, pattern="^admin_create_subbot$"))

    application.add_handler(CallbackQueryHandler(cb_admin_customers, pattern="^admin_customers$"))
    application.add_handler(CallbackQueryHandler(cb_admin_customer_detail, pattern="^adm_cust:"))
    application.add_handler(CallbackQueryHandler(cb_admin_customer_toggle_block, pattern="^adm_cust_block$"))
    application.add_handler(CallbackQueryHandler(cb_admin_customer_ask_wallet, pattern="^adm_cust_wallet$"))

    application.add_handler(CallbackQueryHandler(cb_admin_reseller_requests, pattern="^admin_reseller_requests$"))
    application.add_handler(CallbackQueryHandler(cb_reseller_request_review, pattern="^rr_(ok|no):"))

    application.add_handler(CallbackQueryHandler(cb_admin_channel, pattern="^admin_channel$"))
    application.add_handler(CallbackQueryHandler(cb_admin_toggle_channel, pattern="^adm_toggle_channel$"))
    application.add_handler(CallbackQueryHandler(cb_admin_set_channel, pattern="^adm_set_channel$"))
    application.add_handler(CallbackQueryHandler(cb_admin_referral_bonus, pattern="^admin_referral_bonus$"))
    application.add_handler(CallbackQueryHandler(cb_admin_support_contact, pattern="^admin_support_contact$"))

    application.add_handler(MessageHandler(filters.PHOTO, on_photo))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    return application


async def start_polling_in_background(application: Application) -> None:
    """برای اجرای این ربات داخل یک event loop دیگر (مثل uvicorn/FastAPI)."""
    await application.initialize()
    await application.start()
    await application.updater.start_polling()


async def stop_application(application: Application) -> None:
    if application.updater and application.updater.running:
        await application.updater.stop()
    await application.stop()
    await application.shutdown()
