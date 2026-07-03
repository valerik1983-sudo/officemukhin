import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from aiogram import Bot, Dispatcher
from aiogram.types import Update
import uvicorn


from .config import (
    BOT_TOKEN, TELEGRAM_WEBHOOK_URL, TBANK_WEBHOOK_PATH,
    BASE_URL, MANAGER_IDS
)
from .database import init_db
from .handlers import client, manager
from .services.tbank import verify_webhook_signature
from .keyboards import main_menu
from aiogram.utils.keyboard import InlineKeyboardBuilder

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

dp.include_router(manager.router)
dp.include_router(client.router)


async def telegram_webhook_handler(request: Request):
    update_data = await request.json()
    update = Update(**update_data)
    await dp.process_update(update)
    return {"status": "ok"}


async def tbank_webhook_handler(request: Request):
    data = await request.json()
    
    # === ДИАГНОСТИКА ===
    print("=== WEBHOOK RECEIVED ===")
    print(f"Full data: {data}")
    order_id = data.get("OrderId")
    status = data.get("Status")
    print(f"OrderId: {order_id}, Status: {status}")

    if not verify_webhook_signature(data):
        print("❌ Signature verification FAILED")
        return {"status": "unauthorized"}, 401

    print("✅ Signature OK")

    if order_id and status == "CONFIRMED":
        from .database import get_invoice_by_payment_id, update_invoice_status

        print(f"Searching for invoice with payment_id: {order_id}")
        invoice = get_invoice_by_payment_id(order_id)
        print(f"Invoice found: {invoice}")

        if invoice and invoice["status"] != "paid":
            print("Status is not paid, updating...")
            updated = update_invoice_status(order_id, "paid")
            print(f"Updated invoice: {updated}")

            if updated:
                print("Sending notifications to managers...")
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
                        print(f"Notification sent to manager {manager_id}")
                    except Exception as e:
                        print(f"Failed to send to manager {manager_id}: {e}")

                if updated.get("client_tg_id"):
                    try:
                        order_number = updated.get('order_number') or 'не указан'
                        await bot.send_message(
                            updated["client_tg_id"],
                            f"✅ **Оплата по заявке №{order_number} поступила на счёт.**\n\n"
                            f"Ожидайте подтверждения на сайте в рабочее время и отправки посылки.\n\n"
                            f"Спасибо за доверие!"
                        )
                        print(f"Notification sent to client {updated['client_tg_id']}")
                    except Exception as e:
                        print(f"Failed to send to client: {e}")
            else:
                print("Invoice not found or already paid")
        else:
            print("Invoice not found or already paid")
    else:
        print("OrderId missing or status not CONFIRMED")

    return {"status": "ok"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    await bot.set_webhook(TELEGRAM_WEBHOOK_URL)
    print(f"✅ Webhook установлен: {TELEGRAM_WEBHOOK_URL}")
    yield
    await bot.delete_webhook()
    await bot.session.close()


app = FastAPI(lifespan=lifespan, title="Payment Bot for Bothost")

app.post("/webhook/telegram")(telegram_webhook_handler)
app.post("/webhook/tbank")(tbank_webhook_handler)

@app.get("/ping")
async def ping():
    return {"status": "ok", "message": "Server is alive"}

if __name__ == "__main__":
    import os
    port = int(os.getenv("PORT", 3000))  # Платформа сама подставит PORT
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",  # Обязательно 0.0.0.0
        port=port,
        reload=True
    )