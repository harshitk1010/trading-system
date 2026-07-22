"""Zerodha (Kite Connect) adapter. Two modes on one interface:

  mode="paper" (default) — PaperBroker: place_order logs a simulated fill to
      SQLite, no network. Unchanged from phases 1-4.
  mode="live" — real Kite Connect: quotes/history/orders hit the broker. Only
      reachable after the Phase 3 compliance gate passes (ToS + backtest viewed)
      and real credentials are in the vault.

The live client is injectable (`kite=`) so the live path is unit-testable with a
fake — no real orders in tests. Live orders never touch the paper position book;
the broker's own positions() is the source of truth (reconciliation)."""
from __future__ import annotations
from typing import Callable

from .base import PaperBroker, Bar, Quote, Order, Position
from . import credentials


class LiveOrderError(RuntimeError):
    pass


class ZerodhaBroker(PaperBroker):
    name = "zerodha"

    def __init__(self, quote_source: Callable[[str], Quote | None] | None = None,
                 historical_source: Callable[[str, str, int], list[Bar]] | None = None,
                 db_path=None, customer_id=None, creds=None,
                 mode: str = "paper", kite=None, exchange: str = "NSE"):
        super().__init__(quote_source, historical_source, db_path, customer_id)
        self.creds = creds or credentials.zerodha()
        self.mode = mode
        self.exchange = exchange
        self._kite = kite            # injectable KiteConnect (real or fake)
        self._instruments = None

    @property
    def live(self) -> bool:
        return self.mode == "live"

    def connect(self) -> None:
        if not self.live:
            return None              # paper: no auth
        from .kite_client import build_kite, InstrumentMap
        if self._kite is None:
            self._kite = build_kite(self.creds)
        self._kite.profile()         # validate session; raises if token stale
        self._instruments = InstrumentMap(self._kite, self.exchange)

    # ---- market data ----
    def get_quote(self, symbol: str) -> Quote | None:
        if not self.live:
            return super().get_quote(symbol)
        from brokers.base import now_iso
        key = f"{self.exchange}:{symbol}"
        data = self._kite.ltp([key]).get(key)
        return Quote(symbol, data["last_price"], now_iso()) if data else None

    def get_historical(self, symbol: str, interval: str, limit: int) -> list[Bar]:
        if not self.live:
            return super().get_historical(symbol, interval, limit)
        from .kite_client import kite_interval, date_window
        token = self._instruments.token(symbol)
        if token is None:
            return []
        frm, to = date_window(interval, limit)
        rows = self._kite.historical_data(token, frm, to, kite_interval(interval))
        bars = [Bar(ts=str(r["date"]), open=r["open"], high=r["high"],
                    low=r["low"], close=r["close"], volume=float(r.get("volume", 0)))
                for r in rows]
        return bars[-limit:] if limit else bars

    # ---- orders ----
    def place_order(self, order: Order) -> str:
        if not self.live:
            return super().place_order(order)   # paper SQLite path
        try:
            oid = self._kite.place_order(
                variety="regular", exchange=self.exchange, tradingsymbol=order.symbol,
                transaction_type=order.side, quantity=int(order.quantity),
                product="MIS", order_type="MARKET")
        except Exception as e:                    # fail-safe: never retry blindly
            raise LiveOrderError(f"live order rejected for {order.symbol}: {e}") from e
        # audit the real order to the same table (mode='live'), no paper fill applied
        self._store.log_order(self._conn, order.ts, self.name, order.symbol,
                              order.side, order.quantity, order.price,
                              mode="live", customer_id=self._customer_id)
        return str(oid)

    def cancel_order(self, order_id: str) -> bool:
        if not self.live:
            return super().cancel_order(order_id)
        try:
            self._kite.cancel_order(variety="regular", order_id=order_id)
            return True
        except Exception:
            return False

    def get_positions(self) -> list[Position]:
        if not self.live:
            return super().get_positions()
        net = self._kite.positions().get("net", [])
        return [Position(p["tradingsymbol"], p["quantity"], p["average_price"])
                for p in net if p["quantity"] != 0]
