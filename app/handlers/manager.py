from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from datetime import datetime
import requests
import json

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
    waiting_comment = State()


class ManagerTrackForm(StatesGroup):
    waiting_order_number = State()
    waiting_track_number = State()


class ManagerNotifyForm(StatesGroup):
    waiting_order_number = State()
    waiting_message_text = State()


async def is_manager(user_id: int) -> bool:
    return user_id in MANAGER_IDS


@router.message(Command("menu"))
async def cmd_menu(message: Message):
    if not await is_manager(message.from_user.id):
        await message.answer("⛔️ Нет доступа")
        return
    await message.answer(
        "👋 <b>Главное меню</b>\n\nВыберите действие:",
        parse_mode="HTML",
        reply_markup=main_menu(is_manager=True)
    )


# === Функция нормализации payment_id ===
def normalize_payment_id(pid: str) -> str:
    if pid.startswith("group_"):
        return pid[6:]
    return pid


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
            description=bank_description,
            success_url=f"https://t.me/{bot_username}",
            fail_url=f"https://t.me/{bot_username}",
            client_tg_id=creator_id
        )
        payment_id = payment_result["payment_id"]  # числовой PaymentId
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

    # Нормализуем payment_id перед сохранением
    order_id_normalized = normalize_payment_id(order_id)

    # Сохраняем в БД
    try:
        save_invoice({
            "payment_id": order_id_normalized,
            "amount": amount_rub * 100,
            "amount_rub": amount_rub,
            "order_number": None,
            "delivery_address": None,
            "client_tg_id": creator_id if creator_id else None,
            "client_username": None,
            "creator_tg_id": creator_id,
            "description": comment,
            "is_group": 0,
            "orders_data": None
        })
        saved = get_invoice_by_payment_id(order_id_normalized)
        if saved:
            print(f"✅ Инвойс сохранён: {saved}")
        else:
            print(f"❌ Инвойс НЕ сохранён для payment_id = {order_id_normalized}")
            for manager_id in MANAGER_IDS:
                try:
                    await message.bot.send_message(
                        manager_id,
                        f"⚠️ Не удалось сохранить инвойс с payment_id = {order_id_normalized}. Проверьте БД."
                    )
                except Exception:
                    pass
    except Exception as e:
        print(f"❌ Ошибка при сохранении инвойса: {e}")
        for manager_id in MANAGER_IDS:
            try:
                await message.bot.send_message(
                    manager_id,
                    f"❌ Критическая ошибка при сохранении инвойса {order_id_normalized}: {str(e)}"
                )
            except Exception:
                pass

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()
    builder.button(text="🔗 Создать ещё одну ссылку", callback_data="manager_link")
    builder.button(text="🏠 Главное меню", callback_data="manager_back")
    builder.adjust(1)

    if sbp_available:
        await message.answer(
            f"🔗 <b>Ссылка для оплаты</b>\n"
            f"💰 <b>Сумма:</b> {amount_rub:,} ₽\n\n"
            f"📝 <b>Ваш комментарий:</b> {comment}\n"
            f"🔗 {qr_data}\n\n"
            f"_Ссылка откроется автоматически при нажатии._",
            parse_mode="HTML",
            reply_markup=builder.as_markup()
        )
    else:
        await message.answer(
            f"⚠️ <b>СБП временно недоступна.</b>\n"
            f"Используйте обычную ссылку:\n\n"
            f"💳 {payment_url}\n"
            f"💰 <b>Сумма:</b> {amount_rub:,} ₽\n"
            f"📝 <b>Ваш комментарий:</b> {comment}",
            parse_mode="HTML",
            reply_markup=builder.as_markup()
        )


@router.message(Command("link"))
async def manager_link_command(message: Message):
    if not await is_manager(message.from_user.id):
        await message.answer("⛔️ <b>Нет доступа</b>", parse_mode="HTML")
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
                parse_mode="HTML",
                reply_markup=main_menu(is_manager=True)
            )
            return

        comment = parts[2] if len(parts) > 2 else "Ручная ссылка"
        await create_link(message, message.from_user.id, amount_rub, comment)

    except:
        await message.answer(
            "❌ Неверный формат\n\n"
            "Использование: <code>/link [сумма] [комментарий]</code>\n"
            "Сумма должна быть кратна 1200.",
            parse_mode="HTML",
            reply_markup=main_menu(is_manager=True)
        )


