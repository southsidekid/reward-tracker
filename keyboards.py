from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from catalog import ALFA_SMART, ADDITIONAL_OFFERS, KIDS_CROSS, MEETING_PRODUCTS, TRAVEL_FIXED

RU_MONTHS = (
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
)

_CATEGORY_SUFFIXES = (" РФ", " нерез")


def _short_product_name(name: str) -> str:
    for suffix in _CATEGORY_SUFFIXES:
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def main_menu() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="➕ Добавить встречу", callback_data="menu:add")
    b.button(text="🔁 Повтор предыдущей", callback_data="menu:dup")
    b.button(text="📊 Сегодня", callback_data="menu:today")
    b.button(text="📈 Месяц", callback_data="menu:month")
    b.button(text="🧾 Отчёт за сегодня", callback_data="menu:report")
    b.button(text="🕘 История", callback_data="menu:history")
    b.button(text="↩️ Удалить последнюю", callback_data="menu:undo")
    b.adjust(2, 2, 2, 1)
    return b.as_markup()

def reports_menu() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="📊 Сегодня", callback_data="report:today")
    b.button(text="📈 Месяц", callback_data="report:month")
    b.button(text="🧾 Отчёт за сегодня", callback_data="report:daily")
    b.button(text="🕘 История", callback_data="report:history")
    b.button(text="⬅️ Назад", callback_data="nav:main")
    b.adjust(2, 2, 1)
    return b.as_markup()


def report_result_menu() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="⬅️ В отчёты", callback_data="menu:reports")
    b.button(text="🏠 Меню", callback_data="nav:main")
    b.adjust(2)
    return b.as_markup()


def settings_menu() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="↩️ Удалить последнюю встречу", callback_data="settings:undo")
    b.button(text="⬅️ Назад", callback_data="nav:main")
    b.adjust(1)
    return b.as_markup()


def cancel_to_menu() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="❌ Отмена", callback_data="nav:main")
    b.adjust(1)
    return b.as_markup()


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


_SHORT_OFFER_LABELS = {
    "pp_cc": "PPI/CC · T+30",
    "ks_4_10k": "КС · 4–10 тыс.",
    "ks_2_4k": "КС · 2–4 тыс.",
    "ks_up_to_1k": "КС · до 1 тыс.",
    "broker_50k": "БС · от 50 тыс.",
    "broker_minor_1k": "БС · несовер. от 1 тыс.",
    "broker_20_50k": "БС · 20–50 тыс.",
    "broker_1_20k": "БС · 1–20 тыс.",
    "invest_pocket": "Инвесткопилка · от 2,5 тыс.",
    "izk_active": "ИЗК · пополнение 30 тыс.",
    "izk_connect": "ИЗК · подключение",
    "saving_50k": "НС · от 50 тыс.",
    "saving_10k": "НС · от 10 тыс.",
    "millionaire": "Миллионер · от 10 тыс.",
    "pds": "ПДС · от 2 тыс.",
    "combo_cc1_cc2": "COMBO · CC1/CC2",
    "cross_cc_n2b": "CROSS · CC/N2B",
    "mvno_main_active": "MVNO · основная",
    "mvno_active": "MVNO · активирован",
    "mvno_inactive": "MVNO · не активирован",
    "cross_mvno_smart_gt5": "Кросс-SIM · Smart >5",
    "cross_mvno_like_lt5": "Кросс-SIM · Like <5",
    "cross_mvno_like_gt5": "Кросс-SIM · Like >5",
    "cross_mvno_like_gt10": "Кросс-SIM · Like >10",
}


def _short_offer_button(offer) -> str:
    label = _SHORT_OFFER_LABELS.get(offer.code, offer.name)
    return f"{label} · {offer.reward} ₽"


def offers_flat_keyboard() -> InlineKeyboardMarkup:
    """Единый плоский список оферов: быстрые в ряд, остальные в 2 колонки."""
    b = InlineKeyboardBuilder()

    # Быстрые (3 в один ряд)
    b.button(text=f"📱 Смарт +{ALFA_SMART.reward}", callback_data=f"add:{ALFA_SMART.code}")
    b.button(text=f"✈️ Тревел +{TRAVEL_FIXED.reward}", callback_data=f"add:{TRAVEL_FIXED.code}")
    b.button(text=f"👶 Детская +{KIDS_CROSS.reward}", callback_data=f"add:{KIDS_CROSS.code}")

    # Остальные — плоским списком
    for offer in ADDITIONAL_OFFERS:
        b.button(text=_short_offer_button(offer), callback_data=f"add:{offer.code}")

    # Действия
    b.button(text="✅ Готово", callback_data="offers:done")
    b.button(text="↩️ Удалить последний", callback_data="offers:undo")
    b.button(text="🏠 Меню", callback_data="nav:main")

    n = len(ADDITIONAL_OFFERS)
    pattern = [3]
    pattern += [2] * (n // 2)
    if n % 2:
        pattern.append(1)
    pattern += [1, 1, 1]
    b.adjust(*pattern)
    return b.as_markup()


def confirm_delete() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🗑 Да, удалить", callback_data="undo:yes")
    b.button(text="Отмена", callback_data="undo:no")
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
    b.button(text="⬅️ В отчёты", callback_data="menu:reports")
    b.button(text="🏠 Меню", callback_data="nav:main")
    b.adjust(3, 2)
    return b.as_markup()