# Trading System — Architecture

Phase 1: **single-user, single-broker (Zerodha), paper-trading only, CLI**. Pure
Python stdlib (no numpy/pandas/network deps). SQLite for persistence. Later
phases add more brokers, live orders, multi-tenancy, and an admin UI — the
layering below is designed so those extend, not rewrite, this code.

## Layout

```
brokers/     broker adapters behind one interface (base.Broker) — 4 vendors
strategies/  signal generators behind one interface (base.Strategy)
             + indicators.py (pure-stdlib TA)
risk/        risk.manager — sizing, stops, daily-loss guard
execution/   engine (per-customer loop) + supervisor (multi-tenant orchestration)
backtest/    runner (walk-forward) + synthetic data generator
data/        store.py — SQLite schema & helpers (orders, positions, bars)
tenancy/     models, vault (Fernet), store (scoped tables), service (Phase 3)
api/         FastAPI admin dashboard + customer/compliance endpoints (Phase 3)
config.py    config.yaml/.env loader + build_broker factory
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

## Broker adapters (Phase 2)

Four adapters, all subclassing `PaperBroker` (in `brokers/base.py`), which holds
the shared paper-mode SQLite order/position book. Each vendor file only supplies
credential loading + `connect`/`get_quote`/`get_historical`; `place_order` /
`cancel_order` / `get_positions` are inherited and identical across brokers. All
still **paper-only** — no real orders. Interface gained `cancel_order(order_id)
-> bool` (paper: immediate fills, so it's an idempotent True no-op).

Selection is by `config.yaml` (`broker: zerodha|upstox|angelone|alpaca`);
`config.build_broker(name, quote_source, historical_source)` is the factory. The
engine/strategy/risk code is unchanged — it only sees the `Broker` interface.
Credentials load from `.env` via `brokers/credentials.py` (see `.env.example`);
paper mode tolerates missing creds.

| Adapter | SDK | Auth flow | Token / refresh | Rate limits (live phase) |
|---|---|---|---|---|
| `zerodha.py` | `kiteconnect` | api_key+secret → request_token → **daily** access_token | Access token expires ~6am IST daily; re-login each session | ~3 req/s; historical has per-candle caps |
| `upstox.py` | `upstox-python-sdk` | OAuth2 redirect (api_key/secret/redirect_uri) → access_token | Access token expires **daily**; re-run OAuth redirect | ~25–50 req/s tiered per endpoint |
| `angelone.py` | `SmartApi-python` | `generateSession(client_code, mpin, totp)` | TOTP from 2FA secret each login; JWT + refresh token, short-lived | Publisher limits vary by endpoint; historical is throttled |
| `alpaca.py` | `alpaca-py` | Static api_key_id + secret_key | Long-lived keys, no daily refresh; `paper=True` routes to paper endpoint | ~200 req/min (data plan dependent); US market hours only |

Quirks to remember for the live phase: three of four (Zerodha, Upstox, Angel One)
need a **daily** re-auth; Alpaca does not. Angel One requires a live **TOTP** at
login (store the base32 2FA secret, generate the code at runtime). Zerodha/Upstox/
Angel One are NSE/BSE (INR, IST hours); Alpaca is US equities (USD, US hours) —
symbol formats and trading calendars differ, so watchlists are broker-specific.

## Multi-tenancy (Phase 3)

Phase 3 turns the single-user system into a multi-tenant paper platform **without
rewriting** the Broker/Strategy/risk/backtest cores — those still only see their
existing interfaces. New modules (`tenancy/`, `api/`, `execution/supervisor.py`)
layer on top. Requires `cryptography`, `fastapi`, `uvicorn` (see
`requirements.txt`); run in a venv. Still **paper-only** — no live orders execute.

### Customer model (`tenancy/models.py`)

`Customer(id, name, email, broker, mode, status, equity, watchlist, risk,
error_count, tos_version, tos_accepted_at, backtest_viewed_at)`.
`mode ∈ {paper, live}`; `status ∈ {active, suspended, breaker_tripped, killed}`.
`RiskLimits` mirrors Phase 1 `RiskConfig` fields and adds `is_tighter_or_equal()`
so admin edits that would loosen a customer's limits can be rejected.

### Tenant scoping (`tenancy/store.py`, `data/store.py`)

All customer data lives in the same SQLite file, **every query scoped by
`customer_id`** — there is no unscoped read. `data/store.py` `orders`/`positions`
gained a `customer_id` column (default `"default"` preserves Phase 1/2 behavior);
`PaperBroker`, the four adapters, and `config.build_broker` all take a
`customer_id` that scopes the paper book. New tables: `customers`, `credentials`
(ciphertext only), `consent`, `backtest_views`, `audit_log`, `control`.

### Credential vault (`tenancy/vault.py`, `tenancy/service.py`)

Broker API key/secret/token are encrypted at rest with **Fernet**; the key comes
from the `VAULT_KEY` env var (generate: `python -c "from tenancy.vault import
generate_key; print(generate_key())"`). Plaintext exists only transiently in
memory when a customer's own adapter needs it — never persisted, never logged.
`service.set_broker_credentials` encrypts; `service.load_broker_creds` decrypts
scoped to one `customer_id` and returns a `brokers.credentials.Creds` injected
into that customer's adapter via `build_broker(..., creds=...)`.

