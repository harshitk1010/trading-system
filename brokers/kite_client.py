"""Live Zerodha (Kite Connect) session helpers. Only used when a customer runs in
LIVE mode — paper mode never imports kiteconnect. Real credentials come from the
Phase 3 vault (decrypted per-customer), never hardcoded, never logged."""
from __future__ import annotations
from datetime import datetime, timedelta


def build_kite(creds):
    """Construct an authenticated KiteConnect from decrypted Creds. Access token
    is regenerated daily by the caller's login flow (see docs in CLAUDE.md)."""
    from kiteconnect import KiteConnect  # lazy: live-only dependency
    v = creds.values
    api_key = v.get("KITE_API_KEY", "")
    access_token = v.get("KITE_ACCESS_TOKEN", "")
    if not api_key or not access_token:
        raise ValueError("live mode needs KITE_API_KEY and a fresh KITE_ACCESS_TOKEN")
    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(access_token)
    return kite


class InstrumentMap:
    """tradingsymbol -> instrument_token, needed for historical_data. Lazily
    fetches and caches the exchange instrument dump once per session."""

    def __init__(self, kite, exchange: str = "NSE"):
        self._kite = kite
        self._exchange = exchange
        self._map: dict[str, int] | None = None

    def token(self, symbol: str) -> int | None:
        if self._map is None:
            self._map = {row["tradingsymbol"]: row["instrument_token"]
                         for row in self._kite.instruments(self._exchange)}
        return self._map.get(symbol)


def kite_interval(interval: str) -> str:
    return {"day": "day", "1d": "day", "hour": "60minute",
            "60minute": "60minute", "minute": "minute"}.get(interval, "day")


def date_window(interval: str, limit: int):
    """(from_date, to_date) covering ~`limit` bars of `interval`, generously
    padded for weekends/holidays. Kite wants datetime objects."""
    to = datetime.now()
    if kite_interval(interval) == "day":
        frm = to - timedelta(days=int(limit * 1.6) + 10)
    else:
        frm = to - timedelta(days=int(limit / 6) + 5)
    return frm, to
