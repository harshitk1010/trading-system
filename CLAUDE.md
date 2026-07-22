# Trading System — Architecture

Phase 1: **single-user, single-broker (Zerodha), paper-trading only, CLI**. Pure
Python stdlib (no numpy/pandas/network deps). SQLite for persistence. Later
phases add more brokers, live orders, multi-tenancy, and an admin UI — the
layering below is designed so those extend, not rewrite, this code.

## Layout

```
brokers/     broker adapters behind one interface (base.Broker) — zerodha.py
strategies/  signal generators behind one interface (base.Strategy)
             + indicators.py (pure-stdlib TA)
risk/        risk.manager — sizing, stops, daily-loss guard
execution/   engine — polls watchlist, applies risk, places paper orders
backtest/    runner (walk-forward) + synthetic data generator
data/        store.py — SQLite schema & helpers (orders, positions, bars)
main.py      CLI: backtest | paper | positions
```

Dependency direction: `execution` → `strategies` + `risk` + `brokers`;
`brokers` → `data`; nothing depends on `execution`. Strategies are pure (no I/O).

## Broker interface (`brokers/base.py`)

All broker adapters subclass `Broker`. Code above this layer never imports a
concrete broker — inject the instance.

| Method | Contract |
|---|---|
| `connect()` | Establish/validate session. Paper mode may no-op. |
| `get_quote(symbol) -> Quote \| None` | Latest price; `None` if unavailable (never raise). |
| `get_historical(symbol, interval, limit) -> list[Bar]` | Last `limit` bars, oldest-first, **no future bars**. |
| `place_order(order: Order) -> str` | Submit; returns broker order id. **Paper mode logs a simulated fill to SQLite and returns `PAPER-<id>` — no network call.** |
| `get_positions() -> list[Position]` | Current open positions. |

DTOs: `Bar(ts,open,high,low,close,volume)`, `Quote`, `Order(symbol,side,quantity,price,ts)`, `Position`.

**Adding a broker (later phase):** implement `Broker`, wrap the vendor SDK
(quote/historical) in `get_quote`/`get_historical`, and gate `place_order` on a
`mode` flag — `paper` keeps the SQLite path, `live` calls the real order API.
`ZerodhaBroker` takes `quote_source`/`historical_source` callables so the same
adapter serves synthetic (backtest), CSV, and (later) KiteConnect feeds.

## Strategy interface (`strategies/base.py`)

Subclass `Strategy`:

- `evaluate(bars: list[Bar]) -> Signal` — return a signal for `bars[-1]` using
  **only** `bars[0..-1]`. No I/O, no lookahead.
- `min_bars() -> int` — warmup bars required before signals are valid.

`Signal(action, strength, reason)` — action ∈ {`BUY`,`SELL`,`HOLD`}, strength ∈
[0,1], reason is a human-readable per-indicator breakdown for audit/logs.

**`WeightedIndicatorStrategy`** — each indicator casts a vote in [-1,+1]; votes
are weighted (`DEFAULT_WEIGHTS`) and renormalized over indicators that fired into
a net score. `score ≥ +threshold → BUY`, `≤ -threshold → SELL`, else `HOLD`.
Indicators: EMA stack (20/50/60/200), MACD histogram, RSI, Bollinger position,
Stochastic %K/%D, VWAP. Weights and threshold are constructor args (tune per
instrument; per-symbol weight sets are a natural later extension).

## Risk rules (`risk/manager.py`)

`RiskConfig` (fractions, `0.02 == 2%`):

| Field | Default | Meaning |
|---|---|---|
| `risk_per_trade` | 0.02 | equity fraction risked per trade (drives sizing) |
| `stop_loss_pct` | 0.02 | per-position stop distance |
| `take_profit_pct` | 0.04 | per-position target distance |
| `max_daily_loss_pct` | 0.05 | halt new entries once daily realized loss hits this |
| `max_position_pct` | 0.25 | cap notional per position |

- **Sizing:** shares so a stop-out ≈ `risk_per_trade × equity`, then capped by
  `max_position_pct` notional (`position_size`). Integer shares.
- **Stops/targets:** `stop_price` / `target_price` off entry & side.
- **Daily-loss guard:** `start_day(equity)` at session open; `record_pnl` on each
  close; `halted()` true once cumulative daily loss ≤ `-max_daily_loss_pct`.
  `approve_entry` returns `(ok, qty, reason)` and blocks entries when halted.

## Execution engine (`execution/engine.py`)

`step()` = one poll cycle: for each watchlist symbol pull history → if a position
is open, check stop/target exit → else evaluate strategy, `approve_entry`, place
paper order. Tracks `OpenTrade` (entry/stop/target) and updates `equity` on exit.
Phase 1 runs one cycle per CLI invocation; a live phase wraps `step()` in a
scheduler/loop.

## Backtest (`backtest/runner.py`)

`walk_forward(bars, strategy, cfg, folds)` splits bars into sequential
out-of-sample test segments. Each bar `i` is evaluated on `bars[:i+1]` only — **no
lookahead**. Fill model: enter at signal bar's close; exits checked on later bars
against stop/target using that bar's high/low (stop wins ties); residual closed at
last bar. Reports **real** win-rate, Sharpe (annualized ×√252), max drawdown,
profit factor, net PnL — computed from the simulated equity curve. No target
accuracy, no hardcoded numbers. Synthetic feed: `backtest/synthetic.py`
(seeded GBM with trend/chop regimes).

## CLI

```
python main.py backtest    # walk-forward on synthetic data (default)
python main.py paper       # one paper engine cycle on synthetic feed
python main.py positions   # dump paper position book from SQLite
```

## Phase boundaries (do not build ahead)

Phase 1 is paper-only, single broker, single user, CLI. Multi-broker,
live orders, multi-tenancy, and admin UI are later phases — extend via the
`Broker`/`Strategy` interfaces and a `mode` flag on `place_order`; do not thread
tenant/user concerns into these modules yet.
