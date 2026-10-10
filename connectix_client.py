"""
کلاینت ساده و async برای Connectix Seller External API.
این تنها جایی در کل پروژه است که مستقیماً با Connectix صحبت می‌کند — طبق معماری،
ربات‌های کلاینت هرگز مستقیم به این فایل دسترسی ندارند؛ فقط بک‌اند از آن استفاده می‌کند.
"""
from typing import Any, Optional
import httpx

from backend.config import settings


class ConnectixError(Exception):
    def __init__(self, status_code: int, message: str, payload: Optional[dict] = None):
        self.status_code = status_code
        self.message = message
        self.payload = payload or {}
        super().__init__(f"Connectix API error {status_code}: {message}")


class ConnectixClient:
    def __init__(self, token: Optional[str] = None, base_url: Optional[str] = None):
        self.token = token or settings.CONNECTIX_API_TOKEN
        self.base_url = (base_url or settings.CONNECTIX_BASE_URL).rstrip("/")

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
        }

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        url = f"{self.base_url}/{path.lstrip('/')}"
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.request(method, url, headers=self._headers(), **kwargs)
        if resp.status_code >= 400:
            try:
                data = resp.json()
                message = data.get("message", resp.text)
            except Exception:
                data = {}
                message = resp.text
            raise ConnectixError(resp.status_code, message, data)
        return resp.json()

    async def get_enabled_plans(self) -> list[dict]:
        data = await self._request("GET", "clients/get-enabled-plans")
        return data.get("plans", [])

    async def list_users(
        self, page: int = 1, record_per_page: int = 20,
        username: Optional[str] = None, name: Optional[str] = None,
    ) -> dict:
        params: dict[str, Any] = {"page": page, "recordPerPage": record_per_page}
        if username:
            params["username"] = username
        if name:
            params["name"] = name
        return await self._request("GET", "clients/", params=params)

    async def show_user(self, client_id: str) -> dict:
        data = await self._request("GET", "clients/show", params={"id": client_id})
        return data.get("client", {})

    async def create_user(
        self,
        password: str,
        plan_id: str,
        telegram_id: Optional[str] = None,
        name: Optional[str] = None,
        phone: Optional[str] = None,
        email: Optional[str] = None,
        chat_id: Optional[str] = None,
        enable_plan_after_first_login: Optional[bool] = None,
        is_child_protection_enabled: Optional[bool] = None,
    ) -> dict:
        form: dict[str, Any] = {"password": password, "plan_id": plan_id}
        optional = {
            "telegram_id": telegram_id,
            "name": name,
            "phone": phone,
            "email": email,
            "chat_id": chat_id,
            "enable_plan_after_first_login": enable_plan_after_first_login,
            "is_child_protection_enabled": is_child_protection_enabled,
        }
        for key, value in optional.items():
            if value is not None:
                form[key] = str(value).lower() if isinstance(value, bool) else value
        # Create User از multipart/form-data استفاده می‌کند؛ files={} باعث می‌شود
        # httpx حتی بدون فایل واقعی، بدنه را به‌صورت multipart/form-data بسازد.
        return await self._request("POST", "clients/store", data=form, files={})

    async def add_plan(self, client_id: str, plan_id: str) -> dict:
        return await self._request(
            "POST", "clients/add-plan", json={"id": client_id, "plan_id": plan_id}
        )
