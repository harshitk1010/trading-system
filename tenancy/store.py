"""Tenancy persistence. Customers, encrypted credentials, consent, backtest-view
records, per-customer audit log, and execution control flags. EVERY query is
scoped by customer_id — there is no unscoped read of customer data. Credentials
are stored only as Fernet ciphertext.

Uses the same SQLite file as the trading book so a customer's orders/positions
and audit trail live together."""
from __future__ import annotations
import json
import sqlite3
from pathlib import Path

from data.store import DB_PATH

_SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL,
    broker TEXT NOT NULL DEFAULT 'zerodha',
    mode TEXT NOT NULL DEFAULT 'paper',
    status TEXT NOT NULL DEFAULT 'active',
    equity REAL NOT NULL DEFAULT 100000,
    watchlist TEXT NOT NULL DEFAULT 'DEMO',
    risk_json TEXT NOT NULL,
    error_count INTEGER NOT NULL DEFAULT 0,
    tos_version TEXT,
    tos_accepted_at TEXT,
    backtest_viewed_at TEXT
);
CREATE TABLE IF NOT EXISTS credentials (
    customer_id TEXT NOT NULL,
    field TEXT NOT NULL,
    ciphertext BLOB NOT NULL,
    PRIMARY KEY (customer_id, field),
    FOREIGN KEY (customer_id) REFERENCES customers(id)
);
CREATE TABLE IF NOT EXISTS consent (
    customer_id TEXT NOT NULL,
    tos_version TEXT NOT NULL,
    accepted_at TEXT NOT NULL,
    PRIMARY KEY (customer_id, tos_version)
);
CREATE TABLE IF NOT EXISTS backtest_views (
    customer_id TEXT NOT NULL,
    viewed_at TEXT NOT NULL,
    win_rate REAL, sharpe REAL, max_drawdown REAL,
    PRIMARY KEY (customer_id, viewed_at)
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,          -- signal | order | control | error
    symbol TEXT,
    detail TEXT                  -- JSON: indicator values, action, reason, etc.
);
CREATE INDEX IF NOT EXISTS idx_audit_customer ON audit_log(customer_id, id);
CREATE TABLE IF NOT EXISTS control (
    customer_id TEXT PRIMARY KEY,
    killed INTEGER NOT NULL DEFAULT 0,
    suspended INTEGER NOT NULL DEFAULT 0
);
"""


def connect(path: Path | str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


# ---- customers ----
def upsert_customer(conn, c) -> None:
    conn.execute(
        "INSERT INTO customers (id,name,email,broker,mode,status,equity,watchlist,"
        "risk_json,error_count,tos_version,tos_accepted_at,backtest_viewed_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET name=excluded.name,email=excluded.email,"
        "broker=excluded.broker,mode=excluded.mode,status=excluded.status,"
        "equity=excluded.equity,watchlist=excluded.watchlist,risk_json=excluded.risk_json,"
        "error_count=excluded.error_count,tos_version=excluded.tos_version,"
        "tos_accepted_at=excluded.tos_accepted_at,backtest_viewed_at=excluded.backtest_viewed_at",
        (c.id, c.name, c.email, c.broker, c.mode, c.status, c.equity,
         ",".join(c.watchlist), json.dumps(c.risk.as_dict()), c.error_count,
         c.tos_version, c.tos_accepted_at, c.backtest_viewed_at),
    )
    conn.commit()


def get_customer_row(conn, customer_id):
    return conn.execute("SELECT * FROM customers WHERE id=?", (customer_id,)).fetchone()


def list_customer_rows(conn):
    return conn.execute("SELECT * FROM customers ORDER BY id").fetchall()


def set_error_count(conn, customer_id, n) -> None:
    conn.execute("UPDATE customers SET error_count=? WHERE id=?", (n, customer_id))
    conn.commit()


def set_status(conn, customer_id, status) -> None:
    conn.execute("UPDATE customers SET status=? WHERE id=?", (status, customer_id))
    conn.commit()


# ---- credentials (ciphertext only) ----
def put_credential(conn, customer_id, field, ciphertext: bytes) -> None:
    conn.execute(
        "INSERT INTO credentials (customer_id,field,ciphertext) VALUES (?,?,?) "
        "ON CONFLICT(customer_id,field) DO UPDATE SET ciphertext=excluded.ciphertext",
        (customer_id, field, ciphertext),
    )
    conn.commit()


def get_credential_ciphertext(conn, customer_id, field) -> bytes | None:
    row = conn.execute(
        "SELECT ciphertext FROM credentials WHERE customer_id=? AND field=?",
        (customer_id, field),
    ).fetchone()
    return row["ciphertext"] if row else None


# ---- consent & backtest views ----
def record_consent(conn, customer_id, version, ts) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO consent (customer_id,tos_version,accepted_at) VALUES (?,?,?)",
        (customer_id, version, ts))
    conn.execute(
        "UPDATE customers SET tos_version=?, tos_accepted_at=? WHERE id=?",
        (version, ts, customer_id))
    conn.commit()


def record_backtest_view(conn, customer_id, ts, win_rate, sharpe, max_dd) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO backtest_views "
        "(customer_id,viewed_at,win_rate,sharpe,max_drawdown) VALUES (?,?,?,?,?)",
        (customer_id, ts, win_rate, sharpe, max_dd))
    conn.execute("UPDATE customers SET backtest_viewed_at=? WHERE id=?", (ts, customer_id))
    conn.commit()


# ---- audit ----
def append_audit(conn, customer_id, ts, kind, symbol, detail: dict) -> None:
    conn.execute(
        "INSERT INTO audit_log (customer_id,ts,kind,symbol,detail) VALUES (?,?,?,?,?)",
        (customer_id, ts, kind, symbol, json.dumps(detail)))
    conn.commit()


def get_audit(conn, customer_id, limit=200):
    return conn.execute(
        "SELECT * FROM audit_log WHERE customer_id=? ORDER BY id DESC LIMIT ?",
        (customer_id, limit)).fetchall()


# ---- control flags (kill switch / suspend) ----
def set_control(conn, customer_id, killed=None, suspended=None) -> None:
    conn.execute("INSERT OR IGNORE INTO control (customer_id) VALUES (?)", (customer_id,))
    if killed is not None:
        conn.execute("UPDATE control SET killed=? WHERE customer_id=?",
                     (1 if killed else 0, customer_id))
    if suspended is not None:
        conn.execute("UPDATE control SET suspended=? WHERE customer_id=?",
                     (1 if suspended else 0, customer_id))
    conn.commit()


def get_control(conn, customer_id) -> dict:
    row = conn.execute("SELECT * FROM control WHERE customer_id=?", (customer_id,)).fetchone()
    return {"killed": bool(row["killed"]), "suspended": bool(row["suspended"])} if row \
        else {"killed": False, "suspended": False}
