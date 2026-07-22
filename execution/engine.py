"""Execution engine. Polls a watchlist, pulls history via the broker, asks the
strategy for a signal, applies risk, and places (paper) orders. Manages open
positions with stop-loss / take-profit exits. Paper mode only in Phase 1."""
from __future__ import annotations
from dataclasses import dataclass, field

from brokers.base import Broker, Order
from strategies.base import Strategy, BUY, SELL
from risk.manager import RiskManager


@dataclass
class OpenTrade:
    symbol: str
    side: str
    qty: int
    entry: float
    stop: float
    target: float


@dataclass
class Engine:
    broker: Broker
    strategy: Strategy
    risk: RiskManager
    watchlist: list[str]
    equity: float = 100_000.0
    interval: str = "day"
    open_trades: dict[str, OpenTrade] = field(default_factory=dict)

    def _now(self) -> str:
        q = self.broker.get_quote(self.watchlist[0]) if self.watchlist else None
        return q.ts if q else ""

    def step(self) -> None:
        """One poll cycle across the watchlist."""
        for symbol in self.watchlist:
            bars = self.broker.get_historical(symbol, self.interval, self.strategy.min_bars() + 5)
            if len(bars) < self.strategy.min_bars():
                continue
            price = bars[-1].close
            ts = bars[-1].ts

            # manage existing position first
            if symbol in self.open_trades:
                self._manage_exit(symbol, price, ts)
                continue

            sig = self.strategy.evaluate(bars)
            if sig.action not in (BUY, SELL):
                continue
            ok, qty, reason = self.risk.approve_entry(self.equity, price, sig.action)
            if not ok:
                continue
            self._enter(symbol, sig.action, qty, price, ts, sig.reason)

    def _enter(self, symbol, side, qty, price, ts, why) -> None:
        self.broker.place_order(Order(symbol, side, qty, price, ts))
        self.open_trades[symbol] = OpenTrade(
            symbol, side, qty, price,
            self.risk.stop_price(price, side),
            self.risk.target_price(price, side),
        )

    def _manage_exit(self, symbol, price, ts) -> None:
        t = self.open_trades[symbol]
        hit_stop = price <= t.stop if t.side == BUY else price >= t.stop
        hit_target = price >= t.target if t.side == BUY else price <= t.target
        if not (hit_stop or hit_target):
            return
        exit_side = SELL if t.side == BUY else BUY
        self.broker.place_order(Order(symbol, exit_side, t.qty, price, ts))
        pnl = (price - t.entry) * t.qty * (1 if t.side == BUY else -1)
        self.risk.record_pnl(pnl)
        self.equity += pnl
        del self.open_trades[symbol]
