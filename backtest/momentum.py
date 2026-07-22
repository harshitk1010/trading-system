"""Edge test #2: cross-sectional MOMENTUM on a real NSE universe (free Yahoo
data). Different hypothesis from the indicator-vote timing strategy — rank stocks
by trailing return, hold the top K, rebalance monthly. Momentum is the most
robust documented equity anomaly (Jegadeesh-Titman); this checks whether it
survives out-of-sample AND net of costs on Indian equities.

Survivorship control: the universe deliberately includes big LAGGARDS of the last
decade (Yes Bank, Vodafone Idea, PNB, PSUs, metals) alongside winners, so results
are not flattered by only holding names that thrived. Still not a true
point-in-time index, but far less biased than a winners-only list.

No lookahead: momentum at rebalance day i uses only prices up to day i-1.
Benchmarks: Nifty 50, and equal-weight buy&hold of the whole universe.
Costs modeled via per-rebalance turnover. Run: python -m backtest.momentum
"""
from __future__ import annotations
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.yahoo_feed import YahooFeed

# winners + laggards + cyclicals — broad, includes names that fell 80-95%
UNIVERSE = [
    "RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK", "SBIN", "ITC", "LT",
    "HINDUNILVR", "KOTAKBANK", "AXISBANK", "BHARTIARTL", "BAJFINANCE", "ASIANPAINT",
    "MARUTI", "SUNPHARMA", "TITAN", "ULTRACEMCO", "WIPRO", "HCLTECH", "NESTLEIND",
    "TATAMOTORS", "TATASTEEL", "POWERGRID", "NTPC", "M&M", "ADANIPORTS", "GRASIM",
    "TECHM", "JSWSTEEL", "INDUSINDBK", "BAJAJFINSV", "DRREDDY", "CIPLA", "EICHERMOT",
    "HEROMOTOCO", "BAJAJ-AUTO", "DABUR", "GODREJCP", "PIDILITIND", "BRITANNIA",
    "DIVISLAB", "APOLLOHOSP", "UPL", "COALINDIA", "ONGC", "IOC", "BPCL", "GAIL",
    "VEDL", "HINDALCO", "DLF", "ADANIENT",
    # big laggards / value-destroyers of the decade (anti-survivorship)
    "YESBANK", "IDEA", "PNB", "BANKBARODA", "CANBK", "ZEEL", "GMRINFRA", "IBULHSGFIN",
]
BENCH = "^NSEI"


def _metrics(curve, rets):
    total = curve[-1] - 1
    years = max(len(curve) / 252, 1e-9)
    cagr = curve[-1] ** (1 / years) - 1 if curve[-1] > 0 else -1
    if len(rets) > 1:
        mean = sum(rets) / len(rets)
        sd = (sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)) ** 0.5
        sharpe = (mean / sd * (252 ** 0.5)) if sd else 0.0
    else:
        sharpe = 0.0
    peak = curve[0]; mdd = 0.0
    for v in curve:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    return {"total": total, "cagr": cagr, "sharpe": sharpe, "maxdd": mdd}


def _slice_metrics(curve, a, b):
    sub = curve[a:b + 1]
    base = sub[0] or 1e-9
    norm = [v / base for v in sub]
    rets = [norm[k] / norm[k - 1] - 1 for k in range(1, len(norm)) if norm[k - 1]]
    return _metrics(norm, rets)


def _load(feed, syms):
    data = {}
    for s in syms:
        try:
            bars = feed.bars(s)
            if len(bars) > 300:
                data[s] = {b.ts: b.close for b in bars}
        except Exception:
            pass
    return data


def _sma_trend(bench_close, calendar, window=200):
    dates = [d for d in calendar if d in bench_close]
    closes = [bench_close[d] for d in dates]
    flag = {}
    for i, d in enumerate(dates):
        flag[d] = closes[i] > sum(closes[i - window:i]) / window if i >= window else True
    return flag


