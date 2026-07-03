from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from datetime import datetime
import requests

from ..config import MANAGER_IDS, TBANK_TERMINAL_KEY, TBANK_SECRET_KEY, DATABASE_PATH
from ..services.tbank import create_payment, get_qr, check_payment_status, TBANK_API_URL
from ..database import (
    save_invoice,
    get_invoice_by_payment_id,
    get_invoice_by_order_number,
    update_invoice_status
)
from ..keyboards import main_menu, manager_back_keyboard

router = Router(name="manager")


# === FSM ===
class ManagerLinkForm(StatesGroup):
    waiting_amount = State()
    waiting_comment = State()  # комментарий для внутреннего учёта


class ManagerTrackForm(StatesGroup):
    waiting_order_number = State()
    waiting_track_number = State()


class ManagerNotifyForm(StatesGroup):
    waiting_order_number = State()
    waiting_message_text = State()


async def is_manager(user_id: int) -> bool:
    return user_id in MANAGER_IDS


# === Команда /menu ===
@router.message(Command("menu"))
async def cmd_menu(message: Message):
    if not await is_manager(message.from_user.id):
        await message.answer("⛔️ Нет доступа")
        return
    await message.answer(
        "👋 **Главное меню**\n\nВыберите действие:",
        reply_markup=main_menu(is_manager=True)
    )


# === Функция создания ссылки (с комментарием для себя) ===
async def create_link(message: Message, creator_id: int, amount_rub: int, comment: str):
    """
    Создаёт платёж с фиксированным описанием для банка,
    а комментарий сохраняет в БД для внутреннего учёта.
    """
    bot_info = await message.bot.get_me()
    bot_username = bot_info.username
    order_id = f"MANUAL_{int(datetime.now().timestamp())}"

    # Фиксированное описание для банка (не показываем клиенту)
    bank_description = "Оплата по ссылке"

    try:
        payment_result = create_payment(
            amount=amount_rub * 100,
            order_id=order_id,
            description=bank_description,  # для банка
            success_url=f"https://t.me/{bot_username}",
            fail_url=f"https://t.me/{bot_username}",
            client_tg_id=creator_id
        )
        payment_id = payment_result["payment_id"]
        payment_url = payment_result["payment_url"]

        try:
            qr_result = get_qr(
                payment_id=payment_id,
                order_id=order_id,
                amount=amount_rub * 100,
                description=bank_description,
                data_type="PAYLOAD"
            )
            qr_data = qr_result["qr_data"]
            sbp_available = True
        except Exception as e:
            sbp_available = False
            sbp_error = str(e)

    except Exception as e:
        error_detail = f"❌ Ошибка T-Банк:\n{str(e)}"
        await message.answer(error_detail, reply_markup=main_menu(is_manager=True))
        if creator_id in MANAGER_IDS:
            await message.bot.send_message(creator_id, error_detail)
        return

    # Сохраняем в БД: комментарий кладём в description
    save_invoice({
        "payment_id": order_id,
        "amount": amount_rub * 100,
        "amount_rub": amount_rub,
        "order_number": None,
        "delivery_address": None,
        "client_tg_id": creator_id if creator_id else None,
        "client_username": None,
        "creator_tg_id": creator_id,
        "description": comment  # здесь хранится комментарий менеджера
    })

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()
    builder.button(text="🔗 Создать ещё одну ссылку", callback_data="manager_link")
    builder.button(text="🏠 Главное меню", callback_data="manager_back")
    builder.adjust(1)

    if sbp_available:
        await message.answer(
            f"🔗 **Ссылка для оплаты**\n"
            f"💰 {amount_rub:,} ₽\n\n"
            f"📝 **Ваш комментарий:** {comment}\n"
            f"🔗 {qr_data}\n\n"
            f"_Ссылка откроется автоматически при нажатии._",
            reply_markup=builder.as_markup()
        )
    else:
        await message.answer(
            f"⚠️ **СБП временно недоступна.**\n"
            f"Используйте обычную ссылку:\n\n"
            f"💳 {payment_url}\n"
            f"💰 {amount_rub:,} ₽\n"
            f"📝 **Ваш комментарий:** {comment}",
            reply_markup=builder.as_markup()
        )


