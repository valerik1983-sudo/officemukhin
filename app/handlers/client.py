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
    waiting_client_name = State()          # ФИО заказчика (для каждого заказа)
    waiting_more_orders = State()
    waiting_order_number_group = State()
    waiting_amount_group = State()
    waiting_client_name_group = State()
    # Общие данные для отправки
    waiting_recipient_name = State()
    waiting_recipient_address = State()
    waiting_recipient_phone = State()


@router.message(Command("start"))
async def cmd_start(message: Message):
    is_manager = message.from_user.id in MANAGER_IDS
    await message.answer(
        "👋 **Добро пожаловать!**\n\n"
        "Я помогу вам оплатить один или несколько заказов.\n"
        "Нажмите кнопку **«Начать оформление»** ниже.\n\n"
        "Сначала вы добавите все заказы, а затем укажете общие данные для отправки.",
        reply_markup=main_menu(is_manager)
    )


@router.message(Command("menu"))
async def cmd_menu(message: Message):
    is_manager = message.from_user.id in MANAGER_IDS
    await message.answer(
        "👋 **Главное меню**\n\nВыберите действие:",
        reply_markup=main_menu(is_manager)
    )


@router.message(Command("help"))
async def cmd_help(message: Message):
    is_manager = message.from_user.id in MANAGER_IDS
    await message.answer(
        "❓ **Помощь**\n\n"
        "**Для клиентов:**\n"
        "1. Нажмите «Оформить заказ»\n"
        "2. Введите номер заказа\n"
        "3. Введите сумму (кратную 1200)\n"
        "4. Введите ФИО заказчика\n"
        "5. Добавьте ещё заказы или завершите\n"
        "6. Укажите общие данные для отправки\n"
        "7. Перейдите по ссылке для оплаты\n\n"
        "**Для менеджеров:**\n"
        "• «Создать ссылку» — сгенерировать ссылку для оплаты\n"
        "• «Проверить оплату» — узнать статус платежа",
        reply_markup=main_menu(is_manager)
    )


@router.callback_query(F.data == "help")
async def callback_help(callback: CallbackQuery):
    is_manager = callback.from_user.id in MANAGER_IDS
    await callback.message.edit_text(
        "❓ **Помощь**\n\n"
        "**Для клиентов:**\n"
        "1. Нажмите «Оформить заказ»\n"
        "2. Введите номер заказа\n"
        "3. Введите сумму (кратную 1200)\n"
        "4. Введите ФИО заказчика\n"
        "5. Добавьте ещё заказы или завершите\n"
        "6. Укажите общие данные для отправки\n"
        "7. Перейдите по ссылке для оплаты\n\n"
        "**Для менеджеров:**\n"
        "• «Создать ссылку» — сгенерировать ссылку для оплаты\n"
        "• «Проверить оплату» — узнать статус платежа",
        reply_markup=main_menu(is_manager)
    )
    await callback.answer()


@router.callback_query(F.data == "client_order")
async def start_order(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "📝 **Введите номер вашего заказа**\n\n"
        "Например: 123456789\n\n"
        "Вы можете найти номер на странице с подтверждением заказа.",
        reply_markup=None
    )
    await state.set_state(OrderForm.waiting_order_number)
    await callback.answer()


@router.message(OrderForm.waiting_order_number)
async def process_order_number(message: Message, state: FSMContext):
    order = message.text.strip()
    if not order.isdigit():
        await message.answer(
            "❌ **Номер заказа должен содержать только цифры.**\n\n"
            "Пожалуйста, введите номер заказа ещё раз (например: 123456789).",
            reply_markup=main_menu(is_manager=False)
        )
        return

    await state.update_data(temp_order_number=order)
    await message.answer(
        "💰 **Введите сумму к оплате (в рублях)**\n\n"
        "Сумма должна быть кратна 1200.\n"
        "Или выберите один из вариантов ниже:",
        reply_markup=order_amount_keyboard([2400, 4800, 6000, 12000, 24000])
    )
    await state.set_state(OrderForm.waiting_amount)


