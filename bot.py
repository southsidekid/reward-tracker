from __future__ import annotations

import asyncio
import logging
import re
from datetime import date, datetime

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from catalog import (
    MEETING_PRODUCTS,
    OFFER_ALIASES,
    OFFERS_BY_CODE,
    PRODUCT_ALIASES,
    QUICK_OFFERS,
    next_month_first,
    payout_date_for,
)
from config import load_config
from db import (
    add_offer,
    create_meeting,
    delete_last_meeting_for_date,
    delete_last_offer,
    delete_meeting_by_id,
    delete_offer_by_id,
    get_last_meeting_with_offers_for_date,
    get_meeting_by_id,
    get_meeting_offers,
    init_db,
    meeting_days_with_counts,
    offer_exists,
    today_meetings,
    update_meeting_code,
    upsert_user,
)
from keyboards import (
    cancel_to_menu,
    confirm_delete,
    confirm_meeting_delete,
    day_meetings_keyboard,
    days_list_keyboard,
    id_input_keyboard,
    main_menu,
    meeting_categories,
    meeting_editor_keyboard,
    meeting_products,
    month_selector_keyboard,
    offers_delete_list_keyboard,
    offers_editor_group_keyboard,
    offers_editor_keyboard,
    offers_pending_group_keyboard,
    offers_pending_keyboard,
    stats_menu,
)
from reports import (
    daily_report,
    day_meetings_text,
    history_text,
    main_screen_text,
    month_chart_quickchart,
    month_details_text,
    month_summary_text,
    today_text,
)
from states import AddMeeting, EditMeeting
from time_utils import today_local

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

