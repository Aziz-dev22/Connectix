from pathlib import Path

from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from backend.database import get_session
from backend.models import (
    Bot, Order, OrderStatus, PlanPricing, Plan, WalletTransaction, PaymentMethodSetting,
    Customer, ResellerRequest, ResellerRequestStatus, CustomerWalletTxType, TIER_LABELS_FA,
    ScheduledBroadcast, ScheduledBroadcastStatus, utcnow,
)
from backend.services.broadcast import send_broadcast
from backend.admin.auth import (
    authenticate, is_logged_in, has_role, hash_password,
    FINANCE_ROLES, BOT_MANAGE_ROLES, USER_MANAGE_ROLES, OPS_ROLES,
)
from backend.models import AdminUser, AdminRole, AdminActionLog
from backend.services import pricing as pricing_service
from backend.services import hierarchy as hierarchy_service
from backend.services import stats as stats_service
from backend.services.wallet import topup, customer_topup
from backend.services.audit import log_action
from backend.connectix_client import ConnectixClient, ConnectixError

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def _guard(request: Request):
    if not is_logged_in(request):
        return RedirectResponse("/admin/login", status_code=302)
    return None


def _guard_role(request: Request, *allowed_roles: str):
    """مثل _guard ولی علاوه بر لاگین، نقش را هم چک می‌کند. برای اکشن‌های حساس
    (مالی/ساخت ربات/مدیریت کاربران) استفاده شود؛ اگر نقش کافی نباشد، به داشبورد
    با یک پیام ساده برمی‌گرداند (403)."""
    guard = _guard(request)
    if guard:
        return guard
    if not has_role(request, *allowed_roles):
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="نقش شما اجازه‌ی این عملیات را نمی‌دهد.")
    return None


def _actor(request: Request) -> str:
    return f"web:{request.session.get('admin_username', '?')}"


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@router.post("/login")
def login_submit(request: Request, session: Session = Depends(get_session), username: str = Form(...), password: str = Form(...)):
    user = authenticate(session, username, password)
    if user:
        request.session["admin_user_id"] = user.id
        request.session["admin_username"] = user.username
        request.session["admin_role"] = user.role
        log_action(session, f"web:{user.username}", "login")
        return RedirectResponse("/admin", status_code=302)
    return templates.TemplateResponse(
        "login.html", {"request": request, "error": "نام کاربری یا رمز عبور اشتباه است."}
    )


@router.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/admin/login", status_code=302)


def _build_tree(bots: list[Bot]) -> list[dict]:
    by_parent: dict[int, list[Bot]] = {}
    for b in bots:
        by_parent.setdefault(b.parent_bot_id, []).append(b)

    def node(bot: Bot, depth: int) -> list[dict]:
        rows = [{"bot": bot, "depth": depth}]
        for child in by_parent.get(bot.id, []):
            rows.extend(node(child, depth + 1))
        return rows

    roots = by_parent.get(None, [])
    result = []
    for r in roots:
        result.extend(node(r, 0))
    return result


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, session: Session = Depends(get_session)):
    guard = _guard(request)
    if guard:
        return guard
    bots = session.exec(select(Bot)).all()
    tree = _build_tree(bots)
    total_orders = len(session.exec(select(Order)).all())
    s = stats_service.get_dashboard_stats(session)
    bot_labels = {b.id: b.label for b in bots}
    return templates.TemplateResponse(
        "dashboard.html",
        {"request": request, "tree": tree, "total_orders": total_orders, "stats": s, "bot_labels": bot_labels},
    )


