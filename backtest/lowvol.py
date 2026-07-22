"""Edge test #3: LOW-VOLATILITY anomaly on real NSE data. Pre-committed,
economically-motivated hypothesis (leverage-constrained investors overpay for
high-beta 'lottery' stocks, so low-vol is underpriced). Judged on RISK-ADJUSTED
return (Sharpe) and drawdown — the dimensions momentum/indicator strategies lost
on — against the honest bar: equal-weight buy&hold.

Reuses the survivorship-corrected universe + helpers from backtest.momentum. No
lookahead (vol at rebalance i uses only prices < i). Costs on turnover.
Run: python -m backtest.lowvol
"""
from __future__ import annotations
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.yahoo_feed import YahooFeed
from backtest.momentum import (UNIVERSE, BENCH, _load, _metrics, _slice_metrics,
                               _sma_trend, _row)


def _vol(cl, calendar, i, L):
    """Trailing stdev of daily returns over [i-L, i-1] using only past bars."""
    px = [cl[calendar[j]] for j in range(i - L, i) if calendar[j] in cl]
    if len(px) < L * 0.6:
        return None
    rets = [px[k] / px[k - 1] - 1 for k in range(1, len(px)) if px[k - 1]]
    if len(rets) < 2:
        return None
    m = sum(rets) / len(rets)
    return (sum((r - m) ** 2 for r in rets) / (len(rets) - 1)) ** 0.5


def build_curves(data, bench_close, calendar, L=126, K=10, reb=21, cost_bps=20,
                 trend=None, mode="lowk"):
    """mode: 'lowk' = equal-weight the K lowest-vol names; 'invvol' = hold all,
    weight inversely to volatility (risk-weighted)."""
    start = L + 2
    weights: dict[str, float] = {}
    eq = 1.0
    strat, dates = [1.0], [calendar[start - 1]]
    for i in range(start, len(calendar)):
        d, dp = calendar[i], calendar[i - 1]
        if (i - start) % reb == 0:
            risk_on = True if trend is None else trend.get(d, True)
            vols = {s: v for s, cl in data.items()
                    if (v := _vol(cl, calendar, i, L)) and v > 0}
            new: dict[str, float] = {}
            if risk_on and vols:
                if mode == "lowk":
                    for s in sorted(vols, key=lambda z: vols[z])[:K]:
                        new[s] = 1.0 / min(K, len(vols))
                else:  # invvol
                    inv = {s: 1.0 / v for s, v in vols.items()}
                    tot = sum(inv.values())
                    new = {s: w / tot for s, w in inv.items()}
            turnover = sum(abs(new.get(s, 0) - weights.get(s, 0))
                           for s in set(new) | set(weights)) / 2
            eq *= (1 - cost_bps / 1e4 * turnover)
            weights = new
        r = 0.0
        for s, w in weights.items():
            cl = data[s]
            if d in cl and dp in cl and cl[dp] > 0:
                r += w * (cl[d] / cl[dp] - 1)
        eq *= (1 + r); strat.append(eq); dates.append(d)
    return {"strat": strat, "dates": dates}


def _bench_curves(data, bench_close, calendar, start):
    nifty, ew = [1.0], [1.0]
    for i in range(start, len(calendar)):
        d, dp = calendar[i], calendar[i - 1]
        nifty.append(nifty[-1] * (bench_close[d] / bench_close[dp])
                     if d in bench_close and dp in bench_close and bench_close[dp] else nifty[-1])
        rr, cc = 0.0, 0
        for cl in data.values():
            if d in cl and dp in cl and cl[dp] > 0:
                rr += cl[d] / cl[dp] - 1; cc += 1
        ew.append(ew[-1] * (1 + (rr / cc if cc else 0.0)))
    return nifty, ew


def _m(curve):
    rets = [curve[k] / curve[k - 1] - 1 for k in range(1, len(curve)) if curve[k - 1]]
    return _metrics(curve, rets)


def main():
    feed = YahooFeed(interval="day", rng="10y")
    print(f"\nLoading real NSE data for {len(UNIVERSE)} names + Nifty… (free Yahoo)\n")
    data = _load(feed, UNIVERSE)
    bench = feed.bars(BENCH)
    bench_close = {b.ts: b.close for b in bench}
    calendar = [b.ts for b in bench]
    trend = _sma_trend(bench_close, calendar, 200)
    start = 126 + 2
    print(f"Universe: {len(data)}/{len(UNIVERSE)} names · {len(calendar)} days "
          f"(~{len(calendar)/252:.1f}y)\n")

    primary = build_curves(data, bench_close, calendar)  # plain low-vol bottom-10 (headline)
    configs = {
        "Low-vol bottom-10 (equal wt)": primary,
        "Low-vol bottom-10 + 200d trend": build_curves(data, bench_close, calendar, trend=trend),
        "Inverse-vol (risk-weighted, all)": build_curves(data, bench_close, calendar, mode="invvol"),
    }
    nifty, ew = _bench_curves(data, bench_close, calendar, start)

    print("STRATEGY (low-volatility, monthly rebalance, 20bps cost, out-of-sample):")
    for name, cv in configs.items():
        print(_row(name, _m(cv["strat"])))
    print("\nBENCHMARKS (same window):")
    print(_row("Nifty 50 (buy & hold)", _m(nifty)))
    print(_row("Universe equal-weight (buy & hold)", _m(ew)))

    n = len(primary["strat"])
    thirds = [(0, n // 3), (n // 3, 2 * n // 3), (2 * n // 3, n - 1)]
    nifty_al = [1.0] + nifty[1:n]  # align lengths
    print("\nSUB-PERIOD — primary = Low-vol bottom-10 (headline, each ~3.3y):")
    print(f"  {'period':<20}{'strat Shrp':>11}{'strat CAGR':>12}{'strat DD':>10}"
          f"{'Nifty Shrp':>12}{'EW Shrp':>9}")
    for a, b in thirds:
        d0, d1 = primary["dates"][a][:7], primary["dates"][b][:7]
        sm = _slice_metrics(primary["strat"], a, b)
        nm = _slice_metrics(nifty, a, b)
        em = _slice_metrics(ew, a, b)
        print(f"  {d0}→{d1:<12}{sm['sharpe']:>+11.2f}{sm['cagr']*100:>+11.1f}%"
              f"{sm['maxdd']*100:>+9.1f}%{nm['sharpe']:>+12.2f}{em['sharpe']:>+9.2f}")

    print("\n  Verdict test: low-vol earns its keep ONLY if it beats equal-weight "
          "on SHARPE (risk-adjusted),\n  consistently across sub-periods — not just on "
          "raw return. Costs approx; residual survivorship.\n")


if __name__ == "__main__":
    main()
