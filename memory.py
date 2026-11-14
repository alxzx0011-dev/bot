"""
SQLite memory for the Instagram bot.

Har message (text) permanently store hota hai, thread ka summary bhi,
aur reply timestamps rate-limiting ke liye. Bot restart karne par bhi
sab kuch yaad rehta hai.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any


SCHEMA = """
CREATE TABLE IF NOT EXISTS threads (
    thread_id    TEXT PRIMARY KEY,
    title        TEXT NOT NULL DEFAULT '',
    paused       INTEGER NOT NULL DEFAULT 0,
    last_ts      REAL NOT NULL DEFAULT 0,
    last_message_id TEXT NOT NULL DEFAULT '',
    summary      TEXT NOT NULL DEFAULT '',
    summary_upto REAL NOT NULL DEFAULT 0,
    created_at   REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    message_id TEXT PRIMARY KEY,
    thread_id  TEXT NOT NULL,
    user_id    TEXT,
    username   TEXT,
    text       TEXT NOT NULL,
    ts         REAL NOT NULL,
    is_bot     INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_messages_thread_ts ON messages(thread_id, ts);

CREATE TABLE IF NOT EXISTS replies (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id TEXT NOT NULL,
    ts        REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_replies_ts ON replies(ts);

CREATE TABLE IF NOT EXISTS people (
    thread_id  TEXT NOT NULL,
    user_id    TEXT NOT NULL,
    username   TEXT NOT NULL,
    first_seen REAL NOT NULL,
    last_seen  REAL NOT NULL,
    messages   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (thread_id, user_id)
);

CREATE TABLE IF NOT EXISTS wakeups (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id TEXT NOT NULL,
    ts        REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_wakeups_ts ON wakeups(thread_id, ts);
"""


class Memory:
    def __init__(self, path: str | Path) -> None:
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(SCHEMA)
        # purane DB me naya column add karo
        try:
            self.conn.execute(
                "ALTER TABLE threads ADD COLUMN last_message_id TEXT NOT NULL DEFAULT ''"
            )
        except sqlite3.OperationalError:
            pass
        self.conn.commit()

    # ------------------------------------------------------------------ #
    # Threads
    # ------------------------------------------------------------------ #
    def get_thread(self, thread_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM threads WHERE thread_id = ?", (thread_id,)
        ).fetchone()

    def add_thread(self, thread_id: str, title: str, last_ts: float) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO threads (thread_id, title, last_ts, created_at) "
            "VALUES (?, ?, ?, ?)",
            (thread_id, title, last_ts, time.time()),
        )
        self.conn.commit()

    def set_last_ts(self, thread_id: str, ts: float, message_id: str = "") -> None:
        self.conn.execute(
            "UPDATE threads SET last_ts = ?, last_message_id = ? WHERE thread_id = ?",
            (ts, message_id or "", thread_id),
        )
        self.conn.commit()

    def set_paused(self, thread_id: str, paused: bool) -> None:
        self.conn.execute(
            "UPDATE threads SET paused = ? WHERE thread_id = ?",
            (1 if paused else 0, thread_id),
        )
        self.conn.commit()

    def is_paused(self, thread_id: str) -> bool:
        row = self.get_thread(thread_id)
        return bool(row and row["paused"])

    # ------------------------------------------------------------------ #
    # Messages
    # ------------------------------------------------------------------ #
    def add_message(
        self,
        message_id: str,
        thread_id: str,
        user_id: str,
        username: str,
        text: str,
        ts: float,
        is_bot: bool = False,
        track_person: bool = True,
    ) -> bool:
        """Returns True if this message was new."""
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO messages "
            "(message_id, thread_id, user_id, username, text, ts, is_bot) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (message_id, thread_id, user_id, username, text, ts, 1 if is_bot else 0),
        )
        inserted = cur.rowcount > 0
        # count sirf naye message pe badhao — warna har poll me count inflate hota hai
        if inserted and track_person and not is_bot and username and username != "member":
            self.conn.execute(
                "INSERT INTO people (thread_id, user_id, username, first_seen, last_seen, messages) "
                "VALUES (?, ?, ?, ?, ?, 1) "
                "ON CONFLICT(thread_id, user_id) DO UPDATE SET "
                "username = excluded.username, last_seen = excluded.last_seen, "
                "messages = people.messages + 1",
                (thread_id, user_id, username, ts, ts),
            )
        self.conn.commit()
        return inserted

    def messages_after(
        self, thread_id: str, ts: float, message_id: str = ""
    ) -> list[dict[str, Any]]:
        """ts ke baad ke messages; same-second wale message_id se compare hote hain."""
        rows = self.conn.execute(
            "SELECT * FROM messages WHERE thread_id = ? AND ("
            "ts > ? OR (ts = ? AND ("
            "  length(message_id) > length(?) OR "
            "  (length(message_id) = length(?) AND message_id > ?)"
            "))"
            ") ORDER BY ts ASC, length(message_id) ASC, message_id ASC",
            (thread_id, ts, ts, message_id or "", message_id or "", message_id or ""),
        ).fetchall()
        return [dict(r) for r in rows]

    def recent(self, thread_id: str, limit: int) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM messages WHERE thread_id = ? ORDER BY ts DESC LIMIT ?",
            (thread_id, limit),
        ).fetchall()
        return [dict(r) for r in reversed(rows)]

    def context_lines(self, thread_id: str, limit: int) -> list[str]:
        return [
            f"{r['username'] or 'member'}: {r['text'][:300]}"
            for r in self.recent(thread_id, limit)
        ]

    def search(
        self, thread_id: str, words: list[str], limit: int = 6
    ) -> list[dict[str, Any]]:
        """Keyword search for 'yaad hai?' type questions."""
        if not words:
            return []
        clauses = " OR ".join("text LIKE ?" for _ in words)
        params = [f"%{w.replace('%', '').replace('_', '')}%" for w in words]
        rows = self.conn.execute(
            f"SELECT * FROM messages WHERE thread_id = ? AND ({clauses}) "
            f"ORDER BY ts DESC LIMIT ?",
            (thread_id, *params, limit),
        ).fetchall()
        return [dict(r) for r in reversed(rows)]

    def search_before(
        self, thread_id: str, words: list[str], before_ts: float, limit: int = 5
    ) -> list[dict[str, Any]]:
        """Purani baaton me keyword search (recent messages se pehle ki)."""
        if not words or not before_ts:
            return []
        clauses = " OR ".join("text LIKE ?" for _ in words)
        params = [f"%{w.replace('%', '').replace('_', '')}%" for w in words]
        rows = self.conn.execute(
            f"SELECT * FROM messages WHERE thread_id = ? AND ts < ? AND ({clauses}) "
            f"ORDER BY ts DESC LIMIT ?",
            (thread_id, before_ts, *params, limit),
        ).fetchall()
        return [dict(r) for r in reversed(rows)]

    def message_count(self, thread_id: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM messages WHERE thread_id = ?", (thread_id,)
        ).fetchone()
        return int(row["n"])

    # ------------------------------------------------------------------ #
    # Replies / rate limiting
    # ------------------------------------------------------------------ #
    def record_reply(self, thread_id: str) -> None:
        self.conn.execute(
            "INSERT INTO replies (thread_id, ts) VALUES (?, ?)",
            (thread_id, time.time()),
        )
        self.conn.commit()

    def last_reply_ts(self, thread_id: str | None = None) -> float:
        if thread_id is None:
            row = self.conn.execute("SELECT MAX(ts) AS t FROM replies").fetchone()
        else:
            row = self.conn.execute(
                "SELECT MAX(ts) AS t FROM replies WHERE thread_id = ?", (thread_id,)
            ).fetchone()
        return float(row["t"] or 0)

    def count_replies(self, since: float, thread_id: str | None = None) -> int:
        if thread_id is None:
            row = self.conn.execute(
                "SELECT COUNT(*) AS n FROM replies WHERE ts > ?", (since,)
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT COUNT(*) AS n FROM replies WHERE ts > ? AND thread_id = ?",
                (since, thread_id),
            ).fetchone()
        return int(row["n"])

    # ------------------------------------------------------------------ #
    # People (group members)
    # ------------------------------------------------------------------ #
    def people(self, thread_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT username, messages, first_seen, last_seen FROM people "
            "WHERE thread_id = ? ORDER BY messages DESC",
            (thread_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def backfill_people(self, thread_id: str, exclude_user_id: str | None = None) -> None:
        """Purane messages se members ki list bana lo (migration ke liye)."""
        extra = ""
        params: list[Any] = [thread_id]
        if exclude_user_id:
            extra = " AND user_id != ?"
            params.append(str(exclude_user_id))
            # self (bot account) ko members list se hata do
            self.conn.execute(
                "DELETE FROM people WHERE thread_id = ? AND user_id = ?",
                (thread_id, str(exclude_user_id)),
            )
        self.conn.execute(
            f"""
            INSERT INTO people (thread_id, user_id, username, first_seen, last_seen, messages)
            SELECT thread_id, user_id, username, MIN(ts), MAX(ts), COUNT(*)
            FROM messages
            WHERE thread_id = ? AND is_bot = 0 AND username NOT IN ('', 'member'){extra}
            GROUP BY thread_id, user_id, username
            ON CONFLICT(thread_id, user_id) DO UPDATE SET
                username   = excluded.username,
                first_seen = MIN(people.first_seen, excluded.first_seen),
                last_seen  = MAX(people.last_seen, excluded.last_seen),
                messages   = excluded.messages
            """,
            tuple(params),
        )
        self.conn.commit()

    # ------------------------------------------------------------------ #
    # Wake-up pings
    # ------------------------------------------------------------------ #
    def add_wakeup(self, thread_id: str) -> None:
        self.conn.execute(
            "INSERT INTO wakeups (thread_id, ts) VALUES (?, ?)",
            (thread_id, time.time()),
        )
        self.conn.commit()

    def last_wakeup(self, thread_id: str) -> float:
        row = self.conn.execute(
            "SELECT MAX(ts) AS t FROM wakeups WHERE thread_id = ?", (thread_id,)
        ).fetchone()
        return float(row["t"] or 0)

    def count_wakeups(self, thread_id: str, since: float) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM wakeups WHERE thread_id = ? AND ts > ?",
            (thread_id, since),
        ).fetchone()
        return int(row["n"])

    # ------------------------------------------------------------------ #
    # Long-term summary
    # ------------------------------------------------------------------ #
    def summary(self, thread_id: str) -> str:
        row = self.get_thread(thread_id)
        return (row["summary"] or "") if row else ""

    def summary_upto(self, thread_id: str) -> float:
        row = self.get_thread(thread_id)
        return float(row["summary_upto"] or 0) if row else 0.0

    def set_summary(self, thread_id: str, summary: str, upto_ts: float) -> None:
        self.conn.execute(
            "UPDATE threads SET summary = ?, summary_upto = ? WHERE thread_id = ?",
            (summary, upto_ts, thread_id),
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def checkpoint(self) -> None:
        """WAL ko main DB file me likho (backup se pehle)."""
        try:
            self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self.conn.commit()
        except Exception:  # noqa: BLE001
            pass
