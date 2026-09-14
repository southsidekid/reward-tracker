from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from catalog import (
    ALFA_SMART,
    COMBO_CC1_CC2,
    KIDS_CROSS,
    MEETING_PRODUCTS,
    OFFER_GROUPS,
    OFFERS_BY_GROUP,
    PP_CC,
    TRAVEL_FIXED,
)

RU_MONTHS = (
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
)

_CATEGORY_SUFFIXES = (" РФ", " нерез")

_GROUP_EMOJI = {
    "Страховки": "🛡",
    "Инвест": "💼",
    "Накоп": "💰",
    "СИМ": "📲",
}

_SHORT_OFFER_LABELS = {
    "ks_4_10k": "КС · 4–10 тыс.",
    "ks_2_4k": "КС · 2–4 тыс.",
    "ks_up_to_1k": "КС · до 1 тыс.",
    "broker_50k": "БС · от 50 тыс.",
    "broker_minor_1k": "БС · несовер. от 1 тыс.",
    "broker_20_50k": "БС · 20–50 тыс.",
    "broker_1_20k": "БС · 1–20 тыс.",
    "invest_pocket": "Инвесткопилка",
    "izk_active": "ИЗК · активация",
    "izk_connect": "ИЗК · подключение",
    "saving_50k": "НС · от 50 тыс.",
    "saving_10k": "НС · от 10 тыс.",
    "millionaire": "Миллионер · от 10 тыс.",
    "pds": "ПДС · от 2 тыс.",
    "cross_mvno_smart_gt5": "Кросс-сим · Смарт >5",
    "cross_mvno_like_lt5": "Кросс-сим · Лайк <5",
    "cross_mvno_like_gt5": "Кросс-сим · Лайк >5",
    "cross_mvno_like_gt10": "Кросс-сим · Лайк >10",
}


def _short_product_name(name: str) -> str:
    for suffix in _CATEGORY_SUFFIXES:
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


# ---------------------------------------------------------------------------
# Главное меню и меню статистики
# ---------------------------------------------------------------------------

def main_menu() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="➕ Активность", callback_data="menu:add")
    b.button(text="🔁 Повтор предыдущей", callback_data="menu:dup")
    b.button(text="📊 Статистика", callback_data="menu:stats")
    b.button(text="↩️ Удалить последнюю", callback_data="menu:undo")
    b.adjust(2, 2)
    return b.as_markup()


def stats_menu() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🧾 Отчёты", callback_data="menu:report")
    b.button(text="📈 Месяц", callback_data="menu:month")
    b.button(text="🕘 История", callback_data="menu:history")
    b.button(text="⬅️ Назад", callback_data="nav:main")
    b.adjust(2, 1, 1)
    return b.as_markup()


# ---------------------------------------------------------------------------
# Ввод ID
# ---------------------------------------------------------------------------

def id_input_keyboard() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="⏭ Пропустить", callback_data="id:skip")
    b.button(text="❌ Отмена", callback_data="nav:main")
    b.adjust(1, 1)
    return b.as_markup()


def cancel_to_menu() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="❌ Отмена", callback_data="nav:main")
    b.adjust(1)
    return b.as_markup()


# ---------------------------------------------------------------------------
# Категории и основной продукт
# ---------------------------------------------------------------------------

def meeting_categories() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🇷🇺 РФ", callback_data="meetcat:rf")
    b.button(text="🌍 Нерезидент", callback_data="meetcat:nonresident")
    b.button(text="🛠 Установка", callback_data="meetcat:install")
    b.button(text="⬅️ Назад", callback_data="nav:main")
    b.adjust(2, 1, 1)
    return b.as_markup()


