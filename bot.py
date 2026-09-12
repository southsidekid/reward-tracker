from __future__ import annotations

import asyncio
import logging
import re
from datetime import date, datetime

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
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
    delete_last_meeting,
    delete_last_offer,
    get_last_meeting_with_offers,
    init_db,
    offer_exists,
    upsert_user,
)
from keyboards import (
    cancel_to_menu,
    confirm_delete,
    main_menu,
    meeting_categories,
    meeting_products,
    month_selector_keyboard,
    offers_flat_keyboard,
    report_result_menu,
    reports_menu,
    settings_menu,
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

# Трекер активных сообщений бота в каждом чате (для чистки флуда)
_last_bot_msgs: dict[int, list[int]] = {}

HELLO = (
    "💰 <b>Reward Tracker</b>\n\n"
    "Напиши встречу в чат одной строкой:\n"
    "<i>дк рф смарт защитник</i>\n"
    "<i>х5 нерез тревел детская</i>\n"
    "<i>апельсин рф смарт</i>"
)

# --- Собираем все варианты PRODUCT_ALIASES (со пробелами и без) ----------
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
# Парсер свободного ввода: «дк рф смарт защитник»
# ---------------------------------------------------------------------------

def _normalize(text: str) -> str:
    text = text.lower().replace("ё", "е")
    text = re.sub(r"[^\w\s]+", " ", text, flags=re.UNICODE)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _match_product(normalized: str) -> tuple[str | None, str]:
    """Возвращает (product_code, rest) или (None, исходная строка)."""
    for alias in sorted(_ALL_PRODUCT_ALIASES.keys(), key=len, reverse=True):
        if normalized == alias:
            return _ALL_PRODUCT_ALIASES[alias], ""
        if normalized.startswith(alias):
            nxt = normalized[len(alias)]
            if nxt in " ,;":
                return _ALL_PRODUCT_ALIASES[alias], normalized[len(alias):].strip(" ,;")
    return None, normalized


def parse_free_entry(text: str) -> dict:
    """Парсит «дк рф смарт защитник» → product + offers."""
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
    return f"АВТО-{datetime.now().strftime('%d%m-%H%M%S')}"


# ---------------------------------------------------------------------------
# Старт и навигация
# ---------------------------------------------------------------------------

@dp.message(CommandStart())
async def start(message: Message, state: FSMContext):
    if not await ensure_user(message):
        return
    await state.clear()
    await _reply_and_track(message, HELLO, main_menu())


@dp.callback_query(F.data == "nav:main")
async def nav_main(call: CallbackQuery, state: FSMContext):
    await safe_answer(call)
    if not await ensure_user(call):
        return
    current_state = await state.get_state()
    await state.clear()
    text = HELLO
    if current_state == AddMeeting.waiting_offers.state:
        text = "✅ Встреча сохранена с текущими оферами.\n\n" + HELLO
    await show_text(call, text, main_menu())


@dp.callback_query(F.data == "menu:reports")
async def menu_reports(call: CallbackQuery, state: FSMContext):
    await safe_answer(call)
    if not await ensure_user(call):
        return
    await state.clear()
    await show_text(call, "📊 <b>Отчёты</b>\n\nВыбери раздел:", reports_menu())


@dp.callback_query(F.data == "menu:settings")
async def menu_settings(call: CallbackQuery, state: FSMContext):
    await safe_answer(call)
    if not await ensure_user(call):
        return
    await state.clear()
    await show_text(call, "⚙️ <b>Настройки</b>", settings_menu())


# ---------------------------------------------------------------------------
# Отчёты
# ---------------------------------------------------------------------------

@dp.callback_query(F.data == "report:today")
async def report_today(call: CallbackQuery):
    await safe_answer(call)
    if not await ensure_user(call):
        return
    await show_text(call, today_text(call.from_user.id), report_result_menu())


@dp.callback_query(F.data == "report:daily")
async def report_daily(call: CallbackQuery):
    await safe_answer(call)
    if not await ensure_user(call):
        return
    await show_text(call, daily_report(call.from_user.id), report_result_menu())


@dp.callback_query(F.data == "report:history")
async def report_history(call: CallbackQuery):
    await safe_answer(call)
    if not await ensure_user(call):
        return
    await show_text(call, history_text(call.from_user.id), report_result_menu())


@dp.callback_query(F.data == "report:month")
async def report_month(call: CallbackQuery):
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

    await _forget_and_delete(chat_id, skip=call.message.message_id)
    try:
        await call.message.delete()
    except Exception:
        pass

    photo_msg = await bot.send_photo(
        chat_id,
        BufferedInputFile(chart.getvalue(), filename=chart.name),
        caption=month_summary_text(call.from_user.id, year, month),
    )
    chart.close()

    details_msg = await bot.send_message(
        chat_id,
        month_details_text(call.from_user.id, year, month),
        reply_markup=month_selector_keyboard(year, month),
    )
    _track(chat_id, photo_msg.message_id, details_msg.message_id)


# ---------------------------------------------------------------------------
# Удаление последней встречи
# ---------------------------------------------------------------------------

@dp.callback_query(F.data == "settings:undo")
async def settings_undo(call: CallbackQuery):
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
    await show_text(call, text, settings_menu())


@dp.callback_query(F.data == "undo:no")
async def undo_no(call: CallbackQuery):
    await safe_answer(call)
    if not await ensure_user(call):
        return
    await show_text(call, "⚙️ <b>Настройки</b>", settings_menu())


# ---------------------------------------------------------------------------
# Кнопочный флоу добавления встречи
# ---------------------------------------------------------------------------

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
            cancel_to_menu(),
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
        cancel_to_menu(),
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
    if "category" not in data:
        await _reply_and_track(message, "Сессия истекла, начни заново.", main_menu())
        await state.clear()
        return

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
            "Жми оферы или напиши списком: <i>смарт, защитник, тревел</i>",
            offers_flat_keyboard(),
        )


