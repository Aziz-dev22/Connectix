"""
اسکلت پرداخت ارز دیجیتال. ساده‌ترین روش برای شروع: نمایش آدرس کیف‌پول ثابت
(CRYPTO_WALLET_ADDRESS در .env) به مشتری و تایید دستی توسط ادمین پس از رویت
تراکنش — دقیقاً مثل کارت‌به‌کارت ولی با آدرس کیف‌پول به‌جای شماره کارت.

برای تایید خودکار (بدون دخالت ادمین)، باید به یک سرویس مانیتورینگ بلاکچین
(مثل BlockCypher، Tatum، یا نود اختصاصی) وصل شوید — چون اطلاعات حساب/ولت شما
در دسترس نیست، این بخش به‌صورت TODO باقی مانده است.
"""
from backend.config import settings


def get_wallet_address() -> str:
    return settings.CRYPTO_WALLET_ADDRESS or "تنظیم نشده — CRYPTO_WALLET_ADDRESS را در .env پر کنید"


async def check_transaction_confirmed(tx_hash: str, expected_amount: float) -> bool:
    """TODO: اتصال به یک API مانیتورینگ بلاکچین برای تایید خودکار تراکنش."""
    raise NotImplementedError("تایید خودکار تراکنش کریپتو هنوز پیاده‌سازی نشده است.")
