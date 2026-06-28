from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from datetime import datetime

from ..config import MANAGER_IDS, BASE_URL
from ..database import save_invoice, get_invoice_by_payment_id, update_invoice_status
from ..services.tbank import create_payment, check_payment_status
from ..keyboards import (
    main_menu, order_amount_keyboard, payment_link_keyboard,
    manager_back_keyboard
)


router = Router(name="client")


# === FSM (Finite State Machine) ===
class OrderForm(StatesGroup):
    """Состояния для оформления заказа"""
    waiting_order_number = State()
    waiting_amount = State()
    waiting_address = State()


# === Команды ===
@router.message(Command("start"))
async def cmd_start(message: Message):
    """Стартовая команда"""
    is_manager = message.from_user.id in MANAGER_IDS
    await message.answer(
        "👋 Добро пожаловать в бот для оплаты заказов!\n\n"
        "Я помогу вам быстро и безопасно оплатить заказ.\n"
        "Просто нажмите кнопку ниже, чтобы начать.",
        reply_markup=main_menu(is_manager)
    )


@router.message(Command("help"))
async def cmd_help(message: Message):
    """Помощь"""
    is_manager = message.from_user.id in MANAGER_IDS
    await message.answer(
        "❓ **Помощь**\n\n"
        "**Для клиентов:**\n"
        "1. Нажмите «Оформить заказ»\n"
        "2. Введите номер заказа\n"
        "3. Введите сумму\n"
        "4. Введите адрес доставки\n"
        "5. Перейдите по ссылке для оплаты\n\n"
        "**Для менеджеров:**\n"
        "• «Создать ссылку» — сгенерировать ссылку для оплаты\n"
        "• «Проверить оплату» — узнать статус платежа",
        reply_markup=main_menu(is_manager)
    )


# === Callback-запросы ===
@router.callback_query(F.data == "help")
async def callback_help(callback: CallbackQuery):
    """Помощь через кнопку"""
    is_manager = callback.from_user.id in MANAGER_IDS
    await callback.message.edit_text(
        "❓ **Помощь**\n\n"
        "**Для клиентов:**\n"
        "1. Нажмите «Оформить заказ»\n"
        "2. Введите номер заказа\n"
        "3. Введите сумму\n"
        "4. Введите адрес доставки\n"
        "5. Перейдите по ссылке для оплаты\n\n"
        "**Для менеджеров:**\n"
        "• «Создать ссылку» — сгенерировать ссылку для оплаты\n"
        "• «Проверить оплату» — узнать статус платежа",
        reply_markup=main_menu(is_manager)
    )
    await callback.answer()


@router.callback_query(F.data == "client_order")
async def start_order(callback: CallbackQuery, state: FSMContext):
    """Начинаем оформление заказа"""
    await callback.message.edit_text(
        "📝 **Введите номер вашего заказа**\n\n"
        "Например: A-12345 или 123456\n\n"
        "Вы можете найти номер в письме с подтверждением заказа.",
        reply_markup=None
    )
    await state.set_state(OrderForm.waiting_order_number)
    await callback.answer()


@router.message(OrderForm.waiting_order_number)
async def process_order_number(message: Message, state: FSMContext):
    """Получаем номер заказа"""
    await state.update_data(order_number=message.text.strip())
    await message.answer(
        "💰 **Введите сумму к оплате (в рублях)**\n\n"
        "Или выберите один из вариантов ниже:",
        reply_markup=order_amount_keyboard([1000, 2000, 5000, 10000, 20000])
    )
    await state.set_state(OrderForm.waiting_amount)


@router.callback_query(F.data.startswith("amount_"), OrderForm.waiting_amount)
async def process_amount_button(callback: CallbackQuery, state: FSMContext):
    """Выбор суммы из кнопок"""
    amount = int(callback.data.split("_")[1])
    await state.update_data(amount=amount)
    await callback.message.edit_text(
        f"✅ **Сумма: {amount:,} ₽**\n\n"
        f"📍 **Введите адрес доставки:**\n"
        f"Например: г. Москва, ул. Тверская, д.5, кв.12",
        reply_markup=None
    )
    await state.set_state(OrderForm.waiting_address)
    await callback.answer()


