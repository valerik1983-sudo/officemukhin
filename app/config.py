import os
from dotenv import load_dotenv

# Загружаем переменные из .env (только для локальной разработки)
load_dotenv()

# === Telegram ===
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не задан в переменных окружения")

# ID менеджеров (через запятую: 123456789,987654321)
MANAGER_IDS = []
if os.getenv("MANAGER_IDS"):
    MANAGER_IDS = [int(id.strip()) for id in os.getenv("MANAGER_IDS").split(",") if id.strip()]

# === T‑Банк ===
TBANK_TERMINAL_KEY = os.getenv("TBANK_TERMINAL_KEY")
if not TBANK_TERMINAL_KEY:
    raise ValueError("TBANK_TERMINAL_KEY не задан")

TBANK_SECRET_KEY = os.getenv("TBANK_SECRET_KEY")
if not TBANK_SECRET_KEY:
    raise ValueError("TBANK_SECRET_KEY не задан")

# === Webhook URLs ===
# Базовый URL, на котором висит бот (например, https://ваш-бот.bothost.net)
BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")
TELEGRAM_WEBHOOK_PATH = "/webhook/telegram"
TBANK_WEBHOOK_PATH = "/webhook/tbank"

# Полные URL для вебхуков
TELEGRAM_WEBHOOK_URL = f"{BASE_URL}{TELEGRAM_WEBHOOK_PATH}"
TBANK_WEBHOOK_URL = f"{BASE_URL}{TBANK_WEBHOOK_PATH}"

# === База данных ===
DATABASE_PATH = os.getenv("DATABASE_PATH", "bot.db")