@router.callback_query(F.data.startswith("amount_"), OrderForm.waiting_amount)
async def process_amount_button(callback: CallbackQuery, state: FSMContext):
    if callback.data == "amount_custom":
        await callback.message.edit_text(
            "✏️ **Введите свою сумму в рублях**\n\n"
            "Сумма должна быть кратна 1200 (например: 1200, 2400, 3600...)"
        )
        await callback.answer()
        return

    amount = int(callback.data.split("_")[1])
    await state.update_data(temp_amount=amount)
    await callback.message.edit_text(
        "👤 **Введите ФИО заказчика (получателя) для этого заказа:**\n"
        "Например: Иванов Иван Иванович"
    )
    await state.set_state(OrderForm.waiting_client_name)
    await callback.answer()


@router.message(OrderForm.waiting_amount)
async def process_amount_manual(message: Message, state: FSMContext):
    try:
        amount = int(message.text.replace(" ", "").replace(",", ""))
        if amount < 1:
            raise ValueError
        if amount % 1200 != 0:
            await message.answer(
                "❌ **Ошибка!**\n\n"
                "Сумма должна быть кратна 1200 ₽.\n"
                "Пожалуйста, введите сумму, кратную 1200 (например: 1200, 2400, 3600...)"
            )
            return
        await state.update_data(temp_amount=amount)
        await message.answer(
            "👤 **Введите ФИО заказчика (получателя) для этого заказа:**\n"
            "Например: Иванов Иван Иванович"
        )
        await state.set_state(OrderForm.waiting_client_name)
    except ValueError:
        await message.answer(
            "❌ **Ошибка!**\n\n"
            "Пожалуйста, введите корректное число.\n"
            "Например: 1200, 2400, 6000"
        )


@router.message(OrderForm.waiting_client_name)
async def process_client_name(message: Message, state: FSMContext):
    name = message.text.strip()
    if len(name.split()) < 2:
        await message.answer("❌ Пожалуйста, введите полное ФИО (минимум два слова).")
        return

    data = await state.get_data()
    orders_list = data.get("orders_list", [])
    orders_list.append({
        "order_number": data["temp_order_number"],
        "amount_rub": data["temp_amount"],
        "client_name": name
    })
    await state.update_data(orders_list=orders_list)

    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Добавить ещё заказ", callback_data="add_more_order")
    builder.button(text="✅ Нет, перейти к оплате", callback_data="finish_orders")
    builder.adjust(1)

    await message.answer(
        f"📦 **Заказ {data['temp_order_number']} добавлен!**\n\n"
        f"💰 Сумма: {data['temp_amount']:,} ₽\n"
        f"👤 ФИО заказчика: {name}\n\n"
        f"Хотите добавить ещё один заказ?",
        reply_markup=builder.as_markup()
    )
    await state.set_state(OrderForm.waiting_more_orders)


@router.callback_query(F.data == "add_more_order", OrderForm.waiting_more_orders)
async def add_more_order(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "📝 **Введите номер следующего заказа:**\n\n"
        "Например: 123456789"
    )
    await state.set_state(OrderForm.waiting_order_number_group)
    await callback.answer()


@router.message(OrderForm.waiting_order_number_group)
async def process_order_number_group(message: Message, state: FSMContext):
    order_number = message.text.strip()
    if not order_number.isdigit():
        await message.answer(
            "❌ **Номер заказа должен содержать только цифры.**\n\n"
            "Пожалуйста, введите номер заказа ещё раз."
        )
        return
    await state.update_data(temp_order_number=order_number)
    await message.answer(
        "💰 **Введите сумму для этого заказа (в рублях):**\n\n"
        "Сумма должна быть кратна 1200."
    )
    await state.set_state(OrderForm.waiting_amount_group)


