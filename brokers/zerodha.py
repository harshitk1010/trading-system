"""Zerodha (Kite) adapter. Phase 1: PAPER MODE ONLY. place_order never hits the
real API — it records a simulated fill in SQLite and updates the local position
book. get_quote / get_historical read from an injected data source so the same
adapter drives both the live engine (later: KiteConnect) and backtests (CSV)."""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Callable

from .base import Broker, Bar, Quote, Order, Position
from data import store


class ZerodhaBroker(Broker):
    def __init__(
        self,
        quote_source: Callable[[str], Quote | None] | None = None,
        historical_source: Callable[[str, str, int], list[Bar]] | None = None,
        db_path=store.DB_PATH,
    ):
        # In paper mode these are supplied by the caller (synthetic/CSV feed).
        # In a later live phase they wrap KiteConnect.quote / .historical_data.
        self._quote_source = quote_source
        self._historical_source = historical_source
        self._conn = store.connect(db_path)
        self.name = "zerodha"

    def connect(self) -> None:
        # Paper mode: no auth handshake. Live phase will do KiteConnect login here.
        return None

    def get_quote(self, symbol: str) -> Quote | None:
        if self._quote_source is None:
            return None
        return self._quote_source(symbol)

    def get_historical(self, symbol: str, interval: str, limit: int) -> list[Bar]:
        if self._historical_source is None:
            return []
        return self._historical_source(symbol, interval, limit)

    def place_order(self, order: Order) -> str:
        # PAPER: log fill, update local book. No network call.
        oid = store.log_order(
            self._conn, order.ts, self.name, order.symbol,
            order.side, order.quantity, order.price, mode="paper",
        )
        self._apply_fill(order)
        return f"PAPER-{oid}"

    def get_positions(self) -> list[Position]:
        return [
            Position(r["symbol"], r["quantity"], r["avg_price"])
            for r in store.get_positions(self._conn)
        ]

    def _apply_fill(self, order: Order) -> None:
        book = {p.symbol: p for p in self.get_positions()}
        pos = book.get(order.symbol, Position(order.symbol, 0, 0.0))
        signed = order.quantity if order.side == "BUY" else -order.quantity
        new_qty = pos.quantity + signed
        if pos.quantity == 0 or (pos.quantity > 0) == (signed > 0):
            # opening or adding in same direction -> weighted avg
            total = pos.avg_price * abs(pos.quantity) + order.price * order.quantity
            new_avg = total / abs(new_qty) if new_qty != 0 else 0.0
        else:
            # reducing/closing -> keep avg unless flipped
            new_avg = order.price if (new_qty != 0 and (new_qty > 0) != (pos.quantity > 0)) else pos.avg_price
        store.upsert_position(self._conn, order.symbol, new_qty, new_avg)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