cfg = load_config()
_bot_session = AiohttpSession(timeout=90)
bot = Bot(
    cfg.bot_token,
    session=_bot_session,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = Dispatcher()

_last_bot_msgs: dict[int, list[int]] = {}

VERSION = "3.2.3"

_ALL_PRODUCT_ALIASES: dict[str, str] = {}
for _k, _v in PRODUCT_ALIASES.items():
    _ALL_PRODUCT_ALIASES[_k] = _v
    _ALL_PRODUCT_ALIASES[_k.replace(" ", "")] = _v


def allowed(uid: int) -> bool:
    return not cfg.allowed_user_ids or uid in cfg.allowed_user_ids


# ---------------------------------------------------------------------------
# Хелперы управления сообщениями
# ---------------------------------------------------------------------------

async def _forget_and_delete(chat_id: int, *, skip: int | None = None) -> None:
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
    try:
        await call.answer(text, show_alert=show_alert)
    except TelegramBadRequest:
        pass


async def show_text(call: CallbackQuery, text: str, markup=None) -> None:
    chat_id = call.message.chat.id
    await _forget_and_delete(chat_id, skip=call.message.message_id)
    try:
        await call.message.delete()
    except Exception:
        pass
    msg = await call.message.answer(text, reply_markup=markup)
    _track(chat_id, msg.message_id)


async def _edit(call: CallbackQuery, text: str, markup=None) -> None:
    chat_id = call.message.chat.id
    await _forget_and_delete(chat_id, skip=call.message.message_id)
    try:
        msg = await call.message.edit_text(text, reply_markup=markup)
        _track(chat_id, msg.message_id)
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            _track(chat_id, call.message.message_id)
            return
        msg = await bot.send_message(chat_id, text, reply_markup=markup)
        _track(chat_id, msg.message_id)


async def _reply_and_track(message: Message, text: str, markup=None) -> Message:
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
# Парсер свободного ввода
# ---------------------------------------------------------------------------

def _normalize(text: str) -> str:
    text = text.lower().replace("ё", "е")
    text = re.sub(r"[^\w\s]+", " ", text, flags=re.UNICODE)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _match_product(normalized: str) -> tuple[str | None, str]:
    for alias in sorted(_ALL_PRODUCT_ALIASES.keys(), key=len, reverse=True):
        if normalized == alias:
            return _ALL_PRODUCT_ALIASES[alias], ""
        if normalized.startswith(alias):
            nxt = normalized[len(alias)]
            if nxt in " ,;":
                return _ALL_PRODUCT_ALIASES[alias], normalized[len(alias):].strip(" ,;")
    return None, normalized


def parse_free_entry(text: str) -> dict:
    normalized = _normalize(text)
    product_code, rest = _match_product(normalized)
    if not product_code:
        return {"error": "no_product"}

    category = report_type = None
    base_reward = 0
    for cat, products in MEETING_PRODUCTS.items():
        for code, rtype, base in products:
            if code == product_code:
                category, report_type, base_reward = cat, rtype, base
                break
        if category:
            break
    if not category:
        return {"error": "no_product"}

    tokens = [t for t in re.split(r"[,\s;]+", rest) if t]
    offers = []
    unknown = []
    for tok in tokens:
        code = OFFER_ALIASES.get(tok)
        offer = OFFERS_BY_CODE.get(code) if code else None
        if offer is None:
            unknown.append(tok)
        else:
            offers.append(offer)

    return {
        "product_code": product_code,
        "category": category,
        "report_type": report_type,
        "base_reward": base_reward,
        "offers": offers,
        "unknown": unknown,
    }


def _auto_meeting_code() -> str:
    # АВТО-ДДММ-ЧЧММ (без секунд)
    return f"АВТО-{datetime.now().strftime('%d%m-%H%M')}"


# ---------------------------------------------------------------------------
# Старт и навигация
# ---------------------------------------------------------------------------

@dp.message(CommandStart())
async def start(message: Message, state: FSMContext):
    if not await ensure_user(message):
        return
    await state.clear()
    text = await main_screen_text(message.from_user.id, VERSION)
    await _reply_and_track(message, text, main_menu())


@dp.callback_query(F.data == "nav:main")
async def nav_main(call: CallbackQuery, state: FSMContext):
    await safe_answer(call)
    if not await ensure_user(call):
        return
    await state.clear()
    text = await main_screen_text(call.from_user.id, VERSION)
    await show_text(call, text, main_menu())


# ---------------------------------------------------------------------------
# Статистика
# ---------------------------------------------------------------------------

@dp.callback_query(F.data == "menu:stats")
async def menu_stats(call: CallbackQuery, state: FSMContext):
    await safe_answer(call)
    if not await ensure_user(call):
        return
    await state.clear()
    await show_text(call, "📊 <b>Статистика</b>\n\nВыбери раздел:", stats_menu())


@dp.callback_query(F.data == "menu:report")
async def menu_report(call: CallbackQuery, state: FSMContext):
    await safe_answer(call)
    if not await ensure_user(call):
        return
    await state.clear()
    days = meeting_days_with_counts(call.from_user.id)
    if not days:
        await show_text(call, "🧾 <b>Отчёт</b>\n\nПока нет ни одной встречи.", stats_menu())
        return
    await show_text(
        call,
        "🧾 <b>Отчёт</b>\n\nВыбери день, в котором была активность:",
        days_list_keyboard(days),
    )


@dp.callback_query(F.data.startswith("rep:day:"))
async def rep_day(call: CallbackQuery, state: FSMContext):
    await safe_answer(call)
    if not await ensure_user(call):
        return
    await state.clear()
    day_iso = call.data.split(":", 2)[2]
    meetings = today_meetings(call.from_user.id, day_iso)
    if not meetings:
        text = f"🧾 <b>{day_iso}</b>\n\nВстреч нет.\n\n<i>Можно добавить встречу на этот день.</i>"
    else:
        text = day_meetings_text(call.from_user.id, day_iso) + "\n\n<i>Нажми встречу, чтобы отредактировать.</i>"
    await show_text(call, text, day_meetings_keyboard(meetings, day_iso))


@dp.callback_query(F.data.startswith("rep:newday:"))
async def rep_new_day(call: CallbackQuery, state: FSMContext):
    await safe_answer(call)
    if not await ensure_user(call):
        return
    day_iso = call.data.split(":", 2)[2]
    await state.clear()
    await state.update_data(meeting_date_override=day_iso)
    try:
        d = date.fromisoformat(day_iso)
        pretty = f"{d.day:02d}.{d.month:02d}.{d.year}"
    except Exception:
        pretty = day_iso
    await show_text(
        call,
        f"➕ <b>Активность на {pretty}</b>\n\nВыбери тип встречи:",
        meeting_categories(),
    )


@dp.callback_query(F.data.startswith("rep:mt:"))
async def rep_meeting(call: CallbackQuery, state: FSMContext):
    await safe_answer(call)
    if not await ensure_user(call):
        return
    await state.clear()
    mid = int(call.data.split(":", 2)[2])
    m = get_meeting_by_id(mid, call.from_user.id)
    if not m:
        await safe_answer(call, "Встреча не найдена", show_alert=True)
        return
    offers = get_meeting_offers(mid)
    lines = [
        "🧾 <b>Редактор встречи</b>",
        f"ID: <code>{m['meeting_code']}</code>",
        f"Дата: {m['meeting_date']}",
        f"Тип: <b>{m['report_type']}</b>",
        f"База: <b>{int(m['base_reward'])} ₽</b>",
    ]
    if offers:
        lines.append("")
        lines.append("Оферы:")
        for o in offers:
            lines.append(f"• {o['name']} · +{int(o['reward'])} ₽")
    else:
        lines.append("Оферов нет.")
    lines.append("")
    lines.append(f"Итого: <b>{int(m['total_reward'])} ₽</b>")
    await show_text(
        call,
        "\n".join(lines),
        meeting_editor_keyboard(mid, str(m["meeting_date"])),
    )


# ---- Редактор: ID --------------------------------------------------------

@dp.callback_query(F.data.startswith("rep:eid:"))
async def rep_edit_id(call: CallbackQuery, state: FSMContext):
    await safe_answer(call)
    if not await ensure_user(call):
        return
    mid = int(call.data.split(":", 2)[2])
    m = get_meeting_by_id(mid, call.from_user.id)
    if not m:
        await safe_answer(call, "Не найдено", show_alert=True)
        return
    await state.update_data(edit_mid=mid, edit_day=str(m["meeting_date"]))
    await state.set_state(EditMeeting.waiting_new_id)
    await _edit(
        call,
        f"🆔 Текущий ID: <code>{m['meeting_code']}</code>\n\n"
        "Введи новый ID одним сообщением.",
        cancel_to_menu(),
    )


@dp.message(EditMeeting.waiting_new_id)
async def rep_receive_new_id(message: Message, state: FSMContext):
    if not await ensure_user(message):
        return
    new_code = (message.text or "").strip()
    if not new_code or len(new_code) > 100:
        err = await message.answer("ID должен быть непустым и не длиннее 100 символов.")
        _track_extra(message.chat.id, err.message_id)
        return
    data = await state.get_data()
    mid = int(data.get("edit_mid", 0))
    day_iso = data.get("edit_day", "")
    if not mid:
        await _reply_and_track(message, "Сессия истекла.", main_menu())
        await state.clear()
        return
    update_meeting_code(mid, message.from_user.id, new_code)
    await state.clear()
    m = get_meeting_by_id(mid, message.from_user.id)
    await _reply_and_track(
        message,
        f"✅ ID обновлён: <code>{new_code}</code>",
        meeting_editor_keyboard(mid, str(m["meeting_date"])) if m else main_menu(),
    )


# ---- Редактор: добавить офер --------------------------------------------

@dp.callback_query(F.data.startswith("rep:add:"))
async def rep_add_offer(call: CallbackQuery, state: FSMContext):
    await safe_answer(call)
    if not await ensure_user(call):
        return
    mid = int(call.data.split(":", 2)[2])
    m = get_meeting_by_id(mid, call.from_user.id)
    if not m:
        await safe_answer(call, "Не найдено", show_alert=True)
        return
    offers = get_meeting_offers(mid)
    existing = {str(o["code"]) for o in offers}
    await state.update_data(edit_mid=mid, edit_day=str(m["meeting_date"]))
    await state.set_state(EditMeeting.editing_offers)
    await _edit(
        call,
        f"➕ <b>Добавить офер</b>\n\nК встрече <code>{m['meeting_code']}</code>",
        offers_editor_keyboard(existing),
    )


@dp.callback_query(EditMeeting.editing_offers, F.data.startswith("eadd:"))
async def rep_eadd(call: CallbackQuery, state: FSMContext):
    if not await ensure_user(call):
        return
    code = call.data.split(":", 1)[1]
    offer = OFFERS_BY_CODE.get(code)
    if offer is None:
        await safe_answer(call, "Ошибка", show_alert=True)
        return
    data = await state.get_data()
    mid = int(data.get("edit_mid", 0))
    m = get_meeting_by_id(mid, call.from_user.id)
    if not m:
        await safe_answer(call, "Не найдено", show_alert=True)
        return
    if offer_exists(mid, offer.code):
        await safe_answer(call, "Уже добавлено", show_alert=True)
        return
    add_offer(
        mid, offer.code, offer.name, offer.condition, offer.reward,
        payout_date_for(offer, str(m["meeting_date"])),
    )
    await safe_answer(call, f"✅ {offer.name}")
    offers = get_meeting_offers(mid)
    existing = {str(o["code"]) for o in offers}
    await _edit(
        call,
        f"➕ <b>Добавить офер</b>\n\nК встрече <code>{m['meeting_code']}</code>\n"
        f"Добавлено оферов: <b>{len(offers)}</b>",
        offers_editor_keyboard(existing),
    )


@dp.callback_query(EditMeeting.editing_offers, F.data.startswith("egrp:"))
async def rep_egrp(call: CallbackQuery, state: FSMContext):
    if not await ensure_user(call):
        return
    group = call.data.split(":", 1)[1]
    data = await state.get_data()
    mid = int(data.get("edit_mid", 0))
    offers = get_meeting_offers(mid) if mid else []
    existing = {str(o["code"]) for o in offers}
    if group == "back":
        await safe_answer(call)
        m = get_meeting_by_id(mid, call.from_user.id)
        await _edit(
            call,
            f"➕ <b>Добавить офер</b>\n\nК встрече <code>{m['meeting_code'] if m else ''}</code>",
            offers_editor_keyboard(existing),
        )
        return
    await safe_answer(call)
    await _edit(call, f"📁 <b>{group}</b>", offers_editor_group_keyboard(group, existing))


@dp.callback_query(F.data == "editor:back")
async def editor_back(call: CallbackQuery, state: FSMContext):
    await safe_answer(call)
    if not await ensure_user(call):
        return
    data = await state.get_data()
    mid = int(data.get("edit_mid", 0))
    await state.clear()
    m = get_meeting_by_id(mid, call.from_user.id) if mid else None
    if not m:
        await show_text(call, "🏠 Меню", main_menu())
        return
    offers = get_meeting_offers(mid)
    lines = [
        "🧾 <b>Редактор встречи</b>",
        f"ID: <code>{m['meeting_code']}</code>",
        f"Дата: {m['meeting_date']}",
        f"Тип: <b>{m['report_type']}</b>",
        f"База: <b>{int(m['base_reward'])} ₽</b>",
    ]
    if offers:
        lines.append("")
        for o in offers:
            lines.append(f"• {o['name']} · +{int(o['reward'])} ₽")
    lines.append("")
    lines.append(f"Итого: <b>{int(m['total_reward'])} ₽</b>")
    await show_text(call, "\n".join(lines), meeting_editor_keyboard(mid, str(m["meeting_date"])))


# ---- Редактор: удалить офер ----------------------------------------------

@dp.callback_query(F.data.startswith("rep:delo:"))
async def rep_del_offer_list(call: CallbackQuery, state: FSMContext):
    await safe_answer(call)
    if not await ensure_user(call):
        return
    mid = int(call.data.split(":", 2)[2])
    offers = get_meeting_offers(mid)
    if not offers:
        await safe_answer(call, "Оферов нет", show_alert=True)
        return
    await _edit(
        call,
        "🗑 <b>Удалить офер</b>\n\nВыбери, что удалить:",
        offers_delete_list_keyboard(offers, mid),
    )


@dp.callback_query(F.data.startswith("rep:delx:"))
async def rep_del_offer(call: CallbackQuery, state: FSMContext):
    await safe_answer(call)
    if not await ensure_user(call):
        return
    oid = int(call.data.split(":", 2)[2])
    deleted = delete_offer_by_id(oid, call.from_user.id)
    if not deleted:
        await safe_answer(call, "Не найдено", show_alert=True)
        return
    mid = int(deleted.get("mid"))
    m = get_meeting_by_id(mid, call.from_user.id)
    offers = get_meeting_offers(mid)
    lines = [
        f"🗑 Удалён офер: <b>{deleted['name']}</b> · {int(deleted['reward'])} ₽",
        "",
        "🧾 <b>Редактор встречи</b>",
        f"ID: <code>{m['meeting_code']}</code>",
        f"База: <b>{int(m['base_reward'])} ₽</b>",
    ]
    if offers:
        lines.append("")
        for o in offers:
            lines.append(f"• {o['name']} · +{int(o['reward'])} ₽")
    lines.append("")
    lines.append(f"Итого: <b>{int(m['total_reward'])} ₽</b>")
    await _edit(call, "\n".join(lines), meeting_editor_keyboard(mid, str(m["meeting_date"])))


# ---- Редактор: удалить встречу -------------------------------------------

@dp.callback_query(F.data.startswith("rep:dmt:"))
async def rep_del_meeting(call: CallbackQuery, state: FSMContext):
    await safe_answer(call)
    if not await ensure_user(call):
        return
    mid = int(call.data.split(":", 2)[2])
    m = get_meeting_by_id(mid, call.from_user.id)
    if not m:
        await safe_answer(call, "Не найдено", show_alert=True)
        return
    await _edit(
        call,
        f"⚠️ Удалить встречу <code>{m['meeting_code']}</code>?\n"
        "Действие необратимо.",
        confirm_meeting_delete(mid, str(m["meeting_date"])),
    )


@dp.callback_query(F.data.startswith("rep:dmt_yes:"))
async def rep_del_meeting_yes(call: CallbackQuery, state: FSMContext):
    await safe_answer(call)
    if not await ensure_user(call):
        return
    _, _, mid_s, day_iso = call.data.split(":", 3)
    mid = int(mid_s)
    ok = delete_meeting_by_id(mid, call.from_user.id)
    if not ok:
        await safe_answer(call, "Не найдено", show_alert=True)
        return
    days = meeting_days_with_counts(call.from_user.id)
    if not days:
        await show_text(call, "🗑 Удалено. Встреч больше нет.", stats_menu())
        return
    await show_text(
        call,
        "🗑 Встреча удалена.\n\n🧾 <b>Отчёт</b> — выбери день:",
        days_list_keyboard(days),
    )


# ---- История / Месяц / Удалить последнюю --------------------------------

@dp.callback_query(F.data == "menu:history")
async def menu_history(call: CallbackQuery, state: FSMContext):
    await safe_answer(call)
    if not await ensure_user(call):
        return
    await state.clear()
    await show_text(call, history_text(call.from_user.id), stats_menu())


@dp.callback_query(F.data == "menu:month")
async def menu_month(call: CallbackQuery, state: FSMContext):
    await safe_answer(call)
    if not await ensure_user(call):
        return
    await state.clear()
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
            month_summary_text(call.from_user.id, year, month)
            + "\n\n⚠️ Не удалось построить график (QuickChart недоступен).",
            month_selector_keyboard(year, month),
        )
        return

    await _forget_and_delete(chat_id, skip=call.message.message_id)
    try:
        await call.message.delete()
    except Exception:
        pass

    try:
        photo_msg = await bot.send_photo(
            chat_id,
            BufferedInputFile(chart.getvalue(), filename=chart.name),
            caption=month_summary_text(call.from_user.id, year, month),
            request_timeout=90,
        )
        chart.close()
        _track(chat_id, photo_msg.message_id)
    except Exception:
        log.exception("send_photo failed, falling back to text")
        chart.close()
        await bot.send_message(
            chat_id,
            month_summary_text(call.from_user.id, year, month)
            + "\n\n⚠️ Не удалось отправить график.",
            reply_markup=month_selector_keyboard(year, month),
        )
        return

    try:
        details_msg = await bot.send_message(
            chat_id,
            month_details_text(call.from_user.id, year, month),
            reply_markup=month_selector_keyboard(year, month),
            request_timeout=90,
        )
        _track_extra(chat_id, details_msg.message_id)
    except Exception:
        log.exception("send_message (details) failed")