### Compliance gate (`tenancy/service.py`)

`can_go_live(customer_id)` returns True only when **both** hold: (a) current
`TOS_VERSION` consent is recorded (timestamp + version in `consent`), and (b) the
customer has viewed their real backtested metrics (`backtest_views`, from Phase
1's `walk_forward`). `set_mode(..., "live")` calls the gate and **rejects** if it
fails — enforced in code, the API returns 403, not just hidden in UI. Admins
cannot force live (`actor="admin"` + live → rejected) nor loosen risk limits.
No guaranteed-return or accuracy-target strings appear in code, config, or
templates; backtest figures are labeled historical and non-indicative.

### Per-customer execution (`execution/supervisor.py`)

`Supervisor.run_cycle()` runs one `Engine` per active customer, each with its own
adapter instance, decrypted creds, watchlist and `RiskConfig`. Isolation:
- each customer's cycle is wrapped in its own try/except — one broker's error
  never halts another's loop;
- **circuit breaker**: `service.record_error` counts consecutive errors; at
  `BREAKER_THRESHOLD` (3) the customer's status → `breaker_tripped`, an alert is
  audited, and the loop is skipped until an admin resumes;
- **kill switch / suspend** flags are re-read from `control` at the **top** of
  each customer's cycle, so they take effect before the next poll, not on it.
`Engine` gained an optional `on_event(kind, symbol, detail)` hook (default None =
Phase 1/2 behavior) that writes every signal and order — with indicator values
and timestamps — to that customer's `audit_log` for dispute resolution, plus a
`flatten()` for the kill switch.

### API + admin dashboard (`api/app.py`)

FastAPI (`uvicorn api.app:app`). Customer/compliance: `POST /customers`,
`/customers/{id}/consent`, `/customers/{id}/backtest-view`, `/customers/{id}/mode`
(gate-enforced), `/customers/{id}/kill-switch` (`{flatten}`). Admin (server-
rendered HTML): `GET /admin` (customers with mode/status/error-count), `GET
/admin/customers/{id}` (per-customer audit log), `POST .../suspend|resume`, `POST
.../risk` (tighten-only). Each request opens a scoped SQLite connection.

## Mock dashboard (Phase 4)

A Streamlit dashboard (`dashboard.py`) visualizes the full pipeline for one demo
tenant on **mock data — no credentials, no network**. Adds `streamlit`, `plotly`
(see `requirements.txt`); run `.venv/bin/streamlit run dashboard.py`.

- **Mock broker** (`brokers/mock.py`): a `PaperBroker` (`name="mock"`, registered
  in `config.build_broker`) whose market data comes entirely from an injected
  synthetic feed. No creds — `credentials.Creds("mock", {})`.
- **Mock feed** (`data/mock_feed.py`): reuses Phase 1's `backtest.synthetic.generate`
  to pre-roll a long deterministic series per symbol, then reveals bars one at a
  time (`advance()`) so a running dashboard sees an evolving tape. Exposes
  `quote_source` / `historical_source` callables shaped exactly like the Broker
  interface's `get_quote` / `get_historical`.
- **Demo tenant**: customer `demo` (`broker="mock"`, `mode="paper"`) created on
  first load via the Phase 3 tenancy service; all reads/writes scoped to its
  `customer_id`. The dashboard visualizes only this one tenant.
- **Display**: plotly candlestick + EMA20/50/200 + Bollinger overlay; current
  signal (BUY/SELL/HOLD) with confidence % and per-indicator vote table (parsed
  from `Signal.reason`); open positions + realized/unrealized P&L; recent trades
  from the DB (`data/store.get_orders`); Start/Stop/Reset; a persistent
  "MOCK DATA — NOT LIVE" banner. The run loop advances the feed one bar and calls
  `engine.step()` per refresh.

**Swap contract (by design, verified):** `dashboard.py`, the strategy, and the
risk manager read market data **only** through the Broker interface and trades
**only** from the DB; the mock feed is injected once at `build_broker(...)` time.
There is **no** mock-vs-real branching in the dashboard. Switching the demo
customer's `broker` from `mock` to `zerodha` (with real `KITE_*` creds in the
vault/.env) is a config/credential change only — the dashboard, strategy, and
risk code are untouched; only the data source behind the interface changes (the
real feed arrives with the already-planned Zerodha live phase).

