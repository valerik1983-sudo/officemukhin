from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from datetime import datetime
from aiogram.utils.keyboard import InlineKeyboardBuilder
import json

from ..config import MANAGER_IDS
from ..database import save_invoice, get_invoice_by_payment_id, update_invoice_status
from ..services.tbank import create_payment, get_qr, check_payment_status
from ..keyboards import main_menu, order_amount_keyboard, payment_link_keyboard

router = Router(name="client")


class OrderForm(StatesGroup):
    waiting_order_number = State()
    waiting_amount = State()
    waiting_client_name = State()
    waiting_more_orders = State()
    waiting_order_number_group = State()
    waiting_amount_group = State()
    waiting_client_name_group = State()
    waiting_recipient_name = State()
    waiting_recipient_address = State()
    waiting_recipient_phone = State()


@router.message(Command("start"))
async def cmd_start(message: Message):
    is_manager = message.from_user.id in MANAGER_IDS
    await message.answer(
        "👋 <b>Добро пожаловать!</b>\n\n"
        "Я помогу вам оплатить один или несколько заказов.\n"
        "Нажмите кнопку <b>«Начать оформление»</b> ниже.\n\n"
        "Сначала вы добавите все заказы, а затем укажете общие данные для отправки.",
        parse_mode="HTML",
        reply_markup=main_menu(is_manager)
    )


@router.message(Command("menu"))
async def cmd_menu(message: Message):
    is_manager = message.from_user.id in MANAGER_IDS
    await message.answer(
        "👋 <b>Главное меню</b>\n\nВыберите действие:",
        parse_mode="HTML",
        reply_markup=main_menu(is_manager)
    )


@router.message(Command("help"))
async def cmd_help(message: Message):
    is_manager = message.from_user.id in MANAGER_IDS
    await message.answer(
        "❓ <b>Помощь</b>\n\n"
        "<b>Для клиентов:</b>\n"
        "1. Нажмите «Оформить заказ»\n"
        "2. Введите номер заказа\n"
        "3. Введите сумму (кратную 1200) или 0, если заказ оплачен бонусами\n"
        "4. Введите ФИО заказчика\n"
        "5. Добавьте ещё заказы или завершите\n"
        "6. Укажите общие данные для отправки\n"
        "7. Перейдите по ссылке для оплаты\n\n"
        "<b>Для менеджеров:</b>\n"
        "• «Создать ссылку» — сгенерировать ссылку для оплаты\n"
        "• «Проверить оплату» — узнать статус платежа",
        parse_mode="HTML",
        reply_markup=main_menu(is_manager)
    )


@router.callback_query(F.data == "help")
async def callback_help(callback: CallbackQuery):
    is_manager = callback.from_user.id in MANAGER_IDS
    # При помощи "Помощь" мы редактируем текущее сообщение, это нормально (диалог)
    await callback.message.edit_text(
        "❓ <b>Помощь</b>\n\n"
        "<b>Для клиентов:</b>\n"
        "1. Нажмите «Оформить заказ»\n"
        "2. Введите номер заказа\n"
        "3. Введите сумму (кратную 1200) или 0, если заказ оплачен бонусами\n"
        "4. Введите ФИО заказчика\n"
        "5. Добавьте ещё заказы или завершите\n"
        "6. Укажите общие данные для отправки\n"
        "7. Перейдите по ссылке для оплаты\n\n"
        "<b>Для менеджеров:</b>\n"
        "• «Создать ссылку» — сгенерировать ссылку для оплаты\n"
        "• «Проверить оплату» — узнать статус платежа",
        parse_mode="HTML",
        reply_markup=main_menu(is_manager)
    )
    await callback.answer()


@router.callback_query(F.data == "client_order")
async def start_order(callback: CallbackQuery, state: FSMContext):
    # Отправляем новое сообщение, не заменяя старое
    await callback.message.answer(
        "📝 <b>Введите номер вашего заказа</b>\n\n"
        "Например: 123456789\n\n"
        "Вы можете найти номер на странице с подтверждением заказа.",
        parse_mode="HTML",
        reply_markup=None
    )
    await state.set_state(OrderForm.waiting_order_number)
    await callback.answer()


@router.message(OrderForm.waiting_order_number)
async def process_order_number(message: Message, state: FSMContext):
    order = message.text.strip()
    if not order.isdigit():
        await message.answer(
            "❌ <b>Номер заказа должен содержать только цифры.</b>\n\n"
            "Пожалуйста, введите номер заказа ещё раз (например: 123456789).",
            parse_mode="HTML",
            reply_markup=main_menu(is_manager=False)
        )
        return

    await state.update_data(temp_order_number=order)
    await message.answer(
        "💰 <b>Введите сумму к оплате (в рублях)</b>\n\n"
        "Сумма должна делиться на 1200 без остатка.\n"
        "Если заказ уже оплачен бонусами, введите <b>0</b>.\n",
        parse_mode="HTML",
    )
    await state.set_state(OrderForm.waiting_amount)


# ... (остальные обработчики без изменений, они уже есть в вашем файле) ...

# Только изменить main_menu_callback в конце:
@router.callback_query(F.data == "main_menu")
async def main_menu_callback(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    is_manager = callback.from_user.id in MANAGER_IDS
    # Не удаляем клавиатуру у старого сообщения, отправляем новое
    await callback.message.answer(
        "👋 <b>Главное меню</b>\n\nВыберите действие:",
        parse_mode="HTML",
        reply_markup=main_menu(is_manager)
    )
    await callback.answer()