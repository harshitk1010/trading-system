"""Validation gate: run Phase 1's walk-forward backtest on REAL NSE data (free,
via data/yahoo_feed.py) instead of synthetic bars. Answers the staged-plan
question — does the existing weighted-indicator strategy show any edge on real
prices? Nothing here is tuned to a target; it reports as-measured metrics.

Run:  python -m backtest.real_validate
"""
from __future__ import annotations
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtest.runner import walk_forward, _fmt
from strategies.weighted_indicator import WeightedIndicatorStrategy
from data.yahoo_feed import YahooFeed

# a broad, liquid NSE basket — not cherry-picked — for an honest read
BASKET = ["RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK",
          "SBIN", "ITC", "LT", "HINDUNILVR", "KOTAKBANK"]


def main(symbols=None, folds=4, threshold=0.20):
    symbols = symbols or BASKET
    feed = YahooFeed(interval="day", rng="5y")
    strat = WeightedIndicatorStrategy(threshold=threshold)

    agg_trades = agg_wins = 0
    ret_sum = bh_sum = 0.0
    sharpes, dds, pfs = [], [], []
    beat = 0
    print(f"\nWalk-forward on REAL NSE daily data · {strat.name} · folds={folds}\n")
    print(f"  {'symbol':<12}{'bars':>6}  {'strat ret':>10} {'buy&hold':>9}  metrics")
    for sym in symbols:
        try:
            bars = feed.bars(sym)
        except Exception as e:
            print(f"  {sym:<12}{'--':>6}  fetch error: {type(e).__name__}")
            continue
        if len(bars) < strat.min_bars() + folds * 20:
            print(f"  {sym:<12}{len(bars):>6}  too few bars, skipped")
            continue
        rep = walk_forward(bars, WeightedIndicatorStrategy(threshold=threshold), folds=folds)
        a = rep["aggregate"]
        # buy&hold over the same out-of-sample window (from first tradeable bar)
        w = strat.min_bars()
        bh = bars[-1].close / bars[w].close - 1 if bars[w].close else 0.0
        agg_trades += a["total_trades"]
        agg_wins += round(a["win_rate"] * a["total_trades"])
        ret_sum += a["return_pct"]; bh_sum += bh
        beat += 1 if a["return_pct"] > bh else 0
        sharpes.append(a["avg_fold_sharpe"]); dds.append(a["max_drawdown"])
        if a["profit_factor"] != float("inf"):
            pfs.append(a["profit_factor"])
        print(f"  {sym:<12}{len(bars):>6}  {a['return_pct']*100:+9.1f}% {bh*100:+8.1f}%  "
              f"trades={a['total_trades']:>3} win={a['win_rate']*100:4.1f}% "
              f"PF={a['profit_factor']:.2f} sharpe={a['avg_fold_sharpe']:+.2f} "
              f"maxDD={a['max_drawdown']*100:5.1f}%")

    n = len(sharpes)
    if n:
        print("\n  PORTFOLIO (equal-weight across basket, out-of-sample):")
        print(f"    symbols traded    : {n}")
        print(f"    total trades      : {agg_trades}")
        print(f"    blended win       : {(agg_wins/agg_trades*100) if agg_trades else 0:.1f}%")
        print(f"    avg strat return  : {ret_sum/n*100:+.1f}%")
        print(f"    avg buy&hold      : {bh_sum/n*100:+.1f}%   <- benchmark")
        print(f"    beat buy&hold     : {beat}/{n} symbols")
        print(f"    avg sharpe        : {sum(sharpes)/n:+.2f}")
        print(f"    avg profit factor : {(sum(pfs)/len(pfs)) if pfs else float('nan'):.2f}")
        print(f"    avg max drawdown  : {sum(dds)/n*100:.1f}%")
        print("\n  Reminder: as-measured historical simulation, not a forecast. "
              "Brokerage/STT/slippage NOT modeled — they would reduce strat returns further.\n")


if __name__ == "__main__":
    main()
