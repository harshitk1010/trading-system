"""Weighted-vote strategy. Each indicator casts a vote in [-1, +1]; votes are
combined by configurable weights into a net score in [-1, +1]. score >= +thr =>
BUY, score <= -thr => SELL, else HOLD. Strength is |score|.

Indicators & default weights (tune per instrument later):
  EMA stack (20/50/60/200 alignment) .. trend
  MACD histogram .................... momentum
  RSI ............................... mean-reversion / overbought-oversold
  Bollinger position ................ mean-reversion
  Stochastic %K/%D .................. momentum turn
  VWAP (price vs vwap) .............. intraday bias
"""
from __future__ import annotations
from brokers.base import Bar
from .base import Strategy, Signal, BUY, SELL, HOLD
from . import indicators as ind

DEFAULT_WEIGHTS = {
    "ema": 0.25,
    "macd": 0.20,
    "rsi": 0.15,
    "bollinger": 0.15,
    "stochastic": 0.10,
    "vwap": 0.15,
}


class WeightedIndicatorStrategy(Strategy):
    name = "weighted_indicator"

    def __init__(self, weights: dict | None = None, threshold: float = 0.25):
        self.weights = weights or DEFAULT_WEIGHTS
        self.threshold = threshold

    def min_bars(self) -> int:
        return 200  # longest EMA

    def evaluate(self, bars: list[Bar]) -> Signal:
        closes = [b.close for b in bars]
        highs = [b.high for b in bars]
        lows = [b.low for b in bars]
        vols = [b.volume for b in bars]
        i = len(bars) - 1
        votes: dict[str, float] = {}

        # --- EMA stack alignment ---
        e20 = ind.ema(closes, 20)[i]
        e50 = ind.ema(closes, 50)[i]
        e60 = ind.ema(closes, 60)[i]
        e200 = ind.ema(closes, 200)[i]
        if None not in (e20, e50, e60, e200):
            price = closes[i]
            bull = (price > e20 > e50 > e200)
            bear = (price < e20 < e50 < e200)
            if bull:
                votes["ema"] = 1.0
            elif bear:
                votes["ema"] = -1.0
            else:
                # partial: fraction of "price above EMA" conditions
                ups = sum(price > e for e in (e20, e50, e60, e200))
                votes["ema"] = (ups / 4.0) * 2 - 1

        # --- MACD histogram sign + slope ---
        _, _, hist = ind.macd(closes)
        if hist[i] is not None:
            h = hist[i]
            v = 1.0 if h > 0 else -1.0
            if i > 0 and hist[i - 1] is not None:  # weaken if fading
                if abs(h) < abs(hist[i - 1]):
                    v *= 0.5
            votes["macd"] = v

        # --- RSI ---
        r = ind.rsi(closes, 14)[i]
        if r is not None:
            if r < 30:
                votes["rsi"] = 1.0
            elif r > 70:
                votes["rsi"] = -1.0
            else:
                votes["rsi"] = (50 - r) / 20.0  # mild mean-reversion tilt

        # --- Bollinger position ---
        up, mid, lo = ind.bollinger(closes, 20, 2.0)
        if up[i] is not None and up[i] != lo[i]:
            pos = (closes[i] - lo[i]) / (up[i] - lo[i])  # 0 lower .. 1 upper
            votes["bollinger"] = (0.5 - pos) * 2  # below mid -> buy tilt

        # --- Stochastic ---
        k, d = ind.stochastic(highs, lows, closes, 14, 3)
        if k[i] is not None and d[i] is not None:
            if k[i] < 20:
                votes["stochastic"] = 1.0
            elif k[i] > 80:
                votes["stochastic"] = -1.0
            else:
                votes["stochastic"] = 1.0 if k[i] > d[i] else -1.0

        # --- VWAP ---
        vw = ind.vwap(highs, lows, closes, vols)[i]
        if vw is not None and vw != 0:
            dev = (closes[i] - vw) / vw
            votes["vwap"] = max(-1.0, min(1.0, dev * 50))  # above vwap -> bullish

        # --- weighted combine (renormalize over indicators that voted) ---
        active_w = sum(self.weights[k_] for k_ in votes)
        if active_w == 0:
            return Signal(HOLD, 0.0, "no indicators ready")
        score = sum(votes[k_] * self.weights[k_] for k_ in votes) / active_w

        action = BUY if score >= self.threshold else SELL if score <= -self.threshold else HOLD
        reason = " ".join(f"{k_}={votes[k_]:+.2f}" for k_ in votes)
        return Signal(action, min(1.0, abs(score)), reason)
