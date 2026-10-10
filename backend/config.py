"""
بارگذاری تنظیمات از فایل .env
"""
import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    # Connectix
    CONNECTIX_API_TOKEN: str = os.getenv("CONNECTIX_API_TOKEN", "")
    CONNECTIX_BASE_URL: str = os.getenv(
        "CONNECTIX_BASE_URL", "https://seller-api.connectix.vip/externel/v1"
    )

    # Database
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./connectix_bot.db")

    # Admin panel
    ADMIN_USERNAME: str = os.getenv("ADMIN_USERNAME", "admin")
    ADMIN_PASSWORD: str = os.getenv("ADMIN_PASSWORD", "change-me-now")
    SESSION_SECRET_KEY: str = os.getenv("SESSION_SECRET_KEY", "dev-secret-change-me")

    PUBLIC_BACKEND_URL: str = os.getenv("PUBLIC_BACKEND_URL", "http://127.0.0.1:8000")
    INTERNAL_PORT: int = int(os.getenv("INTERNAL_PORT", "8000"))

    # Payment gateways (optional, fill in later)
    ZARINPAL_MERCHANT_ID: str = os.getenv("ZARINPAL_MERCHANT_ID", "")
    CRYPTO_WALLET_ADDRESS: str = os.getenv("CRYPTO_WALLET_ADDRESS", "")


settings = Settings()