@router.message(OrderForm.waiting_amount)
async def process_amount_manual(message: Message, state: FSMContext):
    """Ручной ввод суммы"""
    try:
        amount = int(message.text.replace(" ", "").replace(",", ""))
        if amount < 1:
            raise ValueError
        await state.update_data(amount=amount)
        await message.answer(
            f"✅ **Сумма: {amount:,} ₽**\n\n"
            f"📍 **Введите адрес доставки:**\n"
            f"Например: г. Москва, ул. Тверская, д.5, кв.12",
            reply_markup=None
        )
        await state.set_state(OrderForm.waiting_address)
    except ValueError:
        await message.answer(
            "❌ **Ошибка!**\n\n"
            "Пожалуйста, введите корректное число.\n"
            "Например: 1500, 2500, 5000"
        )


@router.message(OrderForm.waiting_address)
async def process_address(message: Message, state: FSMContext):
    """Финальный шаг — создаем платеж и отправляем ссылку"""
    data = await state.get_data()
    order_number = data["order_number"]
    amount_rub = data["amount"]
    address = message.text.strip()
    client_id = message.from_user.id
    client_username = message.from_user.username or ""

    # Конвертируем в копейки для T‑Банк
    amount_kopecks = amount_rub * 100

    # Генерируем уникальный OrderId (T‑Банк требует уникальность)
    payment_id = f"ORDER_{order_number}_{int(datetime.now().timestamp())}"
    
    # Создаем платеж в T‑Банк
    try:
        payment_result = create_payment(
            amount=amount_kopecks,
            order_id=payment_id,
            description=f"Заказ {order_number}",
            success_url=f"https://t.me/{message.bot.username}",
            fail_url=f"https://t.me/{message.bot.username}",
            client_tg_id=client_id
        )
    except Exception as e:
        await message.answer(
            f"❌ **Ошибка при создании платежа**\n\n"
            f"Пожалуйста, попробуйте позже или свяжитесь с поддержкой.\n\n"
            f"Текст ошибки: {str(e)}"
        )
        await state.clear()
        return

    # Сохраняем в БД
    invoice_id = save_invoice({
        "payment_id": payment_id,
        "amount": amount_kopecks,
        "amount_rub": amount_rub,
        "order_number": order_number,
        "delivery_address": address,
        "client_tg_id": client_id,
        "client_username": client_username,
        "creator_tg_id": client_id,
        "description": f"Заказ {order_number}"
    })

    # Отправляем клиенту ссылку на оплату
    await message.answer(
        f"✅ **Заказ {order_number} принят!**\n\n"
        f"💰 **Сумма к оплате:** {amount_rub:,} ₽\n"
        f"📍 **Адрес доставки:** {address}\n\n"
        f"Нажмите кнопку ниже, чтобы перейти к оплате.\n"
        f"После оплаты мы начнем подготовку к отправке.",
        reply_markup=payment_link_keyboard(payment_result["payment_url"])
    )

    # Отправляем уведомление менеджерам
    from datetime import datetime
    for manager_id in MANAGER_IDS:
        try:
            await message.bot.send_message(
                manager_id,
                f"🆕 **НОВАЯ ЗАЯВКА НА ОПЛАТУ**\n\n"
                f"📦 **Заказ:** {order_number}\n"
                f"💰 **Сумма:** {amount_rub:,} ₽\n"
                f"📍 **Адрес:** {address}\n"
                f"👤 **Клиент:** @{client_username or 'Не указан'}\n"
                f"🆔 **ID клиента:** {client_id}\n"
                f"🔗 **Payment ID:** {payment_id[:12]}...\n\n"
                f"⏳ Ожидаем поступления платежа..."
            )
        except Exception:
            pass  # Если менеджер заблокировал бота

    # Очищаем состояние
    await state.clear()


@router.callback_query(F.data == "payment_confirmed")
async def payment_confirmed(callback: CallbackQuery, state: FSMContext):
    """Пользователь нажал «Я оплатил»"""
    await callback.message.edit_text(
        "🔄 **Проверяем статус платежа...**\n\n"
        "Пожалуйста, подождите несколько секунд."
    )
    
    # Здесь можно проверить статус через T‑Банк API
    # Пока просто отправляем информационное сообщение
    await callback.message.answer(
        "✅ **Спасибо за подтверждение!**\n\n"
        "Мы проверим поступление платежа и свяжемся с вами.\n"
        "Обычно это занимает 1-5 минут.\n\n"
        "Если у вас есть вопросы, напишите менеджеру."
    )
    await callback.answer()



