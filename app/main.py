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
from .database import init_db, get_all_invoices
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
    if not paid_at_str:
        return 'неизвестно'
    try:
        utc_time = datetime.strptime(paid_at_str, '%Y-%m-%d %H:%M:%S')
        msk_time = utc_time + timedelta(hours=3)
        return msk_time.strftime('%Y-%m-%d %H:%M:%S')
    except:
        return paid_at_str


def clean_group_payment_id(payment_id: str) -> str:
    if not payment_id:
        return payment_id
    while payment_id.startswith(("group_", "GROUP_")):
        payment_id = payment_id[6:]
    return payment_id


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

            print(f"🔍 Ищем инвойс по payment_id = '{order_id}'")
            invoice = get_invoice_by_payment_id(str(order_id))
            print(f"📄 invoice: {invoice}")

            if not invoice:
                clean_id = str(order_id).strip()
                if clean_id != order_id:
                    print(f"🔍 Пробуем без пробелов: '{clean_id}'")
                    invoice = get_invoice_by_payment_id(clean_id)
                    print(f"📄 invoice после очистки: {invoice}")

            if not invoice:
                print("⚠️ Инвойс не найден. Список всех payment_id в БД (первые 50):")
                all_invoices = get_all_invoices(limit=50)
                ids = [inv['payment_id'] for inv in all_invoices]
                print(f"  {', '.join(ids)}")

            if invoice:
                print(f"🔘 Статус в БД: {invoice['status']}")
                if invoice["status"] != "paid":
                    print("🔄 Обновляем статус на paid...")
                    update_invoice_status(str(order_id), "paid")
                    print("✅ Статус обновлён, получаем обновлённую запись...")
                    updated = get_invoice_by_payment_id(str(order_id))
                    print(f"📄 updated: {updated}")
                    if updated:
                        print("📨 Отправляем уведомления менеджерам...")
                        builder = InlineKeyboardBuilder()
                        is_group = updated.get("is_group", 0)
                        order_number = updated.get("order_number")

                        # === Формируем кнопки в зависимости от типа платежа ===
                        if is_group:
                            payment_id = updated.get("payment_id")
                            payment_id = clean_group_payment_id(payment_id)
                            builder.button(text="📦 Отправить трек", callback_data=f"track_group_{payment_id}")
                            builder.button(text="📢 Уведомить", callback_data=f"notify_group_{payment_id}")
                        elif order_number is not None:
                            # Одиночный заказ (создан клиентом)
                            builder.button(text="📦 Отправить трек", callback_data=f"track_{order_number}")
                            builder.button(text="📢 Уведомить", callback_data=f"notify_{order_number}")
                        else:
                            # Ручная ссылка – только главное меню
                            pass  # кнопки не добавляем

                        # Кнопка «Главное меню» добавляется всегда
                        builder.button(text="🏠 Главное меню", callback_data="manager_back")
                        builder.adjust(2, 1)  # если есть две кнопки, то в ряд по две, потом одна

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
                                f"🆔 <b>Payment ID:</b> {updated['payment_id']}\n\n"
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
                                f"🆔 <b>Payment ID:</b> {updated['payment_id']}\n\n"
                                f"⚡️ Готовьте к отправке!"
                            )
                        else:
                            # Ручная ссылка – упрощённое сообщение без кнопок трека/уведомления
                            message_text = (
                                f"<b>✅ ДЕНЬГИ ПОСТУПИЛИ!</b>\n\n"
                                f"📦 <b>Заказ:</b> Ручная ссылка\n"
                                f"📝 <b>Комментарий:</b> {updated.get('description') or 'Не указан'}\n"
                                f"💰 <b>Сумма:</b> {updated['amount_rub']:,} ₽\n"
                                f"📍 <b>Адрес:</b> {updated.get('delivery_address') or 'Не указан'}\n"
                                f"👤 <b>ФИО:</b> {updated.get('client_name') or 'Не указано'}\n"
                                f"📱 <b>Телефон:</b> {updated.get('client_phone') or 'Не указан'}\n"
                                f"{initiator_text}\n" if initiator_text else ""
                                f"🕒 <b>Время оплаты:</b> {paid_at_display}\n"
                                f"🆔 <b>Payment ID:</b> {updated['payment_id']}\n\n"
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
                            except Exception as e:
                                print(f"❌ Не удалось отправить менеджеру {manager_id}: {e}")

                        # === Уведомление клиенту (без изменений) ===
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

                                client_kb = InlineKeyboardBuilder()
                                client_kb.button(text="🏠 Главное меню", callback_data="main_menu")
                                client_kb.button(text="🔄 Оформить новый заказ", callback_data="client_order")
                                client_kb.adjust(1)

                                await bot.send_message(
                                    updated["client_tg_id"],
                                    client_message,
                                    parse_mode="HTML",
                                    reply_markup=client_kb.as_markup()
                                )
                            except Exception as e:
                                print(f"❌ Не удалось отправить клиенту: {e}")
                    else:
                        error_msg = f"⚠️ Не удалось получить обновлённый инвойс для OrderId {order_id} после обновления статуса."
                        print(error_msg)
                        for manager_id in MANAGER_IDS:
                            try:
                                await bot.send_message(manager_id, error_msg)
                            except Exception:
                                pass
                else:
                    print(f"ℹ️ Статус уже paid, пропускаем.")
            else:
                print(f"❌ Инвойс с payment_id = '{order_id}' не найден. (только лог)")
        else:
            print(f"ℹ️ Статус {status} или отсутствует order_id, игнорируем.")

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