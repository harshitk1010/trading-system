"""Pure-stdlib technical indicators. All take a list[float] of closes (or OHLCV
bars where noted) and return a list aligned to the input, with None for warmup
periods where the value is not yet defined. No lookahead: value at index i uses
only bars 0..i."""
from __future__ import annotations
from typing import Sequence, Optional


def ema(values: Sequence[float], period: int) -> list[Optional[float]]:
    out: list[Optional[float]] = [None] * len(values)
    if len(values) < period:
        return out
    k = 2.0 / (period + 1)
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    prev = seed
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def sma(values: Sequence[float], period: int) -> list[Optional[float]]:
    out: list[Optional[float]] = [None] * len(values)
    run = 0.0
    for i, v in enumerate(values):
        run += v
        if i >= period:
            run -= values[i - period]
        if i >= period - 1:
            out[i] = run / period
    return out


def rsi(values: Sequence[float], period: int = 14) -> list[Optional[float]]:
    out: list[Optional[float]] = [None] * len(values)
    if len(values) <= period:
        return out
    gains, losses = 0.0, 0.0
    for i in range(1, period + 1):
        d = values[i] - values[i - 1]
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    avg_gain, avg_loss = gains / period, losses / period
    out[period] = 100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)
    for i in range(period + 1, len(values)):
        d = values[i] - values[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(d, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-d, 0.0)) / period
        out[i] = 100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)
    return out


def macd(values: Sequence[float], fast: int = 12, slow: int = 26, signal: int = 9):
    """Returns (macd_line, signal_line, histogram), each aligned to input."""
    ef, es = ema(values, fast), ema(values, slow)
    line: list[Optional[float]] = [
        (ef[i] - es[i]) if ef[i] is not None and es[i] is not None else None
        for i in range(len(values))
    ]
    defined = [v for v in line if v is not None]
    sig_defined = ema(defined, signal)
    sig: list[Optional[float]] = [None] * len(values)
    j = 0
    for i in range(len(values)):
        if line[i] is not None:
            sig[i] = sig_defined[j]
            j += 1
    hist = [
        (line[i] - sig[i]) if line[i] is not None and sig[i] is not None else None
        for i in range(len(values))
    ]
    return line, sig, hist


def bollinger(values: Sequence[float], period: int = 20, mult: float = 2.0):
    """Returns (upper, mid, lower)."""
    mid = sma(values, period)
    upper: list[Optional[float]] = [None] * len(values)
    lower: list[Optional[float]] = [None] * len(values)
    for i in range(len(values)):
        if mid[i] is None:
            continue
        window = values[i - period + 1: i + 1]
        m = mid[i]
        var = sum((x - m) ** 2 for x in window) / period
        sd = var ** 0.5
        upper[i], lower[i] = m + mult * sd, m - mult * sd
    return upper, mid, lower


def stochastic(highs, lows, closes, k_period: int = 14, d_period: int = 3):
    """Returns (%K, %D)."""
    n = len(closes)
    k: list[Optional[float]] = [None] * n
    for i in range(n):
        if i < k_period - 1:
            continue
        hh = max(highs[i - k_period + 1: i + 1])
        ll = min(lows[i - k_period + 1: i + 1])
        k[i] = 50.0 if hh == ll else 100 * (closes[i] - ll) / (hh - ll)
    k_defined = [v for v in k if v is not None]
    d_smoothed = sma(k_defined, d_period)
    d: list[Optional[float]] = [None] * n
    j = 0
    for i in range(n):
        if k[i] is not None:
            d[i] = d_smoothed[j]
            j += 1
    return k, d


def vwap(highs, lows, closes, volumes) -> list[Optional[float]]:
    """Cumulative session VWAP. Assumes input is a single session/window."""
    out: list[Optional[float]] = [None] * len(closes)
    cum_pv, cum_v = 0.0, 0.0
    for i in range(len(closes)):
        tp = (highs[i] + lows[i] + closes[i]) / 3
        cum_pv += tp * volumes[i]
        cum_v += volumes[i]
        out[i] = cum_pv / cum_v if cum_v else None
    return out