@router.callback_query(F.data == "manager_link")
async def manager_link_start(callback: CallbackQuery, state: FSMContext):
    if not await is_manager(callback.from_user.id):
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return

    await state.clear()
    await state.set_state(ManagerLinkForm.waiting_amount)

    await callback.message.edit_text(
        "🔗 <b>Создание ссылки для оплаты</b>\n\n"
        "Введите сумму в рублях (кратную 1200):\n"
        "Например: 2400, 4800, 6000",
        parse_mode="HTML",
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
                parse_mode="HTML",
                reply_markup=manager_back_keyboard()
            )
            return

        await state.update_data(amount=amount)
        await state.set_state(ManagerLinkForm.waiting_comment)

        await message.answer(
            "✏️ Введите комментарий (для внутреннего учёта).\n"
            "Этот комментарий будет виден только вам в уведомлении об оплате.\n"
            "Можно оставить пустым, нажав «Отправить».",
            parse_mode="HTML",
            reply_markup=manager_back_keyboard()
        )

    except ValueError:
        await message.answer(
            "❌ Ошибка! Введите корректную сумму (целое положительное число).",
            parse_mode="HTML",
            reply_markup=manager_back_keyboard()
        )


@router.message(ManagerLinkForm.waiting_comment)
async def process_manager_comment(message: Message, state: FSMContext):
    data = await state.get_data()
    amount = data.get("amount")
    if not amount:
        await message.answer("❌ Что-то пошло не так. Попробуйте заново.", parse_mode="HTML", reply_markup=main_menu(is_manager=True))
        await state.clear()
        return

    comment = message.text.strip()
    if not comment:
        comment = "Ручная ссылка"

    await create_link(message, message.from_user.id, amount, comment)
    await state.clear()


