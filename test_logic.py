from catalog import (
    ALFA_SMART,
    MEETING_PRODUCTS,
    OFFER_GROUPS,
    OFFERS_BY_CODE,
    TRAVEL_FIXED,
    next_month_first,
    payout_date_for,
)

assert dict((x[0], x[2]) for x in MEETING_PRODUCTS["rf"]) == {
    "x5_rf": 340,
    "dc_rf": 340,
    "cc_rf": 600,
    "re_rf": 340,
}
assert dict((x[0], x[2]) for x in MEETING_PRODUCTS["nonresident"]) == {
    "x5_nonresident": 340,
    "dc_nonresident": 600,
    "cc_nonresident": 600,
}
assert MEETING_PRODUCTS["install"][0][2] == 340

# Смарт больше не отдельная группа с подменю — только быстрый офер.
assert "Смарт" not in OFFER_GROUPS
assert "Доп. тревел" not in OFFER_GROUPS
assert ALFA_SMART.reward == 30
assert ALFA_SMART.deferred is True
assert ALFA_SMART.name == "Смарт"

# Доп. тревел — фиксированная сумма 280 ₽, без ручного ввода.
assert TRAVEL_FIXED.reward == 280
assert TRAVEL_FIXED.deferred is False

# Страхование СС (PPI/CC) идёт первым в списке страховок.
insurance_offers = [o for o in OFFERS_BY_CODE.values() if o.group == "Страховка"]
assert OFFERS_BY_CODE["pp_cc"].condition == "Действующая (T+30)"
assert OFFERS_BY_CODE["pp_cc"].deferred is True

# Инвестиции содержат только инвестиционные продукты.
assert OFFERS_BY_CODE["broker_50k"].group == "Инвестиции"
assert OFFERS_BY_CODE["invest_pocket"].group == "Инвестиции"
for code in ("saving_50k", "saving_10k", "millionaire", "pds"):
    assert OFFERS_BY_CODE[code].group == "Накопительный"
assert "Накопительный" in OFFER_GROUPS
assert "Инвестиции" in OFFER_GROUPS

assert OFFERS_BY_CODE["combo_cc1_cc2"].reward == 630
assert OFFERS_BY_CODE["kids_cross"].reward == 450

assert next_month_first("2026-09-10") == "2026-10-01"
assert next_month_first("2026-12-31") == "2027-01-01"
assert payout_date_for(ALFA_SMART, "2026-09-15") == "2026-10-01"
assert payout_date_for(TRAVEL_FIXED, "2026-09-15") is None

print("logic tests: OK")