@dp.callback_query(F.data == "menu:undo")
async def menu_undo(call: CallbackQuery, state: FSMContext):
    await safe_answer(call)
    if not await ensure_user(call):
        return
    await state.clear()

    today_iso = today_local().isoformat()
    last = get_last_meeting_with_offers_for_date(call.from_user.id, today_iso)
    if not last:
        await show_text(
            call,
            "⚠️ За сегодня встреч ещё не было — удалять нечего.",
            main_menu(),
        )
        return

    await show_text(
        call,
        f"⚠️ Удалить последнюю встречу за сегодня?\n\n"
        f"<code>{last['meeting_code']}</code> · {last['report_type']} · "
        f"<b>{int(last['total_reward'])} ₽</b>\n\n"
        "Действие необратимо.",
        confirm_delete(),
    )


@dp.callback_query(F.data == "undo:yes")
async def undo_yes(call: CallbackQuery):
    if not await ensure_user(call):
        return
    today_iso = today_local().isoformat()
    deleted = delete_last_meeting_for_date(call.from_user.id, today_iso)
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
    await show_text(call, "Отменено.", main_menu())


# ---------------------------------------------------------------------------
# Новая встреча (данные копятся в FSM до «Сохранить»)
# ---------------------------------------------------------------------------

@dp.callback_query(F.data == "menu:add")
async def menu_add(call: CallbackQuery, state: FSMContext):
    await safe_answer(call)
    if not await ensure_user(call):
        return
    await state.clear()
    await show_text(call, "➕ <b>Активность</b>\n\nВыбери тип встречи:", meeting_categories())


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
        await state.update_data(
            product_code="installation", report_type="Установка", base_reward=340,
        )
        await state.set_state(AddMeeting.waiting_id)
        await _edit(
            call,
            "🛠 <b>Установка</b>\n\nБаза: <b>340 ₽</b>\n\n"
            "🆔 Введи ID встречи одним сообщением или пропусти (сгенерируется автоматически).",
            id_input_keyboard(),
        )
    else:
        title = "🇷🇺 <b>РФ</b>" if category == "rf" else "🌍 <b>Нерезидент</b>"
        await _edit(call, title + "\n\nВыбери основной продукт встречи:", meeting_products(category))


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
    await state.update_data(
        category=category,
        product_code=product_code,
        report_type=report_type,
        base_reward=base,
        pending_offers=[],
    )
    await state.set_state(AddMeeting.waiting_id)
    await _edit(
        call,
        f"✅ <b>{report_type}</b>\n"
        f"База за встречу: <b>{base} ₽</b>\n\n"
        "🆔 Введи ID встречи одним сообщением или пропусти (сгенерируется автоматически).",
        id_input_keyboard(),
    )


