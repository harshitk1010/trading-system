"""Tenancy domain models. A Customer owns a broker choice, risk limits, a
mode (paper|live), and consent state. Risk limits reuse Phase 1's RiskConfig
fields — tenancy layers on top of the existing risk module, it does not replace
it."""
from __future__ import annotations
from dataclasses import dataclass, asdict, field

PAPER, LIVE = "paper", "live"
STATUS_ACTIVE, STATUS_SUSPENDED, STATUS_TRIPPED, STATUS_KILLED = (
    "active", "suspended", "breaker_tripped", "killed")

TOS_VERSION = "2026-07-01"  # bump when the disclaimer text changes


@dataclass
class RiskLimits:
    risk_per_trade: float = 0.02
    stop_loss_pct: float = 0.02
    take_profit_pct: float = 0.04
    max_daily_loss_pct: float = 0.05
    max_position_pct: float = 0.25

    def as_dict(self) -> dict:
        return asdict(self)

    def is_tighter_or_equal(self, other: "RiskLimits") -> bool:
        """True if every limit in `other` is no looser than in self. Used to reject
        admin edits that would loosen a customer's own limits."""
        return (
            other.risk_per_trade <= self.risk_per_trade
            and other.stop_loss_pct <= self.stop_loss_pct
            and other.max_position_pct <= self.max_position_pct
            and other.max_daily_loss_pct <= self.max_daily_loss_pct
        )


@dataclass
class Customer:
    id: str
    name: str
    email: str
    broker: str = "zerodha"
    mode: str = PAPER
    status: str = STATUS_ACTIVE
    equity: float = 100_000.0
    watchlist: tuple = ("DEMO",)
    risk: RiskLimits = field(default_factory=RiskLimits)
    error_count: int = 0
    tos_version: str | None = None
    tos_accepted_at: str | None = None
    backtest_viewed_at: str | None = None
