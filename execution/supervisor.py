"""Multi-tenant supervisor. Runs one execution loop per active customer, each
with its own broker adapter instance, decrypted credentials, watchlist and risk
limits. Isolation guarantees:

  - every customer's cycle is wrapped in its own try/except; one customer's broker
    error cannot halt another's loop
  - per-customer circuit breaker: after N consecutive errors the customer's loop
    trips (status set, alert audited) and is skipped
  - kill switch / suspend are re-read from the control table at the TOP of each
    customer's cycle, so they take effect before the next poll, not on it
  - every signal and order is written to that customer's audit log via on_event
"""
from __future__ import annotations
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from execution.engine import Engine
from risk.manager import RiskManager, RiskConfig
from strategies.weighted_indicator import WeightedIndicatorStrategy
from tenancy import service
from tenancy.models import STATUS_KILLED, STATUS_SUSPENDED, STATUS_TRIPPED, STATUS_ACTIVE


class Supervisor:
    def __init__(self, conn, feed_factory):
        # feed_factory(customer) -> (quote_source, historical_source)
        self.conn = conn
        self.feed_factory = feed_factory
        self.engines: dict[str, Engine] = {}

    def _build_engine(self, c) -> Engine:
        quote, hist = self.feed_factory(c)
        creds = service.load_broker_creds(self.conn, c.id, c.broker)
        # mode comes from the customer; live is only reachable past the compliance
        # gate (service.set_mode rejects live without ToS + backtest recorded).
        broker = config.build_broker(
            c.broker, quote_source=quote, historical_source=hist,
            customer_id=c.id, creds=creds, mode=c.mode)
        broker.connect()
        rm = RiskManager(RiskConfig(**c.risk.as_dict()))
        rm.start_day(c.equity)
        cid = c.id
        return Engine(
            broker=broker,
            strategy=WeightedIndicatorStrategy(threshold=0.20),
            risk=rm, watchlist=list(c.watchlist), equity=c.equity,
            on_event=lambda kind, sym, detail: service.audit(self.conn, cid, kind, sym, detail),
        )

    def run_cycle(self) -> dict:
        """One poll pass over all customers. Returns {customer_id: outcome}."""
        results = {}
        for c in service.list_customers(self.conn):
            # re-read control + status BEFORE polling this customer
            flags = service.control_flags(self.conn, c.id)
            fresh = service.get_customer(self.conn, c.id)
            if flags["killed"] or flags["suspended"] or fresh.status in (
                    STATUS_KILLED, STATUS_SUSPENDED, STATUS_TRIPPED):
                results[c.id] = f"skipped:{fresh.status}"
                continue
            eng = self.engines.get(c.id) or self._build_engine(fresh)
            self.engines[c.id] = eng
            try:
                eng.step()
                service.clear_errors(self.conn, c.id)
                results[c.id] = "ok"
            except Exception as e:  # isolation: never propagate across customers
                tripped = service.record_error(self.conn, c.id, repr(e))
                results[c.id] = "error:tripped" if tripped else "error"
        return results
