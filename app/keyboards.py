from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder


def main_menu(is_manager: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    # Для клиентов – большая кнопка "Начать оформление"
    builder.button(text="🚀 Начать оформление", callback_data="client_order")
    
    if is_manager:
        builder.button(text="🔗 Создать ссылку", callback_data="manager_link")
        builder.button(text="🔍 Проверить оплату", callback_data="manager_check")
        builder.button(text="📦 Отправить трек", callback_data="manager_track_start")
        builder.button(text="📢 Уведомить клиента", callback_data="manager_notify_start")
    
    builder.button(text="❓ Помощь", callback_data="help")
    builder.adjust(1)
    return builder.as_markup()


def order_amount_keyboard(amounts: list) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for amt in amounts:
        builder.button(text=f"{amt} ₽", callback_data=f"amount_{amt}")
    builder.button(text="✏️ Своя сумма", callback_data="amount_custom")
    builder.adjust(2)
    return builder.as_markup()


def payment_link_keyboard(payment_url: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="💳 Перейти к оплате", url=payment_url)
    builder.button(text="✅ Я оплатил", callback_data="payment_confirmed")
    builder.adjust(1)
    return builder.as_markup()


def manager_back_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️ Назад", callback_data="manager_back")
    return builder.as_markup()