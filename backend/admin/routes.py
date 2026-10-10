from pathlib import Path

from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from backend.database import get_session
from backend.models import (
    Bot, Order, PlanPricing, Plan, WalletTransaction, PaymentMethodSetting,
    Customer, ResellerRequest, ResellerRequestStatus, CustomerWalletTxType,
)
from backend.admin.auth import check_credentials, is_logged_in
from backend.services import pricing as pricing_service
from backend.services import hierarchy as hierarchy_service
from backend.services.wallet import topup, customer_topup

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def _guard(request: Request):
    if not is_logged_in(request):
        return RedirectResponse("/admin/login", status_code=302)
    return None


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@router.post("/login")
def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    if check_credentials(username, password):
        request.session["logged_in"] = True
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
    total_bots = len(bots)
    return templates.TemplateResponse(
        "dashboard.html",
        {"request": request, "tree": tree, "total_orders": total_orders, "total_bots": total_bots},
    )


@router.post("/bots/new")
async def create_bot(
    request: Request, session: Session = Depends(get_session),
    telegram_token: str = Form(...), telegram_admin_id: str = Form(...),
    label: str = Form(""), parent_bot_id: str = Form(""),
):
    guard = _guard(request)
    if guard:
        return guard
    parent = None
    if parent_bot_id:
        parent = session.get(Bot, int(parent_bot_id))
    bot = hierarchy_service.create_bot(
        session, telegram_token, telegram_admin_id, label, parent_bot=parent
    )
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
        },
    )


@router.post("/bots/{bot_id}/toggle-sub-reseller")
def toggle_sub_reseller(bot_id: int, request: Request, session: Session = Depends(get_session)):
    guard = _guard(request)
    if guard:
        return guard
    bot = session.get(Bot, bot_id)
    bot.can_create_sub_reseller = not bot.can_create_sub_reseller
    session.add(bot)
    session.commit()
    return RedirectResponse(f"/admin/bots/{bot_id}", status_code=302)


@router.post("/bots/{bot_id}/toggle-active")
async def toggle_active(bot_id: int, request: Request, session: Session = Depends(get_session)):
    guard = _guard(request)
    if guard:
        return guard
    bot = session.get(Bot, bot_id)
    bot.is_active = not bot.is_active
    session.add(bot)
    session.commit()
    session.refresh(bot)
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
    guard = _guard(request)
    if guard:
        return guard
    bot = session.get(Bot, bot_id)
    topup(session, bot, amount, note=note or "اصلاح دستی توسط ادمین اصلی")
    return RedirectResponse(f"/admin/bots/{bot_id}", status_code=302)


@router.post("/sync-plans")
async def sync_plans(request: Request, session: Session = Depends(get_session)):
    guard = _guard(request)
    if guard:
        return guard
    await pricing_service.sync_plans_from_connectix(session)
    bots = session.exec(select(Bot)).all()
    for bot in bots:
        pricing_service.ensure_plan_pricing_rows(session, bot)
    return RedirectResponse("/admin", status_code=302)


@router.post("/bots/{bot_id}/settings")
def update_bot_settings_web(
    bot_id: int, request: Request, session: Session = Depends(get_session),
    forced_channel: str = Form(""), forced_channel_enabled: str = Form(""),
    referral_bonus_amount: float = Form(0), support_contact: str = Form(""),
):
    guard = _guard(request)
    if guard:
        return guard
    bot = session.get(Bot, bot_id)
    bot.forced_channel = forced_channel
    bot.forced_channel_enabled = bool(forced_channel_enabled)
    bot.referral_bonus_amount = referral_bonus_amount
    bot.support_contact = support_contact
    session.add(bot)
    session.commit()
    return RedirectResponse(f"/admin/bots/{bot_id}", status_code=302)


@router.post("/bots/{bot_id}/customers/{customer_id}/toggle-block")
def toggle_block_customer_web(bot_id: int, customer_id: int, request: Request, session: Session = Depends(get_session)):
    guard = _guard(request)
    if guard:
        return guard
    customer = session.get(Customer, customer_id)
    if customer and customer.bot_id == bot_id:
        customer.is_blocked = not customer.is_blocked
        session.add(customer)
        session.commit()
    return RedirectResponse(f"/admin/bots/{bot_id}", status_code=302)


@router.post("/bots/{bot_id}/customers/{customer_id}/wallet-adjust")
def adjust_customer_wallet_web(
    bot_id: int, customer_id: int, request: Request, session: Session = Depends(get_session),
    amount: float = Form(...), note: str = Form(""),
):
    guard = _guard(request)
    if guard:
        return guard
    customer = session.get(Customer, customer_id)
    if customer and customer.bot_id == bot_id:
        customer_topup(session, customer, amount, tx_type=CustomerWalletTxType.ADJUSTMENT, note=note or "اصلاح دستی ادمین اصلی")
    return RedirectResponse(f"/admin/bots/{bot_id}", status_code=302)


@router.post("/bots/{bot_id}/reseller-requests/{request_id}/approve")
def approve_reseller_request_web(bot_id: int, request_id: int, request: Request, session: Session = Depends(get_session)):
    guard = _guard(request)
    if guard:
        return guard
    from backend.models import utcnow
    req = session.get(ResellerRequest, request_id)
    if req and req.bot_id == bot_id:
        req.status = ResellerRequestStatus.APPROVED
        req.reviewed_at = utcnow()
        session.add(req)
        session.commit()
    return RedirectResponse(f"/admin/bots/{bot_id}", status_code=302)


@router.post("/bots/{bot_id}/reseller-requests/{request_id}/reject")
def reject_reseller_request_web(bot_id: int, request_id: int, request: Request, session: Session = Depends(get_session)):
    guard = _guard(request)
    if guard:
        return guard
    req = session.get(ResellerRequest, request_id)
    if req and req.bot_id == bot_id:
        req.status = ResellerRequestStatus.REJECTED
        session.add(req)
        session.commit()
    return RedirectResponse(f"/admin/bots/{bot_id}", status_code=302)
