from __future__ import annotations

import os
from datetime import date, datetime
from zoneinfo import ZoneInfo


TIMEZONE_NAME = os.getenv("BOT_TIMEZONE", "Europe/Moscow")
try:
    LOCAL_TZ = ZoneInfo(TIMEZONE_NAME)
except Exception:
    LOCAL_TZ = ZoneInfo("Europe/Moscow")


def today_local() -> date:
    return datetime.now(LOCAL_TZ).date()