# ---------------------------------------------------------------------------
# «Повтор предыдущей» — дублирование последней встречи, новый ID вводится вручную
# ---------------------------------------------------------------------------

@dp.callback_query(F.data == "menu:dup")
async def menu_dup(call: CallbackQuery, state: FSMContext):
    await safe_answer(call)
    if not await ensure_user(call):
        return
    await state.clear()

    last = get_last_meeting_with_offers(call.from_user.id)
    if not last:
        await show_text(call, "🔁 Повторять нечего — прошлых встреч нет.", main_menu())
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

    offers_text = "\n".join(f"• {o['name']} · {int(o['reward'])} ₽" for o in last["offers"]) or "• (без доп. оферов)"
    await show_text(
        call,
        f"🔁 <b>Повтор предыдущей</b>\n\n"
        f"Прошлый тип: <b>{last['report_type']}</b>\n"
        f"База: <b>{int(last['base_reward'])} ₽</b>\n"
        f"Оферы:\n{offers_text}\n\n"
        f"🆔 Введи <b>новый</b> ID встречи одним сообщением.",
        cancel_to_menu(),
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
# Добавление оферов в waiting_offers (кнопки + текст)
# ---------------------------------------------------------------------------

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
    mid = int(data["meeting_id"])
    if offer_exists(mid, offer.code):
        await safe_answer(call, "Этот офер уже добавлен", show_alert=True)
        return
    await safe_answer(call)
    add_offer(
        mid, offer.code, offer.name, offer.condition, offer.reward,
        payout_date_for(offer, today_local().isoformat()),
    )
    count = int(data.get("offer_count", 0)) + 1
    await state.update_data(offer_count=count)
    extra = "\nВ отчёте за сегодня есть, в стату месяца попадёт 1-го числа след. месяца." if offer.deferred else ""
    await _edit(
        call,
        f"✅ <b>Добавлено</b>\n{offer.name}\n+ <b>{offer.reward} ₽</b>{extra}\n\n"
        f"Оферов: <b>{count}</b> · Добавляй ещё или жми «Готово»",
        offers_flat_keyboard(),
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
    await _edit(
        call,
        f"↩️ Удалён: <b>{deleted.get('name', 'Офер')}</b> · {int(deleted.get('reward', 0))} ₽\n\n"
        f"Оферов: <b>{count}</b>",
        offers_flat_keyboard(),
    )


@dp.callback_query(AddMeeting.waiting_offers, F.data == "offers:done")
async def finish_meeting(call: CallbackQuery, state: FSMContext):
    await safe_answer(call)
    if not await ensure_user(call):
        return
    await state.clear()
    await show_text(call, "✅ <b>Встреча зафиксирована.</b>\n\n" + today_text(call.from_user.id), main_menu())


@dp.message(AddMeeting.waiting_offers)
async def text_offers_in_meeting(message: Message, state: FSMContext):
    """Пользователь дописывает оферы текстом, пока встреча в процессе."""
    if not await ensure_user(message):
        return
    raw = (message.text or "").strip()
    if not raw:
        return

    data = await state.get_data()
    mid = data.get("meeting_id")
    if not mid:
        await _reply_and_track(message, "Сессия истекла, начни заново.", main_menu())
        await state.clear()
        return

    tokens = [t for t in re.split(r"[,\s;]+", _normalize(raw)) if t]
    added, skipped, unknown = [], [], []
    for tok in tokens:
        code = OFFER_ALIASES.get(tok)
        offer = OFFERS_BY_CODE.get(code) if code else None
        if offer is None:
            unknown.append(tok)
            continue
        if offer_exists(int(mid), offer.code):
            skipped.append(offer.name)
            continue
        add_offer(
            int(mid), offer.code, offer.name, offer.condition, offer.reward,
            payout_date_for(offer, today_local().isoformat()),
        )
        added.append(f"{offer.name} · +{offer.reward} ₽")

    count = int(data.get("offer_count", 0)) + len(added)
    await state.update_data(offer_count=count)

    lines = ["⚡ <b>Быстрый ввод</b>"]
    if added:
        lines.append("")
        lines.append("✅ Добавлено:")
        lines.extend(f"• {x}" for x in added)
    if skipped:
        lines.append("")
        lines.append("⚠️ Уже были:")
        lines.extend(f"• {x}" for x in skipped)
    if unknown:
        lines.append("")
        lines.append("❓ Не распознал:")
        lines.extend(f"• {x}" for x in unknown)
        lines.append("<i>Сложные тарифы — через кнопки.</i>")
    lines.append("")
    lines.append(f"Всего оферов: <b>{count}</b>")

    await _reply_and_track(message, "\n".join(lines), offers_flat_keyboard())


# ---------------------------------------------------------------------------
# Отмена (совместимость)
# ---------------------------------------------------------------------------

@dp.callback_query(F.data == "cancel")
async def cancel(call: CallbackQuery, state: FSMContext):
    await safe_answer(call)
    if not await ensure_user(call):
        return
    await state.clear()
    await show_text(call, "Действие отменено.", main_menu())


# ---------------------------------------------------------------------------
# Свободный ввод вне FSM: «дк рф смарт защитник» → создаём встречу сразу
# ---------------------------------------------------------------------------

@dp.message(StateFilter(None), F.text, ~F.text.startswith("/"))
async def free_entry(message: Message, state: FSMContext):
    if not await ensure_user(message):
        return
    parsed = parse_free_entry(message.text or "")
    if parsed.get("error") == "no_product":
        # Не похоже на встречу — просто подскажем формат
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

    added_lines = []
    skipped_lines = []
    for offer in parsed["offers"]:
        if offer_exists(mid, offer.code):
            skipped_lines.append(f"• {offer.name}")
            continue
        add_offer(
            mid, offer.code, offer.name, offer.condition, offer.reward,
            payout_date_for(offer, today_iso),
        )
        added_lines.append(f"• {offer.name} · +{offer.reward} ₽")

    total = int(parsed["base_reward"]) + sum(
        int(o.reward) for o in parsed["offers"] if not offer_exists(mid, o.code) or True
    )
    # Правильнее пересчитать из БД:
    from db import get_meeting_offers as _gmo
    total = int(parsed["base_reward"]) + sum(int(o["reward"]) for o in _gmo(mid))

    lines = [
        f"✅ <b>{parsed['report_type']}</b> · <code>{code}</code>",
        f"База: <b>{parsed['base_reward']} ₽</b>",
    ]
    if added_lines:
        lines.append("")
        lines.append("Оферы:")
        lines.extend(added_lines)
    if skipped_lines:
        lines.append("")
        lines.append("⚠️ Уже были (пропущено):")
        lines.extend(skipped_lines)
    if parsed["unknown"]:
        lines.append("")
        lines.append("❓ Не распознал: <i>" + ", ".join(parsed["unknown"]) + "</i>")
    lines.append("")
    lines.append(f"<b>Итого: {total} ₽</b>")

    await _reply_and_track(message, "\n".join(lines), main_menu())


# ---------------------------------------------------------------------------

async def main():
    init_db()
    log.info("Starting reward tracker bot")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())