# === Команда /link (с комментарием) ===
@router.message(Command("link"))
async def manager_link_command(message: Message):
    if not await is_manager(message.from_user.id):
        await message.answer("⛔️ **Нет доступа**")
        return

    try:
        parts = message.text.split(maxsplit=2)
        if len(parts) < 2:
            raise ValueError
        amount_rub = int(parts[1].replace(" ", "").replace(",", ""))

        if amount_rub % 1200 != 0:
            await message.answer(
                "❌ Сумма должна быть кратна 1200 ₽.\n"
                "Пожалуйста, введите сумму, кратную 1200 (например: 1200, 2400, 3600...)",
                reply_markup=main_menu(is_manager=True)
            )
            return

        comment = parts[2] if len(parts) > 2 else "Ручная ссылка"
        await create_link(message, message.from_user.id, amount_rub, comment)

    except:
        await message.answer(
            "❌ Неверный формат\n\n"
            "Использование: `/link [сумма] [комментарий]`\n"
            "Сумма должна быть кратна 1200.",
            reply_markup=main_menu(is_manager=True)
        )


# === КНОПКА «Создать ссылку» (диалог) ===
@router.callback_query(F.data == "manager_link")
async def manager_link_start(callback: CallbackQuery, state: FSMContext):
    if not await is_manager(callback.from_user.id):
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return

    await state.clear()
    await state.set_state(ManagerLinkForm.waiting_amount)

    await callback.message.answer(
        "🔗 **Создание ссылки для оплаты**\n\n"
        "Введите сумму в рублях (кратную 1200):\n"
        "Например: 2400, 4800, 6000",
        reply_markup=manager_back_keyboard()
    )
    await callback.answer()


@router.message(ManagerLinkForm.waiting_amount)
async def process_manager_amount(message: Message, state: FSMContext):
    try:
        amount = int(message.text.replace(" ", "").replace(",", ""))
        if amount <= 0:
            raise ValueError

        if amount % 1200 != 0:
            await message.answer(
                "❌ Ошибка! Сумма должна быть кратна 1200 ₽.\n"
                "Пожалуйста, введите сумму, кратную 1200 (например: 1200, 2400, 3600...)",
                reply_markup=manager_back_keyboard()
            )
            return

        await state.update_data(amount=amount)
        await state.set_state(ManagerLinkForm.waiting_comment)

        await message.answer(
            "✏️ Введите комментарий (для внутреннего учёта).\n"
            "Этот комментарий будет виден только вам в уведомлении об оплате.\n"
            "Можно оставить пустым, нажав «Отправить».",
            reply_markup=manager_back_keyboard()
        )

    except ValueError:
        await message.answer(
            "❌ Ошибка! Введите корректную сумму (целое положительное число).",
            reply_markup=manager_back_keyboard()
        )


@router.message(ManagerLinkForm.waiting_comment)
async def process_manager_comment(message: Message, state: FSMContext):
    data = await state.get_data()
    amount = data.get("amount")
    if not amount:
        await message.answer("❌ Что-то пошло не так. Попробуйте заново.", reply_markup=main_menu(is_manager=True))
        await state.clear()
        return

    comment = message.text.strip()
    if not comment:
        comment = "Ручная ссылка"  # значение по умолчанию, если пользователь отправил пустое

    await create_link(message, message.from_user.id, amount, comment)
    await state.clear()