# ---- Пропустить ID (авто-генерация) -------------------------------------

@dp.callback_query(F.data == "id:skip")
async def id_skip(call: CallbackQuery, state: FSMContext):
    if not await ensure_user(call):
        return
    data = await state.get_data()
    code = _auto_meeting_code()

    # --- повтор предыдущей ---
    if "dup_category" in data and "category" not in data:
        await safe_answer(call, f"ID: {code}")
        try:
            await call.message.delete()
        except Exception:
            pass
        await _finalize_dup(call.message, state, code)
        return

    # --- новая встреча ---
    if not data.get("category"):
        await safe_answer(call, "Сессия истекла", show_alert=True)
        return
    await safe_answer(call, f"ID: {code}")
    await _continue_after_id(call, state, code)


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
    if "category" not in data:
        await _reply_and_track(message, "Сессия истекла, начни заново.", main_menu())
        await state.clear()
        return
    try:
        await message.delete()
    except Exception:
        pass
    await _continue_after_id(message, state, meeting_code)


async def _continue_after_id(update, state: FSMContext, meeting_code: str) -> None:
    data = await state.get_data()
    await state.update_data(meeting_code=meeting_code, pending_offers=[])
    category = data.get("category")
    report_type = data.get("report_type")
    base = data.get("base_reward")

    if category == "install":
        text = (
            f"🛠 <b>Установка</b>\n\n"
            f"ID: <code>{meeting_code}</code>\n"
            f"База: <b>{base} ₽</b>\n\n"
            "Нажми «Сохранить», чтобы записать в базу."
        )
        await state.set_state(AddMeeting.waiting_offers)
        if isinstance(update, CallbackQuery):
            await _edit(update, text, offers_pending_keyboard())
        else:
            await _reply_and_track(update, text, offers_pending_keyboard())
        return

    await state.set_state(AddMeeting.waiting_offers)
    text = (
        f"✅ Основной продукт: <b>{report_type}</b>\n"
        f"ID: <code>{meeting_code}</code>\n"
        f"База: <b>{base} ₽</b>\n\n"
        "Добавь оферы (быстрые кнопки или разделы). Запись произойдёт только после «Сохранить»."
    )
    if isinstance(update, CallbackQuery):
        await _edit(update, text, offers_pending_keyboard())
    else:
        await _reply_and_track(update, text, offers_pending_keyboard())