# ======= ИЗМЕНЕННАЯ КНОПКА «Главное меню» =======
# Теперь она НЕ удаляет клавиатуру у уведомления,
# а просто отправляет новое сообщение с меню.
@router.callback_query(F.data == "manager_back")
async def manager_back(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    is_man = await is_manager(callback.from_user.id)
    # НЕ удаляем клавиатуру у текущего сообщения, оставляем её как есть
    # await callback.message.edit_reply_markup(reply_markup=None)  # закомментировано
    # Отправляем новое сообщение с меню
    await callback.message.answer(
        "👋 <b>Главное меню</b>\n\nВыберите действие:",
        parse_mode="HTML",
        reply_markup=main_menu(is_man)
    )
    await callback.answer()


@router.message(Command("check"))
async def manager_check_command(message: Message):
    if not await is_manager(message.from_user.id):
        await message.answer("⛔️ <b>Нет доступа</b>", parse_mode="HTML")
        return

    parts = message.text.split()
    if len(parts) < 2:
        await message.answer(
            "❌ Использование: <code>/check [ID_платежа или номер_заказа]</code>",
            parse_mode="HTML",
            reply_markup=main_menu(is_manager=True)
        )
        return

    identifier = parts[1].strip()
    invoice = get_invoice_by_payment_id(identifier)
    if not invoice:
        invoice = get_invoice_by_order_number(identifier)

    if not invoice:
        await message.answer(
            f"❌ Платеж с идентификатором <code>{identifier}</code> не найден.",
            parse_mode="HTML",
            reply_markup=main_menu(is_manager=True)
        )
        return

    status = check_payment_status(invoice["payment_id"])
    status_map = {"CONFIRMED": "paid", "REFUNDED": "refunded", "CANCELED": "canceled"}
    new_status = status_map.get(status, "unknown")

    if new_status == "paid" and invoice["status"] != "paid":
        update_invoice_status(invoice["payment_id"], "paid")
        invoice = get_invoice_by_payment_id(invoice["payment_id"])

    answer = f"📊 <b>Информация о платеже</b>\n\n"
    answer += f"🆔 <b>ID платежа:</b> {invoice['payment_id'][:20]}...\n"
    answer += f"💰 <b>Сумма:</b> {invoice['amount_rub']:,} ₽\n"
    answer += f"📦 <b>Заказ:</b> {invoice['order_number'] or 'Ручная ссылка'}\n"
    answer += f"📝 <b>Комментарий (ваш):</b> {invoice.get('description') or 'Не указан'}\n"
    answer += f"📍 <b>Адрес:</b> {invoice['delivery_address'] or 'Не указан'}\n"
    answer += f"👤 <b>ФИО:</b> {invoice.get('client_name') or 'Не указано'}\n"
    answer += f"📱 <b>Телефон:</b> {invoice.get('client_phone') or 'Не указан'}\n"
    answer += f"📅 <b>Создан:</b> {invoice['created_at']}\n"
    answer += f"🔘 <b>Статус в T‑Банк:</b> {status}\n"
    answer += f"🔘 <b>Статус в БД:</b> {invoice['status'].upper()}\n"

    if invoice.get("client_tg_id"):
        answer += f"\n👤 <b>Клиент:</b> @{invoice.get('client_username', 'Неизвестно')} (ID: {invoice['client_tg_id']})"

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
    await message.answer(answer, parse_mode="HTML", reply_markup=builder.as_markup())


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

    await state.update_data(
        order_number=order_number,
        original_chat_id=callback.message.chat.id,
        original_message_id=callback.message.message_id
    )
    await callback.message.reply(
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

    await state.update_data(
        order_number=order_number,
        original_chat_id=callback.message.chat.id,
        original_message_id=callback.message.message_id
    )
    await callback.message.reply(
        f"📢 Введите текст уведомления для заказа {order_number}:"
    )
    await state.set_state(ManagerNotifyForm.waiting_message_text)
    await callback.answer()


# === Гибкие обработчики для групповых платежей ===
@router.callback_query(F.data.startswith("track_group_"))
async def track_group_start(callback: CallbackQuery, state: FSMContext):
    if not await is_manager(callback.from_user.id):
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return
    payment_id = callback.data.split("_", 2)[2]
    invoice = get_invoice_by_payment_id(payment_id)
    if not invoice and payment_id.startswith("group_"):
        alt_id = payment_id[6:]
        invoice = get_invoice_by_payment_id(alt_id)
    if not invoice:
        await callback.answer("❌ Платёж не найден.", show_alert=True)
        return
    if not invoice.get("is_group"):
        await callback.answer("❌ Это не групповой платёж.", show_alert=True)
        return
    await state.update_data(
        group_payment_id=payment_id,
        original_chat_id=callback.message.chat.id,
        original_message_id=callback.message.message_id
    )
    await callback.message.reply(
        f"📦 Введите трек-номер для группового заказа (будет отправлен инициатору):"
    )
    await state.set_state(ManagerTrackForm.waiting_track_number)
    await callback.answer()


@router.callback_query(F.data.startswith("notify_group_"))
async def notify_group_start(callback: CallbackQuery, state: FSMContext):
    if not await is_manager(callback.from_user.id):
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return
    payment_id = callback.data.split("_", 2)[2]
    invoice = get_invoice_by_payment_id(payment_id)
    if not invoice and payment_id.startswith("group_"):
        alt_id = payment_id[6:]
        invoice = get_invoice_by_payment_id(alt_id)
    if not invoice:
        await callback.answer("❌ Платёж не найден.", show_alert=True)
        return
    if not invoice.get("is_group"):
        await callback.answer("❌ Это не групповой платёж.", show_alert=True)
        return
    await state.update_data(
        group_payment_id=payment_id,
        original_chat_id=callback.message.chat.id,
        original_message_id=callback.message.message_id
    )
    await callback.message.reply(
        f"📢 Введите текст уведомления для группового заказа (будет отправлен инициатору):"
    )
    await state.set_state(ManagerNotifyForm.waiting_message_text)
    await callback.answer()


@router.message(ManagerTrackForm.waiting_track_number)
async def process_track_number(message: Message, state: FSMContext):
    data = await state.get_data()
    group_payment_id = data.get("group_payment_id")
    order_number = data.get("order_number")
    track_number = message.text.strip()
    if not track_number:
        await message.answer("❌ Трек-номер не может быть пустым. Введите ещё раз.")
        return

    original_chat_id = data.get("original_chat_id")
    original_message_id = data.get("original_message_id")

    if group_payment_id:
        invoice = get_invoice_by_payment_id(group_payment_id)
        if not invoice and group_payment_id.startswith("group_"):
            alt_id = group_payment_id[6:]
            invoice = get_invoice_by_payment_id(alt_id)
        if not invoice:
            await message.answer("❌ Платёж не найден.", reply_markup=main_menu(is_manager=True))
            await state.clear()
            return
        client_tg_id = invoice.get("client_tg_id")
        if not client_tg_id:
            await message.answer("❌ У группового заказа нет Telegram ID клиента.", reply_markup=main_menu(is_manager=True))
            await state.clear()
            return
        try:
            orders_data = invoice.get("orders_data")
            orders_list = json.loads(orders_data) if orders_data else []
            orders_text = "\n".join([f"• Заказ {o.get('order_number')}" for o in orders_list])
            await message.bot.send_message(
                client_tg_id,
                f"📦 <b>Ваши заказы отправлены!</b>\n\n"
                f"Заказы:\n{orders_text}\n\n"
                f"Трек-номер для отслеживания: <code>{track_number}</code>\n"
                f"Вы можете отследить его на сайте СДЭК.",
                parse_mode="HTML"
            )
            if original_chat_id and original_message_id:
                await message.bot.send_message(
                    original_chat_id,
                    f"✅ Трек-номер отправлен клиенту по групповому заказу.",
                    reply_to_message_id=original_message_id,
                    parse_mode="HTML"
                )
            else:
                await message.answer(f"✅ Трек-номер отправлен клиенту по групповому заказу.", parse_mode="HTML")
        except Exception as e:
            await message.answer(f"❌ Не удалось отправить сообщение: {str(e)}", parse_mode="HTML")
        await state.clear()
        return

    # обычный случай
    if not order_number:
        await message.answer("❌ Ошибка: номер заказа не найден. Попробуйте заново.", parse_mode="HTML", reply_markup=main_menu(is_manager=True))
        await state.clear()
        return

    invoice = get_invoice_by_order_number(order_number)
    if not invoice:
        await message.answer(f"❌ Заказ {order_number} не найден.", parse_mode="HTML", reply_markup=main_menu(is_manager=True))
        await state.clear()
        return

    client_tg_id = invoice.get("client_tg_id")
    if not client_tg_id:
        await message.answer(f"❌ У заказа {order_number} нет Telegram ID клиента.", parse_mode="HTML", reply_markup=main_menu(is_manager=True))
        await state.clear()
        return

    try:
        await message.bot.send_message(
            client_tg_id,
            f"📦 <b>Ваш заказ {order_number} отправлен!</b>\n\n"
            f"Трек-номер для отслеживания: <code>{track_number}</code>\n"
            f"Вы можете отследить его на сайте СДЭК.",
            parse_mode="HTML"
        )
        if original_chat_id and original_message_id:
            await message.bot.send_message(
                original_chat_id,
                f"✅ Трек-номер отправлен клиенту по заказу {order_number}.",
                reply_to_message_id=original_message_id,
                parse_mode="HTML"
            )
        else:
            await message.answer(f"✅ Трек-номер отправлен клиенту по заказу {order_number}.", parse_mode="HTML", reply_markup=main_menu(is_manager=True))
    except Exception as e:
        await message.answer(f"❌ Не удалось отправить сообщение: {str(e)}", parse_mode="HTML", reply_markup=main_menu(is_manager=True))
    await state.clear()


@router.message(ManagerNotifyForm.waiting_message_text)
async def process_notify_text(message: Message, state: FSMContext):
    data = await state.get_data()
    group_payment_id = data.get("group_payment_id")
    order_number = data.get("order_number")
    text = message.text.strip()
    if not text:
        await message.answer("❌ Текст уведомления не может быть пустым. Введите ещё раз.")
        return

    original_chat_id = data.get("original_chat_id")
    original_message_id = data.get("original_message_id")

    if group_payment_id:
        invoice = get_invoice_by_payment_id(group_payment_id)
        if not invoice and group_payment_id.startswith("group_"):
            alt_id = group_payment_id[6:]
            invoice = get_invoice_by_payment_id(alt_id)
        if not invoice:
            await message.answer("❌ Платёж не найден.", reply_markup=main_menu(is_manager=True))
            await state.clear()
            return
        client_tg_id = invoice.get("client_tg_id")
        if not client_tg_id:
            await message.answer("❌ У группового заказа нет Telegram ID клиента.", reply_markup=main_menu(is_manager=True))
            await state.clear()
            return
        try:
            await message.bot.send_message(
                client_tg_id,
                f"📢 <b>Уведомление по групповому заказу</b>\n\n{text}",
                parse_mode="HTML"
            )
            if original_chat_id and original_message_id:
                await message.bot.send_message(
                    original_chat_id,
                    f"✅ Уведомление отправлено клиенту по групповому заказу.",
                    reply_to_message_id=original_message_id,
                    parse_mode="HTML"
                )
            else:
                await message.answer(f"✅ Уведомление отправлено клиенту по групповому заказу.", parse_mode="HTML")
        except Exception as e:
            await message.answer(f"❌ Не удалось отправить сообщение: {str(e)}", parse_mode="HTML")
        await state.clear()
        return

    # обычный случай
    if not order_number:
        await message.answer("❌ Ошибка: номер заказа не найден. Попробуйте заново.", parse_mode="HTML", reply_markup=main_menu(is_manager=True))
        await state.clear()
        return

    invoice = get_invoice_by_order_number(order_number)
    if not invoice:
        await message.answer(f"❌ Заказ {order_number} не найден.", parse_mode="HTML", reply_markup=main_menu(is_manager=True))
        await state.clear()
        return

    client_tg_id = invoice.get("client_tg_id")
    if not client_tg_id:
        await message.answer(f"❌ У заказа {order_number} нет Telegram ID клиента.", parse_mode="HTML", reply_markup=main_menu(is_manager=True))
        await state.clear()
        return

    try:
        await message.bot.send_message(
            client_tg_id,
            f"📢 <b>Уведомление по заказу {order_number}</b>\n\n{text}",
            parse_mode="HTML"
        )
        if original_chat_id and original_message_id:
            await message.bot.send_message(
                original_chat_id,
                f"✅ Уведомление отправлено клиенту по заказу {order_number}.",
                reply_to_message_id=original_message_id,
                parse_mode="HTML"
            )
        else:
            await message.answer(f"✅ Уведомление отправлено клиенту по заказу {order_number}.", parse_mode="HTML", reply_markup=main_menu(is_manager=True))
    except Exception as e:
        await message.answer(f"❌ Не удалось отправить сообщение: {str(e)}", parse_mode="HTML", reply_markup=main_menu(is_manager=True))
    await state.clear()


# === Кнопки главного меню ===
@router.callback_query(F.data == "manager_track_start")
async def manager_track_start(callback: CallbackQuery, state: FSMContext):
    if not await is_manager(callback.from_user.id):
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return

    await callback.message.edit_text(
        "📦 <b>Отправка трек-номера</b>\n\n"
        "Введите номер заказа:",
        parse_mode="HTML"
    )
    await state.set_state(ManagerTrackForm.waiting_order_number)
    await callback.answer()


@router.message(ManagerTrackForm.waiting_order_number)
async def process_track_order_number(message: Message, state: FSMContext):
    order_number = message.text.strip()
    if not order_number.isdigit():
        await message.answer(
            "❌ <b>Номер заказа должен содержать только цифры.</b>\n\n"
            "Пожалуйста, введите номер заказа ещё раз.",
            parse_mode="HTML",
            reply_markup=main_menu(is_manager=True)
        )
        return

    invoice = get_invoice_by_order_number(order_number)
    if not invoice:
        await message.answer(
            f"❌ Заказ с номером {order_number} не найден. Попробуйте ещё раз.",
            parse_mode="HTML",
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
        "📢 <b>Отправка уведомления клиенту</b>\n\n"
        "Введите номер заказа:",
        parse_mode="HTML"
    )
    await state.set_state(ManagerNotifyForm.waiting_order_number)
    await callback.answer()


@router.message(ManagerNotifyForm.waiting_order_number)
async def process_notify_order_number(message: Message, state: FSMContext):
    order_number = message.text.strip()
    if not order_number.isdigit():
        await message.answer(
            "❌ <b>Номер заказа должен содержать только цифры.</b>\n\n"
            "Пожалуйста, введите номер заказа ещё раз.",
            parse_mode="HTML",
            reply_markup=main_menu(is_manager=True)
        )
        return

    invoice = get_invoice_by_order_number(order_number)
    if not invoice:
        await message.answer(
            f"❌ Заказ с номером {order_number} не найден. Попробуйте ещё раз.",
            parse_mode="HTML",
            reply_markup=main_menu(is_manager=True)
        )
        return

    await state.update_data(order_number=order_number)
    await message.answer(f"📢 Введите текст уведомления для заказа {order_number}:")
    await state.set_state(ManagerNotifyForm.waiting_message_text)


@router.message(Command("track"))
async def cmd_track(message: Message):
    if not await is_manager(message.from_user.id):
        await message.answer("⛔️ <b>Нет доступа</b>", parse_mode="HTML")
        return

    parts = message.text.split(maxsplit=2)
    if len(parts) < 3:
        await message.answer(
            "❌ Использование: <code>/track [номер заказа] [трек-номер]</code>",
            parse_mode="HTML",
            reply_markup=main_menu(is_manager=True)
        )
        return

    order_number = parts[1]
    track_number = parts[2]

    invoice = get_invoice_by_order_number(order_number)
    if not invoice:
        await message.answer(
            f"❌ Заказ с номером {order_number} не найден.",
            parse_mode="HTML",
            reply_markup=main_menu(is_manager=True)
        )
        return

    client_tg_id = invoice.get("client_tg_id")
    if not client_tg_id:
        await message.answer(
            "❌ У заказа нет Telegram ID клиента.",
            parse_mode="HTML",
            reply_markup=main_menu(is_manager=True)
        )
        return

    try:
        await message.bot.send_message(
            client_tg_id,
            f"📦 <b>Ваш заказ {order_number} отправлен!</b>\n\n"
            f"Трек-номер для отслеживания: <code>{track_number}</code>\n"
            f"Вы можете отследить его на сайте СДЭК.",
            parse_mode="HTML"
        )
        await message.answer(
            f"✅ Трек-номер отправлен клиенту по заказу {order_number}.",
            parse_mode="HTML",
            reply_markup=main_menu(is_manager=True)
        )
    except Exception as e:
        await message.answer(
            f"❌ Не удалось отправить сообщение: {str(e)}",
            parse_mode="HTML",
            reply_markup=main_menu(is_manager=True)
        )


@router.message(Command("notify"))
async def cmd_notify(message: Message):
    if not await is_manager(message.from_user.id):
        await message.answer("⛔️ <b>Нет доступа</b>", parse_mode="HTML")
        return

    parts = message.text.split(maxsplit=2)
    if len(parts) < 3:
        await message.answer(
            "❌ Использование: <code>/notify [номер заказа] [текст уведомления]</code>",
            parse_mode="HTML",
            reply_markup=main_menu(is_manager=True)
        )
        return

    order_number = parts[1]
    text = parts[2]

    invoice = get_invoice_by_order_number(order_number)
    if not invoice:
        await message.answer(
            f"❌ Заказ с номером {order_number} не найден.",
            parse_mode="HTML",
            reply_markup=main_menu(is_manager=True)
        )
        return

    client_tg_id = invoice.get("client_tg_id")
    if not client_tg_id:
        await message.answer(
            "❌ У заказа нет Telegram ID клиента.",
            parse_mode="HTML",
            reply_markup=main_menu(is_manager=True)
        )
        return

    try:
        await message.bot.send_message(
            client_tg_id,
            f"📢 <b>Уведомление по заказу {order_number}</b>\n\n{text}",
            parse_mode="HTML"
        )
        await message.answer(
            f"✅ Уведомление отправлено клиенту по заказу {order_number}.",
            parse_mode="HTML",
            reply_markup=main_menu(is_manager=True)
        )
    except Exception as e:
        await message.answer(
            f"❌ Не удалось отправить сообщение: {str(e)}",
            parse_mode="HTML",
            reply_markup=main_menu(is_manager=True)
        )


@router.message(Command("showconfig"))
async def show_config(message: Message):
    if not await is_manager(message.from_user.id):
        await message.answer("⛔️ <b>Нет доступа</b>", parse_mode="HTML")
        return

    key = TBANK_TERMINAL_KEY
    if len(key) > 8:
        masked = f"{key[:4]}...{key[-4:]}"
    else:
        masked = "***"

    await message.answer(
        f"🔧 <b>Конфигурация</b>\n\n"
        f"TerminalKey: <code>{masked}</code>\n"
        f"API URL: <code>{TBANK_API_URL}</code>\n"
        f"База данных: <code>{DATABASE_PATH}</code>",
        parse_mode="HTML",
        reply_markup=main_menu(is_manager=True)
    )


@router.message(Command("getip"))
async def get_server_ip(message: Message):
    if not await is_manager(message.from_user.id):
        await message.answer("⛔️ <b>Нет доступа</b>", parse_mode="HTML")
        return
    try:
        ip = requests.get('https://httpbin.org/ip', timeout=5).json()['origin']
        await message.answer(
            f"🌐 <b>IP-адрес сервера:</b> <code>{ip}</code>",
            parse_mode="HTML",
            reply_markup=main_menu(is_manager=True)
        )
    except Exception as e:
        await message.answer(
            f"❌ Не удалось получить IP: {str(e)}",
            parse_mode="HTML",
            reply_markup=main_menu(is_manager=True)
        )