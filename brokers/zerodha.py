"""Zerodha (Kite Connect) adapter. Phase 1-2: PAPER MODE ONLY — place_order logs
a simulated fill to SQLite via PaperBroker, no real API call. Live phase will use
kiteconnect.KiteConnect for quote/historical_data/place_order."""
from __future__ import annotations
from typing import Callable

from .base import PaperBroker, Bar, Quote, now_iso
from . import credentials


class ZerodhaBroker(PaperBroker):
    name = "zerodha"

    def __init__(self, quote_source: Callable[[str], Quote | None] | None = None,
                 historical_source: Callable[[str, str, int], list[Bar]] | None = None,
                 db_path=None, customer_id=None, creds=None):
        super().__init__(quote_source, historical_source, db_path, customer_id)
        self.creds = creds or credentials.zerodha()

    def connect(self) -> None:
        # Paper: no auth. Live: KiteConnect(api_key).set_access_token(access_token).
        return None
