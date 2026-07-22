"""Synthetic OHLCV generator for demo/backtest. Geometric-Brownian price with
alternating trend/chop regimes so signals have something to catch. Deterministic
given a seed."""
from __future__ import annotations
import math
import random
from brokers.base import Bar


def generate(n: int = 1200, start: float = 1000.0, seed: int = 42) -> list[Bar]:
    rng = random.Random(seed)
    bars: list[Bar] = []
    price = start
    for i in range(n):
        # regime flips every ~150 bars: drift alternates sign, vol varies
        regime = (i // 150) % 3
        drift = {0: 0.0008, 1: -0.0006, 2: 0.0}[regime]
        vol = {0: 0.010, 1: 0.013, 2: 0.008}[regime]
        ret = rng.gauss(drift, vol)
        new_price = max(1.0, price * math.exp(ret))
        hi = max(price, new_price) * (1 + abs(rng.gauss(0, vol / 2)))
        lo = min(price, new_price) * (1 - abs(rng.gauss(0, vol / 2)))
        volume = rng.uniform(1e5, 5e5)
        bars.append(Bar(
            ts=f"2020-01-01T00:00:00+{i:04d}",
            open=round(price, 2), high=round(hi, 2), low=round(lo, 2),
            close=round(new_price, 2), volume=round(volume, 0),
        ))
        price = new_price
    return bars
