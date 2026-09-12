from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

DB_PATH = Path(os.getenv("DB_PATH", str(Path(__file__).with_name("reward_bot.sqlite3"))))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    telegram_id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meetings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER NOT NULL,
    meeting_code TEXT NOT NULL,
    category TEXT NOT NULL,
    report_type TEXT NOT NULL,
    meeting_date TEXT NOT NULL,
    created_at TEXT NOT NULL,
    base_reward INTEGER NOT NULL DEFAULT 0,
    total_reward INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (telegram_id) REFERENCES users(telegram_id)
);

CREATE TABLE IF NOT EXISTS offers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id INTEGER NOT NULL,
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    condition TEXT NOT NULL,
    reward INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    payout_date TEXT,
    FOREIGN KEY (meeting_id) REFERENCES meetings(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_meetings_user_date ON meetings(telegram_id, meeting_date);
CREATE INDEX IF NOT EXISTS idx_offers_meeting ON offers(meeting_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_offer_per_meeting ON offers(meeting_id, code);
"""


@contextmanager
def conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init_db() -> None:
    with conn() as c:
        c.executescript(SCHEMA)
        cols = {row[1] for row in c.execute("PRAGMA table_info(offers)")}
        if "payout_date" not in cols:
            c.execute("ALTER TABLE offers ADD COLUMN payout_date TEXT")


def upsert_user(telegram_id: int, username: str | None, first_name: str | None) -> None:
    with conn() as c:
        c.execute(
            """INSERT INTO users (telegram_id, username, first_name, created_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(telegram_id) DO UPDATE SET
               username=excluded.username, first_name=excluded.first_name""",
            (telegram_id, username, first_name, datetime.now().isoformat(timespec="seconds")),
        )


def create_meeting(telegram_id: int, meeting_code: str, category: str, report_type: str,
                   meeting_date: str, base_reward: int = 0) -> int:
    with conn() as c:
        cur = c.execute(
            """INSERT INTO meetings
               (telegram_id, meeting_code, category, report_type, meeting_date, created_at, base_reward, total_reward)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (telegram_id, meeting_code, category, report_type, meeting_date,
             datetime.now().isoformat(timespec="seconds"), base_reward, base_reward),
        )
        return int(cur.lastrowid)


def add_offer(
    meeting_id: int,
    code: str,
    name: str,
    condition: str,
    reward: int,
    payout_date: str | None = None,
) -> int:
    with conn() as c:
        existing = c.execute(
            "SELECT id FROM offers WHERE meeting_id = ? AND code = ? LIMIT 1",
            (meeting_id, code),
        ).fetchone()
        if existing:
            return -int(existing["id"])
        cur = c.execute(
            """INSERT INTO offers
               (meeting_id, code, name, condition, reward, created_at, payout_date)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                meeting_id,
                code,
                name,
                condition,
                reward,
                datetime.now().isoformat(timespec="seconds"),
                payout_date,
            ),
        )
        c.execute("UPDATE meetings SET total_reward = total_reward + ? WHERE id = ?", (reward, meeting_id))
        return int(cur.lastrowid)


def add_custom_offer(meeting_id: int, code: str, name: str, condition: str, reward: int) -> int:
    return add_offer(meeting_id, code, name, condition, reward)


def get_meeting_offers(meeting_id: int):
    with conn() as c:
        return c.execute("SELECT * FROM offers WHERE meeting_id = ? ORDER BY id", (meeting_id,)).fetchall()


def delete_last_meeting(telegram_id: int):
    """Удаляет последнюю встречу и возвращает, что именно было удалено."""
    with conn() as c:
        row = c.execute(
            "SELECT * FROM meetings WHERE telegram_id = ? ORDER BY id DESC LIMIT 1", (telegram_id,)
        ).fetchone()
        if not row:
            return None
        meeting = dict(row)
        offers = [dict(r) for r in c.execute(
            "SELECT * FROM offers WHERE meeting_id = ? ORDER BY id", (int(row["id"]),)
        ).fetchall()]
        c.execute("DELETE FROM meetings WHERE id = ?", (int(row["id"]),))
        meeting["offers"] = offers
        return meeting


def offer_exists(meeting_id: int, code: str) -> bool:
    with conn() as c:
        row = c.execute(
            "SELECT 1 FROM offers WHERE meeting_id = ? AND code = ? LIMIT 1",
            (meeting_id, code),
        ).fetchone()
        return row is not None


def delete_last_offer(meeting_id: int, telegram_id: int):
    """Удаляет последний офер этой встречи и возвращает его данные."""
    with conn() as c:
        ownership = c.execute(
            "SELECT id FROM meetings WHERE id = ? AND telegram_id = ?", (meeting_id, telegram_id)
        ).fetchone()
        if not ownership:
            return None
        row = c.execute(
            "SELECT * FROM offers WHERE meeting_id = ? ORDER BY id DESC LIMIT 1", (meeting_id,)
        ).fetchone()
        if not row:
            return None
        deleted = dict(row)
        c.execute("DELETE FROM offers WHERE id = ?", (int(row["id"]),))
        c.execute(
            "UPDATE meetings SET total_reward = total_reward - ? WHERE id = ?",
            (int(row["reward"]), meeting_id),
        )
        return deleted


def totals_for_month(telegram_id: int, year: int, month: int):
    prefix = f"{year:04d}-{month:02d}-%"
    with conn() as c:
        rows = c.execute(
            """SELECT meeting_date, COALESCE(SUM(total_reward),0) total, COUNT(*) meetings
               FROM meetings WHERE telegram_id = ? AND meeting_date LIKE ?
               GROUP BY meeting_date ORDER BY meeting_date""",
            (telegram_id, prefix),
        ).fetchall()
        total = c.execute(
            "SELECT COALESCE(SUM(total_reward),0) total FROM meetings WHERE telegram_id = ? AND meeting_date LIKE ?",
            (telegram_id, prefix),
        ).fetchone()["total"]
        count = c.execute(
            "SELECT COUNT(*) n FROM meetings WHERE telegram_id = ? AND meeting_date LIKE ?",
            (telegram_id, prefix),
        ).fetchone()["n"]
        return rows, int(total), int(count)


def today_meetings(telegram_id: int, d: str):
    with conn() as c:
        return c.execute(
            "SELECT * FROM meetings WHERE telegram_id = ? AND meeting_date = ? ORDER BY id",
            (telegram_id, d),
        ).fetchall()


def recent_meetings(telegram_id: int, limit: int = 10):
    with conn() as c:
        return c.execute(
            "SELECT * FROM meetings WHERE telegram_id = ? ORDER BY id DESC LIMIT ?",
            (telegram_id, limit),
        ).fetchall()


def month_meetings(telegram_id: int, year: int, month: int):
    prefix = f"{year:04d}-{month:02d}-%"
    with conn() as c:
        return c.execute(
            "SELECT * FROM meetings WHERE telegram_id = ? AND meeting_date LIKE ? ORDER BY id",
            (telegram_id, prefix),
        ).fetchall()


def offers_for_meetings(meeting_ids: list[int]):
    if not meeting_ids:
        return []
    placeholders = ",".join("?" * len(meeting_ids))
    with conn() as c:
        return c.execute(
            f"SELECT * FROM offers WHERE meeting_id IN ({placeholders}) ORDER BY id",
            meeting_ids,
        ).fetchall()


def offers_non_deferred_for_month(telegram_id: int, year: int, month: int):
    """Обычные оферы (без payout_date) для встреч этого месяца."""
    prefix = f"{year:04d}-{month:02d}-%"
    with conn() as c:
        return c.execute(
            """SELECT o.*, m.meeting_date
               FROM offers o
               JOIN meetings m ON m.id = o.meeting_id
               WHERE m.telegram_id = ?
                 AND m.meeting_date LIKE ?
                 AND IFNULL(o.payout_date, '') = ''
               ORDER BY o.id""",
            (telegram_id, prefix),
        ).fetchall()


def offers_deferred_arriving_in_month(telegram_id: int, year: int, month: int):
    """Смарт/страховки, у которых payout_date попадает в этот месяц (прилетели)."""
    prefix = f"{year:04d}-{month:02d}-%"
    with conn() as c:
        return c.execute(
            """SELECT o.*, m.meeting_date
               FROM offers o
               JOIN meetings m ON m.id = o.meeting_id
               WHERE m.telegram_id = ?
                 AND IFNULL(o.payout_date, '') != ''
                 AND o.payout_date LIKE ?
               ORDER BY o.id""",
            (telegram_id, prefix),
        ).fetchall()


def offers_deferred_sold_in_month(telegram_id: int, year: int, month: int):
    """Смарт/страховки, проданные в этом месяце (уйдут в след. месяц)."""
    prefix = f"{year:04d}-{month:02d}-%"
    with conn() as c:
        return c.execute(
            """SELECT o.*, m.meeting_date
               FROM offers o
               JOIN meetings m ON m.id = o.meeting_id
               WHERE m.telegram_id = ?
                 AND m.meeting_date LIKE ?
                 AND IFNULL(o.payout_date, '') != ''
               ORDER BY o.id""",
            (telegram_id, prefix),
        ).fetchall()