# === Кнопка «Назад» ===
@router.callback_query(F.data == "manager_back")
async def manager_back(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    is_man = await is_manager(callback.from_user.id)
    await callback.message.edit_text(
        "👋 **Главное меню**\n\nВыберите действие:",
        reply_markup=main_menu(is_man)
    )
    await callback.answer()


# === Команда /check ===
@router.message(Command("check"))
async def manager_check_command(message: Message):
    if not await is_manager(message.from_user.id):
        await message.answer("⛔️ **Нет доступа**")
        return

    parts = message.text.split()
    if len(parts) < 2:
        await message.answer(
            "❌ Использование: `/check [ID_платежа или номер_заказа]`",
            reply_markup=main_menu(is_manager=True)
        )
        return

    identifier = parts[1].strip()
    invoice = get_invoice_by_payment_id(identifier)
    if not invoice:
        invoice = get_invoice_by_order_number(identifier)

    if not invoice:
        await message.answer(
            f"❌ Платеж с идентификатором `{identifier}` не найден.",
            reply_markup=main_menu(is_manager=True)
        )
        return

    status = check_payment_status(invoice["payment_id"])
    status_map = {"CONFIRMED": "paid", "REFUNDED": "refunded", "CANCELED": "canceled"}
    new_status = status_map.get(status, "unknown")

    if new_status == "paid" and invoice["status"] != "paid":
        update_invoice_status(invoice["payment_id"], "paid")
        invoice = get_invoice_by_payment_id(invoice["payment_id"])

    answer = f"📊 **Информация о платеже**\n\n"
    answer += f"🆔 **ID платежа:** {invoice['payment_id'][:20]}...\n"
    answer += f"💰 **Сумма:** {invoice['amount_rub']:,} ₽\n"
    answer += f"📦 **Заказ:** {invoice['order_number'] or 'Ручная ссылка'}\n"
    answer += f"📝 **Комментарий (ваш):** {invoice.get('description') or 'Не указан'}\n"
    answer += f"📍 **Адрес:** {invoice['delivery_address'] or 'Не указан'}\n"
    answer += f"👤 **ФИО:** {invoice.get('client_name') or 'Не указано'}\n"
    answer += f"📱 **Телефон:** {invoice.get('client_phone') or 'Не указан'}\n"
    answer += f"📅 **Создан:** {invoice['created_at']}\n"
    answer += f"🔘 **Статус в T‑Банк:** {status}\n"
    answer += f"🔘 **Статус в БД:** {invoice['status'].upper()}\n"

    if invoice.get("client_tg_id"):
        answer += f"\n👤 **Клиент:** @{invoice.get('client_username', 'Неизвестно')} (ID: {invoice['client_tg_id']})"

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()
    order_num = invoice['order_number']
    if order_num:
        builder.button(text="📦 Отправить трек", callback_data=f"track_{order_num}")
        builder.button(text="📢 Уведомить клиента", callback_data=f"notify_{order_num}")
    else:
        builder.button(text="📦 Отправить трек", callback_data="track_none")
        builder.button(text="📢 Уведомить клиента", callback_data="notify_none")
    builder.adjust(2)
    await message.answer(answer, reply_markup=builder.as_markup())


# === Обработчики кнопок "Отправить трек" и "Уведомить" ===
@router.callback_query(F.data.startswith("track_"))
async def track_from_check(callback: CallbackQuery, state: FSMContext):
    if not await is_manager(callback.from_user.id):
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return

    order_number = callback.data.split("_", 1)[1]
    if order_number == "none":
        await callback.answer("❌ У этого заказа нет номера.", show_alert=True)
        return

    invoice = get_invoice_by_order_number(order_number)
    if not invoice:
        await callback.answer(f"❌ Заказ {order_number} не найден.", show_alert=True)
        return

    await state.update_data(order_number=order_number)
    await callback.message.answer(
        f"📦 Введите трек-номер для заказа {order_number}:"
    )
    await state.set_state(ManagerTrackForm.waiting_track_number)
    await callback.answer()


@router.callback_query(F.data.startswith("notify_"))
async def notify_from_check(callback: CallbackQuery, state: FSMContext):
    if not await is_manager(callback.from_user.id):
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return

    order_number = callback.data.split("_", 1)[1]
    if order_number == "none":
        await callback.answer("❌ У этого заказа нет номера.", show_alert=True)
        return

    invoice = get_invoice_by_order_number(order_number)
    if not invoice:
        await callback.answer(f"❌ Заказ {order_number} не найден.", show_alert=True)
        return

    await state.update_data(order_number=order_number)
    await callback.message.answer(
        f"📢 Введите текст уведомления для заказа {order_number}:"
    )
    await state.set_state(ManagerNotifyForm.waiting_message_text)
    await callback.answer()


@router.message(ManagerTrackForm.waiting_track_number)
async def process_track_number(message: Message, state: FSMContext):
    data = await state.get_data()
    order_number = data.get("order_number")
    if not order_number:
        await message.answer(
            "❌ Ошибка: номер заказа не найден. Попробуйте заново.",
            reply_markup=main_menu(is_manager=True)
        )
        await state.clear()
        return

    track_number = message.text.strip()
    if not track_number:
        await message.answer("❌ Трек-номер не может быть пустым. Введите ещё раз.")
        return

    invoice = get_invoice_by_order_number(order_number)
    if not invoice:
        await message.answer(
            f"❌ Заказ {order_number} не найден.",
            reply_markup=main_menu(is_manager=True)
        )
        await state.clear()
        return

    client_tg_id = invoice.get("client_tg_id")
    if not client_tg_id:
        await message.answer(
            f"❌ У заказа {order_number} нет Telegram ID клиента.",
            reply_markup=main_menu(is_manager=True)
        )
        await state.clear()
        return

    try:
        await message.bot.send_message(
            client_tg_id,
            f"📦 **Ваш заказ {order_number} отправлен!**\n\n"
            f"Трек-номер для отслеживания: `{track_number}`\n"
            f"Вы можете отследить его на сайте СДЭК."
        )
        await message.answer(
            f"✅ Трек-номер отправлен клиенту по заказу {order_number}.",
            reply_markup=main_menu(is_manager=True)
        )
    except Exception as e:
        await message.answer(
            f"❌ Не удалось отправить сообщение: {str(e)}",
            reply_markup=main_menu(is_manager=True)
        )
    await state.clear()


@router.message(ManagerNotifyForm.waiting_message_text)
async def process_notify_text(message: Message, state: FSMContext):
    data = await state.get_data()
    order_number = data.get("order_number")
    if not order_number:
        await message.answer(
            "❌ Ошибка: номер заказа не найден. Попробуйте заново.",
            reply_markup=main_menu(is_manager=True)
        )
        await state.clear()
        return

    text = message.text.strip()
    if not text:
        await message.answer("❌ Текст уведомления не может быть пустым. Введите ещё раз.")
        return

    invoice = get_invoice_by_order_number(order_number)
    if not invoice:
        await message.answer(
            f"❌ Заказ {order_number} не найден.",
            reply_markup=main_menu(is_manager=True)
        )
        await state.clear()
        return

    client_tg_id = invoice.get("client_tg_id")
    if not client_tg_id:
        await message.answer(
            f"❌ У заказа {order_number} нет Telegram ID клиента.",
            reply_markup=main_menu(is_manager=True)
        )
        await state.clear()
        return

    try:
        await message.bot.send_message(
            client_tg_id,
            f"📢 **Уведомление по заказу {order_number}**\n\n{text}"
        )
        await message.answer(
            f"✅ Уведомление отправлено клиенту по заказу {order_number}.",
            reply_markup=main_menu(is_manager=True)
        )
    except Exception as e:
        await message.answer(
            f"❌ Не удалось отправить сообщение: {str(e)}",
            reply_markup=main_menu(is_manager=True)
        )
    await state.clear()


# === Кнопки в главном меню для трека и уведомления ===
@router.callback_query(F.data == "manager_track_start")
async def manager_track_start(callback: CallbackQuery, state: FSMContext):
    if not await is_manager(callback.from_user.id):
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return

    await callback.message.edit_text(
        "📦 **Отправка трек-номера**\n\n"
        "Введите номер заказа:"
    )
    await state.set_state(ManagerTrackForm.waiting_order_number)
    await callback.answer()


@router.message(ManagerTrackForm.waiting_order_number)
async def process_track_order_number(message: Message, state: FSMContext):
    order_number = message.text.strip()
    if not order_number.isdigit():
        await message.answer(
            "❌ **Номер заказа должен содержать только цифры.**\n\n"
            "Пожалуйста, введите номер заказа ещё раз.",
            reply_markup=main_menu(is_manager=True)
        )
        return

    invoice = get_invoice_by_order_number(order_number)
    if not invoice:
        await message.answer(
            f"❌ Заказ с номером {order_number} не найден. Попробуйте ещё раз.",
            reply_markup=main_menu(is_manager=True)
        )
        return

    await state.update_data(order_number=order_number)
    await message.answer(f"📦 Введите трек-номер для заказа {order_number}:")
    await state.set_state(ManagerTrackForm.waiting_track_number)


@router.callback_query(F.data == "manager_notify_start")
async def manager_notify_start(callback: CallbackQuery, state: FSMContext):
    if not await is_manager(callback.from_user.id):
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return

    await callback.message.edit_text(
        "📢 **Отправка уведомления клиенту**\n\n"
        "Введите номер заказа:"
    )
    await state.set_state(ManagerNotifyForm.waiting_order_number)
    await callback.answer()


@router.message(ManagerNotifyForm.waiting_order_number)
async def process_notify_order_number(message: Message, state: FSMContext):
    order_number = message.text.strip()
    if not order_number.isdigit():
        await message.answer(
            "❌ **Номер заказа должен содержать только цифры.**\n\n"
            "Пожалуйста, введите номер заказа ещё раз.",
            reply_markup=main_menu(is_manager=True)
        )
        return

    invoice = get_invoice_by_order_number(order_number)
    if not invoice:
        await message.answer(
            f"❌ Заказ с номером {order_number} не найден. Попробуйте ещё раз.",
            reply_markup=main_menu(is_manager=True)
        )
        return

    await state.update_data(order_number=order_number)
    await message.answer(f"📢 Введите текст уведомления для заказа {order_number}:")
    await state.set_state(ManagerNotifyForm.waiting_message_text)


# === Команды /track и /notify ===
@router.message(Command("track"))
async def cmd_track(message: Message):
    if not await is_manager(message.from_user.id):
        await message.answer("⛔️ Нет доступа")
        return

    parts = message.text.split(maxsplit=2)
    if len(parts) < 3:
        await message.answer(
            "❌ Использование: `/track [номер заказа] [трек-номер]`",
            reply_markup=main_menu(is_manager=True)
        )
        return

    order_number = parts[1]
    track_number = parts[2]

    invoice = get_invoice_by_order_number(order_number)
    if not invoice:
        await message.answer(
            f"❌ Заказ с номером {order_number} не найден.",
            reply_markup=main_menu(is_manager=True)
        )
        return

    client_tg_id = invoice.get("client_tg_id")
    if not client_tg_id:
        await message.answer(
            "❌ У заказа нет Telegram ID клиента.",
            reply_markup=main_menu(is_manager=True)
        )
        return

    try:
        await message.bot.send_message(
            client_tg_id,
            f"📦 **Ваш заказ {order_number} отправлен!**\n\n"
            f"Трек-номер для отслеживания: `{track_number}`\n"
            f"Вы можете отследить его на сайте СДЭК."
        )
        await message.answer(
            f"✅ Трек-номер отправлен клиенту по заказу {order_number}.",
            reply_markup=main_menu(is_manager=True)
        )
    except Exception as e:
        await message.answer(
            f"❌ Не удалось отправить сообщение: {str(e)}",
            reply_markup=main_menu(is_manager=True)
        )


@router.message(Command("notify"))
async def cmd_notify(message: Message):
    if not await is_manager(message.from_user.id):
        await message.answer("⛔️ Нет доступа")
        return

    parts = message.text.split(maxsplit=2)
    if len(parts) < 3:
        await message.answer(
            "❌ Использование: `/notify [номер заказа] [текст уведомления]`",
            reply_markup=main_menu(is_manager=True)
        )
        return

    order_number = parts[1]
    text = parts[2]

    invoice = get_invoice_by_order_number(order_number)
    if not invoice:
        await message.answer(
            f"❌ Заказ с номером {order_number} не найден.",
            reply_markup=main_menu(is_manager=True)
        )
        return

    client_tg_id = invoice.get("client_tg_id")
    if not client_tg_id:
        await message.answer(
            "❌ У заказа нет Telegram ID клиента.",
            reply_markup=main_menu(is_manager=True)
        )
        return

    try:
        await message.bot.send_message(
            client_tg_id,
            f"📢 **Уведомление по заказу {order_number}**\n\n{text}"
        )
        await message.answer(
            f"✅ Уведомление отправлено клиенту по заказу {order_number}.",
            reply_markup=main_menu(is_manager=True)
        )
    except Exception as e:
        await message.answer(
            f"❌ Не удалось отправить сообщение: {str(e)}",
            reply_markup=main_menu(is_manager=True)
        )


# === Диагностика ===
@router.message(Command("showconfig"))
async def show_config(message: Message):
    if not await is_manager(message.from_user.id):
        await message.answer("⛔️ Нет доступа")
        return

    key = TBANK_TERMINAL_KEY
    if len(key) > 8:
        masked = f"{key[:4]}...{key[-4:]}"
    else:
        masked = "***"

    await message.answer(
        f"🔧 **Конфигурация**\n\n"
        f"TerminalKey: `{masked}`\n"
        f"API URL: `{TBANK_API_URL}`\n"
        f"База данных: `{DATABASE_PATH}`",
        reply_markup=main_menu(is_manager=True)
    )


@router.message(Command("getip"))
async def get_server_ip(message: Message):
    if not await is_manager(message.from_user.id):
        await message.answer("⛔️ Нет доступа")
        return
    try:
        ip = requests.get('https://httpbin.org/ip', timeout=5).json()['origin']
        await message.answer(
            f"🌐 **IP-адрес сервера:** `{ip}`",
            reply_markup=main_menu(is_manager=True)
        )
    except Exception as e:
        await message.answer(
            f"❌ Не удалось получить IP: {str(e)}",
            reply_markup=main_menu(is_manager=True)
        )