# ---- Добавление оферов (pending) ----------------------------------------

@dp.callback_query(AddMeeting.waiting_offers, F.data.startswith("add:"))
async def cb_add_offer(call: CallbackQuery, state: FSMContext):
    if not await ensure_user(call):
        return
    code = call.data.split(":", 1)[1]
    offer = OFFERS_BY_CODE.get(code)
    if offer is None:
        await safe_answer(call, "Ошибка", show_alert=True)
        return
    data = await state.get_data()
    pending = list(data.get("pending_offers", []))
    if any(o.get("code") == offer.code for o in pending):
        await safe_answer(call, "Этот офер уже добавлен", show_alert=True)
        return
    pending.append({
        "code": offer.code,
        "name": offer.name,
        "condition": offer.condition,
        "reward": offer.reward,
        "deferred": offer.deferred,
    })
    await state.update_data(pending_offers=pending)
    await safe_answer(call, f"+ {offer.name}")
    await _render_pending(call, state, flash=f"✅ Добавлено: <b>{offer.name}</b> · +{offer.reward} ₽")


@dp.callback_query(AddMeeting.waiting_offers, F.data.startswith("grp:"))
async def cb_group(call: CallbackQuery, state: FSMContext):
    if not await ensure_user(call):
        return
    group = call.data.split(":", 1)[1]
    await safe_answer(call)
    data = await state.get_data()
    if group == "back":
        await _render_pending(call, state)
        return
    pending = data.get("pending_offers", [])
    count = len(pending)
    await _edit(
        call,
        f"📁 <b>{group}</b>\n\nДобавлено оферов: <b>{count}</b>",
        offers_pending_group_keyboard(group),
    )


