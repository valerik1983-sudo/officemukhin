import hashlib
import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from fastapi import FastAPI, Request
from aiogram import Bot, Dispatcher
from aiogram.types import Update
import uvicorn

from .config import (
    BOT_TOKEN,
    TELEGRAM_WEBHOOK_URL,
    TBANK_WEBHOOK_URL,
    MANAGER_IDS,
    TBANK_SECRET_KEY
)
from .database import init_db
from .handlers import client, manager
from .services.tbank import verify_webhook_signature
from aiogram.utils.keyboard import InlineKeyboardBuilder

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
dp.include_router(manager.router)
dp.include_router(client.router)


async def telegram_webhook_handler(request: Request):
    update_data = await request.json()
    update = Update(**update_data)
    await dp.feed_update(bot, update)
    return {"status": "ok"}


def format_paid_at(paid_at_str):
    """Преобразует UTC время из БД в московское (UTC+3) и форматирует"""
    if not paid_at_str:
        return 'неизвестно'
    try:
        utc_time = datetime.strptime(paid_at_str, '%Y-%m-%d %H:%M:%S')
        msk_time = utc_time + timedelta(hours=3)
        return msk_time.strftime('%Y-%m-%d %H:%M:%S')
    except:
        return paid_at_str


async def tbank_webhook_handler(request: Request):
    data = await request.json()
    print("=== WEBHOOK RECEIVED ===")
    print(data)

    try:
        if not verify_webhook_signature(data):
            print("❌ Подпись не прошла проверку")
            return {"status": "unauthorized"}, 401

        order_id = data.get("OrderId")
        status = data.get("Status")

        if order_id and status == "CONFIRMED":
            from .database import get_invoice_by_payment_id, update_invoice_status

            invoice = get_invoice_by_payment_id(str(order_id))
            if invoice and invoice["status"] != "paid":
                updated = update_invoice_status(str(order_id), "paid")
                if updated:
                    # === Кнопки для менеджера ===
                    builder = InlineKeyboardBuilder()
                    builder.button(text="📦 Отправить трек", callback_data=f"track_{updated['order_number']}")
                    builder.button(text="📢 Уведомить", callback_data=f"notify_{updated['order_number']}")
                    builder.button(text="🏠 Главное меню", callback_data="manager_back")
                    builder.adjust(2, 1)

                    is_group = updated.get("is_group", 0)
                    order_number = updated.get("order_number")
                    initiator_tg_id = updated.get("client_tg_id")
                    initiator_username = updated.get("client_username")
                    
                    initiator_text = ""
                    if initiator_tg_id:
                        if initiator_username:
                            initiator_text = f"👤 <b>Инициатор:</b> @{initiator_username} (ID: {initiator_tg_id})\n🔗 <a href='tg://user?id={initiator_tg_id}'>Написать инициатору</a>"
                        else:
                            initiator_text = f"👤 <b>Инициатор:</b> ID: {initiator_tg_id}\n🔗 <a href='tg://user?id={initiator_tg_id}'>Написать инициатору</a>"

                    paid_at_display = format_paid_at(updated.get('paid_at'))

                    if is_group:
                        orders_data = updated.get("orders_data")
                        try:
                            orders_list = json.loads(orders_data) if orders_data else []
                        except:
                            orders_list = []

                        orders_text = "\n".join([
                            f"• Заказ {o.get('order_number', '?')} – {o.get('amount_rub', 0):,} ₽ (ФИО: {o.get('client_name', 'Не указан')})" +
                            (" <i>(оплачен с бонусного кошелька)</i>" if o.get('is_paid_by_bonus') else "")
                            for o in orders_list
                        ])

                        message_text = (
                            f"<b>✅ ДЕНЬГИ ПОСТУПИЛИ (ГРУППОВОЙ ПЛАТЁЖ)!</b>\n\n"
                            f"📦 <b>Заказы в оплате:</b>\n{orders_text}\n\n"
                            f"💰 <b>Общая сумма:</b> {updated['amount_rub']:,} ₽\n"
                            f"📍 <b>Адрес доставки:</b> {updated.get('delivery_address') or 'Не указан'}\n"
                            f"👤 <b>Получатель:</b> {updated.get('client_name') or 'Не указан'}\n"
                            f"📱 <b>Телефон:</b> {updated.get('client_phone') or 'Не указан'}\n"
                            f"{initiator_text}\n" if initiator_text else ""
                            f"🕒 <b>Время оплаты:</b> {paid_at_display}\n"
                            f"🆔 <b>Payment ID:</b> {order_id[:16]}...\n\n"
                            f"⚡️ Готовьте к отправке!"
                        )
                    elif order_number is not None:
                        message_text = (
                            f"<b>✅ ДЕНЬГИ ПОСТУПИЛИ!</b>\n\n"
                            f"📦 <b>Заказ:</b> {order_number}\n"
                            f"📝 <b>Комментарий:</b> {updated.get('description') or 'Не указан'}\n"
                            f"💰 <b>Сумма:</b> {updated['amount_rub']:,} ₽\n"
                            f"📍 <b>Адрес:</b> {updated.get('delivery_address') or 'Не указан'}\n"
                            f"👤 <b>ФИО получателя:</b> {updated.get('client_name') or 'Не указано'}\n"
                            f"📱 <b>Телефон:</b> {updated.get('client_phone') or 'Не указан'}\n"
                            f"{initiator_text}\n" if initiator_text else ""
                            f"🕒 <b>Время оплаты:</b> {paid_at_display}\n"
                            f"🆔 <b>Payment ID:</b> {order_id[:16]}...\n\n"
                            f"⚡️ Готовьте к отправке!"
                        )
                    else:
                        message_text = (
                            f"<b>✅ ДЕНЬГИ ПОСТУПИЛИ!</b>\n\n"
                            f"📦 <b>Заказ:</b> Ручная ссылка\n"
                            f"📝 <b>Комментарий:</b> {updated.get('description') or 'Не указан'}\n"
                            f"💰 <b>Сумма:</b> {updated['amount_rub']:,} ₽\n"
                            f"📍 <b>Адрес:</b> {updated.get('delivery_address') or 'Не указан'}\n"
                            f"👤 <b>ФИО:</b> {updated.get('client_name') or 'Не указано'}\n"
                            f"📱 <b>Телефон:</b> {updated.get('client_phone') or 'Не указан'}\n"
                            f"🕒 <b>Время оплаты:</b> {paid_at_display}\n"
                            f"🆔 <b>Payment ID:</b> {order_id[:16]}...\n\n"
                            f"⚡️ Готовьте к отправке!"
                        )

                    for manager_id in MANAGER_IDS:
                        try:
                            await bot.send_message(
                                manager_id,
                                message_text,
                                parse_mode="HTML",
                                reply_markup=builder.as_markup()
                            )
                        except Exception:
                            pass

                    # === Уведомление клиенту (инициатору) с номером заказа и суммой ===
                    if updated.get("client_tg_id"):
                        try:
                            amount_rub = updated['amount_rub']
                            if not is_group and order_number is not None:
                                client_message = (
                                    f"✅ Оплата по заявке №<b>{order_number}</b> в размере <b>{amount_rub:,}</b> рублей поступила на счёт.\n\n"
                                    f"Ожидайте подтверждения на сайте в рабочее время и отправки посылки.\n\n"
                                    f"Спасибо за доверие!"
                                )
                            elif is_group:
                                count = len(orders_list) if 'orders_list' in locals() else 0
                                client_message = (
                                    f"✅ Оплата по заявке из <b>{count}</b> заказов в размере <b>{amount_rub:,}</b> рублей поступила на счёт.\n\n"
                                    f"Ожидайте подтверждения на сайте в рабочее время и отправки посылки.\n\n"
                                    f"Спасибо за доверие!"
                                )
                            else:
                                comment = updated.get("description", "ручная ссылка")
                                client_message = (
                                    f"✅ Оплата по заявке на <b>{comment}</b> в размере <b>{amount_rub:,}</b> рублей поступила на счёт.\n\n"
                                    f"Ожидайте подтверждения на сайте в рабочее время и отправки посылки.\n\n"
                                    f"Спасибо за доверие!"
                                )
                            await bot.send_message(
                                updated["client_tg_id"],
                                client_message,
                                parse_mode="HTML"
                            )
                        except Exception:
                            pass
                else:
                    for manager_id in MANAGER_IDS:
                        try:
                            await bot.send_message(
                                manager_id,
                                f"⚠️ Не удалось обновить статус заказа {order_id} в БД."
                            )
                        except Exception:
                            pass
            else:
                # Если заказ не найден – для новых платежей это критично
                if invoice is None:
                    # === ЗДЕСЬ МОЖНО ВРЕМЕННО ОТКЛЮЧИТЬ УВЕДОМЛЕНИЯ (закомментировать блок ниже) ===
                    # for manager_id in MANAGER_IDS:
                    #     try:
                    #         await bot.send_message(
                    #             manager_id,
                    #             f"❌ Заказ с payment_id = {order_id} не найден в БД.\n"
                    #             f"Платёж подтверждён, но ссылка не была создана через бота.\n"
                    #             f"Проверьте вручную в личном кабинете T‑Банк."
                    #         )
                    #     except Exception:
                    #         pass
                    pass
                # Если заказ уже оплачен – просто игнорируем
        else:
            # Игнорируем все не-CONFIRMED вебхуки (AUTHORIZED и другие)
            pass

    except Exception as e:
        error_msg = (
            f"❌ КРИТИЧЕСКАЯ ОШИБКА при обработке вебхука:\n\n"
            f"Текст ошибки: {str(e)}\n\n"
            f"Данные вебхука:\n{json.dumps(data, indent=2)}"
        )
        for manager_id in MANAGER_IDS:
            try:
                await bot.send_message(manager_id, error_msg)
            except Exception:
                pass
        print(f"❌ Ошибка: {e}")

    return {"status": "ok"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    print("🚀 Бот запущен в режиме вебхука.")
    print(f"🌐 Вебхук для Telegram: {TELEGRAM_WEBHOOK_URL}")
    print(f"💳 Вебхук для T‑Банк: {TBANK_WEBHOOK_URL}")
    yield
    await bot.session.close()


app = FastAPI(lifespan=lifespan, title="Payment Bot for Bothost")

app.post("/webhook/telegram")(telegram_webhook_handler)
app.post("/webhook/tbank")(tbank_webhook_handler)

@app.get("/ping")
async def ping():
    return {"status": "ok", "message": "Server is alive"}

if __name__ == "__main__":
    port = int(os.getenv("PORT", 3000))
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=port,
        reload=False
    )