#!/usr/bin/env bash
# نصب/مدیریت یک‌خطی:
#   curl -fsSL https://raw.githubusercontent.com/Aziz-dev22/Connectix/main/install.sh | bash
# یک منوی تعاملی (نصب / آپدیت / پشتیبان‌گیری / حذف) نشان داده می‌شود.
set -e

REPO_URL="https://github.com/Aziz-dev22/Connectix.git"
INSTALL_DIR="$HOME/Connectix"
TTY=/dev/tty

ask() {
  # ask "متن سوال" "مقدار پیش‌فرض"
  local prompt="$1" default="$2" ans
  if [ -n "$default" ]; then
    read -r -p "$prompt [$default]: " ans < "$TTY" || true
    echo "${ans:-$default}"
  else
    read -r -p "$prompt: " ans < "$TTY" || true
    echo "$ans"
  fi
}

ensure_base_packages() {
  if ! command -v git &>/dev/null; then
    sudo apt-get update && sudo apt-get install -y git
  fi
  if ! command -v python3 &>/dev/null; then
    sudo apt-get install -y python3 python3-venv python3-pip
  fi
  command -v unzip &>/dev/null || sudo apt-get install -y unzip
}

clone_or_update_repo() {
  if [ ! -d "$INSTALL_DIR/.git" ]; then
    git clone "$REPO_URL" "$INSTALL_DIR"
  fi
}

setup_venv() {
  cd "$INSTALL_DIR"
  python3 -m venv venv
  source venv/bin/activate
  pip install --upgrade pip --quiet
  pip install -r requirements.txt --quiet
}

write_systemd_service() {
  local name="$1" exec_cmd="$2"
  sudo tee "/etc/systemd/system/${name}.service" > /dev/null << EOF
[Unit]
Description=Connectix VPN Bot - ${name}
After=network.target

[Service]
WorkingDirectory=$INSTALL_DIR
ExecStart=${exec_cmd}
Restart=always
RestartSec=3
EnvironmentFile=$INSTALL_DIR/.env

[Install]
WantedBy=multi-user.target
EOF
  sudo systemctl daemon-reload
  sudo systemctl enable "$name"
  sudo systemctl restart "$name"
}

# ---------------------------------------------------------------------------
# نصب بک‌اند مرکزی
# ---------------------------------------------------------------------------
install_backend() {
  ensure_base_packages
  clone_or_update_repo
  setup_venv
  cd "$INSTALL_DIR"
  [ -f .env ] || cp .env.example .env

  echo ""
  echo "=== تنظیمات بک‌اند مرکزی ==="
  local port admin_user admin_pass connectix_token session_key public_ip

  port=$(ask "پورت اجرای پنل/بک‌اند" "8000")
  admin_user=$(ask "یوزرنیم پنل مدیریت تحت وب" "admin")
  admin_pass=$(ask "پسورد پنل مدیریت تحت وب" "")
  connectix_token=$(ask "CONNECTIX_API_TOKEN (از پنل Connectix → پروفایل → کلید API)" "")
  session_key=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
  public_ip=$(curl -s ifconfig.me 2>/dev/null || echo "127.0.0.1")

  sed -i "s|^CONNECTIX_API_TOKEN=.*|CONNECTIX_API_TOKEN=${connectix_token}|" .env
  sed -i "s|^ADMIN_USERNAME=.*|ADMIN_USERNAME=${admin_user}|" .env
  sed -i "s|^ADMIN_PASSWORD=.*|ADMIN_PASSWORD=${admin_pass}|" .env
  sed -i "s|^SESSION_SECRET_KEY=.*|SESSION_SECRET_KEY=${session_key}|" .env
  sed -i "s|^PUBLIC_BACKEND_URL=.*|PUBLIC_BACKEND_URL=http://${public_ip}:${port}|" .env
  if grep -q "^INTERNAL_PORT=" .env; then
    sed -i "s|^INTERNAL_PORT=.*|INTERNAL_PORT=${port}|" .env
  else
    echo "INTERNAL_PORT=${port}" >> .env
  fi

  write_systemd_service "connectix-backend" "$INSTALL_DIR/venv/bin/uvicorn backend.main:app --host 0.0.0.0 --port ${port}"

  echo ""
  echo "✅ بک‌اند مرکزی بالا آمد: http://${public_ip}:${port}/admin"
  echo "   لاگ‌ها: journalctl -u connectix-backend -f"

  echo ""
  local want_root_bot
  want_root_bot=$(ask "می‌خواهید همین الان ربات اصلی (ریشه) را هم ثبت کنید؟ (y/N)" "N")
  if [[ "$want_root_bot" =~ ^[Yy]$ ]]; then
    local bot_token bot_admin bot_label
    bot_token=$(ask "توکن ربات تلگرام (از BotFather)" "")
    bot_admin=$(ask "آیدی عددی تلگرام ادمین این ربات" "")
    bot_label=$(ask "یک نام برای این ربات" "ربات اصلی")
    sleep 2  # صبر برای بالا آمدن کامل سرویس
    "$INSTALL_DIR/venv/bin/python3" -m backend.cli create-root-bot --token "$bot_token" --admin "$bot_admin" --label "$bot_label"
    sudo systemctl restart connectix-backend
    echo "ربات اصلی ثبت شد و روشن می‌شود."
  else
    echo "می‌توانید بعداً از پنل مدیریت (/admin) ربات اصلی را بسازید."
  fi
}

