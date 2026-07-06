import json
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from aiogram import Bot, Dispatcher
from aiogram.types import Update
import uvicorn

from .config import (
    BOT_TOKEN,
    TELEGRAM_WEBHOOK_URL,
    TBANK_WEBHOOK_URL,
    MANAGER_IDS
)
from .database import init_db
from .handlers import client, manager
from .services.tbank import verify_webhook_signature
from aiogram.utils.keyboard import InlineKeyboardBuilder

# --- Инициализация бота и диспетчера ---
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

dp.include_router(manager.router)
dp.include_router(client.router)

# --- Обработчики вебхуков ---
async def telegram_webhook_handler(request: Request):
    update_data = await request.json()
    update = Update(**update_data)
    await dp.feed_update(bot, update)   # <-- исправлено
    return {"status": "ok"}

async def tbank_webhook_handler(request: Request):
    data = await request.json()
    print("=== WEBHOOK RECEIVED ===")
    print(data)

    if not verify_webhook_signature(data):
        return {"status": "unauthorized"}, 401

    order_id = data.get("OrderId")
    status = data.get("Status")

    if order_id and status == "CONFIRMED":
        from .database import get_invoice_by_payment_id, update_invoice_status

        invoice = get_invoice_by_payment_id(str(order_id))
        if invoice and invoice["status"] != "paid":
            updated = update_invoice_status(str(order_id), "paid")

            if updated:
                builder = InlineKeyboardBuilder()
                builder.button(text="📦 Отправить трек", callback_data=f"track_{updated['order_number']}")
                builder.button(text="📢 Уведомить", callback_data=f"notify_{updated['order_number']}")
                builder.adjust(2)

                for manager_id in MANAGER_IDS:
                    try:
                        await bot.send_message(
                            manager_id,
                            f"✅ **ДЕНЬГИ ПОСТУПИЛИ!**\n\n"
                            f"📦 **Заказ:** {updated.get('order_number') or 'Ручная ссылка'}\n"
                            f"📝 **Комментарий:** {updated.get('description') or 'Не указан'}\n"
                            f"💰 **Сумма:** {updated['amount_rub']:,} ₽\n"
                            f"📍 **Адрес:** {updated.get('delivery_address') or 'Не указан'}\n"
                            f"👤 **ФИО:** {updated.get('client_name') or 'Не указано'}\n"
                            f"📱 **Телефон:** {updated.get('client_phone') or 'Не указан'}\n"
                            f"🕒 **Время оплаты:** {updated.get('paid_at') or 'неизвестно'}\n"
                            f"🆔 **Payment ID:** {order_id[:16]}...\n\n"
                            f"⚡️ Готовьте к отправке!",
                            reply_markup=builder.as_markup()
                        )
                    except Exception:
                        pass

                if updated.get("client_tg_id"):
                    try:
                        order_number = updated.get('order_number') or 'не указан'
                        await bot.send_message(
                            updated["client_tg_id"],
                            f"✅ **Оплата по заявке №{order_number} поступила на счёт.**\n\n"
                            f"Ожидайте подтверждения на сайте в рабочее время и отправки посылки.\n\n"
                            f"Спасибо за доверие!"
                        )
                    except Exception:
                        pass

    return {"status": "ok"}

# --- Функция жизненного цикла приложения ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    print("🚀 Бот запущен в режиме вебхука.")
    print(f"🌐 Вебхук для Telegram: {TELEGRAM_WEBHOOK_URL}")
    print(f"💳 Вебхук для T‑Банк: {TBANK_WEBHOOK_URL}")
    yield
    await bot.session.close()

# --- FastAPI приложение ---
app = FastAPI(lifespan=lifespan, title="Payment Bot for Bothost")

app.post("/webhook/telegram")(telegram_webhook_handler)
app.post("/webhook/tbank")(tbank_webhook_handler)

@app.get("/ping")
async def ping():
    return {"status": "ok", "message": "Server is alive"}

# --- Точка входа ---
if __name__ == "__main__":
    port = int(os.getenv("PORT", 3000))
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=port,
        reload=False
    )