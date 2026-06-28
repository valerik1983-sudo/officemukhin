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


# === Инициализация Aiogram ===
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Подключаем роутеры
dp.include_router(client.router)
dp.include_router(manager.router)


# === Обработчики вебхуков ===
async def telegram_webhook_handler(request: Request):
    """Обработчик вебхука от Telegram"""
    update_data = await request.json()
    update = Update(**update_data)
    await dp.process_update(update)
    return {"status": "ok"}


async def tbank_webhook_handler(request: Request):
    """Обработчик уведомлений от T‑Банк"""
    data = await request.json()
    
    # Проверяем подпись для безопасности
    if not verify_webhook_signature(data):
        return {"status": "unauthorized"}, 401
    
    order_id = data.get("OrderId")
    status = data.get("Status")
    
    if order_id and status == "CONFIRMED":
        from .database import get_invoice_by_payment_id, update_invoice_status
        
        # Ищем заказ в БД
        invoice = get_invoice_by_payment_id(order_id)
        
        if invoice and invoice["status"] != "paid":
            # Обновляем статус
            updated = update_invoice_status(order_id, "paid")
            
            if updated:
                # Уведомляем менеджеров
                for manager_id in MANAGER_IDS:
                    try:
                        await bot.send_message(
                            manager_id,
                            f"✅ **ДЕНЬГИ ПОСТУПИЛИ!**\n\n"
                            f"📦 **Заказ:** {updated.get('order_number') or 'Ручная ссылка'}\n"
                            f"💰 **Сумма:** {updated['amount_rub']:,} ₽\n"
                            f"📍 **Адрес:** {updated.get('delivery_address') or 'Не указан'}\n"
                            f"🆔 **Payment ID:** {order_id[:16]}...\n\n"
                            f"⚡️ Готовьте к отправке!"
                        )
                    except Exception:
                        pass
                
                # Уведомляем клиента
                if updated.get("client_tg_id"):
                    try:
                        await bot.send_message(
                            updated["client_tg_id"],
                            f"✅ **Платеж получен!**\n\n"
                            f"Спасибо за оплату в размере {updated['amount_rub']:,} ₽.\n"
                            f"Мы скоро свяжемся с вами по поводу доставки."
                        )
                    except Exception:
                        pass
    
    return {"status": "ok"}


# === FastAPI приложение ===
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Жизненный цикл приложения"""
    # Инициализируем БД при старте
    init_db()
    
    # Устанавливаем вебхук для Telegram
    await bot.set_webhook(TELEGRAM_WEBHOOK_URL)
    print(f"✅ Webhook установлен: {TELEGRAM_WEBHOOK_URL}")
    
    yield
    
    # При завершении удаляем вебхук
    await bot.delete_webhook()
    await bot.session.close()


app = FastAPI(lifespan=lifespan, title="Payment Bot for Bothost")

# Регистрируем эндпоинты
app.post("/webhook/telegram")(telegram_webhook_handler)
app.post("/webhook/tbank")(tbank_webhook_handler)


# === Точка входа ===
if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True  # Для разработки, на проде выключить
    )