"""Harden the low-vol edge with two robustness checks:

  A. CROSS-MARKET — run the identical low-vol logic on US large-caps (different
     market, different era mix). If the anomaly shows up here too, it is very
     unlikely to be an India-specific or lucky-sample artifact.

  B. UNIVERSE BOOTSTRAP — re-run low-vol vs equal-weight on many RANDOM sub-
     universes of the NSE names. If low-vol beats equal-weight on Sharpe across
     random universes, the edge is intrinsic to low-vol selection, not to the
     specific (survivor-biased) list of names picked.

Reuses the low-vol engine unchanged. Run: python -m backtest.lowvol_robust
"""
from __future__ import annotations
import random
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.yahoo_feed import YahooFeed
from backtest.momentum import UNIVERSE as NSE_UNIVERSE, _load, _slice_metrics, _sma_trend, _row
from backtest.lowvol import build_curves, _bench_curves, _m

# US large-caps incl. clear decade laggards (Intel, Walgreens, Paramount, Boeing,
# AT&T, GE-era, Kraft Heinz, Ford, Xerox…) to blunt survivorship on the US side too.
US_UNIVERSE = [
    "AAPL", "MSFT", "AMZN", "GOOGL", "META", "NVDA", "JPM", "JNJ", "V", "PG",
    "HD", "MA", "XOM", "CVX", "KO", "PEP", "WMT", "DIS", "CSCO", "ORCL",
    "MCD", "NKE", "MRK", "PFE", "BAC", "WFC", "GE", "IBM", "CAT", "BA",
    "INTC", "WBA", "PARA", "KHC", "F", "T", "VZ", "XRX", "HPQ", "C",
]
US_BENCH = "^GSPC"


def run_market(name, feed, universe, bench_sym, K=10):
    data = _load(feed, universe)
    bench = feed.bars(bench_sym)
    bench_close = {b.ts: b.close for b in bench}
    calendar = [b.ts for b in bench]
    if len(calendar) < 400 or len(data) < 15:
        print(f"  {name}: insufficient data ({len(data)} names, {len(calendar)} days)")
        return
    start = 126 + 2
    lv = build_curves(data, bench_close, calendar, K=K)
    nifty, ew = _bench_curves(data, bench_close, calendar, start)
    print(f"\n{name} — {len(data)} names · {len(calendar)} days (~{len(calendar)/252:.1f}y):")
    print(_row("Low-vol bottom-10", _m(lv["strat"])))
    print(_row("Index (buy & hold)", _m(nifty)))
    print(_row("Equal-weight (buy & hold)", _m(ew)))
    s, e = _m(lv["strat"])["sharpe"], _m(ew)["sharpe"]
    verdict = "BEATS" if s > e else "ties" if abs(s - e) < 0.03 else "loses to"
    print(f"  -> low-vol Sharpe {s:+.2f} {verdict} equal-weight {e:+.2f}")


def bootstrap_nse(feed, trials=25, sample=30, K=8, seed=1):
    data = _load(feed, NSE_UNIVERSE)
    bench = feed.bars("^NSEI")
    bench_close = {b.ts: b.close for b in bench}
    calendar = [b.ts for b in bench]
    start = 126 + 2
    names = list(data)
    rng = random.Random(seed)
    wins = 0
    spreads = []
    for _ in range(trials):
        sub = {s: data[s] for s in rng.sample(names, min(sample, len(names)))}
        lv = build_curves(sub, bench_close, calendar, K=K)
        _, ew = _bench_curves(sub, bench_close, calendar, start)
        ls, es = _m(lv["strat"])["sharpe"], _m(ew)["sharpe"]
        spreads.append(ls - es)
        wins += 1 if ls > es else 0
    spreads.sort()
    print(f"\nUNIVERSE BOOTSTRAP (NSE) — low-vol vs equal-weight over {trials} "
          f"random {sample}-name universes:")
    print(f"  low-vol beat equal-weight on Sharpe: {wins}/{trials} universes "
          f"({wins/trials*100:.0f}%)")
    print(f"  Sharpe-spread (low-vol − EW): median {spreads[len(spreads)//2]:+.2f}, "
          f"min {spreads[0]:+.2f}, max {spreads[-1]:+.2f}")
    print("  (consistently positive spread => edge is intrinsic to low-vol "
          "selection, not the specific names)")


def main():
    print("\n=== A. CROSS-MARKET low-vol test ===")
    run_market("US large-caps (S&P)", YahooFeed(rng="10y", suffix=""),
               US_UNIVERSE, US_BENCH)
    run_market("India (NSE)", YahooFeed(rng="10y"), NSE_UNIVERSE, "^NSEI")

    print("\n=== B. UNIVERSE ROBUSTNESS (survivorship control) ===")
    bootstrap_nse(YahooFeed(rng="10y"))
    print("\n  Cross-market + bootstrap are the honest robustness checks. True "
          "point-in-time\n  constituents need a paid/historical source; the "
          "bootstrap approximates it by\n  showing the edge does not depend on the "
          "exact survivor list.\n")


if __name__ == "__main__":
    main()
