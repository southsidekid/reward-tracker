from __future__ import annotations

from calendar import monthrange
from collections import defaultdict
from datetime import date
from io import BytesIO

import aiohttp

from catalog import OFFER_GROUP_ABBR, OFFERS_BY_CODE
from db import (
    get_meeting_offers,
    month_meetings,
    offers_deferred_arriving_in_month,
    offers_deferred_sold_in_month,
    offers_non_deferred_for_month,
    recent_meetings,
    today_meetings,
)
from time_utils import today_local

RU_MONTHS = (
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
)

BG = "#0B0B0B"
CARD = "#161616"
RED = "#E53935"
TEXT = "#F5F5F5"
MUTED = "#8E8E8E"

QUICKCHART_URL = "https://quickchart.io/chart"


def rub(n: int) -> str:
    return f"{n:,}".replace(",", " ") + " ₽"


def _is_deferred_offer(offer_row) -> bool:
    return bool(offer_row["payout_date"])


def _offer_group_label(code: str, name: str) -> str:
    catalog_offer = OFFERS_BY_CODE.get(code)
    if catalog_offer is not None:
        return OFFER_GROUP_ABBR.get(catalog_offer.group, catalog_offer.group.lower())
    return name.split()[0].lower() if name else "офер"


def _group_rows(items: list[tuple[str, str, int]]) -> list[tuple[str, int, int]]:
    qty: dict[str, int] = defaultdict(int)
    money: dict[str, int] = defaultdict(int)
    label_for: dict[str, str] = {}
    order: list[str] = []
    for label, key, reward in items:
        if key not in qty:
            order.append(key)
            label_for[key] = label
        qty[key] += 1
        money[key] += int(reward)
    return [(label_for[k], qty[k], money[k]) for k in order]


def _meeting_row_key(name: str, condition: str) -> str:
    return f"{name}|{condition}"


def _meeting_row_label(name: str, condition: str) -> str:
    return f"{name} · {condition}" if condition else name


# ---------------------------------------------------------------------------
# Отчёт «Сегодня»
# ---------------------------------------------------------------------------

def today_stats(telegram_id: int) -> dict:
    d = today_local()
    meetings = today_meetings(telegram_id, d.isoformat())
    per_meeting = []
    total_with = 0
    total_without = 0
    for m in meetings:
        offers = get_meeting_offers(int(m["id"]))
        deferred_sum = sum(int(o["reward"]) for o in offers if _is_deferred_offer(o))
        total_reward = int(m["total_reward"])
        total_with += total_reward
        total_without += total_reward - deferred_sum
        labels = []
        for o in offers:
            label = _offer_group_label(str(o["code"]), str(o["name"]))
            if label not in labels:
                labels.append(label)
        per_meeting.append({
            "meeting": m,
            "offers": offers,
            "labels": labels,
            "total": total_reward,
        })
    return {
        "date": d,
        "meetings": meetings,
        "per_meeting": per_meeting,
        "total_with": total_with,
        "total_without": total_without,
    }


def today_text(telegram_id: int) -> str:
    s = today_stats(telegram_id)
    lines = [
        f"📊 <b>Сегодня, {s['date']:%d.%m.%Y}</b>",
        f"Встреч: <b>{len(s['meetings'])}</b>",
        f"Итого со Смарт/страховкой: <b>{rub(s['total_with'])}</b>",
    ]
    if s["total_with"] != s["total_without"]:
        lines.append(f"Итого чистыми: <b>{rub(s['total_without'])}</b>")
    return "\n".join(lines)


def daily_report(telegram_id: int) -> str:
    s = today_stats(telegram_id)
    lines = [f"🧾 <b>{s['date']:%d.%m.%Y}</b>"]
    total_line = f"Встреч: <b>{len(s['meetings'])}</b> · Итого: <b>{rub(s['total_with'])}</b>"
    if s["total_with"] != s["total_without"]:
        total_line += f" (чистыми: {rub(s['total_without'])})"
    lines.append(total_line)

    if not s["meetings"]:
        lines.append("\nПусто.")
        return "\n".join(lines)

    lines.append("")
    for i, row in enumerate(s["per_meeting"], 1):
        m = row["meeting"]
        offer_part = ", ".join(row["labels"]) if row["labels"] else "без доп. оферов"
        lines.append(
            f"{i}. <code>{m['meeting_code']}</code>, {m['report_type']}, "
            f"{offer_part} — <b>{rub(row['total'])}</b>"
        )
    return "\n".join(lines)


