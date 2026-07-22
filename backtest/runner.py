"""Walk-forward backtest. Splits bars into sequential folds; each fold's test
segment is evaluated strictly out-of-sample, bar-by-bar, feeding the strategy
only bars[:i+1] (no lookahead). Reports real win-rate, Sharpe, and max drawdown
computed from the simulated equity curve. No target accuracy, nothing hardcoded.

Fill model: signal on bar i's close -> position entered at bar i's close.
Exits checked each subsequent bar against stop/target using that bar's high/low;
otherwise marked-to-close. Conservative: if both stop and target are touched in
the same bar, the stop is taken."""
from __future__ import annotations
import math
from dataclasses import dataclass, field

from brokers.base import Bar
from strategies.base import Strategy, BUY, SELL
from risk.manager import RiskManager, RiskConfig


@dataclass
class Trade:
    side: str
    entry: float
    exit: float
    qty: int
    bars_held: int

    @property
    def pnl(self) -> float:
        return (self.exit - self.entry) * self.qty * (1 if self.side == BUY else -1)


@dataclass
class FoldResult:
    trades: list[Trade]
    equity_curve: list[float]

    @property
    def n(self) -> int:
        return len(self.trades)


def _simulate(bars: list[Bar], strategy: Strategy, risk: RiskManager,
              start_equity: float) -> FoldResult:
    equity = start_equity
    curve = [equity]
    trades: list[Trade] = []
    warmup = strategy.min_bars()
    i = warmup
    open_pos = None  # (side, entry, qty, stop, target, entry_idx)

    while i < len(bars):
        bar = bars[i]
        if open_pos:
            side, entry, qty, stop, target, eidx = open_pos
            hit_stop = bar.low <= stop if side == BUY else bar.high >= stop
            hit_target = bar.high >= target if side == BUY else bar.low <= target
            exit_price = None
            if hit_stop:
                exit_price = stop
            elif hit_target:
                exit_price = target
            if exit_price is not None:
                t = Trade(side, entry, exit_price, qty, i - eidx)
                trades.append(t)
                equity += t.pnl
                risk.record_pnl(t.pnl)
                open_pos = None
            curve.append(equity + _unrealized(open_pos, bar))
            i += 1
            continue

        sig = strategy.evaluate(bars[: i + 1])
        if sig.action in (BUY, SELL):
            ok, qty, _ = risk.approve_entry(equity, bar.close, sig.action)
            if ok:
                open_pos = (
                    sig.action, bar.close, qty,
                    risk.stop_price(bar.close, sig.action),
                    risk.target_price(bar.close, sig.action),
                    i,
                )
        curve.append(equity + _unrealized(open_pos, bar))
        i += 1

    # close any residual position at last close
    if open_pos:
        side, entry, qty, _, _, eidx = open_pos
        t = Trade(side, entry, bars[-1].close, qty, len(bars) - 1 - eidx)
        trades.append(t)
        equity += t.pnl
        curve[-1] = equity
    return FoldResult(trades, curve)


def _unrealized(open_pos, bar) -> float:
    if not open_pos:
        return 0.0
    side, entry, qty, *_ = open_pos
    return (bar.close - entry) * qty * (1 if side == BUY else -1)


def _sharpe(curve: list[float]) -> float:
    rets = [curve[i] / curve[i - 1] - 1 for i in range(1, len(curve)) if curve[i - 1] > 0]
    if len(rets) < 2:
        return 0.0
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    sd = math.sqrt(var)
    if sd == 0:
        return 0.0
    return (mean / sd) * math.sqrt(252)  # annualized, daily bars


def _max_drawdown(curve: list[float]) -> float:
    peak = curve[0]
    mdd = 0.0
    for v in curve:
        peak = max(peak, v)
        if peak > 0:
            mdd = min(mdd, v / peak - 1)
    return mdd


