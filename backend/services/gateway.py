"""
اسکلت اتصال به درگاه پرداخت (پیش‌فرض: زرین‌پال).
برای فعال‌سازی واقعی: MERCHANT_ID را در .env پر کنید و دو تابع زیر را کامل کنید.
این فایل عمداً حداقلی نگه داشته شده چون اطلاعات حساب درگاه پرداخت شما لازم است.
"""
from backend.config import settings

ZARINPAL_REQUEST_URL = "https://api.zarinpal.com/pg/v4/payment/request.json"
ZARINPAL_VERIFY_URL = "https://api.zarinpal.com/pg/v4/payment/verify.json"
ZARINPAL_STARTPAY_URL = "https://www.zarinpal.com/pg/StartPay/"


class GatewayNotConfiguredError(Exception):
    pass


async def request_payment(amount_toman: int, description: str, callback_url: str) -> str:
    """درخواست پرداخت را به زرین‌پال می‌فرستد و لینک پرداخت را برمی‌گرداند.
    TODO: پیاده‌سازی کامل بعد از تنظیم ZARINPAL_MERCHANT_ID."""
    if not settings.ZARINPAL_MERCHANT_ID:
        raise GatewayNotConfiguredError(
            "درگاه پرداخت هنوز تنظیم نشده؛ ZARINPAL_MERCHANT_ID را در .env پر کنید."
        )
    # TODO: ارسال httpx.post به ZARINPAL_REQUEST_URL با merchant_id/amount/callback_url
    # و بازگرداندن ZARINPAL_STARTPAY_URL + authority
    raise NotImplementedError("اتصال واقعی زرین‌پال هنوز پیاده‌سازی نشده است.")


async def verify_payment(authority: str, amount_toman: int) -> bool:
    """TODO: صدا زدن ZARINPAL_VERIFY_URL و بازگرداندن نتیجه‌ی تایید."""
    raise NotImplementedError("تایید پرداخت زرین‌پال هنوز پیاده‌سازی نشده است.")