async def _render_pending(call: CallbackQuery, state: FSMContext, flash: str | None = None) -> None:
    data = await state.get_data()
    pending = data.get("pending_offers", [])
    report_type = data.get("report_type", "—")
    code = data.get("meeting_code", "—")
    base = int(data.get("base_reward", 0))
    lines = [
        f"✅ <b>{report_type}</b>",
        f"ID: <code>{code}</code>",
        f"База: <b>{base} ₽</b>",
    ]
    if pending:
        lines.append("")
        lines.append("Оферы:")
        for o in pending:
            lines.append(f"• {o['name']} · +{int(o['reward'])} ₽")
        total = base + sum(int(o["reward"]) for o in pending)
        lines.append("")
        lines.append(f"Итого: <b>{total} ₽</b>")
    else:
        lines.append("")
        lines.append("Оферы не добавлены.")
    if flash:
        lines.insert(0, flash)
        lines.insert(1, "")
    await _edit(call, "\n".join(lines), offers_pending_keyboard())


@dp.callback_query(AddMeeting.waiting_offers, F.data == "offers:undo")
async def undo_pending_offer(call: CallbackQuery, state: FSMContext):
    if not await ensure_user(call):
        return
    data = await state.get_data()
    pending = list(data.get("pending_offers", []))
    if not pending:
        await safe_answer(call, "Удалять нечего", show_alert=True)
        return
    removed = pending.pop()
    await state.update_data(pending_offers=pending)
    await safe_answer(call, "Удалено")
    await _render_pending(call, state, flash=f"↩️ Удалён: <b>{removed['name']}</b>")


