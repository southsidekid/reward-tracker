"""Каталог мотивации для Reward Tracker."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Offer:
    code: str
    group: str
    name: str
    condition: str
    reward: int
    deferred: bool = False


def next_month_first(iso_date: str) -> str:
    year, month, _ = (int(part) for part in iso_date.split("-"))
    month += 1
    if month == 13:
        year += 1
        month = 1
    return f"{year:04d}-{month:02d}-01"


def payout_date_for(offer: Offer, meeting_date: str) -> str | None:
    if offer.deferred:
        return next_month_first(meeting_date)
    return None


MEETING_PRODUCTS = {
    "rf": (
        ("x5_rf", "X5 РФ", 340),
        ("dc_rf", "ДК РФ", 340),
        ("cc_rf", "КК РФ", 600),
        ("re_rf", "Перевыпуск RE", 340),
    ),
    "nonresident": (
        ("x5_nonresident", "X5 нерез", 340),
        ("dc_nonresident", "ДК нерез", 600),
        ("cc_nonresident", "КК нерез", 600),
    ),
    "install": (
        ("installation", "Установка", 340),
    ),
}

# ---- Быстрые оферы (одно нажатие) ----------------------------------------

ALFA_SMART = Offer(
    "a_smart", "Смарт", "Смарт", "Подписка Альфа Смарт", 30, deferred=True,
)
PP_CC = Offer(
    "pp_cc", "Страховки", "Защитник", "PPI/CC (T+30)", 100, deferred=True,
)
COMBO_CC1_CC2 = Offer(
    "combo_cc1_cc2", "Комбо", "COMBO CC1 / CC2", "Активирован (лимит + ПИН, T+3)", 630,
)
KIDS_CROSS = Offer(
    "kids_cross", "Доп. детская", "Детская DC CROSS KIDS", "Активированная", 450,
)
TRAVEL_FIXED = Offer(
    "travel_fixed", "Доп. тревел", "Доп. тревел", "Фиксированная сумма", 280,
)

QUICK_OFFERS: tuple[Offer, ...] = (
    ALFA_SMART, PP_CC, COMBO_CC1_CC2, KIDS_CROSS, TRAVEL_FIXED,
)

# ---- Менее популярные — по разделам --------------------------------------

ADDITIONAL_OFFERS: tuple[Offer, ...] = (
    # Страховки
    Offer("ks_4_10k", "Страховки", "Коробочное страхование КС", "Тариф 4 000–10 000 ₽", 630, True),
    Offer("ks_2_4k", "Страховки", "Коробочное страхование КС", "Тариф 2 000–3 999 ₽", 450, True),
    Offer("ks_up_to_1k", "Страховки", "Коробочное страхование КС", "Тариф до 1 000 ₽", 100, True),

    # Инвест
    Offer("broker_50k", "Инвест", "Брокерский счёт", "Сделка от 50 000 ₽", 630),
    Offer("broker_minor_1k", "Инвест", "Брокерский счёт", "Несовершеннолетний + сделка от 1 000 ₽", 450),
    Offer("broker_20_50k", "Инвест", "Брокерский счёт", "Сделка 20 000–49 999 ₽", 450),
    Offer("broker_1_20k", "Инвест", "Брокерский счёт", "Сделка 1 000–19 999 ₽", 280),
    Offer("invest_pocket", "Инвест", "Инвесткопилка", "Пополнение от 2 500 ₽ + автонакопление", 280),
    Offer("izk_active", "Инвест", "ИЗК", "Активация (пополнение от 30 000 ₽)", 280),
    Offer("izk_connect", "Инвест", "ИЗК", "Подключение", 0),

    # Накоп
    Offer("saving_50k", "Накоп", "Накопительный счёт", "Пополнение от 50 000 ₽", 280),
    Offer("saving_10k", "Накоп", "Накопительный счёт", "Пополнение от 10 000 ₽", 160),
    Offer("millionaire", "Накоп", "НС «Миллионер»", "Пополнение от 10 000 ₽", 160),
    Offer("pds", "Накоп", "ПДС", "Открыт и пополнен от 2 000 ₽", 280),

    # СИМ — только 4 оффера с картинки (кросс-сим MVNO)
    Offer("cross_mvno_smart_gt5", "СИМ", "Кросс-сим MVNO (тариф Смарт, only)", "Больше 5 штук", 160),
    Offer("cross_mvno_like_lt5", "СИМ", "Кросс-сим MVNO (тариф Лайк)", "Меньше 5 штук", 280),
    Offer("cross_mvno_like_gt5", "СИМ", "Кросс-сим MVNO (тариф Лайк)", "Больше 5 штук", 450),
    Offer("cross_mvno_like_gt10", "СИМ", "Кросс-сим MVNO (тариф Лайк)", "Больше 10 штук", 630),
)

OFFER_GROUPS = tuple(dict.fromkeys(o.group for o in ADDITIONAL_OFFERS))

OFFERS_BY_CODE: dict[str, Offer] = {o.code: o for o in ADDITIONAL_OFFERS}
for _q in QUICK_OFFERS:
    OFFERS_BY_CODE[_q.code] = _q

OFFERS_BY_GROUP = {
    group: tuple(o for o in ADDITIONAL_OFFERS if o.group == group)
    for group in OFFER_GROUPS
}

# Короткие ярлыки для отчёта (в т.ч. для быстрых оферов без папки).
OFFER_GROUP_ABBR = {
    "Смарт": "смарт",
    "Страховки": "страховка",
    "Инвест": "инвест",
    "Накоп": "накоп",
    "Комбо": "комбо",
    "Доп. детская": "кросс детская",
    "СИМ": "сим",
    "Доп. тревел": "кросс тревел",
}

# Алиасы для быстрого ввода оферов текстом.
OFFER_ALIASES: dict[str, str] = {
    # Быстрые
    "смарт": "a_smart",
    "smart": "a_smart",
    "тревел": "travel_fixed",
    "трэвел": "travel_fixed",
    "travel": "travel_fixed",
    "детская": "kids_cross",
    "kids": "kids_cross",
    "crosskids": "kids_cross",

    # Защитник / страховки
    "защитник": "pp_cc",
    "pp": "pp_cc",
    "ppi": "pp_cc",
    "пипи": "pp_cc",
    "сс": "pp_cc",
    "страх": "pp_cc",
    "страховка": "pp_cc",
    "кс": "ks_4_10k",
    "кс10": "ks_4_10k",
    "кс4": "ks_2_4k",
    "кс1": "ks_up_to_1k",

    # Инвест
    "бс": "broker_1_20k",
    "брокер": "broker_50k",
    "бс50": "broker_50k",
    "бс20": "broker_20_50k",
    "инвесткопилка": "invest_pocket",
    "копилка": "invest_pocket",
    "изк": "izk_active",

    # Накоп
    "нс": "pds",
    "накоп": "pds",
    "накопительный": "saving_50k",
    "миллионер": "millionaire",
    "пдс": "pds",

    # Комбо / CROSS
    "кл": "combo_cc1_cc2",
    "комбо": "combo_cc1_cc2",
    "combo": "combo_cc1_cc2",
    "n2b": "cross_cc_n2b",
    "кроссcc": "cross_cc_n2b",

    # СИМ (кросс-сим MVNO)
    "сим": "cross_mvno_like_lt5",
    "симка": "cross_mvno_like_lt5",
}

PRODUCT_ALIASES: dict[str, str] = {
    "х5 рф": "x5_rf",
    "x5 рф": "x5_rf",
    "апельсин рф": "x5_rf",
    "дк рф": "dc_rf",
    "кк рф": "cc_rf",
    "ре рф": "re_rf",
    "перевыпуск ре": "re_rf",

    "х5 нерез": "x5_nonresident",
    "х5 нерезидент": "x5_nonresident",
    "x5 нерез": "x5_nonresident",
    "x5 нерезидент": "x5_nonresident",
    "апельсин нерез": "x5_nonresident",
    "дк нерез": "dc_nonresident",
    "дк нерезидент": "dc_nonresident",
    "кк нерез": "cc_nonresident",
    "кк нерезидент": "cc_nonresident",

    "мп": "installation",
    "установка": "installation",
    "установка мп": "installation",
    "установка ам": "installation",
    "установка аи": "installation",
}

ALL_OFFERS: tuple[Offer, ...] = tuple(QUICK_OFFERS) + tuple(ADDITIONAL_OFFERS)