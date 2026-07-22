"""Streamlit paper-trading dashboard for the `demo` tenant, mock data only.

Design contract (keep it swappable): this file reads market data ONLY through the
Broker interface (get_historical / get_quote / get_positions) and reads trades
ONLY from the DB. The mock feed is injected once, at broker-build time, via
config.build_broker(...). Switching the demo customer's broker from `mock` to
`zerodha` (real KITE_* keys in the vault/.env) needs no change here, in the
strategy, or in the risk manager — only the data source behind the interface
changes. There is deliberately NO mock-vs-real branching in this file.
"""
from __future__ import annotations
import sqlite3
import time

import streamlit as st
import plotly.graph_objects as go

import config
from data import store as dstore
from data.mock_feed import MockFeed
from tenancy import store as tstore, service
from tenancy.models import PAPER
from strategies.weighted_indicator import WeightedIndicatorStrategy
from strategies import indicators as ind
from risk.manager import RiskManager, RiskConfig
from execution.engine import Engine

DEMO_ID = "demo"
REFRESH_SECS = 2

st.set_page_config(page_title="Paper Trading — Mock", layout="wide")


def open_conn():
    dstore.connect().close()   # ensure orders/positions schema
    tstore.connect().close()   # ensure tenancy schema
    conn = sqlite3.connect(str(dstore.DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_demo(conn):
    c = service.get_customer(conn, DEMO_ID)
    if c is None:
        c = service.create_customer(
            conn, id=DEMO_ID, name="Demo Tenant", email="demo@example.com",
            broker="mock", equity=100_000.0, watchlist=("NIFTY-MOCK", "BANKNIFTY-MOCK"))
    return c


def build_pipeline(conn, customer):
    feed = MockFeed(symbols=customer.watchlist, seed=11)
    creds = service.load_broker_creds(conn, customer.id, customer.broker)
    broker = config.build_broker(
        customer.broker, quote_source=feed.quote_source,
        historical_source=feed.historical_source, customer_id=customer.id, creds=creds)
    broker.connect()
    rm = RiskManager(RiskConfig(**customer.risk.as_dict()))
    rm.start_day(customer.equity)
    engine = Engine(
        broker=broker, strategy=WeightedIndicatorStrategy(threshold=0.20),
        risk=rm, watchlist=list(customer.watchlist), equity=customer.equity,
        on_event=lambda kind, sym, detail: service.audit(conn, customer.id, kind, sym, detail))
    return {"feed": feed, "broker": broker, "engine": engine,
            "strategy": WeightedIndicatorStrategy(threshold=0.20)}


def reset_demo(conn):
    conn.execute("DELETE FROM orders WHERE customer_id=?", (DEMO_ID,))
    conn.execute("DELETE FROM positions WHERE customer_id=?", (DEMO_ID,))
    conn.execute("DELETE FROM audit_log WHERE customer_id=?", (DEMO_ID,))
    conn.commit()


# ---------- session state ----------
if "conn" not in st.session_state:
    st.session_state.conn = open_conn()
conn = st.session_state.conn
customer = ensure_demo(conn)

if "pipe" not in st.session_state:
    st.session_state.pipe = build_pipeline(conn, customer)
if "running" not in st.session_state:
    st.session_state.running = False

pipe = st.session_state.pipe
feed, broker, engine, strat = pipe["feed"], pipe["broker"], pipe["engine"], pipe["strategy"]

# ---------- banner ----------
st.markdown(
    "<div style='background:#b45309;color:#fff;padding:8px 14px;border-radius:6px;"
    "font-weight:700;letter-spacing:.5px;text-align:center'>⚠️ MOCK DATA — NOT LIVE · "
    "simulated paper trading, no broker connection, no real orders</div>",
    unsafe_allow_html=True)
st.title("Paper Trading Dashboard")
st.caption(f"tenant: `{customer.id}` · broker: `{broker.name}` · mode: `{customer.mode}` "
           f"(paper) · past results are historical simulation, not indicative of future performance")

# ---------- controls ----------
c1, c2, c3, c4 = st.columns([1, 1, 1, 3])
if c1.button("▶ Start", use_container_width=True, disabled=st.session_state.running):
    st.session_state.running = True
    st.rerun()
if c2.button("⏸ Stop", use_container_width=True, disabled=not st.session_state.running):
    st.session_state.running = False
    st.rerun()
if c3.button("↺ Reset", use_container_width=True):
    reset_demo(conn)
    st.session_state.pipe = build_pipeline(conn, customer)
    st.session_state.running = False
    st.rerun()
symbol = c4.selectbox("Symbol", list(customer.watchlist))

status = "🟢 running" if st.session_state.running else "⚪ stopped"
c4.caption(f"feed: {status} · refresh ~{REFRESH_SECS}s/bar")

# ---------- advance one cycle if running (BEFORE render) ----------
if st.session_state.running:
    feed.advance()
    engine.step()

# ---------- market data via Broker interface only ----------
bars = broker.get_historical(symbol, "day", strat.min_bars() + 60)
closes = [b.close for b in bars]
x = list(range(len(bars)))

# ---------- chart ----------
st.subheader(f"{symbol} — price + EMA/Bollinger")
if len(bars) >= strat.min_bars():
    e20, e50, e200 = ind.ema(closes, 20), ind.ema(closes, 50), ind.ema(closes, 200)
    bb_up, bb_mid, bb_lo = ind.bollinger(closes, 20, 2.0)
    fig = go.Figure()
    fig.add_trace(go.Candlestick(
        x=x, open=[b.open for b in bars], high=[b.high for b in bars],
        low=[b.low for b in bars], close=closes, name="price",
        increasing_line_color="#16a34a", decreasing_line_color="#dc2626"))
    for series, name, color in ((e20, "EMA20", "#2563eb"), (e50, "EMA50", "#7c3aed"),
                                (e200, "EMA200", "#f59e0b")):
        fig.add_trace(go.Scatter(x=x, y=series, name=name, mode="lines",
                                 line=dict(width=1.3, color=color)))
    fig.add_trace(go.Scatter(x=x, y=bb_up, name="BB upper", mode="lines",
                             line=dict(width=1, color="#94a3b8", dash="dot")))
    fig.add_trace(go.Scatter(x=x, y=bb_lo, name="BB lower", mode="lines",
                             line=dict(width=1, color="#94a3b8", dash="dot"),
                             fill="tonexty", fillcolor="rgba(148,163,184,0.10)"))
    fig.update_layout(height=460, margin=dict(l=10, r=10, t=10, b=10),
                      xaxis_rangeslider_visible=False, showlegend=True,
                      legend=dict(orientation="h", y=1.02))
    st.plotly_chart(fig, use_container_width=True)
else:
    st.info("Warming up indicators…")

# ---------- current signal + breakdown ----------
left, right = st.columns([1, 1])
with left:
    st.subheader("Current signal")
    if len(bars) >= strat.min_bars():
        sig = strat.evaluate(bars)
        color = {"BUY": "#16a34a", "SELL": "#dc2626", "HOLD": "#64748b"}[sig.action]
        st.markdown(f"<h2 style='color:{color};margin:0'>{sig.action}</h2>",
                    unsafe_allow_html=True)
        st.metric("Confidence", f"{sig.strength * 100:.0f}%")
        rows = []
        for tok in sig.reason.split():
            if "=" in tok:
                k, v = tok.split("=", 1)
                rows.append({"indicator": k, "vote": v})
        if rows:
            st.dataframe(rows, use_container_width=True, hide_index=True)
        else:
            st.caption(sig.reason)
    else:
        st.caption("insufficient history")

with right:
    st.subheader("Open positions & P&L")
    positions = broker.get_positions()  # scoped to demo customer_id
    realized = engine.equity - customer.equity
    unrealized = 0.0
    prows = []
    for p in positions:
        last = feed.last_price(p.symbol) or p.avg_price
        upnl = (last - p.avg_price) * p.quantity
        unrealized += upnl
        prows.append({"symbol": p.symbol, "qty": p.quantity,
                      "avg": round(p.avg_price, 2), "last": round(last, 2),
                      "uPnL": round(upnl, 2)})
    m1, m2, m3 = st.columns(3)
    m1.metric("Equity", f"{engine.equity:,.0f}")
    m2.metric("Realized P&L", f"{realized:,.0f}")
    m3.metric("Unrealized P&L", f"{unrealized:,.0f}")
    if prows:
        st.dataframe(prows, use_container_width=True, hide_index=True)
    else:
        st.caption("no open positions")

# ---------- recent trade log from DB ----------
st.subheader("Recent trades (from DB)")
orders = dstore.get_orders(conn, DEMO_ID, limit=30)
if orders:
    st.dataframe(
        [{"id": o["id"], "ts": o["ts"], "symbol": o["symbol"], "side": o["side"],
          "qty": o["quantity"], "price": round(o["price"], 2), "mode": o["mode"]}
         for o in orders],
        use_container_width=True, hide_index=True)
else:
    st.caption("no trades yet — press Start")

# ---------- loop ----------
if st.session_state.running:
    time.sleep(REFRESH_SECS)
    st.rerun()
