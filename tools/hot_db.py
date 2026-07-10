from __future__ import annotations

import os
import sqlite3
import time
from typing import Any


def _connect(db_path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn


def ensure_schema(*, db_path: str) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS selected_hotspots (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              run_date TEXT NOT NULL,
              keyword TEXT NOT NULL,
              platform TEXT,
              platform_rank INTEGER,
              thread_id TEXT,
              reason TEXT,
              created_at INTEGER NOT NULL
            );
            """
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_selected_hotspots_run_date_keyword "
            "ON selected_hotspots(run_date, keyword);"
        )


def has_selected(
    *, db_path: str, run_date: str, keyword: str
) -> bool:
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT 1 FROM selected_hotspots WHERE run_date=? AND keyword=? LIMIT 1;",
            (run_date, keyword),
        ).fetchone()
        return row is not None


def add_selected(
    *,
    db_path: str,
    run_date: str,
    keyword: str,
    platform: str | None,
    platform_rank: int | None,
    thread_id: str | None,
    reason: str | None,
) -> None:
    created_at = int(time.time())
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO selected_hotspots
              (run_date, keyword, platform, platform_rank, thread_id, reason, created_at)
            VALUES
              (?, ?, ?, ?, ?, ?, ?);
            """,
            (run_date, keyword, platform, platform_rank, thread_id, reason, created_at),
        )


def list_recent_keywords(*, db_path: str, limit: int) -> list[str]:
    """
    返回最近写入数据库的若干个热点关键词（按 created_at 倒序）。
    用于在 LLM 选题阶段提前规避近期已选热点，减少在 check_hotspot_db 上来回重选的次数。
    """

    n = int(limit or 0)
    if n <= 0:
        return []
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT keyword
            FROM selected_hotspots
            ORDER BY created_at DESC, id DESC
            LIMIT ?;
            """,
            (n,),
        ).fetchall()
    out: list[str] = []
    for (kw,) in rows or []:
        kw = str(kw or "").strip()
        if kw:
            out.append(kw)
    return out
