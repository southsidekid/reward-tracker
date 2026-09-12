from __future__ import annotations

import asyncio
import logging
from datetime import date

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from catalog import MEETING_PRODUCTS, OFFER_GROUPS, OFFERS_BY_GROUP, QUICK_OFFERS, payout_date_for
from config import load_config
from db import (
    add_offer,
    create_meeting,
    delete_last_meeting,
    delete_last_offer,
    init_db,
    offer_exists,
    upsert_user,
)
from keyboards import (
    confirm_delete,
    main_menu,
    meeting_categories,
    meeting_products,
    offer_groups,
    offers_keyboard,
    month_selector_keyboard,
)
from reports import (
    daily_report,
    history_text,
    month_chart_quickchart,
    month_details_text,
    month_summary_text,
    today_text,
)
from states import AddMeeting
from time_utils import today_local

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

cfg = load_config()
bot = Bot(cfg.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

QUICK_OFFERS_BY_CODE = {o.code: o for o in QUICK_OFFERS}

# Список "активных" сообщений бота в каждом чате. Перед отправкой нового
# ответа их удаляем, чтобы не копить флуд в истории.
_last_bot_msgs: dict[int, list[int]] = {}


def allowed(uid: int) -> bool:
    return not cfg.allowed_user_ids or uid in cfg.allowed_user_ids


# ---------------------------------------------------------------------------
# Хелперы для управления "последним сообщением"
# ---------------------------------------------------------------------------

async def _forget_and_delete(chat_id: int, *, skip: int | None = None) -> None:
    """Удаляем все трекнутые сообщения бота в чате (кроме skip)."""
    ids = _last_bot_msgs.pop(chat_id, [])
    for mid in ids:
        if skip is not None and mid == skip:
            continue
        try:
            await bot.delete_message(chat_id, mid)
        except Exception:
            pass


def _track(chat_id: int, *message_ids: int) -> None:
    _last_bot_msgs[chat_id] = list(message_ids)


def _track_extra(chat_id: int, message_id: int) -> None:
    _last_bot_msgs.setdefault(chat_id, []).append(message_id)


async def safe_answer(call: CallbackQuery, text: str | None = None, show_alert: bool = False) -> None:
    """Отвечаем на callback сразу, игнорируя 'query is too old'."""
    try:
        await call.answer(text, show_alert=show_alert)
    except TelegramBadRequest:
        pass


async def show_text(call: CallbackQuery, text: str, markup=None) -> None:
    """Удаляем старые сообщения бота + сообщение с кнопкой → шлём новое."""
    chat_id = call.message.chat.id
    await _forget_and_delete(chat_id, skip=call.message.message_id)
    try:
        await call.message.delete()
    except Exception:
        pass
    msg = await call.message.answer(text, reply_markup=markup)
    _track(chat_id, msg.message_id)


async def show_photo(call: CallbackQuery, image, caption: str, markup=None) -> None:
    chat_id = call.message.chat.id
    await _forget_and_delete(chat_id, skip=call.message.message_id)
    try:
        await call.message.delete()
    except Exception:
        pass
    msg = await call.message.answer_photo(
        BufferedInputFile(image.getvalue(), filename=image.name),
        caption=caption,
        reply_markup=markup,
    )
    _track(chat_id, msg.message_id)


async def _edit(call: CallbackQuery, text: str, markup=None) -> None:
    """Редактируем сообщение на месте, сохраняя его как «текущее»."""
    chat_id = call.message.chat.id
    await _forget_and_delete(chat_id, skip=call.message.message_id)
    try:
        msg = await call.message.edit_text(text, reply_markup=markup)
        _track(chat_id, msg.message_id)
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            _track(chat_id, call.message.message_id)
            return
        # Фоллбэк: отправляем новым сообщением (редкий случай)
        msg = await bot.send_message(chat_id, text, reply_markup=markup)
        _track(chat_id, msg.message_id)


async def _reply_and_track(message: Message, text: str, markup=None) -> Message:
    """Ответ на текстовое сообщение пользователя: чистим старое, шлём новое."""
    chat_id = message.chat.id
    try:
        await message.delete()
    except Exception:
        pass
    await _forget_and_delete(chat_id)
    msg = await message.answer(text, reply_markup=markup)
    _track(chat_id, msg.message_id)
    return msg


async def ensure_user(message: Message | CallbackQuery):
    u = message.from_user
    if not allowed(u.id):
        if isinstance(message, CallbackQuery):
            await safe_answer(message, "Доступ закрыт.", show_alert=True)
        else:
            await message.answer("Доступ закрыт для этого аккаунта.")
        return False
    upsert_user(u.id, u.username, u.first_name)
    return True


# ---------------------------------------------------------------------------
# Хендлеры
# ---------------------------------------------------------------------------

@dp.message(CommandStart())
async def start(message: Message, state: FSMContext):
    if not await ensure_user(message):
        return
    await state.clear()
    await _reply_and_track(
        message,
        "🅰️ <b>Reward Tracker</b> — version 3.0.4." \
        "\n\nДобавляй встречи, фиксируй оферы и смотри статистику по дням и месяцам." \
        " \n\nВыбери действие:",
        main_menu(),
    )


@dp.callback_query(F.data == "menu:add")
async def menu_add(call: CallbackQuery, state: FSMContext):
    await safe_answer(call)
    if not await ensure_user(call):
        return
    await state.clear()
    await show_text(call, "➕ <b>Добавить встречу</b>\n\nВыбери тип встречи:", meeting_categories())


@dp.callback_query(F.data.startswith("meetcat:"))
async def choose_meeting_category(call: CallbackQuery, state: FSMContext):
    await safe_answer(call)
    if not await ensure_user(call):
        return
    category = call.data.split(":", 1)[1]
    if category not in MEETING_PRODUCTS:
        await safe_answer(call, "Ошибка", show_alert=True)
        return
    await state.update_data(category=category)
    if category == "install":
        await state.update_data(product_code="installation", report_type="Установка", base_reward=340)
        await state.set_state(AddMeeting.waiting_id)
        await _edit(
            call,
            "🛠 <b>Установка</b>\n\nБаза: <b>340 ₽</b>\n\nТеперь введи ID встречи.",
        )
    else:
        title = "🇷🇺 <b>РФ</b>" if category == "rf" else "🌍 <b>Нерезидент</b>"
        await _edit(
            call,
            title + "\n\nВыбери основной продукт встречи:",
            meeting_products(category),
        )


@dp.callback_query(F.data.startswith("mprod:"))
async def choose_main_product(call: CallbackQuery, state: FSMContext):
    await safe_answer(call)
    if not await ensure_user(call):
        return
    _, category, product_code = call.data.split(":", 2)
    product = next((x for x in MEETING_PRODUCTS[category] if x[0] == product_code), None)
    if product is None:
        await safe_answer(call, "Ошибка", show_alert=True)
        return
    _, report_type, base = product
    await state.update_data(category=category, product_code=product_code, report_type=report_type, base_reward=base)
    await state.set_state(AddMeeting.waiting_id)
    await _edit(
        call,
        f"✅ <b>{report_type}</b>\n"
        f"База за встречу: <b>{base} ₽</b>\n\n"
        "🆔 Введи ID встречи одним сообщением.",
    )


@dp.message(AddMeeting.waiting_id)
async def enter_meeting_id(message: Message, state: FSMContext):
    if not await ensure_user(message):
        return
    meeting_code = (message.text or "").strip()
    if not meeting_code or len(meeting_code) > 100:
        err = await message.answer("ID должен быть непустым и не длиннее 100 символов.")
        _track_extra(message.chat.id, err.message_id)
        return

    data = await state.get_data()
    mid = create_meeting(
        message.from_user.id,
        meeting_code,
        data["category"],
        data["report_type"],
        today_local().isoformat(),
        int(data["base_reward"]),
    )
    await state.update_data(meeting_id=mid, offer_count=0)

    if data["category"] == "install":
        await state.clear()
        await _reply_and_track(
            message,
            "✅ <b>Установка зафиксирована.</b>\n\n" + today_text(message.from_user.id),
            main_menu(),
        )
    else:
        await state.set_state(AddMeeting.waiting_offers)
        await _reply_and_track(
            message,
            f"✅ Встреча сохранена: <code>{meeting_code}</code>\n"
            f"Тип: <b>{data['report_type']}</b>\n"
            f"База: <b>{data['base_reward']} ₽</b>\n\n"
            "Теперь добавляй дополнительные оферы. Можно добавить несколько.",
            offer_groups(),
        )


@dp.callback_query(AddMeeting.waiting_offers, F.data.startswith("ogroup:"))
async def choose_offer_group(call: CallbackQuery, state: FSMContext):
    await safe_answer(call)
    if not await ensure_user(call):
        return
    idx = int(call.data.split(":")[1])
    if idx < 0 or idx >= len(OFFER_GROUPS):
        await safe_answer(call, "Ошибка группы", show_alert=True)
        return
    group = OFFER_GROUPS[idx]
    await state.update_data(current_group_index=idx)
    await _edit(call, f"📦 <b>{group}</b>\n\nВыбери офер:", offers_keyboard(idx, OFFERS_BY_GROUP[group]))


@dp.callback_query(AddMeeting.waiting_offers, F.data == "offers:backgroups")
async def back_offer_groups(call: CallbackQuery, state: FSMContext):
    await safe_answer(call)
    if not await ensure_user(call):
        return
    await _edit(call, "📦 <b>Дополнительные оферы</b>\n\nВыбери группу:", offer_groups())


@dp.callback_query(AddMeeting.waiting_offers, F.data.startswith("offer:"))
async def add_selected_offer(call: CallbackQuery, state: FSMContext):
    if not await ensure_user(call):
        return
    _, idx_s, opt_s = call.data.split(":")
    idx, opt = int(idx_s), int(opt_s)
    if idx < 0 or idx >= len(OFFER_GROUPS):
        await safe_answer(call, "Ошибка", show_alert=True)
        return
    group = OFFER_GROUPS[idx]
    options = OFFERS_BY_GROUP.get(group, ())
    if opt < 0 or opt >= len(options):
        await safe_answer(call, "Ошибка", show_alert=True)
        return
    offer = options[opt]
    data = await state.get_data()
    mid = int(data["meeting_id"])
    if offer_exists(mid, offer.code):
        await safe_answer(call, "Этот офер уже добавлен в эту встречу", show_alert=True)
        return
    await safe_answer(call)
    add_offer(
        mid,
        offer.code,
        offer.name,
        offer.condition,
        offer.reward,
        payout_date_for(offer, today_local().isoformat()),
    )
    count = int(data.get("offer_count", 0)) + 1
    await state.update_data(offer_count=count)
    extra = ""
    if offer.deferred:
        extra = "\nВ отчёте за сегодня есть, в стату месяца попадёт 1-го числа следующего месяца."
    await _edit(
        call,
        f"✅ <b>Добавлено</b>\n{offer.name}\n{offer.condition}\n+ <b>{offer.reward} ₽</b>{extra}\n\n"
        f"Оферов: <b>{count}</b> · Добавить ещё?",
        offer_groups(),
    )


@dp.callback_query(AddMeeting.waiting_offers, F.data.startswith("oquick:"))
async def add_quick_offer(call: CallbackQuery, state: FSMContext):
    if not await ensure_user(call):
        return
    code = call.data.split(":", 1)[1]
    offer = QUICK_OFFERS_BY_CODE.get(code)
    if offer is None:
        await safe_answer(call, "Ошибка", show_alert=True)
        return
    data = await state.get_data()
    mid = int(data["meeting_id"])
    if offer_exists(mid, offer.code):
        await safe_answer(call, "Этот офер уже добавлен в эту встречу", show_alert=True)
        return
    await safe_answer(call)
    add_offer(
        mid,
        offer.code,
        offer.name,
        offer.condition,
        offer.reward,
        payout_date_for(offer, today_local().isoformat()),
    )
    count = int(data.get("offer_count", 0)) + 1
    await state.update_data(offer_count=count)
    extra = "\nВ отчёте за сегодня есть, в стату месяца попадёт 1-го числа следующего месяца." if offer.deferred else ""
    await _edit(
        call,
        f"✅ <b>Добавлено</b>\n{offer.name}\n+ <b>{offer.reward} ₽</b>{extra}\n\n"
        f"Оферов: <b>{count}</b> · Добавить ещё?",
        offer_groups(),
    )


@dp.callback_query(AddMeeting.waiting_offers, F.data == "offers:undo")
async def undo_offer(call: CallbackQuery, state: FSMContext):
    if not await ensure_user(call):
        return
    data = await state.get_data()
    deleted = delete_last_offer(int(data["meeting_id"]), call.from_user.id)
    if deleted is None:
        await safe_answer(call, "Удалять нечего", show_alert=True)
        return
    await safe_answer(call)
    count = max(0, int(data.get("offer_count", 0)) - 1)
    await state.update_data(offer_count=count)
    deleted_name = deleted.get("name", "Офер")
    deleted_reward = int(deleted.get("reward", 0))
    await _edit(
        call,
        f"↩️ Удалён: <b>{deleted_name}</b> · {deleted_reward} ₽\n\nВыбери следующий:",
        offer_groups(),
    )


@dp.callback_query(AddMeeting.waiting_offers, F.data == "offers:done")
async def finish_meeting(call: CallbackQuery, state: FSMContext):
    await safe_answer(call)
    if not await ensure_user(call):
        return
    await state.clear()
    await show_text(call, "✅ <b>Встреча зафиксирована.</b>\n\n" + today_text(call.from_user.id), main_menu())


@dp.callback_query(AddMeeting.waiting_offers, F.data == "meeting:cancel")
async def cancel_meeting(call: CallbackQuery, state: FSMContext):
    await safe_answer(call)
    if not await ensure_user(call):
        return
    await state.clear()
    deleted = delete_last_meeting(call.from_user.id)
    text = "🗑 Черновик встречи удалён." if deleted else "Действие отменено."
    await show_text(call, text, main_menu())


@dp.callback_query(F.data == "menu:today")
async def menu_today(call: CallbackQuery):
    await safe_answer(call)
    if not await ensure_user(call):
        return
    await show_text(call, today_text(call.from_user.id), main_menu())


@dp.callback_query(F.data == "menu:month")
async def menu_month(call: CallbackQuery):
    await safe_answer(call)
    if not await ensure_user(call):
        return
    today = date.today()
    await show_text(
        call,
        "📈 <b>Отчёт за месяц</b>\n\nВыбери месяц:",
        month_selector_keyboard(today.year, today.month),
    )


@dp.callback_query(F.data.startswith("month:"))
async def select_month(call: CallbackQuery):
    if not await ensure_user(call):
        return
    try:
        year_s, month_s = call.data.split(":", 1)[1].split("-", 1)
        year, month = int(year_s), int(month_s)
        if month < 1 or month > 12 or year < 2000 or year > 2100:
            raise ValueError
    except (ValueError, IndexError):
        await safe_answer(call, "Некорректный месяц", show_alert=True)
        return

    await safe_answer(call)
    chat_id = call.message.chat.id

    try:
        chart = await month_chart_quickchart(call.from_user.id, year, month)
    except Exception:
        log.exception("QuickChart request failed for %04d-%02d", year, month)
        await show_text(
            call,
            month_summary_text(call.from_user.id, year, month) +
            "\n\n⚠️ Не удалось построить график (QuickChart недоступен).",
            month_selector_keyboard(year, month),
        )
        return

    # Чистим всё старое (включая текущее сообщение с кнопкой)
    await _forget_and_delete(chat_id, skip=call.message.message_id)
    try:
        await call.message.delete()
    except Exception:
        pass

    # 1) Фото с короткой подписью, без кнопок
    photo_msg = await bot.send_photo(
        chat_id,
        BufferedInputFile(chart.getvalue(), filename=chart.name),
        caption=month_summary_text(call.from_user.id, year, month),
    )
    chart.close()

    # 2) Одно компактное сообщение с деталями и кнопками
    details_msg = await bot.send_message(
        chat_id,
        month_details_text(call.from_user.id, year, month),
        reply_markup=month_selector_keyboard(year, month),
    )
    _track(chat_id, photo_msg.message_id, details_msg.message_id)


@dp.callback_query(F.data == "menu:report")
async def menu_report(call: CallbackQuery):
    await safe_answer(call)
    if not await ensure_user(call):
        return
    await show_text(call, daily_report(call.from_user.id), main_menu())


@dp.callback_query(F.data == "menu:history")
async def menu_history(call: CallbackQuery):
    await safe_answer(call)
    if not await ensure_user(call):
        return
    await show_text(call, history_text(call.from_user.id), main_menu())


@dp.callback_query(F.data == "menu:undo")
async def menu_undo(call: CallbackQuery):
    await safe_answer(call)
    if not await ensure_user(call):
        return
    await show_text(call, "⚠️ Удалить последнюю добавленную встречу?\nУдаление необратимо.", confirm_delete())


@dp.callback_query(F.data == "undo:yes")
async def undo_yes(call: CallbackQuery):
    if not await ensure_user(call):
        return
    deleted = delete_last_meeting(call.from_user.id)
    await safe_answer(call)
    if deleted:
        deleted_offers = deleted.get("offers", [])
        details = "\n".join(
            f"• {o['name']} · {int(o['reward'])} ₽" for o in deleted_offers
        ) or "• Доп. оферов не было"
        text = (
            f"🗑 Удалена встреча <code>{deleted['meeting_code']}</code>\n"
            f"Тип: <b>{deleted['report_type']}</b>\n"
            f"База: {int(deleted['base_reward'])} ₽\n"
            f"Удалено оферов: {len(deleted_offers)}\n{details}\n\n"
            + today_text(call.from_user.id)
        )
    else:
        text = "Нечего удалять."
    await show_text(call, text, main_menu())


@dp.callback_query(F.data == "undo:no")
async def undo_no(call: CallbackQuery):
    await safe_answer(call)
    if not await ensure_user(call):
        return
    await show_text(call, "Удаление отменено.", main_menu())


@dp.callback_query(F.data == "cancel")
async def cancel(call: CallbackQuery, state: FSMContext):
    await safe_answer(call)
    if not await ensure_user(call):
        return
    await state.clear()
    await show_text(call, "Действие отменено.", main_menu())


async def main():
    init_db()
    log.info("Starting reward tracker bot")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())