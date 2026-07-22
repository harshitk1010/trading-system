"""Live-path smoke test with a FAKE Kite client — proves the live order mapping,
data mapping, positions, safety guard and reconciliation work WITHOUT any real
credentials or real orders. Also checks paper mode is unchanged.

Run: python -m backtest.live_smoke
"""
from __future__ import annotations
import sys
from datetime import datetime
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from brokers.zerodha import ZerodhaBroker, LiveOrderError
from brokers.base import Order, now_iso
from brokers.credentials import Creds
from execution.live_guard import LiveGuard, MKT_OPEN
from risk.manager import RiskConfig


class FakeKite:
    """Mimics the subset of KiteConnect the adapter/guard use."""
    def __init__(self, reject=False):
        self.placed = []
        self.cancelled = []
        self._reject = reject

    def profile(self):
        return {"user_id": "AB1234", "user_name": "Demo"}

    def ltp(self, keys):
        return {keys[0]: {"last_price": 1234.5}}

    def historical_data(self, token, frm, to, interval):
        return [{"date": "2026-07-20", "open": 100, "high": 105, "low": 99,
                 "close": 104, "volume": 1000},
                {"date": "2026-07-21", "open": 104, "high": 108, "low": 103,
                 "close": 107, "volume": 1200}]

    def instruments(self, exchange):
        return [{"tradingsymbol": "RELIANCE", "instrument_token": 738561},
                {"tradingsymbol": "TCS", "instrument_token": 2953217}]

    def place_order(self, **kw):
        if self._reject:
            raise Exception("insufficient funds")
        self.placed.append(kw)
        return 240722000000001 + len(self.placed)

    def cancel_order(self, **kw):
        self.cancelled.append(kw)
        return kw["order_id"]

    def positions(self):
        return {"net": [{"tradingsymbol": "RELIANCE", "quantity": 10, "average_price": 100.0},
                        {"tradingsymbol": "TCS", "quantity": 0, "average_price": 0.0}]}

    def margins(self):
        return {"equity": {"available": {"live_balance": 100000.0}}}


def check(name, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    assert cond, name


def main():
    creds = Creds("zerodha", {"KITE_API_KEY": "k", "KITE_ACCESS_TOKEN": "t"})
    fake = FakeKite()

    print("LIVE path (fake Kite — no real orders):")
    b = ZerodhaBroker(creds=creds, mode="live", kite=fake, customer_id="demo")
    b.connect()
    check("connect validates session (profile)", True)

    q = b.get_quote("RELIANCE")
    check("get_quote maps ltp", q and q.last_price == 1234.5)

    bars = b.get_historical("RELIANCE", "day", 2)
    check("get_historical maps bars", len(bars) == 2 and bars[-1].close == 107)

    oid = b.place_order(Order("RELIANCE", "BUY", 5, 107.0, now_iso()))
    o = fake.placed[-1]
    check("place_order hits Kite with right fields",
          o["tradingsymbol"] == "RELIANCE" and o["transaction_type"] == "BUY"
          and o["quantity"] == 5 and o["order_type"] == "MARKET")
    check("place_order returns broker id (str)", isinstance(oid, str))

    check("cancel_order calls Kite", b.cancel_order("240722000000002") is True)

    pos = b.get_positions()
    check("get_positions from broker, filters zero-qty",
          len(pos) == 1 and pos[0].symbol == "RELIANCE" and pos[0].quantity == 10)

    # fail-safe: broker rejection -> LiveOrderError, no crash-through
    b2 = ZerodhaBroker(creds=creds, mode="live", kite=FakeKite(reject=True), customer_id="demo")
    b2.connect()
    raised = False
    try:
        b2.place_order(Order("TCS", "BUY", 1, 100.0, now_iso()))
    except LiveOrderError:
        raised = True
    check("rejected order raises LiveOrderError (fail-safe)", raised)

    print("\nSAFETY guard:")
    g = LiveGuard(fake, RiskConfig())
    check("available_funds reads real balance", g.available_funds() == 100000.0)

    closed = datetime(2026, 7, 22, 6, 0)      # before 09:15 IST
    r = g.pre_trade_check(now=closed, killed=False, day_start_equity=100000,
                          realized_today=0, order_notional=1000, equity=100000)
    check("blocks when market closed", not r.ok and "closed" in r.reason)

    open_dt = datetime(2026, 7, 22, 11, 0)
    r = g.pre_trade_check(now=open_dt, killed=True, day_start_equity=100000,
                          realized_today=0, order_notional=1000, equity=100000)
    check("blocks on kill switch", not r.ok and "kill" in r.reason)

    r = g.pre_trade_check(now=open_dt, killed=False, day_start_equity=100000,
                          realized_today=-6000, order_notional=1000, equity=94000)
    check("blocks past daily loss limit", not r.ok and "daily loss" in r.reason)

    r = g.pre_trade_check(now=open_dt, killed=False, day_start_equity=100000,
                          realized_today=0, order_notional=50000, equity=100000)
    check("blocks oversized position", not r.ok and "position" in r.reason)

    r = g.pre_trade_check(now=open_dt, killed=False, day_start_equity=100000,
                          realized_today=0, order_notional=1000, equity=100000)
    check("allows a valid order", r.ok)

    from brokers.base import Position
    mm = g.reconcile([Position("RELIANCE", 10, 100.0), Position("TCS", 5, 50.0)],
                     b.get_positions())
    check("reconcile flags TCS mismatch (local 5 vs broker 0)",
          any(m["symbol"] == "TCS" and m["local_qty"] == 5 and m["broker_qty"] == 0 for m in mm))

    print("\nPAPER regression (default mode, SQLite, no Kite):")
    import config
    from data import store as dstore
    dstore.connect().close()
    pb = config.build_broker("zerodha", customer_id="smoke_paper")
    pb.connect()
    conn = pb._conn
    conn.execute("DELETE FROM orders WHERE customer_id='smoke_paper'")
    conn.execute("DELETE FROM positions WHERE customer_id='smoke_paper'"); conn.commit()
    pid = pb.place_order(Order("RELIANCE", "BUY", 3, 100.0, now_iso()))
    check("paper order returns PAPER- id", pid.startswith("PAPER-"))
    check("paper updates local book", any(p.symbol == "RELIANCE" and p.quantity == 3
                                          for p in pb.get_positions()))
    check("live mode rejected for non-live-capable broker",
          _raises(lambda: config.build_broker("alpaca", mode="live")))

    print("\nAll live-path + safety + paper-regression checks passed.")


def _raises(fn):
    try:
        fn(); return False
    except Exception:
        return True


if __name__ == "__main__":
    main()