@router.get("/orders", response_class=HTMLResponse)
def orders_page(
    request: Request, session: Session = Depends(get_session),
    bot_id: str = "", status: str = "", q: str = "",
):
    guard = _guard(request)
    if guard:
        return guard
    stmt = select(Order)
    if bot_id:
        stmt = stmt.where(Order.bot_id == int(bot_id))
    if status:
        stmt = stmt.where(Order.status == status)
    orders = session.exec(stmt.order_by(Order.created_at.desc()).limit(300)).all()
    if q:
        ql = q.strip().lower()
        orders = [o for o in orders if ql in str(o.id) or ql in (o.plan_id or "").lower()]
    bots = session.exec(select(Bot)).all()
    bot_labels = {b.id: b.label for b in bots}
    return templates.TemplateResponse(
        "orders.html",
        {
            "request": request, "orders": orders, "bots": bots, "bot_labels": bot_labels,
            "filter_bot_id": bot_id, "filter_status": status, "filter_q": q,
            "statuses": list(OrderStatus),
        },
    )


@router.get("/orders/export.csv")
def export_orders_csv(request: Request, session: Session = Depends(get_session)):
    guard = _guard(request)
    if guard:
        return guard
    import csv
    import io
    from fastapi.responses import StreamingResponse

    orders = session.exec(select(Order).order_by(Order.created_at.desc())).all()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["id", "bot_id", "customer_id", "plan_id", "cost_price", "sell_price", "status", "created_at", "fulfilled_at"])
    for o in orders:
        writer.writerow([o.id, o.bot_id, o.customer_id, o.plan_id, o.cost_price, o.sell_price, o.status, o.created_at, o.fulfilled_at or ""])
    buf.seek(0)
    return StreamingResponse(
        buf, media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=orders.csv"},
    )


@router.get("/orders/export.xlsx")
def export_orders_xlsx(request: Request, session: Session = Depends(get_session)):
    guard = _guard(request)
    if guard:
        return guard
    import io
    from fastapi.responses import StreamingResponse
    from openpyxl import Workbook
    from openpyxl.styles import Font

    orders = session.exec(select(Order).order_by(Order.created_at.desc())).all()
    bot_labels = {b.id: b.label for b in session.exec(select(Bot)).all()}

    wb = Workbook()
    ws = wb.active
    ws.title = "Orders"
    headers = ["شناسه", "ربات", "شناسه مشتری", "پلن", "قیمت تمام‌شده", "قیمت فروش", "سود", "وضعیت", "تاریخ ثبت", "تاریخ تحویل"]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for o in orders:
        ws.append([
            o.id, bot_labels.get(o.bot_id, o.bot_id), o.customer_id, o.plan_id,
            o.cost_price, o.sell_price, o.sell_price - o.cost_price, o.status.value if hasattr(o.status, "value") else o.status,
            o.created_at.strftime("%Y-%m-%d %H:%M") if o.created_at else "",
            o.fulfilled_at.strftime("%Y-%m-%d %H:%M") if o.fulfilled_at else "",
        ])
    for i, width in enumerate([8, 18, 12, 24, 12, 12, 10, 14, 16, 16], start=1):
        ws.column_dimensions[chr(64 + i)].width = width

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return StreamingResponse(
        buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=orders.xlsx"},
    )


@router.get("/suppliers", response_class=HTMLResponse)
async def suppliers_page(request: Request, session: Session = Depends(get_session)):
    guard = _guard(request)
    if guard:
        return guard
    status_ok, message, plan_count = True, "متصل", 0
    try:
        client = ConnectixClient()
        plans = await client.get_enabled_plans()
        plan_count = len(plans)
    except ConnectixError as e:
        status_ok, message = False, f"خطا {e.status_code}: {e.message}"
    except Exception as e:  # noqa: BLE001
        status_ok, message = False, str(e)
    last_sync = None
    latest_plan = session.exec(select(Plan).order_by(Plan.last_synced_at.desc())).first()
    if latest_plan:
        last_sync = latest_plan.last_synced_at
    return templates.TemplateResponse(
        "suppliers.html",
        {"request": request, "status_ok": status_ok, "message": message, "plan_count": plan_count, "last_sync": last_sync},
    )


