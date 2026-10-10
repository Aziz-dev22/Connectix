"""
راه‌اندازی اتصال دیتابیس (SQLModel روی SQLAlchemy).
"""
from sqlmodel import SQLModel, Session, create_engine
from backend.config import settings

connect_args = {}
if settings.DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

engine = create_engine(settings.DATABASE_URL, echo=False, connect_args=connect_args)


def init_db() -> None:
    """ساخت تمام جدول‌ها در صورت نبودن (مناسب برای SQLite ساده؛ برای تغییرات ساختاری
    آینده بهتر است از Alembic استفاده شود)."""
    SQLModel.metadata.create_all(engine)


def get_session():
    with Session(engine) as session:
        yield session


def get_session_direct() -> Session:
    """برای استفاده در جاهایی که وابستگی FastAPI (Depends) در دسترس نیست،
    مثل تسک‌های پس‌زمینه‌ی asyncio."""
    return Session(engine)
