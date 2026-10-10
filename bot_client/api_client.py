"""
کلاینت ساده‌ی HTTP برای صحبت ربات تلگرامی با بک‌اند مرکزی.
این فایل هیچ منطق تجاری ندارد — فقط لفاف فراخوانی API است. چه ربات روی سرور
مرکزی اجرا شود (BACKEND_URL=http://127.0.0.1:8000) چه روی سرور یک نماینده
(BACKEND_URL=آدرس عمومی بک‌اند)، این کلاینت یکسان کار می‌کند.
"""
from typing import Any, Optional
import httpx


class BackendApiError(Exception):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"Backend API error {status_code}: {detail}")


class BackendApiClient:
    def __init__(self, backend_url: str, api_key: str):
        self.backend_url = backend_url.rstrip("/")
        self.api_key = api_key

    async def _request(self, method: str, path: str, **kwargs) -> Any:
        url = f"{self.backend_url}{path}"
        headers = {"X-Bot-Api-Key": self.api_key}
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.request(method, url, headers=headers, **kwargs)
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("detail", resp.text)
            except Exception:
                detail = resp.text
            raise BackendApiError(resp.status_code, detail)
        if resp.status_code == 204:
            return None
        return resp.json()

    # ---- bot meta ----
    async def bot_info(self) -> dict:
        return await self._request("GET", "/api/v1/bot/info")

    async def is_admin(self, telegram_id: str) -> bool:
        data = await self._request("GET", "/api/v1/bot/is-admin", params={"telegram_id": telegram_id})
        return data["is_admin"]

    # ---- plans ----
    async def list_plans(self) -> list[dict]:
        return await self._request("GET", "/api/v1/plans")

    async def list_all_plans_for_admin(self, requesting_telegram_id: str) -> list[dict]:
        return await self._request(
            "GET", "/api/v1/plans/all-for-admin", params={"requesting_telegram_id": requesting_telegram_id}
        )

    async def set_plan_price(self, plan_id: str, requesting_telegram_id: str, sell_price: float, is_offered: bool) -> dict:
        return await self._request(
            "POST", f"/api/v1/plans/{plan_id}/price",
            json={"requesting_telegram_id": requesting_telegram_id, "sell_price": sell_price, "is_offered": is_offered},
        )

    # ---- payment methods ----
    async def list_payment_methods(self) -> list[dict]:
        return await self._request("GET", "/api/v1/payment-methods")

    async def set_payment_method(self, method: str, requesting_telegram_id: str, enabled: bool, details: Optional[str] = None) -> dict:
        return await self._request(
            "POST", f"/api/v1/payment-methods/{method}",
            json={"requesting_telegram_id": requesting_telegram_id, "enabled": enabled, "details": details},
        )

    # ---- customers ----
    async def upsert_customer(
        self, telegram_id: str, telegram_username: str = "", name: str = "", phone: str = "", email: str = "",
        referred_by_telegram_id: Optional[str] = None,
    ) -> dict:
        return await self._request(
            "POST", "/api/v1/customers/upsert",
            json={
                "telegram_id": telegram_id, "telegram_username": telegram_username,
                "name": name, "phone": phone, "email": email,
                "referred_by_telegram_id": referred_by_telegram_id,
            },
        )

    async def list_customers(self, requesting_telegram_id: str) -> list[dict]:
        return await self._request(
            "GET", "/api/v1/customers", params={"requesting_telegram_id": requesting_telegram_id}
        )

    async def toggle_block_customer(self, customer_id: int, requesting_telegram_id: str) -> dict:
        return await self._request(
            "POST", f"/api/v1/customers/{customer_id}/toggle-block",
            json={"requesting_telegram_id": requesting_telegram_id},
        )

    async def adjust_customer_wallet(self, customer_id: int, requesting_telegram_id: str, amount: float, note: str = "") -> dict:
        return await self._request(
            "POST", f"/api/v1/customers/{customer_id}/wallet-adjust",
            json={"requesting_telegram_id": requesting_telegram_id, "amount": amount, "note": note},
        )

    async def get_customer_services(self, telegram_id: str) -> dict:
        return await self._request("GET", f"/api/v1/customers/{telegram_id}/services")

    # ---- orders ----
    async def create_order(self, telegram_id: str, plan_id: str, payment_method: str) -> dict:
        return await self._request(
            "POST", "/api/v1/orders",
            json={"telegram_id": telegram_id, "plan_id": plan_id, "payment_method": payment_method},
        )

    async def submit_order_receipt(self, order_id: int, telegram_file_id: str, note: str = "") -> dict:
        return await self._request(
            "POST", f"/api/v1/orders/{order_id}/receipt",
            json={"telegram_file_id": telegram_file_id, "note": note},
        )

    # ---- wallet ----
    async def get_wallet(self) -> dict:
        return await self._request("GET", "/api/v1/wallet")

    async def request_wallet_topup(self, amount: float, telegram_file_id: str, note: str = "") -> dict:
        return await self._request(
            "POST", "/api/v1/wallet/topup-request",
            json={"amount": amount, "telegram_file_id": telegram_file_id, "note": note},
        )

    # ---- receipts ----
    async def list_pending_receipts(self, requesting_telegram_id: str) -> list[dict]:
        return await self._request(
            "GET", "/api/v1/receipts/pending", params={"requesting_telegram_id": requesting_telegram_id}
        )

    async def approve_receipt(self, receipt_id: int, requesting_telegram_id: str) -> dict:
        return await self._request(
            "POST", f"/api/v1/receipts/{receipt_id}/approve",
            json={"requesting_telegram_id": requesting_telegram_id},
        )

    async def reject_receipt(self, receipt_id: int, requesting_telegram_id: str, note: str = "") -> dict:
        return await self._request(
            "POST", f"/api/v1/receipts/{receipt_id}/reject",
            json={"requesting_telegram_id": requesting_telegram_id, "note": note},
        )

    # ---- sub-bots ----
    async def create_sub_bot(self, requesting_telegram_id: str, telegram_token: str, telegram_admin_id: str, label: str = "") -> dict:
        return await self._request(
            "POST", "/api/v1/sub-bots",
            json={
                "requesting_telegram_id": requesting_telegram_id,
                "telegram_token": telegram_token,
                "telegram_admin_id": telegram_admin_id,
                "label": label,
            },
        )

    # ---- trial plan ----
    async def get_trial_plan(self, telegram_id: str) -> dict:
        return await self._request("GET", "/api/v1/plans/trial", params={"telegram_id": telegram_id})

    async def create_trial_order(self, telegram_id: str) -> dict:
        return await self._request("POST", "/api/v1/orders/trial", json={"telegram_id": telegram_id})

    # ---- customer wallet ----
    async def get_customer_wallet(self, telegram_id: str) -> dict:
        return await self._request("GET", "/api/v1/wallet/customer", params={"telegram_id": telegram_id})

    async def request_customer_wallet_topup(self, telegram_id: str, amount: float, telegram_file_id: str, note: str = "") -> dict:
        return await self._request(
            "POST", "/api/v1/wallet/customer/topup-request",
            json={"telegram_id": telegram_id, "amount": amount, "telegram_file_id": telegram_file_id, "note": note},
        )

    async def pay_from_wallet(self, telegram_id: str, plan_id: str) -> dict:
        return await self._request(
            "POST", "/api/v1/orders/pay-from-wallet",
            json={"telegram_id": telegram_id, "plan_id": plan_id},
        )

    # ---- referral ----
    async def referral_me(self, telegram_id: str) -> dict:
        return await self._request("GET", "/api/v1/referral/me", params={"telegram_id": telegram_id})

    # ---- reseller requests ----
    async def create_reseller_request(self, telegram_id: str, note: str = "") -> dict:
        return await self._request(
            "POST", "/api/v1/reseller-requests", json={"telegram_id": telegram_id, "note": note}
        )

    async def list_pending_reseller_requests(self, requesting_telegram_id: str) -> list[dict]:
        return await self._request(
            "GET", "/api/v1/reseller-requests/pending", params={"requesting_telegram_id": requesting_telegram_id}
        )

    async def approve_reseller_request(self, request_id: int, requesting_telegram_id: str) -> dict:
        return await self._request(
            "POST", f"/api/v1/reseller-requests/{request_id}/approve",
            json={"requesting_telegram_id": requesting_telegram_id},
        )

    async def reject_reseller_request(self, request_id: int, requesting_telegram_id: str) -> dict:
        return await self._request(
            "POST", f"/api/v1/reseller-requests/{request_id}/reject",
            json={"requesting_telegram_id": requesting_telegram_id},
        )

    # ---- bot settings (کانال اجباری، پشتیبانی، درصد رفرال) ----
    async def update_bot_settings(
        self, requesting_telegram_id: str, forced_channel: Optional[str] = None,
        forced_channel_enabled: Optional[bool] = None, support_contact: Optional[str] = None,
        referral_bonus_amount: Optional[float] = None,
    ) -> dict:
        return await self._request(
            "POST", "/api/v1/bot/settings",
            json={
                "requesting_telegram_id": requesting_telegram_id,
                "forced_channel": forced_channel,
                "forced_channel_enabled": forced_channel_enabled,
                "support_contact": support_contact,
                "referral_bonus_amount": referral_bonus_amount,
            },
        )