def build_curves(data, bench_close, calendar, L=252, skip=21, K=5, reb=21,
                 cost_bps=20, trend=None):
    """One aligned daily pass — returns strat/nifty/ew equity curves + dates so
    sub-period metrics come from identical date slices."""
    start = L + skip + 1
    held: set[str] = set()
    eq = 1.0
    strat, nifty, ew, dates = [1.0], [1.0], [1.0], [calendar[start - 1]]
    for i in range(start, len(calendar)):
        d, dp = calendar[i], calendar[i - 1]
        if (i - start) % reb == 0:
            risk_on = True if trend is None else trend.get(d, True)
            if not risk_on:
                new = set()
            else:
                a, b = calendar[i - skip], calendar[i - skip - L]
                scores = {s: cl[a] / cl[b] - 1 for s, cl in data.items()
                          if a in cl and b in cl and cl[b] > 0}
                new = set([s for s in sorted(scores, key=lambda z: -scores[z])
                           if scores[s] > 0][:K])
            eq *= (1 - cost_bps / 1e4 * (len(new ^ held) / max(1, K)))
            held = new
        r, cnt = 0.0, 0
        for s in held:
            cl = data[s]
            if d in cl and dp in cl and cl[dp] > 0:
                r += cl[d] / cl[dp] - 1; cnt += 1
        eq *= (1 + (r / cnt if cnt else 0.0)); strat.append(eq)
        nifty.append(nifty[-1] * (bench_close[d] / bench_close[dp])
                     if d in bench_close and dp in bench_close and bench_close[dp]
                     else nifty[-1])
        rr, cc = 0.0, 0
        for cl in data.values():
            if d in cl and dp in cl and cl[dp] > 0:
                rr += cl[d] / cl[dp] - 1; cc += 1
        ew.append(ew[-1] * (1 + (rr / cc if cc else 0.0))); dates.append(d)
    return {"strat": strat, "nifty": nifty, "ew": ew, "dates": dates}


def _metrics_only(curves, key):
    c = curves[key]
    rets = [c[k] / c[k - 1] - 1 for k in range(1, len(c)) if c[k - 1]]
    return _metrics(c, rets)


def _row(name, m):
    return (f"  {name:<36} CAGR={m['cagr']*100:+6.1f}%  Sharpe={m['sharpe']:+5.2f}  "
            f"maxDD={m['maxdd']*100:6.1f}%  total={m['total']*100:+8.1f}%")


def main():
    feed = YahooFeed(interval="day", rng="10y")
    print(f"\nLoading real NSE data for {len(UNIVERSE)} names + Nifty… (free Yahoo)\n")
    data = _load(feed, UNIVERSE)
    bench = feed.bars(BENCH)
    bench_close = {b.ts: b.close for b in bench}
    calendar = [b.ts for b in bench]
    trend = _sma_trend(bench_close, calendar, 200)
    print(f"Universe loaded: {len(data)}/{len(UNIVERSE)} names · calendar "
          f"{len(calendar)} days (~{len(calendar)/252:.1f}y)\n")

    # --- config comparison (aggregate) ---
    cfgs = {
        "Momentum 12-1, top5": dict(),
        "Momentum 12-1, top5 + 200d trend": dict(trend=trend),
        "Momentum 6m, top5": dict(L=126, skip=0),
        "Momentum 12-1, top8 (diversified)": dict(K=8),
    }
    primary = None
    print("STRATEGY configs (monthly rebalance, 20bps cost/turnover, out-of-sample):")
    for name, cfg in cfgs.items():
        cv = build_curves(data, bench_close, calendar, **cfg)
        print(_row(name, _metrics_only(cv, "strat")))
        if "200d trend" in name:
            primary = cv
    nifty_m = _metrics_only(primary, "nifty")
    ew_m = _metrics_only(primary, "ew")
    print("\nBENCHMARKS (same window, no timing):")
    print(_row("Nifty 50 (buy & hold)", nifty_m))
    print(_row("Universe equal-weight (buy & hold)", ew_m))

    # --- sub-period robustness for the primary (trend-filtered) strategy ---
    n = len(primary["strat"])
    thirds = [(0, n // 3), (n // 3, 2 * n // 3), (2 * n // 3, n - 1)]
    print("\nSUB-PERIOD robustness — primary = Momentum 12-1 + 200d trend "
          "(each ~3.3y, out-of-sample):")
    print(f"  {'period':<20}{'strat CAGR':>12}{'Nifty CAGR':>12}{'EW CAGR':>10}"
          f"{'strat Shrp':>12}{'strat DD':>10}")
    for (a, b) in thirds:
        d0, d1 = primary["dates"][a][:7], primary["dates"][b][:7]
        sm = _slice_metrics(primary["strat"], a, b)
        nm = _slice_metrics(primary["nifty"], a, b)
        em = _slice_metrics(primary["ew"], a, b)
        print(f"  {d0}→{d1:<12}{sm['cagr']*100:>+11.1f}%{nm['cagr']*100:>+11.1f}%"
              f"{em['cagr']*100:>+9.1f}%{sm['sharpe']:>+12.2f}{sm['maxdd']*100:>+9.1f}%")

    print("\n  Honest read: compare the strategy to BOTH benchmarks, per sub-period. "
          "Beating Nifty consistently\n  = real; failing to beat equal-weight = the "
          "'edge' is mostly the universe, not the timing. Costs approx; ~10y is one\n"
          "  broad regime; universe still has residual survivorship.\n")


if __name__ == "__main__":
    main()
