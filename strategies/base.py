"""Strategy interface. A strategy consumes a rolling window of bars and emits a
Signal for the most recent bar. Strategies are pure: no I/O, no order placement,
no lookahead — evaluate(bars) may only read bars[:] up to the last element."""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass

from brokers.base import Bar

BUY, SELL, HOLD = "BUY", "SELL", "HOLD"


@dataclass
class Signal:
    action: str            # BUY | SELL | HOLD
    strength: float        # 0..1 confidence (weighted vote score, normalized)
    reason: str            # human-readable breakdown for logs/audit


class Strategy(ABC):
    name: str

    @abstractmethod
    def evaluate(self, bars: list[Bar]) -> Signal:
        """Return a Signal for bars[-1] using only bars[0..-1]."""

    @abstractmethod
    def min_bars(self) -> int:
        """Warmup: minimum bars required before signals are meaningful."""
