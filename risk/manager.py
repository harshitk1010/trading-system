"""Risk manager. Sits between strategy signals and order placement. Enforces:
  - position sizing as % of equity per trade
  - per-position stop-loss (%) and take-profit (%)
  - max daily loss (% of starting-day equity) -> halt new entries for the day

Stateless sizing helpers + a stateful daily-loss tracker. All percentages are
fractions (0.02 == 2%)."""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class RiskConfig:
    risk_per_trade: float = 0.02      # fraction of equity risked per trade
    stop_loss_pct: float = 0.02       # per-position stop distance
    take_profit_pct: float = 0.04     # per-position target distance
    max_daily_loss_pct: float = 0.05  # halt entries after this daily drawdown
    max_position_pct: float = 0.25    # cap notional per position


@dataclass
class RiskManager:
    cfg: RiskConfig = field(default_factory=RiskConfig)
    day_start_equity: float = 0.0
    realized_pnl_today: float = 0.0

    def start_day(self, equity: float) -> None:
        self.day_start_equity = equity
        self.realized_pnl_today = 0.0

    def record_pnl(self, pnl: float) -> None:
        self.realized_pnl_today += pnl

    def halted(self) -> bool:
        if self.day_start_equity <= 0:
            return False
        return self.realized_pnl_today <= -self.cfg.max_daily_loss_pct * self.day_start_equity

    def position_size(self, equity: float, price: float) -> int:
        """Shares to buy. Sized so that a stop-loss hit ~= risk_per_trade of
        equity, then capped by max_position_pct notional. Integer shares."""
        if price <= 0:
            return 0
        risk_amount = equity * self.cfg.risk_per_trade
        per_share_risk = price * self.cfg.stop_loss_pct
        qty_by_risk = int(risk_amount / per_share_risk) if per_share_risk > 0 else 0
        qty_by_cap = int((equity * self.cfg.max_position_pct) / price)
        return max(0, min(qty_by_risk, qty_by_cap))

    def stop_price(self, entry: float, side: str) -> float:
        return entry * (1 - self.cfg.stop_loss_pct) if side == "BUY" \
            else entry * (1 + self.cfg.stop_loss_pct)

    def target_price(self, entry: float, side: str) -> float:
        return entry * (1 + self.cfg.take_profit_pct) if side == "BUY" \
            else entry * (1 - self.cfg.take_profit_pct)

    def approve_entry(self, equity: float, price: float, side: str):
        """Returns (approved: bool, qty: int, reason: str)."""
        if self.halted():
            return False, 0, "daily loss limit reached"
        qty = self.position_size(equity, price)
        if qty <= 0:
            return False, 0, "size rounds to zero"
        return True, qty, "ok"
