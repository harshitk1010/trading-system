"""Free real historical data via Yahoo Finance's public chart API — no key, no
paid feed. Returns the same `Bar` shape as the mock feed and exposes
`historical_source` / `quote_source` callables with the Broker-interface
signatures, so it is a drop-in replacement for data/mock_feed.py behind
config.build_broker(...). NSE symbols use the `.NS` suffix; the Nifty 50 index is
`^NSEI`.

This is read-only market data for backtesting/validation — no orders, no auth."""
from __future__ import annotations
from datetime import datetime, timezone

import httpx

from brokers.base import Bar, Quote

BASE = "https://query1.finance.yahoo.com/v8/finance/chart/"
_HEADERS = {"User-Agent": "Mozilla/5.0"}
_INTERVAL = {"day": "1d", "1d": "1d", "week": "1wk", "hour": "1h", "60minute": "1h"}


def yahoo_symbol(symbol: str) -> str:
    """Map a plain NSE ticker to Yahoo's symbol. Indices/already-suffixed pass through."""
    if symbol.startswith("^") or "." in symbol:
        return symbol
    return f"{symbol}.NS"


def fetch(symbol: str, interval: str = "day", rng: str = "5y",
          timeout: float = 20.0) -> list[Bar]:
    r = httpx.get(BASE + yahoo_symbol(symbol),
                  params={"range": rng, "interval": _INTERVAL.get(interval, "1d")},
                  headers=_HEADERS, timeout=timeout)
    r.raise_for_status()
    res = r.json()["chart"]["result"][0]
    ts = res.get("timestamp") or []
    q = res["indicators"]["quote"][0]
    bars: list[Bar] = []
    for i, t in enumerate(ts):
        o, h, l, c, v = q["open"][i], q["high"][i], q["low"][i], q["close"][i], q["volume"][i]
        if None in (o, h, l, c):          # Yahoo emits gaps as null — skip them
            continue
        bars.append(Bar(
            ts=datetime.fromtimestamp(t, timezone.utc).date().isoformat(),
            open=round(o, 2), high=round(h, 2), low=round(l, 2),
            close=round(c, 2), volume=float(v or 0)))
    return bars


class YahooFeed:
    """Caches fetched series so repeated get_historical calls don't refetch."""

    def __init__(self, interval: str = "day", rng: str = "5y"):
        self.interval, self.rng = interval, rng
        self._cache: dict[str, list[Bar]] = {}

    def bars(self, symbol: str, limit: int | None = None) -> list[Bar]:
        if symbol not in self._cache:
            self._cache[symbol] = fetch(symbol, self.interval, self.rng)
        b = self._cache[symbol]
        return b[-limit:] if limit else b

    def historical_source(self, symbol: str, interval: str, limit: int) -> list[Bar]:
        return self.bars(symbol, limit)

    def quote_source(self, symbol: str) -> Quote | None:
        b = self.bars(symbol, 1)
        return Quote(symbol, b[-1].close, b[-1].ts) if b else None