@router.message(OrderForm.waiting_amount_group)
async def process_amount_group(message: Message, state: FSMContext):
    try:
        amount = int(message.text.replace(" ", "").replace(",", ""))
        if amount < 1:
            raise ValueError
        if amount % 1200 != 0:
            await message.answer(
                "❌ **Ошибка!**\n\n"
                "Сумма должна быть кратна 1200 ₽.\n"
                "Пожалуйста, введите сумму, кратную 1200 (например: 1200, 2400, 3600...)"
            )
            return
        await state.update_data(temp_amount=amount)
        await message.answer(
            "👤 **Введите ФИО заказчика для этого заказа:**\n"
            "Например: Иванов Иван Иванович"
        )
        await state.set_state(OrderForm.waiting_client_name_group)
    except ValueError:
        await message.answer(
            "❌ **Ошибка!**\n\n"
            "Пожалуйста, введите корректное число.\n"
            "Например: 1200, 2400, 6000"
        )


@router.message(OrderForm.waiting_client_name_group)
async def process_client_name_group(message: Message, state: FSMContext):
    name = message.text.strip()
    if len(name.split()) < 2:
        await message.answer("❌ Пожалуйста, введите полное ФИО (минимум два слова).")
        return

    data = await state.get_data()
    orders_list = data.get("orders_list", [])
    orders_list.append({
        "order_number": data["temp_order_number"],
        "amount_rub": data["temp_amount"],
        "client_name": name
    })
    await state.update_data(orders_list=orders_list)

    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Добавить ещё заказ", callback_data="add_more_order")
    builder.button(text="✅ Нет, перейти к оплате", callback_data="finish_orders")
    builder.adjust(1)

    await message.answer(
        f"📦 **Заказ {data['temp_order_number']} добавлен!**\n\n"
        f"💰 Сумма: {data['temp_amount']:,} ₽\n"
        f"👤 ФИО заказчика: {name}\n\n"
        f"Хотите добавить ещё один заказ?",
        reply_markup=builder.as_markup()
    )
    await state.set_state(OrderForm.waiting_more_orders)