## Real-data validation (edge gate)

Before any live capital, the strategy was validated on **real** free NSE data
(`data/yahoo_feed.py`, a drop-in historical source behind the Broker interface):
- `backtest/real_validate.py` — existing walk-forward on real prices + buy&hold.
- `backtest/momentum.py` — cross-sectional momentum, survivorship-corrected.

- `backtest/lowvol.py` — low-volatility anomaly (rank by trailing vol, hold
  lowest, monthly rebalance).

**Findings:** (1) weighted-indicator strategy returns ~0 vs buy&hold — no edge;
(2) momentum loses to Nifty and equal-weight once laggards are added — no edge;
(3) **low-volatility DOES show a risk-adjusted edge** — Sharpe 1.08 vs equal-weight
0.94 vs Nifty 0.76, drawdown −29% vs −39%, beats Nifty on Sharpe in every
sub-period, low turnover (cost/tax friendly). Documented anomaly, not curve-fit.
Caveat: modest vs equal-weight, lags in strong bull rips, ~10y single sample.

Robustness (`backtest/lowvol_robust.py`): (a) NSE universe **bootstrap** — low-vol
beat equal-weight on Sharpe in 21/25 random sub-universes (84%), so the India edge
is intrinsic to low-vol selection, not the specific survivor list; (b) **cross-
market** — low-vol did NOT replicate in the US (Sharpe 0.45 vs 0.74 equal-weight)
over 2015-25, a decade dominated by high-vol mega-cap tech. Net: the edge is real
and robust **within Indian large-caps** but **regime/market-dependent, not a
universal law**. Treat as an India-specific candidate held with humility; not
confirmed enough for real capital without more history/periods.

## Live trading (Zerodha) — behind the compliance gate

Live order execution exists for Zerodha only, opt-in via `mode="live"`; every
other broker and the default remain paper. Live is only reachable after the
Phase 3 gate passes (`service.set_mode(..,"live")` requires ToS consent + a viewed
backtest) and real credentials are in the vault. Needs `kiteconnect`.

- `brokers/zerodha.py` — `mode="paper"` (default) is the unchanged PaperBroker
  SQLite path; `mode="live"` routes `get_quote`/`get_historical`/`place_order`/
  `cancel_order`/`get_positions` to Kite Connect. The Kite client is **injectable**
  (`kite=`) so the live path is unit-tested with a fake (no real orders). Live
  orders are logged with `mode='live'` and do **not** touch the paper book —
  `kite.positions()` is the source of truth. Order rejections raise
  `LiveOrderError` (fail-safe; never blind-retry).
- `brokers/kite_client.py` — `build_kite(creds)` (lazy import), `InstrumentMap`
  (tradingsymbol→instrument_token for historical), interval/date-window helpers.
- `execution/live_guard.py` — `LiveGuard`: pre-trade checks (market hours, kill
  switch, daily-loss vs the **real** account balance from `kite.margins()`,
  max-position notional) and `reconcile(local, broker)` to detect position drift
  (halt-and-investigate, never auto-correct).
- `config.build_broker(.., mode=, kite=)` and the supervisor pass `customer.mode`.
- `backtest/live_smoke.py` — fake-Kite smoke test of the whole live path + guard
  + paper regression. **No real credentials or orders are exercised anywhere.**

Operational reality (must be done by the account owner, not automated blindly):
Kite API subscription (₹500/mo); **daily** access-token regeneration (~6am IST);
SEBI retail-algo rules (verify with the broker); start at 1 share to prove
plumbing; taxes/slippage worsen the already-absent edge. Real-account validation
requires the owner's own credentials — the code path is tested, live fills are not.

## Phase boundaries (do not build ahead)

Phases 1-4 are **paper-only** (Phase 4 is mock data on top of paper). Live
Zerodha execution is built and unit-tested with a fake client behind the
`place_order` `mode` flag + compliance gate, but has **NOT** been run against a
real account (needs the owner's credentials + funded account) and the strategy
has **no proven edge** (see the edge gate). Do not enable live mode with real
funds on these signals, and never add accuracy/return promises anywhere.
