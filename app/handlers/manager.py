from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command

from ..config import MANAGER_IDS, BASE_URL
from ..database import (
    get_invoice_by_payment_id, update_invoice_status, 
    get_invoice_by_order_number, get_all_invoices
)
from ..services.tbank import create_payment, check_payment_status
from ..keyboards import main_menu, manager_back_keyboard

router = Router(name="manager")


# === Фильтр для проверки прав менеджера ===
async def is_manager(user_id: int) -> bool:
    return user_id in MANAGER_IDS


# === Команды ===
@router.message(Command("link"))
async def manager_link_command(message: Message):
    """Команда /link 1500 Комментарий"""
    if not await is_manager(message.from_user.id):
        await message.answer("⛔️ **Нет доступа**\n\nЭта команда только для менеджеров.")
        return
    
    try:
        parts = message.text.split()
        if len(parts) < 2:
            raise ValueError
        amount_rub = int(parts[1].replace(" ", "").replace(",", ""))
        comment = " ".join(parts[2:]) if len(parts) > 2 else "Ручная ссылка"
    except:
        await message.answer(
            "❌ **Неверный формат**\n\n"
            "Использование: `/link [сумма] [комментарий]`\n"
            "Пример: `/link 1500 Оплата заказа A-123`"
        )
        return
    
    # Генерируем уникальный OrderId
    from datetime import datetime
    payment_id = f"MANUAL_{int(datetime.now().timestamp())}"
    
    # Создаем платеж в T‑Банк
    try:
        payment_result = create_payment(
            amount=amount_rub * 100,  # В копейках
            order_id=payment_id,
            description=comment[:100],
            success_url=f"https://t.me/{message.bot.username}",
            fail_url=f"https://t.me/{message.bot.username}"
        )
    except Exception as e:
        await message.answer(
            f"❌ **Ошибка при создании платежа**\n\n{str(e)}"
        )
        return
    
    # Сохраняем в БД
    from ..database import save_invoice
    invoice_id = save_invoice({
        "payment_id": payment_id,
        "amount": amount_rub * 100,
        "amount_rub": amount_rub,
        "order_number": None,
        "delivery_address": None,
        "client_tg_id": None,
        "client_username": None,
        "creator_tg_id": message.from_user.id,
        "description": comment[:100]
    })
    
    await message.answer(
        f"🔗 **Ссылка на {amount_rub:,} ₽ готова**\n\n"
        f"📝 **Комментарий:** {comment}\n"
        f"🆔 **ID платежа:** {payment_id[:16]}...\n\n"
        f"Ссылка для оплаты:\n"
        f"{payment_result['payment_url']}\n\n"
        f"⬇️ Вы можете скопировать ссылку и отправить клиенту."
    )


@router.message(Command("check"))
async def manager_check_command(message: Message):
    """Команда /check ORDER_123 или payment_id"""
    if not await is_manager(message.from_user.id):
        await message.answer("⛔️ **Нет доступа**")
        return
    
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer(
            "❌ **Неверный формат**\n\n"
            "Использование: `/check [ID_платежа или номер_заказа]`"
        )
        return
    
    identifier = parts[1].strip()
    
    # Ищем в БД
    invoice = get_invoice_by_payment_id(identifier)
    if not invoice:
        # Попробуем найти по номеру заказа
        invoice = get_invoice_by_order_number(identifier)
    
    if not invoice:
        await message.answer(
            f"❌ **Платеж не найден**\n\n"
            f"Запись с идентификатором `{identifier}` не найдена в базе данных."
        )
        return
    
    # Проверяем статус в T‑Банк
    status = check_payment_status(invoice["payment_id"])
    
    # Если T‑Банк говорит, что оплачено, обновляем БД
    status_map = {
        "CONFIRMED": "paid",
        "REFUNDED": "refunded",
        "CANCELED": "canceled"
    }
    new_status = status_map.get(status, "unknown")
    
    if new_status == "paid" and invoice["status"] != "paid":
        update_invoice_status(invoice["payment_id"], "paid")
        invoice = get_invoice_by_payment_id(invoice["payment_id"])
    
    # Формируем ответ
    answer = f"📊 **Информация о платеже**\n\n"
    answer += f"🆔 **ID платежа:** {invoice['payment_id'][:20]}...\n"
    answer += f"💰 **Сумма:** {invoice['amount_rub']:,} ₽\n"
    answer += f"📦 **Заказ:** {invoice['order_number'] or 'Не указан (ручная ссылка)'}\n"
    answer += f"📍 **Адрес:** {invoice['delivery_address'] or 'Не указан'}\n"
    answer += f"📅 **Создан:** {invoice['created_at']}\n"
    answer += f"🔘 **Статус в T‑Банк:** {status}\n"
    answer += f"🔘 **Статус в БД:** {invoice['status'].upper()}\n"
    
    if invoice.get("client_tg_id"):
        answer += f"\n👤 **Клиент:** @{invoice.get('client_username', 'Неизвестно')} (ID: {invoice['client_tg_id']})"
    
    await message.answer(answer)


# === Callback-запросы ===
@router.callback_query(F.data == "manager_link")
async def manager_link_start(callback: CallbackQuery):
    """Менеджер начинает создание ссылки"""
    if not await is_manager(callback.from_user.id):
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return
    
    await callback.message.edit_text(
        "🔗 **Создание ссылки для оплаты**\n\n"
        "Введите сумму в рублях и комментарий через пробел.\n"
        "Например: `1500 Оплата заказа для Ивана`\n\n"
        "Или используйте команду `/link 1500 Комментарий`",
        reply_markup=manager_back_keyboard()
    )
    await callback.answer()


@router.callback_query(F.data == "manager_check")
async def manager_check_start(callback: CallbackQuery):
    """Менеджер проверяет оплату"""
    if not await is_manager(callback.from_user.id):
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return
    
    await callback.message.edit_text(
        "🔍 **Проверка статуса оплаты**\n\n"
        "Введите ID платежа или номер заказа.\n"
        "Например: `ORDER_A-123_1705526400` или `A-12345`\n\n"
        "Или используйте команду `/check [ID_платежа]`",
        reply_markup=manager_back_keyboard()
    )
    await callback.answer()


@router.callback_query(F.data == "manager_back")
async def manager_back(callback: CallbackQuery):
    """Возврат в главное меню"""
    is_manager = callback.from_user.id in MANAGER_IDS
    await callback.message.edit_text(
        "👋 **Главное меню**\n\n"
        "Выберите действие:",
        reply_markup=main_menu(is_manager)
    )
    await callback.answer()


@router.message()
async def handle_unknown_message(message: Message):
    """Обработка неизвестных сообщений"""
    # Если сообщение пришло в режиме диалога от менеджера - игнорируем
    # (менеджеры используют команды)
    pass