@dp.callback_query(AddMeeting.waiting_offers, F.data == "offers:save")
async def save_pending_meeting(call: CallbackQuery, state: FSMContext):
    await safe_answer(call)
    if not await ensure_user(call):
        return
    data = await state.get_data()
    if not data.get("meeting_code"):
        await safe_answer(call, "Сессия истекла", show_alert=True)
        return
    today_iso = today_local().isoformat()
    meeting_date = data.get("meeting_date_override") or today_iso
    mid = create_meeting(
        call.from_user.id,
        str(data["meeting_code"]),
        str(data["category"]),
        str(data["report_type"]),
        meeting_date,
        int(data["base_reward"]),
    )
    for o in data.get("pending_offers", []):
        cat = OFFERS_BY_CODE.get(o["code"])
        payout = payout_date_for(cat, meeting_date) if cat else None
        add_offer(mid, o["code"], o["name"], o["condition"], int(o["reward"]), payout)
    await state.clear()
    await show_text(
        call,
        "💾 <b>Встреча сохранена.</b>\n\n" + today_text(call.from_user.id),
        main_menu(),
    )


# ---- Текстовый быстрый ввод оферов (pending) ----------------------------

@dp.message(AddMeeting.waiting_offers)
async def text_offers_in_meeting(message: Message, state: FSMContext):
    if not await ensure_user(message):
        return
    raw = (message.text or "").strip()
    if not raw:
        return

    data = await state.get_data()
    pending = list(data.get("pending_offers", []))
    existing = {o["code"] for o in pending}

    tokens = [t for t in re.split(r"[,\s;]+", _normalize(raw)) if t]
    added, skipped, unknown = [], [], []
    for tok in tokens:
        code = OFFER_ALIASES.get(tok)
        offer = OFFERS_BY_CODE.get(code) if code else None
        if offer is None:
            unknown.append(tok)
            continue
        if offer.code in existing:
            skipped.append(offer.name)
            continue
        pending.append({
            "code": offer.code,
            "name": offer.name,
            "condition": offer.condition,
            "reward": offer.reward,
            "deferred": offer.deferred,
        })
        existing.add(offer.code)
        added.append(f"{offer.name} · +{offer.reward} ₽")

    await state.update_data(pending_offers=pending)

    lines = ["⚡ <b>Быстрый ввод</b>"]
    if added:
        lines += ["", "✅ Добавлено:"] + [f"• {x}" for x in added]
    if skipped:
        lines += ["", "⚠️ Уже были:"] + [f"• {x}" for x in skipped]
    if unknown:
        lines += ["", "❓ Не распознал:"] + [f"• {x}" for x in unknown]
        lines.append("<i>Сложные тарифы — через кнопки.</i>")
    lines += ["", f"Оферов: <b>{len(pending)}</b>"]

    await _reply_and_track(message, "\n".join(lines), offers_pending_keyboard())


# ---------------------------------------------------------------------------
# «Повтор предыдущей» (только за сегодня)
# ---------------------------------------------------------------------------

@dp.callback_query(F.data == "menu:dup")
async def menu_dup(call: CallbackQuery, state: FSMContext):
    await safe_answer(call)
    if not await ensure_user(call):
        return
    await state.clear()

    today_iso = today_local().isoformat()
    last = get_last_meeting_with_offers_for_date(call.from_user.id, today_iso)
    if not last:
        await show_text(
            call,
            "🔁 За сегодня встреч ещё не было — повторять нечего.",
            main_menu(),
        )
        return

    await state.update_data(
        dup_category=last["category"],
        dup_report_type=last["report_type"],
        dup_base_reward=int(last["base_reward"]),
        dup_offers=[
            {
                "code": o["code"],
                "name": o["name"],
                "condition": o["condition"],
                "reward": int(o["reward"]),
                "payout_date": o["payout_date"],
            }
            for o in last["offers"]
        ],
    )
    await state.set_state(AddMeeting.waiting_dup_id)

    offers_text = "\n".join(
        f"• {o['name']} · {int(o['reward'])} ₽" for o in last["offers"]
    ) or "• (без доп. оферов)"
    await show_text(
        call,
        f"🔁 <b>Повтор предыдущей (за сегодня)</b>\n\n"
        f"Прошлый тип: <b>{last['report_type']}</b>\n"
        f"База: <b>{int(last['base_reward'])} ₽</b>\n"
        f"Оферы:\n{offers_text}\n\n"
        f"🆔 Введи <b>новый</b> ID встречи или пропусти (сгенерируется автоматически).",
        id_input_keyboard(),
    )


