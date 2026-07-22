"""Mock broker. A PaperBroker whose market data comes entirely from an injected
synthetic feed (data/mock_feed.py) — no credentials, no network. It exists so a
demo tenant can run the full pipeline offline. Swapping the customer's broker to
`zerodha` (with real keys) is a config change only; nothing above the Broker
interface knows this adapter is mock."""
from __future__ import annotations
from typing import Callable

from .base import PaperBroker, Bar, Quote
from . import credentials


class MockBroker(PaperBroker):
    name = "mock"

    def __init__(self, quote_source: Callable[[str], Quote | None] | None = None,
                 historical_source: Callable[[str, str, int], list[Bar]] | None = None,
                 db_path=None, customer_id=None, creds=None):
        super().__init__(quote_source, historical_source, db_path, customer_id)
        self.creds = creds or credentials.Creds("mock", {})

    def connect(self) -> None:
        return None  # no auth ever
