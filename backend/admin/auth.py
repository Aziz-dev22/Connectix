from fastapi import Request

from backend.config import settings


def check_credentials(username: str, password: str) -> bool:
    return username == settings.ADMIN_USERNAME and password == settings.ADMIN_PASSWORD


def is_logged_in(request: Request) -> bool:
    return bool(request.session.get("logged_in"))
