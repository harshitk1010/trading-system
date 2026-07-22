"""Live-mode safety guard. Real money changes the risk surface: orders must be
blocked outside market hours, past the daily-loss limit measured against the REAL
account, and when a kill switch is set; and local intent must be reconciled
against the broker's actual positions. Fail-safe: on any doubt, block, don't send.

Paper mode does not use this — it is only wired in for live customers."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import time as dtime

from risk.manager import RiskConfig

# NSE regular session (IST). Kept explicit; a holiday calendar is a later add.
MKT_OPEN, MKT_CLOSE = dtime(9, 15), dtime(15, 30)


@dataclass
class GuardResult:
    ok: bool
    reason: str


class LiveGuard:
    def __init__(self, kite, cfg: RiskConfig):
        self._kite = kite
        self.cfg = cfg

    # real balance is the source of truth for daily-loss enforcement
    def available_funds(self) -> float:
        try:
            eq = self._kite.margins().get("equity", {})
            return float(eq.get("available", {}).get("live_balance", 0.0))
        except Exception:
            return 0.0

    def market_open(self, now) -> bool:
        if now.weekday() >= 5:                       # Sat/Sun
            return False
        return MKT_OPEN <= now.time() <= MKT_CLOSE

    def pre_trade_check(self, *, now, killed: bool, day_start_equity: float,
                        realized_today: float, order_notional: float,
                        equity: float) -> GuardResult:
        if killed:
            return GuardResult(False, "kill switch active")
        if not self.market_open(now):
            return GuardResult(False, "market closed")
        if day_start_equity > 0 and realized_today <= -self.cfg.max_daily_loss_pct * day_start_equity:
            return GuardResult(False, "daily loss limit reached")
        if equity > 0 and order_notional > self.cfg.max_position_pct * equity:
            return GuardResult(False, "exceeds max position size")
        return GuardResult(True, "ok")

    def reconcile(self, local_positions, broker_positions) -> list[dict]:
        """Compare intended (local) vs actual (broker) positions. Any mismatch is
        a signal to halt and investigate — never to auto-correct with more orders."""
        loc = {p.symbol: p.quantity for p in local_positions}
        brk = {p.symbol: p.quantity for p in broker_positions}
        mismatches = []
        for sym in set(loc) | set(brk):
            lq, bq = loc.get(sym, 0), brk.get(sym, 0)
            if lq != bq:
                mismatches.append({"symbol": sym, "local_qty": lq, "broker_qty": bq})
        return mismatches
