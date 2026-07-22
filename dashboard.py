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
import json
import sqlite3
import time
from datetime import datetime, timezone

import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots

import config
from data import store as dstore
from data.mock_feed import MockFeed
from tenancy import store as tstore, service
from strategies.weighted_indicator import WeightedIndicatorStrategy
from strategies import indicators as ind
from risk.manager import RiskManager, RiskConfig
from execution.engine import Engine

DEMO_ID = "demo"
REFRESH_SECS = 2
WATCHLIST = ("RELIANCE", "TCS", "INFY", "HDFCBANK")

UP, DOWN, FLAT = "#22c55e", "#ef4444", "#64748b"
ACTION_COLOR = {"BUY": UP, "SELL": DOWN, "HOLD": FLAT}

st.set_page_config(page_title="Paper Trading Terminal", layout="wide",
                   page_icon="📈", initial_sidebar_state="collapsed")

CSS = """
<style>
#MainMenu, footer, header {visibility: hidden;}
.block-container {padding-top: 1rem; padding-bottom: 2rem; max-width: 1500px;}
div[data-testid="stMetric"] {
  background: linear-gradient(180deg,#111a2e 0%,#0d1526 100%);
  border: 1px solid #1e293b; border-radius: 12px; padding: 14px 16px;
}
div[data-testid="stMetricLabel"] p {font-size:.72rem; letter-spacing:.06em;
  text-transform:uppercase; color:#94a3b8;}
div[data-testid="stMetricValue"] {font-variant-numeric: tabular-nums;
  font-weight:700; font-size:1.5rem;}
.stButton>button {border-radius:10px; font-weight:600; border:1px solid #1e293b;}
.badge {display:inline-block; padding:3px 10px; border-radius:999px;
  font-size:.72rem; font-weight:700; letter-spacing:.04em;}
.chip {display:inline-block; padding:2px 9px; border-radius:6px; font-size:.72rem;
  background:#111a2e; border:1px solid #1e293b; color:#cbd5e1; margin-right:6px;
  font-family:ui-monospace,monospace;}
.tape {font-family:ui-monospace,monospace;}
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)


# ---------- setup ----------
def open_conn():
    dstore.connect().close()
    tstore.connect().close()
    conn = sqlite3.connect(str(dstore.DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_demo(conn):
    c = service.get_customer(conn, DEMO_ID)
    if c is None:
        c = service.create_customer(
            conn, id=DEMO_ID, name="Demo Tenant", email="demo@example.com",
            broker="mock", equity=100_000.0, watchlist=WATCHLIST)
    elif tuple(c.watchlist) != WATCHLIST:
        c.watchlist = WATCHLIST
        tstore.upsert_customer(conn, c)
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
    for t in ("orders", "positions", "audit_log"):
        conn.execute(f"DELETE FROM {t} WHERE customer_id=?", (DEMO_ID,))
    conn.commit()


def realized_stats(conn):
    """Realized win-rate + PnL from the audit log (exit orders carry pnl)."""
    wins = losses = 0
    pnl = 0.0
    for r in service.get_audit(conn, DEMO_ID, limit=2000):
        if r["kind"] != "order":
            continue
        d = json.loads(r["detail"] or "{}")
        if "pnl" not in d:
            continue
        pnl += d["pnl"]
        if d["pnl"] > 0:
            wins += 1
        elif d["pnl"] < 0:
            losses += 1
    n = wins + losses
    return {"wins": wins, "losses": losses, "closed": n,
            "win_rate": (wins / n) if n else 0.0, "realized": pnl}


def rupee(v):
    return f"₹{v:,.0f}"


# ---------- session state ----------
if "conn" not in st.session_state:
    st.session_state.conn = open_conn()
conn = st.session_state.conn
customer = ensure_demo(conn)
if "pipe" not in st.session_state:
    st.session_state.pipe = build_pipeline(conn, customer)
if "running" not in st.session_state:
    st.session_state.running = False
if "equity_curve" not in st.session_state:
    st.session_state.equity_curve = [customer.equity]

pipe = st.session_state.pipe
feed, broker, engine, strat = pipe["feed"], pipe["broker"], pipe["engine"], pipe["strategy"]

# ---------- advance one cycle if running (BEFORE render) ----------
if st.session_state.running:
    feed.advance()
    engine.step()
    st.session_state.equity_curve.append(engine.equity)
    st.session_state.equity_curve = st.session_state.equity_curve[-500:]

# ---------- header ----------
running = st.session_state.running
status_badge = (f"<span class='badge' style='background:{UP}22;color:{UP};border:1px solid {UP}55'>● LIVE FEED</span>"
                if running else
                f"<span class='badge' style='background:{FLAT}22;color:#94a3b8;border:1px solid #334155'>■ STOPPED</span>")
now = datetime.now(timezone.utc).astimezone().strftime("%H:%M:%S")
st.markdown(
    f"<div style='display:flex;align-items:center;gap:14px;flex-wrap:wrap'>"
    f"<span style='font-size:1.6rem;font-weight:800'>📈 Paper Trading Terminal</span>"
    f"{status_badge}"
    f"<span class='badge' style='background:#b4530922;color:#f59e0b;border:1px solid #b4530955'>⚠ MOCK DATA — NOT LIVE</span>"
    f"<span style='flex:1'></span>"
    f"<span class='chip'>tenant: {customer.id}</span>"
    f"<span class='chip'>broker: {broker.name}</span>"
    f"<span class='chip'>mode: {customer.mode}</span>"
    f"<span class='chip tape'>⏱ {now}</span>"
    f"</div>", unsafe_allow_html=True)
st.caption("Simulated paper trading on synthetic data — no broker connection, no real orders. "
           "Past results are historical simulation, not indicative of future performance.")

# ---------- controls ----------
c1, c2, c3, c4, c5 = st.columns([1, 1, 1, 2, 2])
if c1.button("▶  Start", use_container_width=True, disabled=running, type="primary"):
    st.session_state.running = True; st.rerun()
if c2.button("⏸  Stop", use_container_width=True, disabled=not running):
    st.session_state.running = False; st.rerun()
if c3.button("↺  Reset", use_container_width=True):
    reset_demo(conn)
    st.session_state.pipe = build_pipeline(conn, customer)
    st.session_state.equity_curve = [customer.equity]
    st.session_state.running = False; st.rerun()
symbol = c4.selectbox("Symbol", list(customer.watchlist), label_visibility="collapsed")
c5.markdown(f"<div class='tape' style='padding-top:6px;color:#94a3b8'>bars streamed: "
            f"{len(st.session_state.equity_curve)-1} · refresh ~{REFRESH_SECS}s</div>",
            unsafe_allow_html=True)

# ---------- market data via Broker interface only ----------
bars = broker.get_historical(symbol, "day", strat.min_bars() + 120)
closes = [b.close for b in bars]
positions = broker.get_positions()
stats = realized_stats(conn)
last = feed.last_price(symbol) or (closes[-1] if closes else 0)
prev = closes[-2] if len(closes) > 1 else last
chg = last - prev
chg_pct = (chg / prev * 100) if prev else 0
unrealized = sum(((feed.last_price(p.symbol) or p.avg_price) - p.avg_price) * p.quantity
                 for p in positions)
session_pnl = engine.equity - customer.equity

# ---------- KPI row ----------
k = st.columns(6)
k[0].metric("Equity", rupee(engine.equity), f"{session_pnl:+,.0f}")
k[1].metric("Unrealized P&L", rupee(unrealized))
k[2].metric(f"{symbol} last", rupee(last), f"{chg:+.2f} ({chg_pct:+.2f}%)")
k[3].metric("Open positions", str(len(positions)))
k[4].metric("Closed trades", str(stats["closed"]))
k[5].metric("Win rate", f"{stats['win_rate']*100:.0f}%",
            f"{stats['wins']}W / {stats['losses']}L", delta_color="off")

# ---------- chart (price+volume) | signal ----------
chart_col, sig_col = st.columns([2.4, 1])
with chart_col:
    if len(bars) >= strat.min_bars():
        e20, e50, e200 = ind.ema(closes, 20), ind.ema(closes, 50), ind.ema(closes, 200)
        bb_up, _, bb_lo = ind.bollinger(closes, 20, 2.0)
        x = list(range(len(bars)))
        fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.76, 0.24],
                            vertical_spacing=0.03)
        fig.add_trace(go.Candlestick(
            x=x, open=[b.open for b in bars], high=[b.high for b in bars],
            low=[b.low for b in bars], close=closes, name=symbol,
            increasing_line_color=UP, decreasing_line_color=DOWN, whiskerwidth=0.4), row=1, col=1)
        fig.add_trace(go.Scatter(x=x, y=bb_up, name="BB", mode="lines",
                                 line=dict(width=1, color="#334155")), row=1, col=1)
        fig.add_trace(go.Scatter(x=x, y=bb_lo, name="BB", mode="lines", showlegend=False,
                                 line=dict(width=1, color="#334155"),
                                 fill="tonexty", fillcolor="rgba(51,65,85,0.18)"), row=1, col=1)
        for s, nm, col in ((e20, "EMA20", "#38bdf8"), (e50, "EMA50", "#a78bfa"),
                           (e200, "EMA200", "#f59e0b")):
            fig.add_trace(go.Scatter(x=x, y=s, name=nm, mode="lines",
                                     line=dict(width=1.4, color=col)), row=1, col=1)
        vol_colors = [UP if bars[i].close >= bars[i].open else DOWN for i in range(len(bars))]
        fig.add_trace(go.Bar(x=x, y=[b.volume for b in bars], name="Vol",
                             marker_color=vol_colors, opacity=0.5, showlegend=False), row=2, col=1)
        fig.update_layout(
            template="plotly_dark", height=520, margin=dict(l=8, r=8, t=8, b=8),
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            xaxis_rangeslider_visible=False, showlegend=True, bargap=0.1,
            legend=dict(orientation="h", y=1.03, x=0, bgcolor="rgba(0,0,0,0)"))
        fig.update_xaxes(showgrid=False)
        fig.update_yaxes(gridcolor="#1e293b", zeroline=False)
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
    else:
        st.info("Warming up indicators…")

with sig_col:
    if len(bars) >= strat.min_bars():
        sig = strat.evaluate(bars)
        col = ACTION_COLOR[sig.action]
        st.markdown(
            f"<div style='text-align:center;padding:10px;border:1px solid #1e293b;"
            f"border-radius:14px;background:linear-gradient(180deg,#111a2e,#0d1526)'>"
            f"<div style='color:#94a3b8;font-size:.75rem;letter-spacing:.08em'>SIGNAL · {symbol}</div>"
            f"<div style='font-size:2.6rem;font-weight:800;color:{col};line-height:1.1'>{sig.action}</div>"
            f"<div style='color:#94a3b8;font-size:.8rem'>confidence</div>"
            f"<div style='font-size:1.4rem;font-weight:700;color:{col}'>{sig.strength*100:.0f}%</div>"
            f"</div>", unsafe_allow_html=True)
        st.progress(min(1.0, sig.strength))
        votes = []
        for tok in sig.reason.split():
            if "=" in tok:
                key, val = tok.split("=", 1)
                try:
                    votes.append((key, float(val)))
                except ValueError:
                    pass
        if votes:
            names = [v[0] for v in votes]
            vals = [v[1] for v in votes]
            colors = [UP if v > 0 else DOWN if v < 0 else FLAT for v in vals]
            vfig = go.Figure(go.Bar(x=vals, y=names, orientation="h",
                                    marker_color=colors, text=[f"{v:+.2f}" for v in vals],
                                    textposition="auto"))
            vfig.update_layout(template="plotly_dark", height=230,
                               margin=dict(l=6, r=6, t=6, b=6),
                               paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                               xaxis=dict(range=[-1.1, 1.1], zeroline=True,
                                          zerolinecolor="#334155", gridcolor="#1e293b"))
            vfig.update_yaxes(autorange="reversed")
            st.plotly_chart(vfig, use_container_width=True, config={"displayModeBar": False})
    else:
        st.caption("insufficient history")

# ---------- positions | equity curve ----------
pos_col, eq_col = st.columns([1.3, 1])
with pos_col:
    st.markdown("###### Open positions")
    if positions:
        rows = []
        for p in positions:
            lp = feed.last_price(p.symbol) or p.avg_price
            upnl = (lp - p.avg_price) * p.quantity
            rows.append({"Symbol": p.symbol, "Qty": p.quantity,
                         "Avg": round(p.avg_price, 2), "LTP": round(lp, 2),
                         "uPnL ₹": round(upnl, 2)})
        st.dataframe(rows, use_container_width=True, hide_index=True)
    else:
        st.caption("no open positions — press ▶ Start")

with eq_col:
    st.markdown("###### Equity curve")
    ec = st.session_state.equity_curve
    up_curve = ec[-1] >= ec[0]
    line_col = UP if up_curve else DOWN
    fill_col = "rgba(34,197,94,0.12)" if up_curve else "rgba(239,68,68,0.12)"
    efig = go.Figure(go.Scatter(y=ec, mode="lines", line=dict(color=line_col, width=2),
                                fill="tozeroy", fillcolor=fill_col))
    efig.update_layout(template="plotly_dark", height=200, margin=dict(l=6, r=6, t=6, b=6),
                       paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                       yaxis=dict(gridcolor="#1e293b"), xaxis=dict(showgrid=False))
    efig.update_yaxes(range=[min(ec) * 0.999, max(ec) * 1.001])
    st.plotly_chart(efig, use_container_width=True, config={"displayModeBar": False})

# ---------- trade blotter ----------
st.markdown("###### Trade blotter")
orders = dstore.get_orders(conn, DEMO_ID, limit=40)
if orders:
    def badge(side):
        c = UP if side == "BUY" else DOWN
        return f"<span class='badge' style='background:{c}22;color:{c};border:1px solid {c}55'>{side}</span>"
    html = ("<table style='width:100%;border-collapse:collapse;font-family:ui-monospace,monospace;"
            "font-size:.82rem'><thead><tr style='color:#94a3b8;text-align:left;border-bottom:1px solid #1e293b'>"
            "<th style='padding:6px'>#</th><th>Time</th><th>Symbol</th><th>Side</th>"
            "<th style='text-align:right'>Qty</th><th style='text-align:right'>Price ₹</th><th>Mode</th></tr></thead><tbody>")
    for o in orders:
        t = (o["ts"] or "")[11:19] or "—"
        html += (f"<tr style='border-bottom:1px solid #0f1a2e'><td style='padding:6px;color:#64748b'>{o['id']}</td>"
                 f"<td>{t}</td><td style='color:#e2e8f0;font-weight:600'>{o['symbol']}</td>"
                 f"<td>{badge(o['side'])}</td><td style='text-align:right'>{o['quantity']}</td>"
                 f"<td style='text-align:right'>{o['price']:,.2f}</td>"
                 f"<td style='color:#64748b'>{o['mode']}</td></tr>")
    html += "</tbody></table>"
    st.markdown(html, unsafe_allow_html=True)
else:
    st.caption("no trades yet — press ▶ Start")

# ---------- loop ----------
if st.session_state.running:
    time.sleep(REFRESH_SECS)
    st.rerun()
