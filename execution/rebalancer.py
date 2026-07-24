"""Paper portfolio rebalancer. Moves the paper book from its current holdings to
a set of target weights by placing paper orders through the Broker interface —
no real orders. Cash and portfolio value are derived from the orders log, so the
whole thing is stateless and DB-backed.

Used by execution/live_runner.py for live-data paper trading of the low-vol
portfolio. Scoped strictly to one customer_id."""
from __future__ import annotations

from brokers.base import Order, now_iso
from data.store import get_orders


def portfolio_cash(conn, customer_id, capital: float) -> float:
    """Starting capital minus net cost of all paper orders so far (BUY spends,
    SELL returns). Fully derived from the orders log."""
    cash = capital
    for o in get_orders(conn, customer_id, limit=1_000_000):
        cash += (-1 if o["side"] == "BUY" else 1) * o["quantity"] * o["price"]
    return cash


def portfolio_value(broker, conn, customer_id, capital, prices) -> float:
    holdings = sum(p.quantity * prices.get(p.symbol, 0.0) for p in broker.get_positions())
    return portfolio_cash(conn, customer_id, capital) + holdings


def rebalance(broker, conn, customer_id, targets: dict, prices: dict,
              capital: float, on_event=None) -> dict:
    """Rebalance to `targets` (weights) at `prices`. Places paper orders for the
    delta between current and target share counts. Returns a summary."""
    value = portfolio_value(broker, conn, customer_id, capital, prices)
    current = {p.symbol: p.quantity for p in broker.get_positions()}
    target_qty = {}
    for s, w in targets.items():
        px = prices.get(s)
        if px and px > 0:
            target_qty[s] = int(w * value / px)

    orders = []
    # sells first (free up cash), then buys
    deltas = {s: target_qty.get(s, 0) - current.get(s, 0)
              for s in set(current) | set(target_qty)}
    for s in sorted(deltas, key=lambda z: deltas[z]):
        delta = deltas[s]
        if delta == 0:
            continue
        side = "BUY" if delta > 0 else "SELL"
        px = prices.get(s) or 0.0
        oid = broker.place_order(Order(s, side, abs(delta), px, now_iso()))
        orders.append({"symbol": s, "side": side, "qty": abs(delta), "price": px, "id": oid})
        if on_event:
            on_event("order", s, {"side": side, "qty": abs(delta), "price": px,
                                  "reason": "rebalance", "order_id": oid})
    return {"value": value, "target_qty": target_qty, "orders": orders,
            "cash_after": portfolio_cash(conn, customer_id, capital)}
