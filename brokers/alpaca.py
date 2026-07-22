"""Alpaca adapter (alpaca-py). US equities. Paper mode only here — orders log to
SQLite via PaperBroker. Live phase: alpaca.trading.TradingClient(paper=True) plus
alpaca.data StockHistoricalDataClient for quote/bars. Keys are long-lived; the
`paper=True` flag routes to Alpaca's own paper endpoint."""
from __future__ import annotations
from typing import Callable

from .base import PaperBroker, Bar, Quote
from . import credentials


class AlpacaBroker(PaperBroker):
    name = "alpaca"

    def __init__(self, quote_source: Callable[[str], Quote | None] | None = None,
                 historical_source: Callable[[str, str, int], list[Bar]] | None = None,
                 db_path=None, customer_id=None, creds=None):
        super().__init__(quote_source, historical_source, db_path, customer_id)
        self.creds = creds or credentials.alpaca()

    def connect(self) -> None:
        # Paper: no auth. Live: TradingClient(api_key, secret_key, paper=True).
        return None