@dp.message(AddMeeting.waiting_dup_id)
async def enter_dup_id(message: Message, state: FSMContext):
    if not await ensure_user(message):
        return
    meeting_code = (message.text or "").strip()
    if not meeting_code or len(meeting_code) > 100:
        err = await message.answer("ID должен быть непустым и не длиннее 100 символов.")
        _track_extra(message.chat.id, err.message_id)
        return

    data = await state.get_data()
    if "dup_category" not in data:
        await _reply_and_track(message, "Сессия истекла, начни заново.", main_menu())
        await state.clear()
        return

    try:
        await message.delete()
    except Exception:
        pass
    await _finalize_dup(message, state, meeting_code)


async def _finalize_dup(message: Message, state: FSMContext, meeting_code: str) -> None:
    data = await state.get_data()
    today_iso = today_local().isoformat()
    mid = create_meeting(
        message.from_user.id,
        meeting_code,
        data["dup_category"],
        data["dup_report_type"],
        today_iso,
        int(data["dup_base_reward"]),
    )
    for old in data.get("dup_offers", []):
        code = old["code"]
        cat_offer = OFFERS_BY_CODE.get(code)
        if cat_offer is not None:
            payout = payout_date_for(cat_offer, today_iso)
        elif old.get("payout_date"):
            payout = next_month_first(today_iso)
        else:
            payout = None
        add_offer(mid, code, old["name"], old["condition"], int(old["reward"]), payout)

    await state.clear()
    await _reply_and_track(
        message,
        f"✅ <b>Повтор создан</b>\n"
        f"ID: <code>{meeting_code}</code>\n"
        f"Тип: <b>{data['dup_report_type']}</b>\n\n"
        + today_text(message.from_user.id),
        main_menu(),
    )


# ---------------------------------------------------------------------------
# Отмена
# ---------------------------------------------------------------------------

@dp.callback_query(F.data == "cancel")
async def cancel(call: CallbackQuery, state: FSMContext):
    await safe_answer(call)
    if not await ensure_user(call):
        return
    await state.clear()
    await show_text(call, "Действие отменено.", main_menu())


# ---------------------------------------------------------------------------
# Свободный ввод: «дк рф смарт защитник» → создаём сразу
# ---------------------------------------------------------------------------

@dp.message(StateFilter(None), F.text, ~F.text.startswith("/"))
async def free_entry(message: Message, state: FSMContext):
    if not await ensure_user(message):
        return
    parsed = parse_free_entry(message.text or "")
    if parsed.get("error") == "no_product":
        await _reply_and_track(
            message,
            "❌ Не понял тип встречи.\n\n"
            "Примеры:\n"
            "<i>дк рф смарт защитник</i>\n"
            "<i>х5 нерез тревел детская</i>\n"
            "<i>апельсин рф смарт</i>",
            main_menu(),
        )
        return

    today_iso = today_local().isoformat()
    code = _auto_meeting_code()
    mid = create_meeting(
        message.from_user.id,
        code,
        parsed["category"],
        parsed["report_type"],
        today_iso,
        int(parsed["base_reward"]),
    )

    added_lines, skipped_lines = [], []
    for offer in parsed["offers"]:
        if offer_exists(mid, offer.code):
            skipped_lines.append(f"• {offer.name}")
            continue
        add_offer(
            mid, offer.code, offer.name, offer.condition, offer.reward,
            payout_date_for(offer, today_iso),
        )
        added_lines.append(f"• {offer.name} · +{offer.reward} ₽")

    total = int(parsed["base_reward"]) + sum(int(o["reward"]) for o in get_meeting_offers(mid))

    lines = [
        f"✅ <b>{parsed['report_type']}</b> · <code>{code}</code>",
        f"База: <b>{parsed['base_reward']} ₽</b>",
    ]
    if added_lines:
        lines += ["", "Оферы:"] + added_lines
    if skipped_lines:
        lines += ["", "⚠️ Уже были (пропущено):"] + skipped_lines
    if parsed["unknown"]:
        lines += ["", "❓ Не распознал: <i>" + ", ".join(parsed["unknown"]) + "</i>"]
    lines += ["", f"<b>Итого: {total} ₽</b>"]

    await _reply_and_track(message, "\n".join(lines), main_menu())


# ---------------------------------------------------------------------------
# Запуск
# ---------------------------------------------------------------------------

async def main():
    init_db()
    log.info("Starting reward tracker bot v%s", VERSION)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())