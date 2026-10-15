"""
لاگ حسابرسی: هر عملیات مهم مدیریتی/مالی (چه از پنل وب، چه از داخل ربات) این‌جا
ثبت می‌شود. این لاگ هرگز از داخل برنامه حذف یا ویرایش نمی‌شود — فقط افزوده می‌شود.
"""
from sqlmodel import Session

from backend.models import AdminActionLog


def log_action(session: Session, actor: str, action: str, bot_id: int = None, detail: str = "") -> None:
    session.add(AdminActionLog(actor=actor, action=action, bot_id=bot_id, detail=detail))
    session.commit()
