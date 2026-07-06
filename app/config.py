import os
from dotenv import load_dotenv

# Загружаем .env только для других переменных (токен бота, BASE_URL и т.д.)
load_dotenv()

# === Telegram ===
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не задан в переменных окружения")

# === ID менеджеров (хардкод) ===
MANAGER_IDS = [258670125, 2126934529]

# === T‑Банк (хардкод реальных ключей) ===
TBANK_TERMINAL_KEY = "1764171907776"
TBANK_SECRET_KEY = "MaD6WiZa0j8pS99q"

# Проверка (на всякий случай)
if not TBANK_TERMINAL_KEY or not TBANK_SECRET_KEY:
    raise ValueError("Ключи T‑Банк не заданы")

# === Webhook URLs ===
BASE_URL = os.getenv("BASE_URL", "https://officemukhin.bothost.tech")
TELEGRAM_WEBHOOK_PATH = "/webhook/telegram"
TBANK_WEBHOOK_PATH = "/webhook/tbank"

TELEGRAM_WEBHOOK_URL = f"{BASE_URL}{TELEGRAM_WEBHOOK_PATH}"
TBANK_WEBHOOK_URL = f"{BASE_URL}{TBANK_WEBHOOK_PATH}"

# === База данных ===
DATABASE_PATH = os.getenv("DATABASE_PATH", "bot.db")