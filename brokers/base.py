"""Broker interface. Every broker adapter (Zerodha now; Upstox, Fyers, etc.
later) implements this. Nothing above this layer knows which broker is live."""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass


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
    def get_positions(self) -> list[Position]:
        """Current open positions."""