@router.callback_query(F.data == "finish_orders", OrderForm.waiting_more_orders)
async def finish_orders(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    orders_list = data.get("orders_list", [])
    if not orders_list:
        await callback.message.answer("❌ Нет заказов для оплаты.")
        await state.clear()
        return

    # Показываем сводку и запрашиваем общие данные
    orders_text = "\n".join([
        f"• Заказ {o['order_number']} – {o['amount_rub']:,} ₽ (ФИО: {o['client_name']})"
        for o in orders_list
    ])
    total_amount = sum(o["amount_rub"] for o in orders_list)

    await callback.message.edit_text(
        f"📦 **Сводка заказов**\n\n"
        f"{orders_text}\n\n"
        f"💰 **Общая сумма:** {total_amount:,} ₽\n\n"
        f"Теперь укажите общие данные для отправки.\n"
        f"Введите **ФИО получателя** (на кого оформлена доставка):"
    )
    await state.set_state(OrderForm.waiting_recipient_name)
    await callback.answer()


@router.message(OrderForm.waiting_recipient_name)
async def process_recipient_name(message: Message, state: FSMContext):
    name = message.text.strip()
    if len(name.split()) < 2:
        await message.answer("❌ Пожалуйста, введите полное ФИО (минимум два слова).")
        return
    await state.update_data(recipient_name=name)
    await message.answer(
        "📍 **Введите адрес доставки:**\n"
        "Например: г. Москва, ул. Тверская, д.5, кв.12"
    )
    await state.set_state(OrderForm.waiting_recipient_address)


@router.message(OrderForm.waiting_recipient_address)
async def process_recipient_address(message: Message, state: FSMContext):
    address = message.text.strip()
    await state.update_data(recipient_address=address)
    await message.answer(
        "📱 **Введите номер телефона получателя:**\n"
        "Например: +7 999 123-45-67"
    )
    await state.set_state(OrderForm.waiting_recipient_phone)


@router.message(OrderForm.waiting_recipient_phone)
async def process_recipient_phone(message: Message, state: FSMContext):
    phone = message.text.strip()
    if len(phone) < 10 or not any(char.isdigit() for char in phone):
        await message.answer("❌ Номер телефона слишком короткий или содержит недопустимые символы. Введите ещё раз.")
        return
    await state.update_data(recipient_phone=phone)

    # Теперь все данные собраны – создаём платёж
    data = await state.get_data()
    orders_list = data["orders_list"]
    total_amount = sum(o["amount_rub"] for o in orders_list)
    amount_kopecks = total_amount * 100
    client_id = message.from_user.id
    client_username = message.from_user.username or ""
    recipient_name = data["recipient_name"]
    recipient_address = data["recipient_address"]
    recipient_phone = data["recipient_phone"]

    payment_id_str = f"GROUP_{int(datetime.now().timestamp())}"

    bot_info = await message.bot.get_me()
    bot_username = bot_info.username

    try:
        payment_result = create_payment(
            amount=amount_kopecks,
            order_id=payment_id_str,
            description=f"Оплата {len(orders_list)} заказов",
            success_url=f"https://t.me/{bot_username}",
            fail_url=f"https://t.me/{bot_username}",
            client_tg_id=client_id
        )

        bank_payment_id = payment_result["payment_id"]
        payment_url = payment_result["payment_url"]

        try:
            qr_result = get_qr(
                payment_id=bank_payment_id,
                order_id=payment_id_str,
                amount=amount_kopecks,
                description=f"Оплата {len(orders_list)} заказов",
                data_type="PAYLOAD"
            )
            qr_data = qr_result["qr_data"]
            sbp_available = True
        except Exception as e:
            sbp_available = False
            sbp_error = str(e)

    except Exception as e:
        await message.answer(
            f"❌ **Ошибка при создании платежа**\n\n"
            f"Текст ошибки: {str(e)}"
        )
        await state.clear()
        return

    # Сохраняем в БД
    save_invoice({
        "payment_id": payment_id_str,
        "amount": amount_kopecks,
        "amount_rub": total_amount,
        "order_number": None,
        "delivery_address": recipient_address,
        "client_tg_id": client_id,
        "client_username": client_username,
        "client_name": recipient_name,
        "client_phone": recipient_phone,
        "creator_tg_id": client_id,
        "description": f"Групповая оплата {len(orders_list)} заказов",
        "is_group": 1,
        "orders_data": json.dumps(orders_list, ensure_ascii=False)
    })

    # Клавиатура после получения ссылки
    builder = InlineKeyboardBuilder()
    builder.button(text="🔄 Оформить новый заказ", callback_data="client_order")
    builder.button(text="🏠 Главное меню", callback_data="main_menu")
    builder.adjust(1)

    orders_text = "\n".join([
        f"• Заказ {o['order_number']} – {o['amount_rub']:,} ₽ (ФИО: {o['client_name']})"
        for o in orders_list
    ])

    if sbp_available:
        await message.answer(
            f"🔗 **Ссылка для оплаты {len(orders_list)} заказов**\n"
            f"💰 **Общая сумма:** {total_amount:,} ₽\n\n"
            f"📦 **Заказы:**\n{orders_text}\n\n"
            f"📍 **Адрес:** {recipient_address}\n"
            f"👤 **Получатель:** {recipient_name}\n"
            f"📱 **Телефон:** {recipient_phone}\n\n"
            f"_Автоматически поступит на счёт после оплаты._",
            reply_markup=builder.as_markup()
        )
    else:
        await message.answer(
            f"⚠️ **СБП временно недоступна.**\n"
            f"Используйте обычную ссылку:\n\n"
            f"💳 {payment_url}\n"
            f"💰 {total_amount:,} ₽\n\n"
            f"📦 **Заказы:**\n{orders_text}\n\n"
            f"📍 **Адрес:** {recipient_address}\n"
            f"👤 **Получатель:** {recipient_name}\n"
            f"📱 **Телефон:** {recipient_phone}\n\n"
            f"_Оплата по этой ссылке также автоматически поступит на счёт._",
            reply_markup=builder.as_markup()
        )

    await state.clear()


@router.callback_query(F.data == "main_menu")
async def main_menu_callback(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    is_manager = callback.from_user.id in MANAGER_IDS
    await callback.message.edit_text(
        "👋 **Главное меню**\n\nВыберите действие:",
        reply_markup=main_menu(is_manager)
    )
    await callback.answer()