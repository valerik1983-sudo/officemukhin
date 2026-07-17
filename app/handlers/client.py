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
    # Редактируем текущее сообщение, чтобы перейти к диалогу
    await callback.message.edit_text(
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


@router.callback_query(F.data.startswith("amount_"), OrderForm.waiting_amount)
async def process_amount_button(callback: CallbackQuery, state: FSMContext):
    if callback.data == "amount_custom":
        await callback.message.edit_text(
            "✏️ <b>Введите свою сумму в рублях</b>\n\n"
            "Сумма должна быть кратна 1200 (делиться на 1200), или 0 для оплаты бонусами.",
            parse_mode="HTML"
        )
        await callback.answer()
        return

    amount = int(callback.data.split("_")[1])
    await state.update_data(temp_amount=amount)
    await callback.message.edit_text(
        "👤 <b>Введите ФИО для этого заказа:</b>\n"
        "Например: Иванов Иван Иванович",
        parse_mode="HTML"
    )
    await state.set_state(OrderForm.waiting_client_name)
    await callback.answer()


@router.message(OrderForm.waiting_amount)
async def process_amount_manual(message: Message, state: FSMContext):
    try:
        amount = int(message.text.replace(" ", "").replace(",", ""))
        if amount < 0:
            raise ValueError
        if amount != 0 and amount % 1200 != 0:
            await message.answer(
                "❌ <b>Ошибка!</b>\n\n"
                "Сумма должна быть кратна 1200 ₽ (делиться на 1200 без остатка), или 0 для оплаты бонусами.\n"
                "Пожалуйста, введите корректную сумму.",
                parse_mode="HTML"
            )
            return
        await state.update_data(temp_amount=amount)
        await message.answer(
            "👤 <b>Введите ФИО заказчика (получателя) для этого заказа:</b>\n"
            "Например: Иванов Иван Иванович",
            parse_mode="HTML"
        )
        await state.set_state(OrderForm.waiting_client_name)
    except ValueError:
        await message.answer(
            "❌ <b>Ошибка!</b>\n\n"
            "Пожалуйста, введите корректное число.\n"
            "Например: 1200, 2400, 6000 (или 0 для оплаченного заказа)",
            parse_mode="HTML"
        )


@router.message(OrderForm.waiting_client_name)
async def process_client_name(message: Message, state: FSMContext):
    name = message.text.strip()
    if len(name.split()) < 2:
        await message.answer(
            "❌ Пожалуйста, введите полное ФИО (минимум два слова).",
            parse_mode="HTML"
        )
        return

    data = await state.get_data()
    orders_list = data.get("orders_list", [])
    temp_amount = data["temp_amount"]
    orders_list.append({
        "order_number": data["temp_order_number"],
        "amount_rub": temp_amount,
        "client_name": name,
        "is_paid_by_bonus": temp_amount == 0
    })
    await state.update_data(orders_list=orders_list)

    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Добавить ещё заказ", callback_data="add_more_order")
    builder.button(text="✅ Нет, перейти к оплате", callback_data="finish_orders")
    builder.adjust(1)

    await message.answer(
        f"📦 <b>Заказ {data['temp_order_number']} добавлен!</b>\n\n"
        f"💰 Сумма: {temp_amount:,} ₽" + (" <i>(оплачен бонусами)</i>" if temp_amount == 0 else "") + "\n"
        f"👤 ФИО заказа: {name}\n\n"
        f"Хотите добавить ещё один заказ?",
        parse_mode="HTML",
        reply_markup=builder.as_markup()
    )
    await state.set_state(OrderForm.waiting_more_orders)


@router.callback_query(F.data == "add_more_order", OrderForm.waiting_more_orders)
async def add_more_order(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "📝 <b>Введите номер следующего заказа:</b>\n\n"
        "Например: 123456789",
        parse_mode="HTML"
    )
    await state.set_state(OrderForm.waiting_order_number_group)
    await callback.answer()


@router.message(OrderForm.waiting_order_number_group)
async def process_order_number_group(message: Message, state: FSMContext):
    order_number = message.text.strip()
    if not order_number.isdigit():
        await message.answer(
            "❌ <b>Номер заказа должен содержать только цифры.</b>\n\n"
            "Пожалуйста, введите номер заказа ещё раз.",
            parse_mode="HTML"
        )
        return
    await state.update_data(temp_order_number=order_number)
    await message.answer(
        "💰 <b>Введите сумму для этого заказа (в рублях):</b>\n\n"
        "Сумма должна быть кратна 1200, или 0 для оплаты бонусами.",
        parse_mode="HTML"
    )
    await state.set_state(OrderForm.waiting_amount_group)


@router.message(OrderForm.waiting_amount_group)
async def process_amount_group(message: Message, state: FSMContext):
    try:
        amount = int(message.text.replace(" ", "").replace(",", ""))
        if amount < 0:
            raise ValueError
        if amount != 0 and amount % 1200 != 0:
            await message.answer(
                "❌ <b>Ошибка!</b>\n\n"
                "Сумма должна быть кратна 1200 ₽, или 0 для оплаты бонусами.\n"
                "Пожалуйста, введите корректную сумму.",
                parse_mode="HTML"
            )
            return
        await state.update_data(temp_amount=amount)
        await message.answer(
            "👤 <b>Введите ФИО заказчика для этого заказа:</b>\n"
            "Например: Иванов Иван Иванович",
            parse_mode="HTML"
        )
        await state.set_state(OrderForm.waiting_client_name_group)
    except ValueError:
        await message.answer(
            "❌ <b>Ошибка!</b>\n\n"
            "Пожалуйста, введите корректное число.\n"
            "Например: 1200, 2400, 6000 (или 0 для оплаченного заказа)",
            parse_mode="HTML"
        )


@router.message(OrderForm.waiting_client_name_group)
async def process_client_name_group(message: Message, state: FSMContext):
    name = message.text.strip()
    if len(name.split()) < 2:
        await message.answer(
            "❌ Пожалуйста, введите полное ФИО (минимум два слова).",
            parse_mode="HTML"
        )
        return

    data = await state.get_data()
    orders_list = data.get("orders_list", [])
    temp_amount = data["temp_amount"]
    orders_list.append({
        "order_number": data["temp_order_number"],
        "amount_rub": temp_amount,
        "client_name": name,
        "is_paid_by_bonus": temp_amount == 0
    })
    await state.update_data(orders_list=orders_list)

    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Добавить ещё заказ", callback_data="add_more_order")
    builder.button(text="✅ Нет, перейти к оплате", callback_data="finish_orders")
    builder.adjust(1)

    await message.answer(
        f"📦 <b>Заказ {data['temp_order_number']} добавлен!</b>\n\n"
        f"💰 Сумма: {temp_amount:,} ₽" + (" <i>(оплачен бонусами)</i>" if temp_amount == 0 else "") + "\n"
        f"👤 ФИО заказа: {name}\n\n"
        f"Хотите добавить ещё один заказ?",
        parse_mode="HTML",
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

    orders_text = "\n".join([
        f"• Заказ {o['order_number']} – {o['amount_rub']:,} ₽ (ФИО: {o['client_name']})" +
        (" <i>(оплачен с бонусного кошелька)</i>" if o.get('is_paid_by_bonus') else "")
        for o in orders_list
    ])
    total_amount = sum(o["amount_rub"] for o in orders_list)

    await callback.message.edit_text(
        f"📦 <b>Сводка заказов</b>\n\n"
        f"{orders_text}\n\n"
        f"💰 <b>Общая сумма к оплате:</b> {total_amount:,} ₽\n\n"
        f"Теперь укажите общие данные для отправки.\n"
        f"Введите <b>ФИО получателя</b> (на кого оформляется доставка):",
        parse_mode="HTML"
    )
    await state.set_state(OrderForm.waiting_recipient_name)
    await callback.answer()


@router.message(OrderForm.waiting_recipient_name)
async def process_recipient_name(message: Message, state: FSMContext):
    name = message.text.strip()
    if len(name.split()) < 2:
        await message.answer(
            "❌ Пожалуйста, введите полное ФИО (минимум два слова).",
            parse_mode="HTML"
        )
        return
    await state.update_data(recipient_name=name)
    await message.answer(
        "📍 <b>Введите адрес доставки СДЭК:</b>\n"
        "Например: г. Москва, ул. Тверская, д.5, кв.12",
        parse_mode="HTML"
    )
    await state.set_state(OrderForm.waiting_recipient_address)


@router.message(OrderForm.waiting_recipient_address)
async def process_recipient_address(message: Message, state: FSMContext):
    address = message.text.strip()
    await state.update_data(recipient_address=address)
    await message.answer(
        "📱 <b>Введите номер телефона получателя:</b>\n"
        "Например: 79991234567",
        parse_mode="HTML"
    )
    await state.set_state(OrderForm.waiting_recipient_phone)


@router.message(OrderForm.waiting_recipient_phone)
async def process_recipient_phone(message: Message, state: FSMContext):
    phone = message.text.strip()
    if len(phone) < 10 or not any(char.isdigit() for char in phone):
        await message.answer(
            "❌ Номер телефона слишком короткий или содержит недопустимые символы. Введите ещё раз.",
            parse_mode="HTML"
        )
        return
    await state.update_data(recipient_phone=phone)

    data = await state.get_data()
    orders_list = data["orders_list"]
    total_amount = sum(o["amount_rub"] for o in orders_list)
    client_id = message.from_user.id
    client_username = message.from_user.username or ""
    recipient_name = data["recipient_name"]
    recipient_address = data["recipient_address"]
    recipient_phone = data["recipient_phone"]

    orders_text = "\n".join([
        f"• Заказ {o['order_number']} – {o['amount_rub']:,} ₽ (ФИО: {o['client_name']})" +
        (" <i>(оплачен с бонусного кошелька)</i>" if o.get('is_paid_by_bonus') else "")
        for o in orders_list
    ])

    # === Функция нормализации payment_id ===
    def normalize_payment_id(pid: str) -> str:
        if pid.startswith("group_"):
            return pid[6:]  # убираем префикс "group_"
        return pid

    if total_amount == 0:
        payment_id = f"BONUS_{int(datetime.now().timestamp())}"
        payment_id = normalize_payment_id(payment_id)
        save_invoice({
            "payment_id": payment_id,
            "amount": 0,
            "amount_rub": 0,
            "order_number": None,
            "delivery_address": recipient_address,
            "client_tg_id": client_id,
            "client_username": client_username,
            "client_name": recipient_name,
            "client_phone": recipient_phone,
            "creator_tg_id": client_id,
            "description": f"Групповая оплата бонусами ({len(orders_list)} заказов)",
            "is_group": 1,
            "orders_data": json.dumps(orders_list, ensure_ascii=False)
        })

        # === Сообщение клиенту с кнопками ===
        client_builder = InlineKeyboardBuilder()
        client_builder.button(text="🔄 Оформить новый заказ", callback_data="client_order")
        client_builder.button(text="🏠 Главное меню", callback_data="main_menu")
        client_builder.adjust(1)

        await message.answer(
            f"✅ <b>Все заказы уже оплачены бонусами!</b>\n\n"
            f"📦 Заказы:\n{orders_text}\n\n"
            f"📍 Адрес доставки: {recipient_address}\n"
            f"👤 Получатель: {recipient_name}\n"
            f"📱 Телефон: {recipient_phone}\n\n"
            f"Спасибо за доверие! Менеджер получит уведомление.",
            parse_mode="HTML",
            reply_markup=client_builder.as_markup()
        )

        # === Уведомление менеджеру с кнопками ===
        manager_builder = InlineKeyboardBuilder()
        manager_builder.button(text="📦 Отправить трек", callback_data=f"track_group_{payment_id}")
        manager_builder.button(text="📢 Уведомить", callback_data=f"notify_group_{payment_id}")
        manager_builder.button(text="🏠 Главное меню", callback_data="manager_back")
        manager_builder.adjust(2, 1)

        for manager_id in MANAGER_IDS:
            try:
                await message.bot.send_message(
                    manager_id,
                    f"<b>✅ ЗАКАЗЫ ОПЛАЧЕНЫ БОНУСАМИ!</b>\n\n"
                    f"📦 Заказы:\n{orders_text}\n\n"
                    f"📍 Адрес: {recipient_address}\n"
                    f"👤 Получатель: {recipient_name}\n"
                    f"📱 Телефон: {recipient_phone}\n"
                    f"👤 Инициатор: @{client_username or 'без username'} (ID: {client_id})",
                    parse_mode="HTML",
                    reply_markup=manager_builder.as_markup()
                )
            except Exception:
                pass

        await state.clear()
        return

    amount_kopecks = total_amount * 100
    payment_id_str = f"GROUP_{int(datetime.now().timestamp())}"
    payment_id_str = normalize_payment_id(payment_id_str)

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
            f"❌ <b>Ошибка при создании платежа</b>\n\n"
            f"Текст ошибки: {str(e)}",
            parse_mode="HTML"
        )
        await state.clear()
        return

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

    builder = InlineKeyboardBuilder()
    builder.button(text="🔄 Оформить новый заказ", callback_data="client_order")
    builder.button(text="🏠 Главное меню", callback_data="main_menu")
    builder.adjust(1)

    if sbp_available:
        await message.answer(
            f"🔗 <b>Ссылка для оплаты {len(orders_list)} заказов:</b>\n{qr_data}\n\n"
            f"💰 <b>Общая сумма:</b> {total_amount:,} ₽\n\n"
            f"📦 Заказы:\n{orders_text}\n\n"
            f"📍 Адрес: {recipient_address}\n"
            f"👤 Получатель: {recipient_name}\n"
            f"📱 Телефон: {recipient_phone}\n\n"
            f"<i>Автоматически поступит на счёт после оплаты.</i>",
            parse_mode="HTML",
            reply_markup=builder.as_markup()
        )
    else:
        await message.answer(
            f"⚠️ <b>СБП временно недоступна.</b>\n"
            f"Используйте обычную ссылку:\n\n"
            f"💳 {payment_url}\n"
            f"💰 <b>Общая сумма:</b> {total_amount:,} ₽\n\n"
            f"📦 Заказы:\n{orders_text}\n\n"
            f"📍 Адрес: {recipient_address}\n"
            f"👤 Получатель: {recipient_name}\n"
            f"📱 Телефон: {recipient_phone}\n\n"
            f"<i>Оплата по этой ссылке также автоматически поступит на счёт.</i>",
            parse_mode="HTML",
            reply_markup=builder.as_markup()
        )

    await state.clear()


@router.callback_query(F.data == "main_menu")
async def main_menu_callback(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    is_manager = callback.from_user.id in MANAGER_IDS
    # Убираем клавиатуру у текущего сообщения, чтобы не было висячих кнопок
    await callback.message.edit_reply_markup(reply_markup=None)
    # Отправляем новое сообщение с меню
    await callback.message.answer(
        "👋 <b>Главное меню</b>\n\nВыберите действие:",
        parse_mode="HTML",
        reply_markup=main_menu(is_manager)
    )
    await callback.answer()