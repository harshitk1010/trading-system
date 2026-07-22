"""CLI entrypoint. Phase 1: single user, single broker (Zerodha), paper only.

  python main.py backtest              walk-forward backtest on synthetic data
  python main.py paper                 run one paper engine cycle on synthetic feed
  python main.py positions             show paper position book
"""
from __future__ import annotations
import sys

from brokers.zerodha import ZerodhaBroker, now_iso
from brokers.base import Quote, Bar
from strategies.weighted_indicator import WeightedIndicatorStrategy
from risk.manager import RiskManager, RiskConfig
from execution.engine import Engine
from backtest import synthetic


def _synthetic_feed():
    bars = synthetic.generate(n=400, seed=11)

    def historical(symbol, interval, limit):
        return bars[-limit:]

    def quote(symbol):
        return Quote(symbol, bars[-1].close, bars[-1].ts)

    return quote, historical


def cmd_backtest():
    from backtest.runner import main as bt_main
    bt_main()


def cmd_paper():
    quote, historical = _synthetic_feed()
    broker = ZerodhaBroker(quote_source=quote, historical_source=historical)
    broker.connect()
    engine = Engine(
        broker=broker,
        strategy=WeightedIndicatorStrategy(threshold=0.20),
        risk=RiskManager(RiskConfig()),
        watchlist=["DEMO"],
    )
    engine.risk.start_day(engine.equity)
    engine.step()
    print("paper cycle complete. positions:")
    for p in broker.get_positions():
        print(f"  {p.symbol} qty={p.quantity} avg={p.avg_price:.2f}")
    if not broker.get_positions():
        print("  (none)")


def cmd_positions():
    broker = ZerodhaBroker()
    for p in broker.get_positions():
        print(f"  {p.symbol} qty={p.quantity} avg={p.avg_price:.2f}")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "backtest"
    {"backtest": cmd_backtest, "paper": cmd_paper, "positions": cmd_positions}.get(
        cmd, cmd_backtest
    )()


if __name__ == "__main__":
    main()
