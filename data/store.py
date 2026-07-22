"""SQLite persistence for paper-mode fills, positions and bar cache. Multi-tenant:
orders and positions are scoped by customer_id (default "default" preserves the
Phase 1/2 single-user behavior). Schema is created on first use."""
from __future__ import annotations
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).with_name("trading.db")
DEFAULT_CUSTOMER = "default"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id TEXT NOT NULL DEFAULT 'default',
    ts TEXT NOT NULL,
    broker TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    price REAL NOT NULL,
    mode TEXT NOT NULL DEFAULT 'paper'
);
CREATE TABLE IF NOT EXISTS positions (
    customer_id TEXT NOT NULL DEFAULT 'default',
    symbol TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    avg_price REAL NOT NULL,
    PRIMARY KEY (customer_id, symbol)
);
CREATE TABLE IF NOT EXISTS bars (
    symbol TEXT NOT NULL,
    interval TEXT NOT NULL,
    ts TEXT NOT NULL,
    open REAL, high REAL, low REAL, close REAL, volume REAL,
    PRIMARY KEY (symbol, interval, ts)
);
"""


def connect(path: Path | str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def log_order(conn, ts, broker, symbol, side, quantity, price, mode="paper",
              customer_id=DEFAULT_CUSTOMER) -> int:
    cur = conn.execute(
        "INSERT INTO orders (customer_id, ts, broker, symbol, side, quantity, price, mode) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (customer_id, ts, broker, symbol, side, quantity, price, mode),
    )
    conn.commit()
    return cur.lastrowid


def upsert_position(conn, symbol, quantity, avg_price, customer_id=DEFAULT_CUSTOMER) -> None:
    if quantity == 0:
        conn.execute("DELETE FROM positions WHERE customer_id=? AND symbol=?",
                     (customer_id, symbol))
    else:
        conn.execute(
            "INSERT INTO positions (customer_id, symbol, quantity, avg_price) VALUES (?,?,?,?) "
            "ON CONFLICT(customer_id, symbol) DO UPDATE SET quantity=excluded.quantity, "
            "avg_price=excluded.avg_price",
            (customer_id, symbol, quantity, avg_price),
        )
    conn.commit()


def get_positions(conn, customer_id=DEFAULT_CUSTOMER) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM positions WHERE customer_id=?", (customer_id,)).fetchall()


def get_orders(conn, customer_id=DEFAULT_CUSTOMER, limit=50) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM orders WHERE customer_id=? ORDER BY id DESC LIMIT ?",
        (customer_id, limit)).fetchall()
