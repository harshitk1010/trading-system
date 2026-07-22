"""Tenancy service layer — the only module that mediates customer state. Enforces
the compliance gate (no live mode without recorded ToS consent + a viewed
backtest), encrypts credentials through the vault, and owns the per-customer
circuit breaker and kill switch. All access is by explicit customer_id; nothing
here returns another customer's data.

Nothing in this file promises returns or accuracy — backtest figures are reported
as-measured, historical, and non-indicative."""
from __future__ import annotations
import json

from brokers.base import now_iso
from . import store as tstore
from . import vault
from brokers import credentials as broker_creds
from .models import (
    Customer, RiskLimits, PAPER, LIVE, TOS_VERSION,
    STATUS_ACTIVE, STATUS_SUSPENDED, STATUS_TRIPPED, STATUS_KILLED,
)

BREAKER_THRESHOLD = 3          # consecutive errors before a customer's loop trips

# fields we vault per broker (mirrors brokers/credentials.py naming)
CRED_FIELDS = {
    "zerodha": ["KITE_API_KEY", "KITE_API_SECRET", "KITE_ACCESS_TOKEN"],
    "upstox": ["UPSTOX_API_KEY", "UPSTOX_API_SECRET", "UPSTOX_REDIRECT_URI", "UPSTOX_ACCESS_TOKEN"],
    "angelone": ["ANGELONE_API_KEY", "ANGELONE_CLIENT_CODE", "ANGELONE_MPIN", "ANGELONE_TOTP_SECRET"],
    "alpaca": ["ALPACA_API_KEY", "ALPACA_SECRET_KEY", "ALPACA_PAPER"],
}


def _row_to_customer(row) -> Customer:
    return Customer(
        id=row["id"], name=row["name"], email=row["email"], broker=row["broker"],
        mode=row["mode"], status=row["status"], equity=row["equity"],
        watchlist=tuple(row["watchlist"].split(",")) if row["watchlist"] else ("DEMO",),
        risk=RiskLimits(**json.loads(row["risk_json"])),
        error_count=row["error_count"], tos_version=row["tos_version"],
        tos_accepted_at=row["tos_accepted_at"], backtest_viewed_at=row["backtest_viewed_at"],
    )


# ---------------- customers ----------------
def create_customer(conn, id, name, email, broker="zerodha", equity=100_000.0,
                    watchlist=("DEMO",), risk: RiskLimits | None = None) -> Customer:
    c = Customer(id=id, name=name, email=email, broker=broker, equity=equity,
                 watchlist=tuple(watchlist), risk=risk or RiskLimits())
    tstore.upsert_customer(conn, c)
    return c


def get_customer(conn, customer_id) -> Customer | None:
    row = tstore.get_customer_row(conn, customer_id)
    return _row_to_customer(row) if row else None


def list_customers(conn) -> list[Customer]:
    return [_row_to_customer(r) for r in tstore.list_customer_rows(conn)]


# ---------------- credential vault ----------------
def set_broker_credentials(conn, customer_id, values: dict) -> None:
    """Encrypt and store each credential field. Plaintext is never persisted or
    logged; only ciphertext hits the DB."""
    for field, plaintext in values.items():
        tstore.put_credential(conn, customer_id, field, vault.encrypt(str(plaintext)))


def load_broker_creds(conn, customer_id, broker) -> broker_creds.Creds:
    """Decrypt this customer's credentials into a broker_creds.Creds. Scoped
    strictly to the given customer_id."""
    values = {}
    for field in CRED_FIELDS.get(broker, []):
        ct = tstore.get_credential_ciphertext(conn, customer_id, field)
        values[field] = vault.decrypt(ct) if ct else ""
    return broker_creds.Creds(broker, values)


# ---------------- backtest (real, historical, non-indicative) ----------------
def run_backtest(conn, customer_id, seed=7, n=1400, folds=4) -> dict:
    """Run Phase 1's walk-forward backtest for this customer's strategy and return
    the as-measured aggregate metrics (win_rate, sharpe, max_drawdown, ...). These
    are historical simulation results, not a forecast or a target."""
    from backtest.runner import walk_forward
    from backtest.synthetic import generate
    from strategies.weighted_indicator import WeightedIndicatorStrategy
    from risk.manager import RiskConfig
    c = get_customer(conn, customer_id)
    cfg = RiskConfig(**c.risk.as_dict()) if c else RiskConfig()
    report = walk_forward(generate(n=n, seed=seed),
                          WeightedIndicatorStrategy(threshold=0.20), cfg, folds=folds)
    a = report["aggregate"]
    return {
        "win_rate": a["win_rate"], "sharpe": a["avg_fold_sharpe"],
        "max_drawdown": a["max_drawdown"], "profit_factor": a["profit_factor"],
        "total_trades": a["total_trades"], "return_pct": a["return_pct"],
    }


# ---------------- compliance gate ----------------
def record_consent(conn, customer_id, version=TOS_VERSION) -> str:
    ts = now_iso()
    tstore.record_consent(conn, customer_id, version, ts)
    audit(conn, customer_id, "control", None, {"event": "tos_accepted", "version": version})
    return ts


def record_backtest_view(conn, customer_id, metrics: dict) -> str:
    ts = now_iso()
    tstore.record_backtest_view(conn, customer_id, ts,
                                metrics.get("win_rate"), metrics.get("sharpe"),
                                metrics.get("max_drawdown"))
    audit(conn, customer_id, "control", None, {"event": "backtest_viewed", **metrics})
    return ts