@router.get("/users", response_class=HTMLResponse)
def users_page(request: Request, session: Session = Depends(get_session)):
    guard = _guard_role(request, *USER_MANAGE_ROLES)
    if guard:
        return guard
    users = session.exec(select(AdminUser)).all()
    return templates.TemplateResponse(
        "users.html", {"request": request, "users": users, "roles": list(AdminRole)}
    )


@router.post("/users/new")
def create_admin_user(
    request: Request, session: Session = Depends(get_session),
    username: str = Form(...), password: str = Form(...), role: str = Form(...),
):
    guard = _guard_role(request, *USER_MANAGE_ROLES)
    if guard:
        return guard
    existing = session.exec(select(AdminUser).where(AdminUser.username == username)).first()
    if not existing:
        user = AdminUser(username=username, password_hash=hash_password(password), role=role)
        session.add(user)
        session.commit()
        log_action(session, _actor(request), "create_admin_user", detail=f"username={username} role={role}")
    return RedirectResponse("/admin/users", status_code=302)


@router.post("/users/{user_id}/toggle-active")
def toggle_admin_user_active(user_id: int, request: Request, session: Session = Depends(get_session)):
    guard = _guard_role(request, *USER_MANAGE_ROLES)
    if guard:
        return guard
    user = session.get(AdminUser, user_id)
    if user and user.id != request.session.get("admin_user_id"):  # نمی‌تواند خودش را غیرفعال کند
        user.is_active = not user.is_active
        session.add(user)
        session.commit()
        log_action(session, _actor(request), "toggle_admin_user_active", detail=f"user_id={user_id} new_value={user.is_active}")
    return RedirectResponse("/admin/users", status_code=302)


@router.get("/audit-log", response_class=HTMLResponse)
def audit_log_page(
    request: Request, session: Session = Depends(get_session),
    bot_id: str = "", action: str = "",
):
    guard = _guard_role(request, *OPS_ROLES)
    if guard:
        return guard
    stmt = select(AdminActionLog)
    if bot_id:
        stmt = stmt.where(AdminActionLog.bot_id == int(bot_id))
    if action:
        stmt = stmt.where(AdminActionLog.action == action)
    logs = session.exec(stmt.order_by(AdminActionLog.created_at.desc()).limit(300)).all()
    bots = session.exec(select(Bot)).all()
    bot_labels = {b.id: b.label for b in bots}
    distinct_actions = sorted({row[0] for row in session.exec(select(AdminActionLog.action)).all()})
    return templates.TemplateResponse(
        "audit_log.html",
        {
            "request": request, "logs": logs, "bots": bots, "bot_labels": bot_labels,
            "filter_bot_id": bot_id, "filter_action": action, "distinct_actions": distinct_actions,
        },
    )


@router.get("/broadcast", response_class=HTMLResponse)
def broadcast_page(request: Request, session: Session = Depends(get_session)):
    guard = _guard(request)
    if guard:
        return guard
    bots = session.exec(select(Bot).where(Bot.is_active == True)).all()  # noqa: E712
    pending = session.exec(
        select(ScheduledBroadcast)
        .where(ScheduledBroadcast.status == ScheduledBroadcastStatus.PENDING)
        .order_by(ScheduledBroadcast.send_at)
    ).all()
    bot_labels = {b.id: b.label for b in session.exec(select(Bot)).all()}
    return templates.TemplateResponse(
        "broadcast.html",
        {"request": request, "bots": bots, "result": None, "pending": pending, "bot_labels": bot_labels},
    )