def history_text(telegram_id: int) -> str:
    meetings = recent_meetings(telegram_id, 10)
    if not meetings:
        return "🕘 История пуста."
    lines = ["🕘 <b>Последние встречи</b>", ""]
    for m in meetings:
        lines.append(
            f"{m['meeting_date']} · <code>{m['meeting_code']}</code> · {m['report_type']} · <b>{rub(int(m['total_reward']))}</b>"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Отчёт «Месяц»
#
#   Итого чистыми            = база + обычные оферы этого месяца
#                              (то, что рисуется на графике)
#   Итого со Смарт/страховкой = чистыми + Смарт/страховки этого месяца
#                              + Смарт/страховки, прилетевшие из прошлого месяца
# ---------------------------------------------------------------------------

def month_dashboard_data(telegram_id: int, year: int, month: int) -> dict:
    days = monthrange(year, month)[1]
    meetings = month_meetings(telegram_id, year, month)
    non_deferred = offers_non_deferred_for_month(telegram_id, year, month)
    deferred_arriving = offers_deferred_arriving_in_month(telegram_id, year, month)
    deferred_sold = offers_deferred_sold_in_month(telegram_id, year, month)

    # График — «чистыми» по дням
    values = [0] * days
    for m in meetings:
        day = int(str(m["meeting_date"])[-2:])
        values[day - 1] += int(m["base_reward"])
    for o in non_deferred:
        day = int(str(o["meeting_date"])[-2:])
        values[day - 1] += int(o["reward"])

    base_sum = sum(int(m["base_reward"]) for m in meetings)
    non_deferred_sum = sum(int(o["reward"]) for o in non_deferred)
    arriving_sum = sum(int(o["reward"]) for o in deferred_arriving)
    sold_sum = sum(int(o["reward"]) for o in deferred_sold)

    total_without = base_sum + non_deferred_sum
    total_with = total_without + arriving_sum + sold_sum

    main_rows = _group_rows(
        [(str(m["report_type"]), str(m["report_type"]), int(m["base_reward"])) for m in meetings]
    )
    extra_rows = _group_rows(
        [
            (
                _meeting_row_label(str(o["name"]), str(o["condition"])),
                _meeting_row_key(str(o["name"]), str(o["condition"])),
                int(o["reward"]),
            )
            for o in non_deferred
        ]
    )
    sold_rows = _group_rows(
        [
            (
                _meeting_row_label(str(o["name"]), str(o["condition"])),
                _meeting_row_key(str(o["name"]), str(o["condition"])),
                int(o["reward"]),
            )
            for o in deferred_sold
        ]
    )
    arriving_rows = _group_rows(
        [
            (
                _meeting_row_label(str(o["name"]), str(o["condition"])),
                _meeting_row_key(str(o["name"]), str(o["condition"])),
                int(o["reward"]),
            )
            for o in deferred_arriving
        ]
    )
    return {
        "year": year,
        "month": month,
        "days": days,
        "values": values,
        "total_with": total_with,
        "total_without": total_without,
        "meetings": len(meetings),
        "main_rows": main_rows,
        "main_total": base_sum,
        "extra_rows": extra_rows,
        "extra_total": non_deferred_sum,
        "deferred_total": sold_sum,
        "deferred_rows": sold_rows,
        "deferred_arriving_total": arriving_sum,
        "deferred_arriving_rows": arriving_rows,
    }


def month_summary_text(telegram_id: int, year: int | None = None, month: int | None = None) -> str:
    today = date.today()
    year = today.year if year is None else year
    month = today.month if month is None else month
    data = month_dashboard_data(telegram_id, year, month)
    lines = [
        f"📈 <b>{RU_MONTHS[month - 1]} {year}</b>",
        f"Встреч: <b>{data['meetings']}</b>",
        f"Итого со Смарт/страховкой: <b>{rub(data['total_with'])}</b>",
        f"Итого чистыми: <b>{rub(data['total_without'])}</b>",
    ]
    if data["deferred_arriving_total"]:
        lines.append(
            f"➕ Прилетело из прошлого месяца: <b>{rub(data['deferred_arriving_total'])}</b>"
        )
    if data["deferred_total"]:
        lines.append(
            f"⏳ Продано в этом месяце, попадёт в стату 1-го числа след. месяца: "
            f"<b>{rub(data['deferred_total'])}</b>"
        )
    return "\n".join(lines)


def _rows_to_text(title: str, total: int, rows: list[tuple[str, int, int]]) -> str:
    lines = [f"<b>{title} — {rub(total)}</b>"]
    if not rows:
        lines.append("Нет данных")
        return "\n".join(lines)
    for name, qty, money in rows:
        lines.append(f"• {name} — {qty} шт. · {rub(money)}")
    return "\n".join(lines)


def month_details_text(telegram_id: int, year: int | None = None, month: int | None = None) -> str:
    today = date.today()
    year = today.year if year is None else year
    month = today.month if month is None else month
    data = month_dashboard_data(telegram_id, year, month)
    parts = [
        _rows_to_text("Основные продукты за месяц", data["main_total"], data["main_rows"]),
        "",
        _rows_to_text("Доп. продукты и услуги за месяц", data["extra_total"], data["extra_rows"]),
    ]
    if data["deferred_arriving_rows"]:
        parts.append("")
        parts.append(
            _rows_to_text(
                f"Прилетело из прошлого месяца · {rub(data['deferred_arriving_total'])}",
                data["deferred_arriving_total"],
                data["deferred_arriving_rows"],
            )
        )
    if data["deferred_rows"]:
        parts.append("")
        parts.append(
            _rows_to_text(
                f"Отложено на 1-е следующего месяца · {rub(data['deferred_total'])}",
                data["deferred_total"],
                data["deferred_rows"],
            )
        )
    return "\n".join(parts)


def _quickchart_config(data: dict, title: str) -> dict:
    labels = [str(d) for d in range(1, data["days"] + 1)]
    return {
        "type": "bar",
        "data": {
            "labels": labels,
            "datasets": [
                {
                    "label": "Итого чистыми, ₽",
                    "data": data["values"],
                    "backgroundColor": RED,
                    "borderRadius": 4,
                }
            ],
        },
        "options": {
            "title": {"display": True, "text": title, "fontColor": TEXT, "fontSize": 18},
            "legend": {"display": False},
            "scales": {
                "xAxes": [
                    {"gridLines": {"color": "#2A2A2A"}, "ticks": {"fontColor": MUTED}}
                ],
                "yAxes": [
                    {
                        "gridLines": {"color": "#2A2A2A"},
                        "ticks": {"fontColor": MUTED, "beginAtZero": True},
                    }
                ],
            },
        },
    }


async def month_chart_quickchart(
    telegram_id: int,
    year: int | None = None,
    month: int | None = None,
    timeout_seconds: float = 15.0,
) -> BytesIO:
    today = date.today()
    year = today.year if year is None else year
    month = today.month if month is None else month
    data = month_dashboard_data(telegram_id, year, month)
    # Заголовок: месяц год · сумма по графику (чистыми)
    title = f"{RU_MONTHS[month - 1]} {year} · {rub(data['total_without'])}"
    config = _quickchart_config(data, title)
    payload = {
        "chart": config,
        "backgroundColor": BG,
        "width": 900,
        "height": 420,
        "devicePixelRatio": 2,
        "format": "png",
    }
    timeout = aiohttp.ClientTimeout(total=timeout_seconds)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(QUICKCHART_URL, json=payload) as resp:
            resp.raise_for_status()
            content = await resp.read()
    buf = BytesIO(content)
    buf.name = f"reward_{year}_{month:02d}.png"
    buf.seek(0)
    return buf