def can_go_live(conn, customer_id) -> tuple[bool, str]:
    """Gate: (a) current-version ToS consent recorded, (b) backtest viewed. Both
    enforced here, not just in UI."""
    c = get_customer(conn, customer_id)
    if not c:
        return False, "unknown customer"
    if c.tos_version != TOS_VERSION or not c.tos_accepted_at:
        return False, f"ToS {TOS_VERSION} not accepted"
    if not c.backtest_viewed_at:
        return False, "backtest results not yet viewed"
    return True, "ok"


def set_mode(conn, customer_id, mode, actor="customer") -> tuple[bool, str]:
    """Toggle paper/live. Live is rejected unless the compliance gate passes.
    `actor` is recorded for audit; admins may not force live (see api layer)."""
    if mode not in (PAPER, LIVE):
        return False, "invalid mode"
    if mode == LIVE and actor == "admin":
        return False, "admin may not force live mode for a customer"
    if mode == LIVE:
        ok, reason = can_go_live(conn, customer_id)
        if not ok:
            audit(conn, customer_id, "control",
                  None, {"event": "live_toggle_rejected", "reason": reason, "actor": actor})
            return False, reason
    c = get_customer(conn, customer_id)
    c.mode = mode
    tstore.upsert_customer(conn, c)
    audit(conn, customer_id, "control", None, {"event": "mode_set", "mode": mode, "actor": actor})
    return True, "ok"


# ---------------- risk (admin cannot loosen) ----------------
def admin_update_risk(conn, customer_id, new_limits: RiskLimits) -> tuple[bool, str]:
    c = get_customer(conn, customer_id)
    if not c:
        return False, "unknown customer"
    if not c.risk.is_tighter_or_equal(new_limits):
        return False, "admin may not loosen a customer's risk limits"
    c.risk = new_limits
    tstore.upsert_customer(conn, c)
    audit(conn, customer_id, "control", None, {"event": "risk_tightened", "actor": "admin"})
    return True, "ok"


# ---------------- circuit breaker ----------------
def record_error(conn, customer_id, message) -> bool:
    """Increment consecutive-error count; trip the breaker at threshold. Returns
    True if the breaker tripped on this error."""
    c = get_customer(conn, customer_id)
    n = c.error_count + 1
    tstore.set_error_count(conn, customer_id, n)
    audit(conn, customer_id, "error", None, {"message": str(message)[:300], "count": n})
    if n >= BREAKER_THRESHOLD:
        tstore.set_status(conn, customer_id, STATUS_TRIPPED)
        audit(conn, customer_id, "control", None,
              {"event": "breaker_tripped", "alert": True, "consecutive_errors": n})
        return True
    return False


def clear_errors(conn, customer_id) -> None:
    c = get_customer(conn, customer_id)
    if c.error_count:
        tstore.set_error_count(conn, customer_id, 0)


# ---------------- kill switch / suspend ----------------
def flatten_positions(conn, customer_id) -> int:
    """Close every open position in this customer's paper book at its avg price.
    Scoped strictly to the customer. Returns count closed."""
    from brokers.base import Order
    import config
    c = get_customer(conn, customer_id)
    if not c:
        return 0
    creds = load_broker_creds(conn, customer_id, c.broker)
    broker = config.build_broker(c.broker, customer_id=customer_id, creds=creds)
    closed = 0
    for p in broker.get_positions():
        if p.quantity == 0:
            continue
        side = "SELL" if p.quantity > 0 else "BUY"
        oid = broker.place_order(Order(p.symbol, side, abs(p.quantity), p.avg_price, now_iso()))
        audit(conn, customer_id, "order", p.symbol,
              {"order_id": oid, "side": side, "qty": abs(p.quantity),
               "price": p.avg_price, "reason": "kill_switch_flatten"})
        closed += 1
    return closed


def kill_switch(conn, customer_id, flatten=False) -> int:
    tstore.set_control(conn, customer_id, killed=True)
    tstore.set_status(conn, customer_id, STATUS_KILLED)
    audit(conn, customer_id, "control", None,
          {"event": "kill_switch", "alert": True, "flatten": flatten})
    return flatten_positions(conn, customer_id) if flatten else 0


def suspend(conn, customer_id) -> None:
    tstore.set_control(conn, customer_id, suspended=True)
    tstore.set_status(conn, customer_id, STATUS_SUSPENDED)
    audit(conn, customer_id, "control", None, {"event": "suspended", "actor": "admin"})


def resume(conn, customer_id) -> None:
    tstore.set_control(conn, customer_id, killed=False, suspended=False)
    tstore.set_error_count(conn, customer_id, 0)
    tstore.set_status(conn, customer_id, STATUS_ACTIVE)
    audit(conn, customer_id, "control", None, {"event": "resumed", "actor": "admin"})


def control_flags(conn, customer_id) -> dict:
    return tstore.get_control(conn, customer_id)


# ---------------- audit ----------------
def audit(conn, customer_id, kind, symbol, detail: dict) -> None:
    tstore.append_audit(conn, customer_id, now_iso(), kind, symbol, detail)


def get_audit(conn, customer_id, limit=200):
    return tstore.get_audit(conn, customer_id, limit)
