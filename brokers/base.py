"""Broker interface. Every broker adapter (Zerodha, Upstox, Angel One, Alpaca)
implements this. Nothing above this layer knows which broker is live."""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable


@dataclass
class Bar:
    ts: str        # ISO timestamp
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class Quote:
    symbol: str
    last_price: float
    ts: str


@dataclass
class Order:
    symbol: str
    side: str          # "BUY" | "SELL"
    quantity: int
    price: float       # limit/fill price used for the sim
    ts: str


@dataclass
class Position:
    symbol: str
    quantity: int
    avg_price: float


class Broker(ABC):
    """Contract for all brokers. Implementations must not raise for unknown
    symbols in paper mode — return empty/None instead so the engine keeps
    running."""

    @abstractmethod
    def connect(self) -> None:
        """Establish/validate a session. In paper mode may be a no-op."""

    @abstractmethod
    def get_quote(self, symbol: str) -> Quote | None:
        """Latest price for a symbol, or None if unavailable."""

    @abstractmethod
    def get_historical(self, symbol: str, interval: str, limit: int) -> list[Bar]:
        """Most recent `limit` bars at `interval`, oldest-first. No future bars."""

    @abstractmethod
    def place_order(self, order: Order) -> str:
        """Submit an order. Returns a broker order id. In paper mode this logs a
        simulated fill to SQLite and returns a synthetic id — no real API call."""

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """Cancel a resting order by id. Paper mode fills immediately, so there is
        nothing resting — returns True as an idempotent no-op ack."""

    @abstractmethod
    def get_positions(self) -> list[Position]:
        """Current open positions."""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class PaperBroker(Broker):
    """Shared paper-mode implementation. Subclasses (one per vendor) supply
    credential loading + connect/get_quote/get_historical; the SQLite order/
    position book is identical across all of them. No real orders are sent."""

    name = "paper"

    def __init__(
        self,
        quote_source: Callable[[str], "Quote | None"] | None = None,
        historical_source: Callable[[str, str, int], list[Bar]] | None = None,
        db_path=None,
        customer_id: str | None = None,
    ):
        # In paper mode feeds are injected (synthetic/CSV). A live phase wraps the
        # vendor SDK's quote/historical calls in these two hooks. customer_id scopes
        # the order/position book so tenants never share fills.
        from data import store
        self._store = store
        self._quote_source = quote_source
        self._historical_source = historical_source
        self._customer_id = customer_id or store.DEFAULT_CUSTOMER
        self._conn = store.connect(db_path or store.DB_PATH)

    def get_quote(self, symbol: str) -> Quote | None:
        return self._quote_source(symbol) if self._quote_source else None

    def get_historical(self, symbol: str, interval: str, limit: int) -> list[Bar]:
        return self._historical_source(symbol, interval, limit) if self._historical_source else []

    def place_order(self, order: Order) -> str:
        oid = self._store.log_order(
            self._conn, order.ts, self.name, order.symbol,
            order.side, order.quantity, order.price, mode="paper",
            customer_id=self._customer_id,
        )
        self._apply_fill(order)
        return f"PAPER-{oid}"

    def cancel_order(self, order_id: str) -> bool:
        return True  # paper fills are immediate; nothing resting to cancel

    def get_positions(self) -> list[Position]:
        return [
            Position(r["symbol"], r["quantity"], r["avg_price"])
            for r in self._store.get_positions(self._conn, self._customer_id)
        ]

    def _apply_fill(self, order: Order) -> None:
        book = {p.symbol: p for p in self.get_positions()}
        pos = book.get(order.symbol, Position(order.symbol, 0, 0.0))
        signed = order.quantity if order.side == "BUY" else -order.quantity
        new_qty = pos.quantity + signed
        if pos.quantity == 0 or (pos.quantity > 0) == (signed > 0):
            total = pos.avg_price * abs(pos.quantity) + order.price * order.quantity
            new_avg = total / abs(new_qty) if new_qty != 0 else 0.0
        else:
            new_avg = order.price if (new_qty != 0 and (new_qty > 0) != (pos.quantity > 0)) else pos.avg_price
        self._store.upsert_position(self._conn, order.symbol, new_qty, new_avg, self._customer_id)
