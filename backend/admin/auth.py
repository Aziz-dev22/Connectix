"""
احراز هویت و نقش‌بندی پنل مدیریت (چندکاربره). اولین بار که برنامه اجرا می‌شود،
اگر هیچ AdminUser ای در دیتابیس نباشد، یک حساب super_admin از روی
ADMIN_USERNAME/ADMIN_PASSWORD در .env ساخته می‌شود (برای سازگاری با نصب‌های قبلی).
"""
from typing import Optional

from fastapi import Request
from passlib.context import CryptContext
from sqlmodel import Session, select

from backend.config import settings
from backend.models import AdminUser, AdminRole

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return pwd_context.verify(password, password_hash)
    except Exception:
        return False


def ensure_default_admin(session: Session) -> None:
    existing = session.exec(select(AdminUser)).first()
    if existing:
        return
    user = AdminUser(
        username=settings.ADMIN_USERNAME,
        password_hash=hash_password(settings.ADMIN_PASSWORD),
        role=AdminRole.SUPER_ADMIN,
    )
    session.add(user)
    session.commit()


def authenticate(session: Session, username: str, password: str) -> Optional[AdminUser]:
    user = session.exec(select(AdminUser).where(AdminUser.username == username)).first()
    if not user or not user.is_active:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


def is_logged_in(request: Request) -> bool:
    return bool(request.session.get("admin_user_id"))


def current_role(request: Request) -> Optional[str]:
    return request.session.get("admin_role")


def has_role(request: Request, *allowed_roles: str) -> bool:
    role = current_role(request)
    return role in allowed_roles


# دسترسی‌های مالی/حساس: فقط مدیر کل و مدیر مالی
FINANCE_ROLES = (AdminRole.SUPER_ADMIN.value, AdminRole.FINANCE.value)
# مدیریت ربات/نماینده‌ها: مدیر کل و مدیر مالی (ساخت ربات تاثیر مالی دارد)
BOT_MANAGE_ROLES = (AdminRole.SUPER_ADMIN.value, AdminRole.FINANCE.value)
# فقط مدیر کل: مدیریت حساب‌های ادمین
USER_MANAGE_ROLES = (AdminRole.SUPER_ADMIN.value,)
# عملیات عملیاتی/پشتیبانی (غیرمالی): مدیر کل، مالی و پشتیبان — نه viewer
OPS_ROLES = (AdminRole.SUPER_ADMIN.value, AdminRole.FINANCE.value, AdminRole.SUPPORT.value)