# ---------------------------------------------------------------------------
# نصب کلاینت ربات (برای سرور شخصیِ یک نماینده)
# ---------------------------------------------------------------------------
install_bot_client() {
  ensure_base_packages
  clone_or_update_repo
  setup_venv
  cd "$INSTALL_DIR"
  [ -f .env ] || cp .env.example .env

  echo ""
  echo "=== تنظیمات کلاینت ربات ==="
  local backend_url api_key bot_token

  backend_url=$(ask "آدرس بک‌اند مرکزی (BACKEND_URL)" "http://127.0.0.1:8000")
  api_key=$(ask "API Key ربات (از پنل مدیریت بک‌اند گرفته‌اید)" "")
  bot_token=$(ask "توکن ربات تلگرام" "")

  sed -i "s|^BACKEND_URL=.*|BACKEND_URL=${backend_url}|" .env
  sed -i "s|^BOT_API_KEY=.*|BOT_API_KEY=${api_key}|" .env
  sed -i "s|^BOT_TELEGRAM_TOKEN=.*|BOT_TELEGRAM_TOKEN=${bot_token}|" .env

  write_systemd_service "connectix-bot" "$INSTALL_DIR/venv/bin/python -m bot_client"

  echo ""
  echo "✅ کلاینت ربات اجرا شد: journalctl -u connectix-bot -f"
}

do_install() {
  echo ""
  echo "کدام بخش نصب شود؟"
  echo "  1) بک‌اند مرکزی (سرور اصلی شما)"
  echo "  2) کلاینت ربات (این سرور قرار است فقط یک ربات نمایندگی را اجرا کند)"
  local choice
  choice=$(ask "عدد را وارد کنید" "1")
  case "$choice" in
    2) install_bot_client ;;
    *) install_backend ;;
  esac
}

# ---------------------------------------------------------------------------
# آپدیت
# ---------------------------------------------------------------------------
do_update() {
  if [ ! -d "$INSTALL_DIR/.git" ]; then
    echo "پروژه‌ای نصب‌شده پیدا نشد ($INSTALL_DIR)."
    return
  fi
  cd "$INSTALL_DIR"
  echo "در حال دریافت آخرین نسخه از گیت‌هاب..."
  git pull
  source venv/bin/activate
  pip install --upgrade -r requirements.txt --quiet

  if systemctl is-enabled connectix-backend &>/dev/null; then
    sudo systemctl restart connectix-backend
    echo "✅ connectix-backend ری‌استارت شد."
  fi
  if systemctl is-enabled connectix-bot &>/dev/null; then
    sudo systemctl restart connectix-bot
    echo "✅ connectix-bot ری‌استارت شد."
  fi
  echo "آپدیت کامل شد."
}

# ---------------------------------------------------------------------------
# پشتیبان‌گیری کامل
# ---------------------------------------------------------------------------
do_backup() {
  if [ ! -d "$INSTALL_DIR" ]; then
    echo "پروژه‌ای نصب‌شده پیدا نشد ($INSTALL_DIR)."
    return
  fi
  local out="$HOME/connectix-backup-$(date +%Y%m%d-%H%M%S).tar.gz"
  tar czf "$out" -C "$HOME" --exclude="Connectix/venv" --exclude="Connectix/__pycache__" "$(basename "$INSTALL_DIR")"
  echo "✅ پشتیبان کامل (شامل .env و دیتابیس) ساخته شد:"
  echo "   $out"
  echo "برای دانلود از گوشی/کامپیوتر، از همون اپی که باهاش به سرور وصلید (مثل Termius → SFTP) استفاده کنید."
}

# ---------------------------------------------------------------------------
# حذف کامل پروژه
# ---------------------------------------------------------------------------
do_remove() {
  local confirm
  confirm=$(ask "⚠️ این کار همه‌چیز (کد، دیتابیس، تنظیمات) را حذف می‌کند. مطمئنید؟ (yes/NO)" "NO")
  if [ "$confirm" != "yes" ]; then
    echo "لغو شد."
    return
  fi
  sudo systemctl stop connectix-backend 2>/dev/null || true
  sudo systemctl disable connectix-backend 2>/dev/null || true
  sudo rm -f /etc/systemd/system/connectix-backend.service
  sudo systemctl stop connectix-bot 2>/dev/null || true
  sudo systemctl disable connectix-bot 2>/dev/null || true
  sudo rm -f /etc/systemd/system/connectix-bot.service
  sudo systemctl daemon-reload
  rm -rf "$INSTALL_DIR"
  echo "✅ پروژه کاملاً حذف شد."
}

# ---------------------------------------------------------------------------
# منوی اصلی
# ---------------------------------------------------------------------------
main_menu() {
  echo "======================================"
  echo "  Connectix VPN Bot — نصب و مدیریت"
  echo "======================================"
  echo "  1) نصب"
  echo "  2) آپدیت"
  echo "  3) پشتیبان‌گیری کامل از پروژه"
  echo "  4) حذف پروژه"
  echo "======================================"
  local choice
  choice=$(ask "یک گزینه را انتخاب کنید" "1")
  case "$choice" in
    1) do_install ;;
    2) do_update ;;
    3) do_backup ;;
    4) do_remove ;;
    *) echo "گزینه نامعتبر." ;;
  esac
}

# پشتیبانی از اجرای مستقیم با آرگومان (برای استفاده‌های خودکار/اسکریپتی) — اختیاری
case "${1:-}" in
  backend) install_backend ;;
  bot) install_bot_client ;;
  update) do_update ;;
  backup) do_backup ;;
  remove) do_remove ;;
  *) main_menu ;;
esac