def _metrics(fold: FoldResult) -> dict:
    wins = [t for t in fold.trades if t.pnl > 0]
    gross_win = sum(t.pnl for t in wins)
    gross_loss = -sum(t.pnl for t in fold.trades if t.pnl < 0)
    return {
        "trades": fold.n,
        "win_rate": (len(wins) / fold.n) if fold.n else 0.0,
        "sharpe": _sharpe(fold.equity_curve),
        "max_drawdown": _max_drawdown(fold.equity_curve),
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else float("inf"),
        "net_pnl": fold.equity_curve[-1] - fold.equity_curve[0],
        "return_pct": (fold.equity_curve[-1] / fold.equity_curve[0] - 1) if fold.equity_curve[0] else 0.0,
    }


def walk_forward(bars: list[Bar], strategy: Strategy, cfg: RiskConfig | None = None,
                 folds: int = 4, start_equity: float = 100_000.0) -> dict:
    """Split into `folds` sequential test segments (each evaluated out-of-sample)
    and aggregate. Warmup bars from before each segment are included so the
    strategy has history, but signals/trades are only taken within the segment."""
    cfg = cfg or RiskConfig()
    warmup = strategy.min_bars()
    usable = len(bars) - warmup
    if usable < folds * 20:
        folds = max(1, usable // 40)
    seg = usable // folds
    fold_metrics = []
    all_trades: list[Trade] = []

    for f in range(folds):
        seg_start = warmup + f * seg
        seg_end = len(bars) if f == folds - 1 else warmup + (f + 1) * seg
        # include prior `warmup` bars for indicator history, no future bars
        window = bars[seg_start - warmup: seg_end]
        risk = RiskManager(cfg)
        risk.start_day(start_equity)
        result = _simulate(window, strategy, risk, start_equity)
        fold_metrics.append(_metrics(result))
        all_trades.extend(result.trades)

    agg = _aggregate(all_trades, start_equity, fold_metrics)
    return {"folds": fold_metrics, "aggregate": agg}


def _aggregate(trades: list[Trade], start_equity: float, fold_metrics: list[dict]) -> dict:
    # rebuild a continuous equity curve from all out-of-sample trades in order
    equity = start_equity
    curve = [equity]
    for t in trades:
        equity += t.pnl
        curve.append(equity)
    wins = [t for t in trades if t.pnl > 0]
    gross_win = sum(t.pnl for t in wins)
    gross_loss = -sum(t.pnl for t in trades if t.pnl < 0)
    avg_sharpe = sum(m["sharpe"] for m in fold_metrics) / len(fold_metrics) if fold_metrics else 0.0
    return {
        "total_trades": len(trades),
        "win_rate": (len(wins) / len(trades)) if trades else 0.0,
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else float("inf"),
        "net_pnl": equity - start_equity,
        "return_pct": (equity / start_equity - 1),
        "max_drawdown": _max_drawdown(curve),
        "avg_fold_sharpe": avg_sharpe,
    }


def _fmt(m: dict) -> str:
    return (f"trades={m['trades']:>3}  win_rate={m['win_rate']*100:5.1f}%  "
            f"sharpe={m['sharpe']:+6.2f}  maxDD={m['max_drawdown']*100:6.1f}%  "
            f"PF={m['profit_factor']:.2f}  ret={m['return_pct']*100:+6.1f}%")


def main() -> None:
    from strategies.weighted_indicator import WeightedIndicatorStrategy
    from backtest.synthetic import generate

    bars = generate(n=1400, seed=7)
    strat = WeightedIndicatorStrategy(threshold=0.20)
    report = walk_forward(bars, strat, folds=4)

    print(f"\nWalk-forward backtest — {strat.name} on {len(bars)} synthetic bars\n")
    for i, m in enumerate(report["folds"]):
        print(f"  fold {i+1}: {_fmt(m)}")
    a = report["aggregate"]
    print("\n  AGGREGATE (all out-of-sample trades):")
    print(f"    total_trades   : {a['total_trades']}")
    print(f"    win_rate       : {a['win_rate']*100:.1f}%")
    print(f"    profit_factor  : {a['profit_factor']:.2f}")
    print(f"    net_pnl        : {a['net_pnl']:+,.0f}")
    print(f"    return         : {a['return_pct']*100:+.1f}%")
    print(f"    max_drawdown   : {a['max_drawdown']*100:.1f}%")
    print(f"    avg_fold_sharpe: {a['avg_fold_sharpe']:+.2f}\n")


if __name__ == "__main__":
    main()
