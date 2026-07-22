"""Angel One (SmartAPI) adapter (SmartApi-python). Paper mode only — orders log to
SQLite via PaperBroker. Live phase: SmartConnect.generateSession(client_code, mpin,
totp) then getCandleData / ltpData / placeOrder. TOTP is derived from the account's
2FA secret each login; session token is short-lived."""
from __future__ import annotations
from typing import Callable

from .base import PaperBroker, Bar, Quote
from . import credentials


class AngelOneBroker(PaperBroker):
    name = "angelone"

    def __init__(self, quote_source: Callable[[str], Quote | None] | None = None,
                 historical_source: Callable[[str, str, int], list[Bar]] | None = None,
                 db_path=None, customer_id=None, creds=None):
        super().__init__(quote_source, historical_source, db_path, customer_id)
        self.creds = creds or credentials.angelone()

    def connect(self) -> None:
        # Paper: no auth. Live: SmartConnect(api_key).generateSession(code, mpin, totp).
        return None