def meeting_products(category: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for code, text, reward in MEETING_PRODUCTS[category]:
        label = _short_product_name(text)
        b.button(text=f"{label} · {reward} ₽", callback_data=f"mprod:{category}:{code}")
    b.button(text="⬅️ Назад", callback_data="menu:add")
    b.button(text="🏠 Меню", callback_data="nav:main")
    b.adjust(2, 1, 1)
    return b.as_markup()


# ---------------------------------------------------------------------------
# Клавиатура оферов
# ---------------------------------------------------------------------------

def _quick_row(b: InlineKeyboardBuilder, prefix: str) -> None:
    b.button(text=f"📱 Смарт +{ALFA_SMART.reward}",   callback_data=f"{prefix}{ALFA_SMART.code}")
    b.button(text=f"🛡 Защитник +{PP_CC.reward}",    callback_data=f"{prefix}{PP_CC.code}")
    b.button(text=f"🎁 Комбо +{COMBO_CC1_CC2.reward}", callback_data=f"{prefix}{COMBO_CC1_CC2.code}")
    b.button(text=f"👶 Детская +{KIDS_CROSS.reward}", callback_data=f"{prefix}{KIDS_CROSS.code}")
    b.button(text=f"✈️ Тревел +{TRAVEL_FIXED.reward}", callback_data=f"{prefix}{TRAVEL_FIXED.code}")


def _group_rows(b: InlineKeyboardBuilder, group_prefix: str) -> None:
    for group in OFFER_GROUPS:
        emoji = _GROUP_EMOJI.get(group, "📁")
        b.button(text=f"{emoji} {group}", callback_data=f"{group_prefix}{group}")


def offers_pending_keyboard() -> InlineKeyboardMarkup:
    """Экран оферов для новой встречи (данные копятся в FSM)."""
    b = InlineKeyboardBuilder()
    _quick_row(b, "add:")
    _group_rows(b, "grp:")
    b.button(text="✅ Сохранить", callback_data="offers:save")
    b.button(text="↩️ Удалить последний", callback_data="offers:undo")
    b.button(text="❌ Отмена", callback_data="nav:main")
    b.adjust(3, 2, 2, 2, 1, 1, 1)
    return b.as_markup()


def offers_pending_group_keyboard(group: str) -> InlineKeyboardMarkup:
    """Экран внутри папки — без кнопки «Отмена»."""
    b = InlineKeyboardBuilder()
    for offer in OFFERS_BY_GROUP[group]:
        label = _SHORT_OFFER_LABELS.get(offer.code, offer.name)
        b.button(text=f"{label} · {offer.reward} ₽", callback_data=f"add:{offer.code}")
    b.button(text="⬅️ К разделам", callback_data="grp:back")
    b.button(text="✅ Сохранить", callback_data="offers:save")
    n = len(OFFERS_BY_GROUP[group])
    pattern = [2] * (n // 2) + ([1] if n % 2 else []) + [1, 1]
    b.adjust(*pattern)
    return b.as_markup()


def offers_editor_keyboard(existing_codes: set[str]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for off in (ALFA_SMART, PP_CC, COMBO_CC1_CC2, KIDS_CROSS, TRAVEL_FIXED):
        mark = "✓ " if off.code in existing_codes else ""
        b.button(text=f"{mark}{off.name} +{off.reward}", callback_data=f"eadd:{off.code}")
    for group in OFFER_GROUPS:
        emoji = _GROUP_EMOJI.get(group, "📁")
        b.button(text=f"{emoji} {group}", callback_data=f"egrp:{group}")
    b.button(text="⬅️ Назад", callback_data="editor:back")
    b.adjust(3, 2, 2, 2, 1)
    return b.as_markup()


def offers_editor_group_keyboard(group: str, existing_codes: set[str]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for offer in OFFERS_BY_GROUP[group]:
        label = _SHORT_OFFER_LABELS.get(offer.code, offer.name)
        mark = "✓ " if offer.code in existing_codes else ""
        b.button(text=f"{mark}{label} · {offer.reward} ₽", callback_data=f"eadd:{offer.code}")
    b.button(text="⬅️ К разделам", callback_data="egrp:back")
    n = len(OFFERS_BY_GROUP[group])
    pattern = [2] * (n // 2) + ([1] if n % 2 else []) + [1]
    b.adjust(*pattern)
    return b.as_markup()


# ---------------------------------------------------------------------------
# Отчёты
# ---------------------------------------------------------------------------

def days_list_keyboard(days: list[tuple[str, int]]) -> InlineKeyboardMarkup:
    from datetime import date as _date
    b = InlineKeyboardBuilder()
    for iso, n in days:
        try:
            label = _date.fromisoformat(iso).strftime("%d.%m.%Y")
        except Exception:
            label = iso
        b.button(text=f"📅 {label} · {n}", callback_data=f"rep:day:{iso}")
    b.button(text="⬅️ Назад", callback_data="menu:stats")
    b.adjust(2)
    return b.as_markup()


def day_meetings_keyboard(meetings, day_iso: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for m in meetings:
        b.button(
            text=f"✏️ {m['meeting_code']} · {m['report_type']}",
            callback_data=f"rep:mt:{int(m['id'])}",
        )
    b.button(text="➕ Добавить на этот день", callback_data=f"rep:newday:{day_iso}")
    b.button(text="⬅️ К дням", callback_data="menu:report")
    b.adjust(1)
    return b.as_markup()


def meeting_editor_keyboard(meeting_id: int, day_iso: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✏️ Изменить ID", callback_data=f"rep:eid:{meeting_id}")
    b.button(text="➕ Добавить офер", callback_data=f"rep:add:{meeting_id}")
    b.button(text="🗑 Удалить офер", callback_data=f"rep:delo:{meeting_id}")
    b.button(text="❌ Удалить встречу", callback_data=f"rep:dmt:{meeting_id}")
    b.button(text="⬅️ Назад", callback_data=f"rep:day:{day_iso}")
    b.adjust(2, 2, 1)
    return b.as_markup()


def offers_delete_list_keyboard(offers, meeting_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for o in offers:
        b.button(
            text=f"🗑 {o['name']} · {int(o['reward'])} ₽",
            callback_data=f"rep:delx:{int(o['id'])}",
        )
    b.button(text="⬅️ Назад", callback_data=f"rep:mt:{meeting_id}")
    b.adjust(1)
    return b.as_markup()


# ---------------------------------------------------------------------------
# Прочее
# ---------------------------------------------------------------------------

def confirm_delete() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🗑 Да, удалить", callback_data="undo:yes")
    b.button(text="Отмена", callback_data="undo:no")
    b.adjust(2)
    return b.as_markup()


def confirm_meeting_delete(meeting_id: int, day_iso: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🗑 Да, удалить", callback_data=f"rep:dmt_yes:{meeting_id}:{day_iso}")
    b.button(text="Отмена", callback_data=f"rep:mt:{meeting_id}")
    b.adjust(2)
    return b.as_markup()


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    total = year * 12 + (month - 1) + delta
    return total // 12, total % 12 + 1


def month_selector_keyboard(year: int, month: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for delta in (-1, 0, 1):
        y, m = _shift_month(year, month, delta)
        prefix = "◀️ " if delta == -1 else ("📊 " if delta == 0 else "▶️ ")
        b.button(
            text=f"{prefix}{RU_MONTHS[m - 1]}",
            callback_data=f"month:{y:04d}-{m:02d}",
        )
    b.button(text="🏠 Меню", callback_data="nav:main")
    b.adjust(3, 1)
    return b.as_markup()