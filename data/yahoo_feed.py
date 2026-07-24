"""Free real historical data via Yahoo Finance's public chart API — no key, no
paid feed. Returns the same `Bar` shape as the mock feed and exposes
`historical_source` / `quote_source` callables with the Broker-interface
signatures, so it is a drop-in replacement for data/mock_feed.py behind
config.build_broker(...). NSE symbols use the `.NS` suffix; the Nifty 50 index is
`^NSEI`.

This is read-only market data for backtesting/validation — no orders, no auth."""
from __future__ import annotations
import time
from datetime import datetime, timezone

import httpx

from brokers.base import Bar, Quote

BASE = "https://query1.finance.yahoo.com/v8/finance/chart/"
_HEADERS = {"User-Agent": "Mozilla/5.0"}
_INTERVAL = {"day": "1d", "1d": "1d", "week": "1wk", "hour": "1h", "60minute": "1h"}


def yahoo_symbol(symbol: str, suffix: str = ".NS") -> str:
    """Map a plain ticker to Yahoo's symbol. Indices/already-suffixed pass
    through; suffix="" (e.g. US market) passes the raw ticker."""
    if symbol.startswith("^") or "." in symbol or not suffix:
        return symbol
    return f"{symbol}{suffix}"


class FeedError(RuntimeError):
    pass


def fetch(symbol: str, interval: str = "day", rng: str = "5y",
          timeout: float = 20.0, suffix: str = ".NS", retries: int = 2) -> list[Bar]:
    """Fetch OHLCV, **back-adjusted for splits/dividends** using Yahoo's adjclose
    so corporate actions don't appear as false price gaps. Retries transient
    network/rate-limit errors; raises FeedError on give-up."""
    last_err = None
    for attempt in range(retries + 1):
        try:
            r = httpx.get(BASE + yahoo_symbol(symbol, suffix),
                          params={"range": rng, "interval": _INTERVAL.get(interval, "1d")},
                          headers=_HEADERS, timeout=timeout)
            r.raise_for_status()
            return _parse(r.json())
        except Exception as e:                       # network, 429, malformed, etc.
            last_err = e
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
    raise FeedError(f"could not fetch {symbol}: {last_err}")


def _parse(payload) -> list[Bar]:
    try:
        res = payload["chart"]["result"][0]
    except (KeyError, IndexError, TypeError):
        raise FeedError("unexpected response shape from data source")
    ts = res.get("timestamp") or []
    q = res["indicators"]["quote"][0]
    adj = (res["indicators"].get("adjclose") or [{}])[0].get("adjclose")
    bars: list[Bar] = []
    for i, t in enumerate(ts):
        o, h, l, c, v = q["open"][i], q["high"][i], q["low"][i], q["close"][i], q["volume"][i]
        if None in (o, h, l, c) or o <= 0 or c <= 0 or h < l:   # skip gaps / bad ticks
            continue
        # back-adjust OHLC by the split/dividend factor (adjclose / close)
        f = (adj[i] / c) if (adj and i < len(adj) and adj[i] is not None and c) else 1.0
        bars.append(Bar(
            ts=datetime.fromtimestamp(t, timezone.utc).date().isoformat(),
            open=round(o * f, 2), high=round(h * f, 2), low=round(l * f, 2),
            close=round(c * f, 2), volume=float(v or 0)))
    return bars


class YahooFeed:
    """Caches fetched series so repeated get_historical calls don't refetch."""

    def __init__(self, interval: str = "day", rng: str = "5y", suffix: str = ".NS"):
        self.interval, self.rng, self.suffix = interval, rng, suffix
        self._cache: dict[str, list[Bar]] = {}

    def bars(self, symbol: str, limit: int | None = None) -> list[Bar]:
        if symbol not in self._cache:
            self._cache[symbol] = fetch(symbol, self.interval, self.rng, suffix=self.suffix)
        b = self._cache[symbol]
        return b[-limit:] if limit else b

    def historical_source(self, symbol: str, interval: str, limit: int) -> list[Bar]:
        return self.bars(symbol, limit)

    def quote_source(self, symbol: str) -> Quote | None:
        b = self.bars(symbol, 1)
        return Quote(symbol, b[-1].close, b[-1].ts) if b else None