@router.post("/broadcast", response_class=HTMLResponse)
async def broadcast_send(
    request: Request, session: Session = Depends(get_session),
    bot_id: int = Form(...), message: str = Form(...), send_at: str = Form(""),
):
    guard = _guard(request)
    if guard:
        return guard
    bot = session.get(Bot, bot_id)
    result = None

    scheduled_dt = None
    if send_at.strip():
        from datetime import datetime, timezone
        try:
            scheduled_dt = datetime.fromisoformat(send_at).replace(tzinfo=timezone.utc)
        except ValueError:
            scheduled_dt = None

    if bot and scheduled_dt and scheduled_dt > utcnow():
        sb = ScheduledBroadcast(
            bot_id=bot_id, message=message, send_at=scheduled_dt, created_by=_actor(request)
        )
        session.add(sb)
        log_action(session, _actor(request), "broadcast_scheduled", bot_id=bot_id, detail=f"send_at={scheduled_dt}")
        result = {"scheduled": True, "send_at": scheduled_dt}
    elif bot:
        result = await send_broadcast(session, bot, message)
        log_action(
            session, _actor(request), "broadcast_sent", bot_id=bot_id,
            detail=f"sent={result['sent']} failed={result['failed']} total={result['total']}",
        )

    bots = session.exec(select(Bot).where(Bot.is_active == True)).all()  # noqa: E712
    pending = session.exec(
        select(ScheduledBroadcast)
        .where(ScheduledBroadcast.status == ScheduledBroadcastStatus.PENDING)
        .order_by(ScheduledBroadcast.send_at)
    ).all()
    bot_labels = {b.id: b.label for b in session.exec(select(Bot)).all()}
    return templates.TemplateResponse(
        "broadcast.html",
        {"request": request, "bots": bots, "result": result, "pending": pending, "bot_labels": bot_labels},
    )


@router.post("/broadcast/scheduled/{scheduled_id}/cancel")
def cancel_scheduled_broadcast(scheduled_id: int, request: Request, session: Session = Depends(get_session)):
    guard = _guard(request)
    if guard:
        return guard
    sb = session.get(ScheduledBroadcast, scheduled_id)
    if sb and sb.status == ScheduledBroadcastStatus.PENDING:
        sb.status = ScheduledBroadcastStatus.CANCELLED
        session.add(sb)
        log_action(session, _actor(request), "broadcast_cancelled", bot_id=sb.bot_id, detail=f"scheduled_id={scheduled_id}")
        session.commit()
    return RedirectResponse("/admin/broadcast", status_code=302)


@router.post("/bots/new")
async def create_bot(
    request: Request, session: Session = Depends(get_session),
    telegram_token: str = Form(...), telegram_admin_id: str = Form(...),
    label: str = Form(""), parent_bot_id: str = Form(""),
):
    guard = _guard_role(request, *BOT_MANAGE_ROLES)
    if guard:
        return guard
    parent = None
    if parent_bot_id:
        parent = session.get(Bot, int(parent_bot_id))
        if parent and parent.max_sub_bots is not None:
            existing_children = session.exec(select(Bot).where(Bot.parent_bot_id == parent.id)).all()
            if len(existing_children) >= parent.max_sub_bots:
                from fastapi import HTTPException
                raise HTTPException(422, "این نماینده به سقف تعداد زیرنماینده‌ی مجازش رسیده است.")
    bot = hierarchy_service.create_bot(
        session, telegram_token, telegram_admin_id, label, parent_bot=parent
    )
    log_action(session, _actor(request), "bot_create", bot_id=bot.id, detail=f"label={label}")
    from backend.main import launch_bot_runtime
    await launch_bot_runtime(bot)
    return RedirectResponse(f"/admin/bots/{bot.id}", status_code=302)


