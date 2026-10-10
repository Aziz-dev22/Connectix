"""
اجرای مستقل کلاینت ربات (مثلاً روی سرور شخصی یک نماینده، جدا از بک‌اند مرکزی).

استفاده:
    BOT_API_KEY=... BOT_TELEGRAM_TOKEN=... BACKEND_URL=https://your-backend ... \
        python -m bot_client

یا با فایل .env در همین پوشه (نگاه کنید به .env.example در ریشه‌ی پروژه).
"""
import logging
import os
import sys

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def main() -> None:
    api_key = os.getenv("BOT_API_KEY")
    telegram_token = os.getenv("BOT_TELEGRAM_TOKEN")
    backend_url = os.getenv("BACKEND_URL", "http://127.0.0.1:8000")

    if not api_key or not telegram_token:
        print("خطا: BOT_API_KEY و BOT_TELEGRAM_TOKEN باید در .env یا متغیرهای محیطی تنظیم شوند.")
        sys.exit(1)

    from bot_client.bot import build_application

    application = build_application(telegram_token, api_key, backend_url)
    print(f"ربات در حال اجراست و به بک‌اند {backend_url} وصل است. برای توقف Ctrl+C بزنید.")
    application.run_polling()


if __name__ == "__main__":
    main()
