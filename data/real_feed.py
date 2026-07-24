"""Real NSE equity data feed — a drop-in replacement for data/mock_feed.py that
serves ACTUAL end-of-day prices (free, via data/yahoo_feed.py) instead of
synthetic bars. Same interface (advance / bars / historical_source /
quote_source / last_price), so the dashboard, strategy and risk code use it with
no changes — only the data source differs.

Equity only, daily bars, EOD/delayed (free source is not real-time). It reveals
real history one bar at a time so the paper engine can walk forward over real
prices; when it reaches the latest real bar it holds there."""
from __future__ import annotations
from brokers.base import Bar, Quote
from data.yahoo_feed import YahooFeed


class RealReplayFeed:
    def __init__(self, symbols=("RELIANCE",), warmup: int = 220, rng: str = "3y"):
        self.symbols = tuple(symbols)
        yf = YahooFeed(interval="day", rng=rng)
        self._series: dict[str, list[Bar]] = {s: yf.bars(s) for s in self.symbols}
        self._maxlen = min(len(v) for v in self._series.values())
        self._warmup = min(warmup, max(1, self._maxlen - 1))
        self._cursor = self._warmup

    def advance(self, steps: int = 1) -> None:
        for _ in range(steps):
            if self._cursor < self._maxlen:
                self._cursor += 1

    @property
    def at_end(self) -> bool:
        return self._cursor >= self._maxlen

    def bars(self, symbol: str, limit: int | None = None) -> list[Bar]:
        revealed = self._series[symbol][: self._cursor]
        return revealed[-limit:] if limit else revealed

    def historical_source(self, symbol: str, interval: str, limit: int) -> list[Bar]:
        return self.bars(symbol, limit)

    def quote_source(self, symbol: str) -> Quote | None:
        b = self.bars(symbol, 1)
        return Quote(symbol, b[-1].close, b[-1].ts) if b else None

    def last_price(self, symbol: str) -> float | None:
        b = self.bars(symbol, 1)
        return b[-1].close if b else None