@router.get("/bots/{bot_id}", response_class=HTMLResponse)
def bot_detail(bot_id: int, request: Request, session: Session = Depends(get_session)):
    guard = _guard(request)
    if guard:
        return guard
    bot = session.get(Bot, bot_id)
    pricing_rows = session.exec(
        select(PlanPricing, Plan).where(PlanPricing.bot_id == bot_id, PlanPricing.plan_id == Plan.id)
    ).all()
    orders = session.exec(
        select(Order).where(Order.bot_id == bot_id).order_by(Order.created_at.desc()).limit(50)
    ).all()
    wallet_txs = session.exec(
        select(WalletTransaction).where(WalletTransaction.bot_id == bot_id).order_by(WalletTransaction.created_at.desc()).limit(50)
    ).all()
    children = session.exec(select(Bot).where(Bot.parent_bot_id == bot_id)).all()
    all_bots = session.exec(select(Bot)).all()
    payment_methods = session.exec(
        select(PaymentMethodSetting).where(PaymentMethodSetting.bot_id == bot_id)
    ).all()
    parent = session.get(Bot, bot.parent_bot_id) if bot.parent_bot_id else None
    customers = session.exec(select(Customer).where(Customer.bot_id == bot_id)).all()
    pending_requests = session.exec(
        select(ResellerRequest).where(
            ResellerRequest.bot_id == bot_id, ResellerRequest.status == ResellerRequestStatus.PENDING
        )
    ).all()
    return templates.TemplateResponse(
        "bot_detail.html",
        {
            "request": request, "bot": bot, "pricing_rows": pricing_rows,
            "orders": orders, "wallet_txs": wallet_txs, "children": children,
            "all_bots": all_bots, "payment_methods": payment_methods, "parent": parent,
            "customers": customers, "pending_requests": pending_requests,
            "tier_labels": TIER_LABELS_FA,
        },
    )


@router.post("/bots/{bot_id}/toggle-sub-reseller")
def toggle_sub_reseller(bot_id: int, request: Request, session: Session = Depends(get_session)):
    guard = _guard_role(request, *BOT_MANAGE_ROLES)
    if guard:
        return guard
    bot = session.get(Bot, bot_id)
    bot.can_create_sub_reseller = not bot.can_create_sub_reseller
    session.add(bot)
    session.commit()
    log_action(session, _actor(request), "toggle_sub_reseller_permission", bot_id=bot_id, detail=f"new_value={bot.can_create_sub_reseller}")
    return RedirectResponse(f"/admin/bots/{bot_id}", status_code=302)


@router.post("/bots/{bot_id}/toggle-active")
async def toggle_active(bot_id: int, request: Request, session: Session = Depends(get_session)):
    guard = _guard_role(request, *BOT_MANAGE_ROLES)
    if guard:
        return guard
    bot = session.get(Bot, bot_id)
    bot.is_active = not bot.is_active
    session.add(bot)
    session.commit()
    session.refresh(bot)
    log_action(session, _actor(request), "toggle_bot_active", bot_id=bot_id, detail=f"new_value={bot.is_active}")
    from backend.main import launch_bot_runtime, stop_bot_runtime
    if bot.is_active:
        await launch_bot_runtime(bot)
    else:
        await stop_bot_runtime(bot.id)
    return RedirectResponse(f"/admin/bots/{bot_id}", status_code=302)


@router.post("/bots/{bot_id}/wallet/adjust")
def wallet_adjust(
    bot_id: int, request: Request, session: Session = Depends(get_session),
    amount: float = Form(...), note: str = Form(""),
):
    guard = _guard_role(request, *FINANCE_ROLES)
    if guard:
        return guard
    bot = session.get(Bot, bot_id)
    topup(session, bot, amount, note=note or "اصلاح دستی توسط ادمین اصلی")
    log_action(session, _actor(request), "bot_wallet_adjust", bot_id=bot_id, detail=f"amount={amount} note={note}")
    return RedirectResponse(f"/admin/bots/{bot_id}", status_code=302)


@router.post("/sync-plans")
async def sync_plans(request: Request, session: Session = Depends(get_session)):
    guard = _guard_role(request, *BOT_MANAGE_ROLES)
    if guard:
        return guard
    await pricing_service.sync_plans_from_connectix(session)
    bots = session.exec(select(Bot)).all()
    for bot in bots:
        pricing_service.ensure_plan_pricing_rows(session, bot)
    log_action(session, _actor(request), "sync_plans")
    return RedirectResponse("/admin", status_code=302)


