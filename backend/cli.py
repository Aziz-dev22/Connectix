"""
ابزار خط‌فرمان کوچک برای کارهای نصبی/مدیریتی — اصلی‌اش ثبت ربات ریشه هنگام
نصب (`install.sh`) است، بدون نیاز به لاگین در پنل وب.

استفاده:
    python3 -m backend.cli create-root-bot --token <TELEGRAM_TOKEN> --admin <ADMIN_TELEGRAM_ID> [--label "..."]
    python3 -m backend.cli list-bots
"""
import argparse

from backend.database import init_db, get_session_direct
from backend.services import hierarchy as hierarchy_service
from backend.models import Bot


def create_root_bot(token: str, admin_id: str, label: str = "") -> None:
    init_db()
    session = get_session_direct()
    try:
        bot = hierarchy_service.create_bot(
            session, telegram_token=token, telegram_admin_id=admin_id,
            label=label or "ربات اصلی", parent_bot=None,
        )
        print(f"ربات ریشه ساخته شد. bot_id={bot.id} api_key={bot.api_key}")
    finally:
        session.close()


def list_bots() -> None:
    from sqlmodel import select
    init_db()
    session = get_session_direct()
    try:
        bots = session.exec(select(Bot)).all()
        if not bots:
            print("هیچ رباتی ثبت نشده.")
        for b in bots:
            print(f"#{b.id} | {b.label} | parent={b.parent_bot_id} | active={b.is_active} | api_key={b.api_key}")
    finally:
        session.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Connectix VPN Bot — management CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p_create = sub.add_parser("create-root-bot", help="ثبت ربات ریشه (بدون والد)")
    p_create.add_argument("--token", required=True)
    p_create.add_argument("--admin", required=True)
    p_create.add_argument("--label", default="")

    sub.add_parser("list-bots", help="لیست همه‌ی ربات‌های ثبت‌شده")

    args = parser.parse_args()
    if args.command == "create-root-bot":
        create_root_bot(args.token, args.admin, args.label)
    elif args.command == "list-bots":
        list_bots()


if __name__ == "__main__":
    main()
