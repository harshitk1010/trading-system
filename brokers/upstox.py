"""Upstox adapter (upstox-python-sdk). Paper mode only — orders log to SQLite via
PaperBroker. Live phase: upstox_client MarketQuoteApi / HistoryApi / OrderApi with
an OAuth2 access token (expires daily, re-auth via redirect flow)."""
from __future__ import annotations
from typing import Callable

from .base import PaperBroker, Bar, Quote
from . import credentials


class UpstoxBroker(PaperBroker):
    name = "upstox"

    def __init__(self, quote_source: Callable[[str], Quote | None] | None = None,
                 historical_source: Callable[[str, str, int], list[Bar]] | None = None,
                 db_path=None):
        super().__init__(quote_source, historical_source, db_path)
        self.creds = credentials.upstox()

    def connect(self) -> None:
        # Paper: no auth. Live: upstox_client.Configuration(access_token=...).
        return None