@router.post("/bots/{bot_id}/settings")
def update_bot_settings_web(
    bot_id: int, request: Request, session: Session = Depends(get_session),
    forced_channel: str = Form(""), forced_channel_enabled: str = Form(""),
    referral_bonus_amount: float = Form(0), support_contact: str = Form(""),
    tier: str = Form("normal"), tier_discount_percent: float = Form(0),
    credit_limit: float = Form(0), max_sub_bots: str = Form(""),
):
    guard = _guard_role(request, *BOT_MANAGE_ROLES)
    if guard:
        return guard
    bot = session.get(Bot, bot_id)
    bot.forced_channel = forced_channel
    bot.forced_channel_enabled = bool(forced_channel_enabled)
    bot.referral_bonus_amount = referral_bonus_amount
    bot.support_contact = support_contact
    bot.tier = tier
    bot.tier_discount_percent = max(0, min(100, tier_discount_percent))
    bot.credit_limit = max(0, credit_limit)
    bot.max_sub_bots = int(max_sub_bots) if max_sub_bots.strip().isdigit() else None
    session.add(bot)
    session.commit()
    session.refresh(bot)
    pricing_service.refresh_cost_prices(session, bot)
    log_action(
        session, _actor(request), "update_bot_settings", bot_id=bot_id,
        detail=f"tier={tier} discount={bot.tier_discount_percent} credit_limit={bot.credit_limit} max_sub_bots={bot.max_sub_bots}",
    )
    return RedirectResponse(f"/admin/bots/{bot_id}", status_code=302)


@router.post("/bots/{bot_id}/customers/{customer_id}/toggle-block")
def toggle_block_customer_web(bot_id: int, customer_id: int, request: Request, session: Session = Depends(get_session)):
    guard = _guard_role(request, *OPS_ROLES)
    if guard:
        return guard
    customer = session.get(Customer, customer_id)
    if customer and customer.bot_id == bot_id:
        customer.is_blocked = not customer.is_blocked
        session.add(customer)
        session.commit()
        log_action(session, _actor(request), "toggle_customer_block", bot_id=bot_id, detail=f"customer_id={customer_id} new_value={customer.is_blocked}")
    return RedirectResponse(f"/admin/bots/{bot_id}", status_code=302)


@router.post("/bots/{bot_id}/customers/{customer_id}/wallet-adjust")
def adjust_customer_wallet_web(
    bot_id: int, customer_id: int, request: Request, session: Session = Depends(get_session),
    amount: float = Form(...), note: str = Form(""),
):
    guard = _guard_role(request, *FINANCE_ROLES)
    if guard:
        return guard
    customer = session.get(Customer, customer_id)
    if customer and customer.bot_id == bot_id:
        customer_topup(session, customer, amount, tx_type=CustomerWalletTxType.ADJUSTMENT, note=note or "اصلاح دستی ادمین اصلی")
        log_action(session, _actor(request), "customer_wallet_adjust", bot_id=bot_id, detail=f"customer_id={customer_id} amount={amount} note={note}")
    return RedirectResponse(f"/admin/bots/{bot_id}", status_code=302)


@router.post("/bots/{bot_id}/reseller-requests/{request_id}/approve")
def approve_reseller_request_web(bot_id: int, request_id: int, request: Request, session: Session = Depends(get_session)):
    guard = _guard_role(request, *BOT_MANAGE_ROLES)
    if guard:
        return guard
    from backend.models import utcnow
    req = session.get(ResellerRequest, request_id)
    if req and req.bot_id == bot_id:
        req.status = ResellerRequestStatus.APPROVED
        req.reviewed_at = utcnow()
        session.add(req)
        session.commit()
        log_action(session, _actor(request), "approve_reseller_request", bot_id=bot_id, detail=f"request_id={request_id}")
    return RedirectResponse(f"/admin/bots/{bot_id}", status_code=302)


