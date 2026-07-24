"""Live-data PAPER trading runner for the low-volatility portfolio.

Fetches real NSE data, computes low-vol target weights, and rebalances a paper
portfolio (no real orders) scoped to a dedicated tenant. Runs today on the free
Yahoo feed (real, EOD/delayed); once broker API keys exist, point the data source
at the broker for real-time — the strategy/rebalancer don't change.

Usage:
  python -m execution.live_runner                # one rebalance now, print status
  python -m execution.live_runner --status       # just show current portfolio
  python -m execution.live_runner --loop 86400   # re-run every N seconds (daily)

Rebalance cadence is monthly by design; --force (default for a manual run) lets
you rebalance immediately for testing. This is PAPER — a validation harness to
run for weeks/months before any real capital."""
from __future__ import annotations
import sys
import time as _time
from datetime import datetime
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from data import store as dstore
from data.yahoo_feed import YahooFeed, FeedError
from tenancy import store as tstore, service
from tenancy.models import RiskLimits
from strategies.lowvol_portfolio import LowVolPortfolio
from execution.rebalancer import rebalance, portfolio_value, portfolio_cash

CUSTOMER = "lowvol_paper"
CAPITAL = 100_000.0
BENCH = "^NSEI"
UNIVERSE = [
    "RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK", "SBIN", "ITC", "LT",
    "HINDUNILVR", "KOTAKBANK", "AXISBANK", "BHARTIARTL", "ASIANPAINT", "MARUTI",
    "SUNPHARMA", "TITAN", "NESTLEIND", "HCLTECH", "ULTRACEMCO", "POWERGRID",
    "NTPC", "COALINDIA", "DABUR", "BRITANNIA", "HEROMOTOCO",
]


def _open_conn():
    dstore.connect().close()
    tstore.connect().close()
    import sqlite3
    conn = sqlite3.connect(str(dstore.DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_customer(conn):
    c = service.get_customer(conn, CUSTOMER)
    if c is None:
        c = service.create_customer(conn, id=CUSTOMER, name="Low-Vol Paper",
                                    email="lowvol@example.com", broker="mock",
                                    equity=CAPITAL, watchlist=tuple(UNIVERSE),
                                    risk=RiskLimits())
    return c


def _load_market(feed):
    history, dropped = {}, []
    for s in UNIVERSE:
        try:
            bars = feed.bars(s)
            if len(bars) > 210:
                history[s] = bars
            else:
                dropped.append(s)
        except FeedError:
            dropped.append(s)
    bench = feed.bars(BENCH)
    prices = {s: bars[-1].close for s, bars in history.items()}
    return history, bench, prices, dropped


def _print_status(broker, conn, prices):
    positions = [p for p in broker.get_positions() if p.quantity != 0]
    value = portfolio_value(broker, conn, CUSTOMER, CAPITAL, prices)
    cash = portfolio_cash(conn, CUSTOMER, CAPITAL)
    pnl = value - CAPITAL
    print(f"\n  Portfolio value : ₹{value:,.0f}   (cash ₹{cash:,.0f})")
    print(f"  P&L vs capital  : ₹{pnl:+,.0f}  ({pnl/CAPITAL*100:+.2f}%)")
    if positions:
        print(f"  Holdings ({len(positions)}):")
        print(f"    {'symbol':<12}{'qty':>6}{'avg':>10}{'ltp':>10}{'value':>12}{'wt%':>7}")
        for p in sorted(positions, key=lambda z: -z.quantity * prices.get(z.symbol, 0)):
            ltp = prices.get(p.symbol, p.avg_price)
            mv = p.quantity * ltp
            print(f"    {p.symbol:<12}{p.quantity:>6}{p.avg_price:>10.1f}{ltp:>10.1f}"
                  f"{mv:>12,.0f}{mv/value*100:>6.1f}%")
    else:
        print("  Holdings: none (cash / risk-off)")


def run_once(force=True):
    conn = _open_conn()
    _ensure_customer(conn)
    try:
        feed = YahooFeed(interval="day", rng="2y")
        history, bench, prices, dropped = _load_market(feed)
    except FeedError as e:
        print(f"  data source error: {e}")
        return
    if not history:
        print("  no market data available — aborting (nothing traded).")
        return

    creds = service.load_broker_creds(conn, CUSTOMER, "mock")
    broker = config.build_broker("mock", customer_id=CUSTOMER, creds=creds)
    broker.connect()

    strat = LowVolPortfolio(lookback=126, top_k=10, trend_filter=True)
    targets = strat.target_weights(history, bench)
    latest_date = bench[-1].ts if bench else "?"

    print(f"\n=== Low-vol paper rebalance · data through {latest_date} ===")
    if dropped:
        print(f"  (skipped {len(dropped)} symbols with insufficient/no data)")
    if not targets:
        print("  Trend filter RISK-OFF (index below 200d) — target = cash. "
              "Would sell all holdings.")
    else:
        picks = sorted(targets)
        print(f"  Target: {len(targets)} lowest-vol names, equal weight "
              f"({targets[picks[0]]*100:.1f}% each):")
        print("   ", ", ".join(picks))

    if force:
        res = rebalance(broker, conn, CUSTOMER, targets, prices, CAPITAL,
                        on_event=lambda k, s, d: service.audit(conn, CUSTOMER, k, s, d))
        service.audit(conn, CUSTOMER, "control", None,
                      {"event": "rebalance", "value": round(res["value"]),
                       "orders": len(res["orders"]), "date": latest_date})
        print(f"  Placed {len(res['orders'])} paper orders to reach target.")
    _print_status(broker, conn, prices)
    print()


def status():
    conn = _open_conn()
    _ensure_customer(conn)
    feed = YahooFeed(interval="day", rng="2y")
    _, _, prices, _ = _load_market(feed)
    broker = config.build_broker("mock", customer_id=CUSTOMER)
    broker.connect()
    _print_status(broker, conn, prices)
    print()


def main():
    args = sys.argv[1:]
    if "--status" in args:
        status()
    elif "--loop" in args:
        every = int(args[args.index("--loop") + 1]) if len(args) > args.index("--loop") + 1 else 86400
        print(f"Looping every {every}s (paper). Ctrl+C to stop.")
        while True:
            run_once(force=True)
            _time.sleep(every)
    else:
        run_once(force=True)


if __name__ == "__main__":
    main()
