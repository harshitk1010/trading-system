"""Low-volatility PORTFOLIO strategy — the one hypothesis that showed a real
(India-specific) edge in validation. Unlike the per-symbol signal strategy, this
picks a basket: rank the universe by trailing volatility, hold the K calmest,
equal-weight, rebalance monthly, optionally step to cash when the index is below
its long trend. Emits target weights; the rebalancer turns them into paper orders.

Same selection logic as backtest/lowvol.py, packaged for live paper use."""
from __future__ import annotations
from brokers.base import Bar


def trailing_vol(closes: list[float]) -> float | None:
    if len(closes) < 2:
        return None
    rets = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes)) if closes[i - 1]]
    if len(rets) < 2:
        return None
    m = sum(rets) / len(rets)
    return (sum((r - m) ** 2 for r in rets) / (len(rets) - 1)) ** 0.5


class LowVolPortfolio:
    name = "lowvol_portfolio"

    def __init__(self, lookback: int = 126, top_k: int = 10,
                 trend_filter: bool = True, trend_window: int = 200):
        self.lookback = lookback
        self.top_k = top_k
        self.trend_filter = trend_filter
        self.trend_window = trend_window

    def min_bars(self) -> int:
        return max(self.lookback, self.trend_window) + 2

    def target_weights(self, history: dict[str, list[Bar]],
                       benchmark: list[Bar] | None = None) -> dict[str, float]:
        """history: {symbol: recent bars}. Returns {symbol: weight} summing to 1,
        or {} to hold cash (trend filter off / nothing qualifies)."""
        if self.trend_filter and benchmark and len(benchmark) > self.trend_window:
            bc = [b.close for b in benchmark]
            sma = sum(bc[-self.trend_window:]) / self.trend_window
            if bc[-1] < sma:                       # index below trend -> risk-off
                return {}
        vols: dict[str, float] = {}
        for s, bars in history.items():
            closes = [b.close for b in bars][-(self.lookback + 1):]
            v = trailing_vol(closes)
            if v and v > 0:
                vols[s] = v
        chosen = sorted(vols, key=lambda z: vols[z])[:self.top_k]
        if not chosen:
            return {}
        w = 1.0 / len(chosen)
        return {s: w for s in chosen}