@router.post("/bots/{bot_id}/reseller-requests/{request_id}/reject")
def reject_reseller_request_web(bot_id: int, request_id: int, request: Request, session: Session = Depends(get_session)):
    guard = _guard_role(request, *BOT_MANAGE_ROLES)
    if guard:
        return guard
    req = session.get(ResellerRequest, request_id)
    if req and req.bot_id == bot_id:
        req.status = ResellerRequestStatus.REJECTED
        session.add(req)
        session.commit()
        log_action(session, _actor(request), "reject_reseller_request", bot_id=bot_id, detail=f"request_id={request_id}")
    return RedirectResponse(f"/admin/bots/{bot_id}", status_code=302)


@router.post("/bots/{bot_id}/plans/{plan_id}/toggle")
def toggle_plan_offered_web(bot_id: int, plan_id: str, request: Request, session: Session = Depends(get_session)):
    guard = _guard_role(request, *FINANCE_ROLES)
    if guard:
        return guard
    pp = session.exec(
        select(PlanPricing).where(PlanPricing.bot_id == bot_id, PlanPricing.plan_id == plan_id)
    ).first()
    if pp:
        pp.is_offered = not pp.is_offered
        session.add(pp)
        session.commit()
        log_action(session, _actor(request), "toggle_plan_offered", bot_id=bot_id, detail=f"plan_id={plan_id} new_value={pp.is_offered}")
    return RedirectResponse(f"/admin/bots/{bot_id}", status_code=302)


@router.post("/bots/{bot_id}/plans/{plan_id}/price")
def set_plan_price_web(
    bot_id: int, plan_id: str, request: Request, session: Session = Depends(get_session),
    sell_price: float = Form(...),
):
    guard = _guard_role(request, *FINANCE_ROLES)
    if guard:
        return guard
    pp = session.exec(
        select(PlanPricing).where(PlanPricing.bot_id == bot_id, PlanPricing.plan_id == plan_id)
    ).first()
    if pp and (sell_price == 0 or sell_price >= pp.cost_price):
        pp.sell_price = sell_price
        pp.is_offered = True
        session.add(pp)
        session.commit()
        children = session.exec(select(Bot).where(Bot.parent_bot_id == bot_id)).all()
        for child in children:
            pricing_service.refresh_cost_prices(session, child)
        log_action(session, _actor(request), "set_plan_price", bot_id=bot_id, detail=f"plan_id={plan_id} sell_price={sell_price}")
    return RedirectResponse(f"/admin/bots/{bot_id}", status_code=302)


@router.post("/bots/{bot_id}/payment-methods/{method}/toggle")
def toggle_payment_method_web(bot_id: int, method: str, request: Request, session: Session = Depends(get_session)):
    guard = _guard(request)
    if guard:
        return guard
    pm = session.exec(
        select(PaymentMethodSetting).where(PaymentMethodSetting.bot_id == bot_id, PaymentMethodSetting.method == method)
    ).first()
    if pm:
        pm.enabled = not pm.enabled
        session.add(pm)
        session.commit()
    return RedirectResponse(f"/admin/bots/{bot_id}", status_code=302)


@router.post("/bots/{bot_id}/payment-methods/{method}/details")
def set_payment_method_details_web(
    bot_id: int, method: str, request: Request, session: Session = Depends(get_session),
    details: str = Form(""),
):
    guard = _guard(request)
    if guard:
        return guard
    pm = session.exec(
        select(PaymentMethodSetting).where(PaymentMethodSetting.bot_id == bot_id, PaymentMethodSetting.method == method)
    ).first()
    if pm:
        pm.details = details
        session.add(pm)
        session.commit()
    return RedirectResponse(f"/admin/bots/{bot_id}", status_code=302)
