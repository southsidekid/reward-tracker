"""Получение текущей погоды через Open-Meteo API.

Open-Meteo — бесплатный API без ключа и регистрации.
Документация: https://open-meteo.com/en/docs
"""
from __future__ import annotations

import aiohttp

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

# Координаты по умолчанию — Москва
DEFAULT_LAT = 55.751244
DEFAULT_LON = 37.618423

_WMO_EMOJI = {
    0: "☀️",
    1: "🌤",
    2: "⛅",
    3: "☁️",
    45: "🌫",
    48: "🌫",
    51: "🌦",
    53: "🌦",
    55: "🌦",
    56: "🌧",
    57: "🌧",
    61: "🌧",
    63: "🌧",
    65: "🌧",
    66: "🌧",
    67: "🌧",
    71: "🌨",
    73: "🌨",
    75: "❄️",
    77: "❄️",
    80: "🌦",
    81: "🌧",
    82: "🌧",
    85: "🌨",
    86: "❄️",
    95: "⛈",
    96: "⛈",
    99: "⛈",
}


def _emoji_for_code(code: int) -> str:
    return _WMO_EMOJI.get(code, "🌡")


async def get_current_weather(lat: float = DEFAULT_LAT, lon: float = DEFAULT_LON) -> dict | None:
    """
    Возвращает {'temperature': int, 'emoji': str} или None, если не удалось.
    """
    params = {
        "latitude": lat,
        "longitude": lon,
        "current_weather": "true",
    }
    try:
        timeout = aiohttp.ClientTimeout(total=8)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(OPEN_METEO_URL, params=params) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                cw = data.get("current_weather")
                if not cw:
                    return None
                return {
                    "temperature": round(float(cw["temperature"])),
                    "emoji": _emoji_for_code(int(cw.get("weathercode", 0))),
                }
    except Exception:
        return None