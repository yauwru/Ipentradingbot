"""
SQLite-backed trade tracker.

Stores every trade we've seen from Capitol Trades and every order we've
placed on Alpaca so we never duplicate an order.
"""

import logging
import sqlite3
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent / "trades.db"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create tables if they don't exist."""
    with _conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS seen_trades (
                trade_id    TEXT PRIMARY KEY,
                politician  TEXT,
                ticker      TEXT,
                trade_type  TEXT,
                trade_date  TEXT,
                amount_usd  REAL,
                asset_type  TEXT,
                seen_at     TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS placed_orders (
                trade_id        TEXT PRIMARY KEY,
                alpaca_order_id TEXT,
                ticker          TEXT,
                side            TEXT,
                qty             REAL,
                position_usd    REAL,
                status          TEXT,
                placed_at       TEXT,
                politician      TEXT
            )
        """)
        conn.commit()
    logger.info("Database initialised at %s", DB_PATH)


def is_seen(trade_id: str) -> bool:
    with _conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM seen_trades WHERE trade_id = ?", (trade_id,)
        ).fetchone()
    return row is not None


def mark_seen(trade: dict) -> None:
    with _conn() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO seen_trades
               (trade_id, politician, ticker, trade_type, trade_date,
                amount_usd, asset_type, seen_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                trade["trade_id"],
                trade.get("politician"),
                trade.get("ticker"),
                trade.get("trade_type"),
                trade.get("trade_date"),
                trade.get("amount_usd", 0),
                trade.get("asset_type", "stock"),
                datetime.utcnow().isoformat(),
            ),
        )
        conn.commit()


def record_order(
    trade_id: str,
    alpaca_order_id: str,
    ticker: str,
    side: str,
    qty: float,
    position_usd: float,
    status: str,
    politician: str,
) -> None:
    with _conn() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO placed_orders
               (trade_id, alpaca_order_id, ticker, side, qty,
                position_usd, status, placed_at, politician)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                trade_id,
                alpaca_order_id,
                ticker,
                side,
                qty,
                position_usd,
                status,
                datetime.utcnow().isoformat(),
                politician,
            ),
        )
        conn.commit()


def get_all_orders() -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM placed_orders ORDER BY placed_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_all_seen_trades() -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM seen_trades ORDER BY seen_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]
