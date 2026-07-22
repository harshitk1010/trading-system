"""SQLite persistence for paper-mode fills, positions and bar cache. Single
file, single user. Schema is created on first use."""
from __future__ import annotations
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).with_name("trading.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    broker TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    price REAL NOT NULL,
    mode TEXT NOT NULL DEFAULT 'paper'
);
CREATE TABLE IF NOT EXISTS positions (
    symbol TEXT PRIMARY KEY,
    quantity INTEGER NOT NULL,
    avg_price REAL NOT NULL
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


def log_order(conn, ts, broker, symbol, side, quantity, price, mode="paper") -> int:
    cur = conn.execute(
        "INSERT INTO orders (ts, broker, symbol, side, quantity, price, mode) "
        "VALUES (?,?,?,?,?,?,?)",
        (ts, broker, symbol, side, quantity, price, mode),
    )
    conn.commit()
    return cur.lastrowid


def upsert_position(conn, symbol, quantity, avg_price) -> None:
    if quantity == 0:
        conn.execute("DELETE FROM positions WHERE symbol=?", (symbol,))
    else:
        conn.execute(
            "INSERT INTO positions (symbol, quantity, avg_price) VALUES (?,?,?) "
            "ON CONFLICT(symbol) DO UPDATE SET quantity=excluded.quantity, "
            "avg_price=excluded.avg_price",
            (symbol, quantity, avg_price),
        )
    conn.commit()


def get_positions(conn) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM positions").fetchall()
