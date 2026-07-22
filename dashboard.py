"""Streamlit paper-trading dashboard for the `demo` tenant, mock data only.

Design contract (keep it swappable): this file reads market data ONLY through the
Broker interface (get_historical / get_quote / get_positions) and reads trades
ONLY from the DB. The mock feed is injected once, at broker-build time, via
config.build_broker(...). Switching the demo customer's broker from `mock` to
`zerodha` (real KITE_* keys in the vault/.env) needs no change here, in the
strategy, or in the risk manager. There is deliberately NO mock-vs-real branching.
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

PROFIT, LOSS, FLAT = "#3fb950", "#f85149", "#8b949e"
ACTION_COLOR = {"BUY": PROFIT, "SELL": LOSS, "HOLD": FLAT}
# mode badge palette — color changes with the customer's ACTUAL state
MODE_BADGE = {
    "mock":  ("#d29922", "MOCK DATA · SIMULATED"),   # amber
    "paper": ("#388bfd", "PAPER TRADING"),           # blue
    "live":  ("#f85149", "LIVE TRADING"),            # red
}

st.set_page_config(page_title="Paper Trading Terminal", layout="wide",
                   page_icon="📈", initial_sidebar_state="collapsed")

st.markdown("""
<style>
#MainMenu, footer, header {visibility:hidden;}
.block-container {padding-top:1.1rem; padding-bottom:2rem; max-width:1500px;}
div[data-testid="stMetricValue"] {font-variant-numeric:tabular-nums; font-weight:700;}
div[data-testid="stMetricLabel"] p {font-size:.72rem; letter-spacing:.05em;
  text-transform:uppercase; color:#8b949e;}
div[data-testid="stVerticalBlockBorderWrapper"] {background:#0f141c; border-radius:14px;}
.stButton>button {border-radius:9px; font-weight:600;}
.tape {font-family:ui-monospace,monospace;}
</style>
""", unsafe_allow_html=True)


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


def closed_trades(conn):
    """Realized exits (each carries pnl) in chronological order, from the DB."""
    out = []
    for r in reversed(service.get_audit(conn, DEMO_ID, limit=5000)):
        if r["kind"] != "order":
            continue
        d = json.loads(r["detail"] or "{}")
        if "pnl" in d:
            out.append({"ts": r["ts"], "pnl": d["pnl"]})
    return out


def equity_curve(conn, starting):
    """Cumulative account equity over closed trades — the DB is the source."""
    eq = starting
    xs, ys = [0], [starting]
    for i, t in enumerate(closed_trades(conn), start=1):
        eq += t["pnl"]
        xs.append(i); ys.append(eq)
    return xs, ys


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

pipe = st.session_state.pipe
feed, broker, engine, strat = pipe["feed"], pipe["broker"], pipe["engine"], pipe["strategy"]
running = st.session_state.running

# ---------- advance one cycle if running (BEFORE render) ----------
if running:
    feed.advance()
    engine.step()

# ---------- mode badge (very top, impossible to miss) ----------
badge_color, badge_text = MODE_BADGE.get(
    "live" if customer.mode == "live" else ("mock" if broker.name == "mock" else "paper"),
    MODE_BADGE["paper"])
live_dot = (f"<span style='color:{PROFIT}'>● STREAMING</span>" if running
            else "<span style='color:#8b949e'>■ STOPPED</span>")
st.markdown(
    f"<div style='background:{badge_color};color:#0d1117;padding:9px 16px;border-radius:10px;"
    f"font-weight:800;letter-spacing:.08em;display:flex;justify-content:space-between;"
    f"align-items:center'><span>⚠ {badge_text}</span>"
    f"<span style='font-size:.85rem;font-weight:700'>NO REAL ORDERS · NO BROKER CONNECTION</span>"
    f"</div>", unsafe_allow_html=True)

hc1, hc2 = st.columns([3, 2])
hc1.markdown("<div style='font-size:1.55rem;font-weight:800;margin-top:6px'>"
             "📈 Paper Trading Terminal</div>", unsafe_allow_html=True)
now = datetime.now(timezone.utc).astimezone().strftime("%H:%M:%S")
hc2.markdown(
    f"<div class='tape' style='text-align:right;margin-top:12px;color:#8b949e'>"
    f"{live_dot} &nbsp;·&nbsp; tenant <b>{customer.id}</b> &nbsp;·&nbsp; broker <b>{broker.name}</b>"
    f" &nbsp;·&nbsp; ⏱ {now}</div>", unsafe_allow_html=True)

# ---------- controls ----------
c1, c2, c3, c4 = st.columns([1, 1, 1, 3])
if c1.button("▶  Start", use_container_width=True, disabled=running, type="primary"):
    st.session_state.running = True; st.rerun()
if c2.button("⏸  Stop", use_container_width=True, disabled=not running):
    st.session_state.running = False; st.rerun()
if c3.button("↺  Reset", use_container_width=True):
    reset_demo(conn)
    st.session_state.pipe = build_pipeline(conn, customer)
    st.session_state.running = False; st.rerun()
symbol = c4.selectbox("Symbol", list(customer.watchlist), label_visibility="collapsed")

# ---------- shared reads (Broker interface + DB only) ----------
bars = broker.get_historical(symbol, "day", strat.min_bars() + 120)
closes = [b.close for b in bars]
positions = broker.get_positions()
trades = closed_trades(conn)
wins = sum(1 for t in trades if t["pnl"] > 0)
losses = sum(1 for t in trades if t["pnl"] < 0)
win_rate = wins / (wins + losses) if (wins + losses) else 0.0
realized = engine.equity - customer.equity
unrealized = sum(((feed.last_price(p.symbol) or p.avg_price) - p.avg_price) * p.quantity
                 for p in positions)
last = feed.last_price(symbol) or (closes[-1] if closes else 0)
prev = closes[-2] if len(closes) > 1 else last
chg, chg_pct = last - prev, ((last - prev) / prev * 100 if prev else 0)

# ---------- SECTION: Account Summary ----------
with st.container(border=True):
    st.markdown("##### Account summary")
    m = st.columns(5)
    m[0].metric("Equity", rupee(engine.equity), f"{realized:+,.0f}")
    m[1].metric("Today's P&L", rupee(realized), f"{(realized/customer.equity*100):+.2f}%")
    m[2].metric("Unrealized P&L", rupee(unrealized))
    m[3].metric("Win rate", f"{win_rate*100:.0f}%", f"{wins}W / {losses}L", delta_color="off")
    m[4].metric("Open / Closed", f"{len(positions)} / {wins + losses}")

# ---------- SECTION: Equity Curve (from DB) ----------
with st.container(border=True):
    st.markdown("##### Account equity curve")
    xs, ys = equity_curve(conn, customer.equity)
    up = ys[-1] >= ys[0]
    col = PROFIT if up else LOSS
    fill = "rgba(63,185,80,0.12)" if up else "rgba(248,81,73,0.12)"
    efig = go.Figure(go.Scatter(x=xs, y=ys, mode="lines", line=dict(color=col, width=2.2),
                                fill="tozeroy", fillcolor=fill, name="equity"))
    efig.add_hline(y=customer.equity, line=dict(color="#30363d", width=1, dash="dot"))
    efig.update_layout(template="plotly_dark", height=240, margin=dict(l=8, r=8, t=8, b=8),
                       paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                       xaxis=dict(title="closed trades", showgrid=False),
                       yaxis=dict(gridcolor="#21262d"))
    if len(ys) > 1:
        efig.update_yaxes(range=[min(ys) * 0.999, max(ys) * 1.001])
    st.plotly_chart(efig, use_container_width=True, config={"displayModeBar": False})
    if len(ys) <= 1:
        st.caption("no closed trades yet — press ▶ Start to build the curve")

# ---------- SECTION: Price Chart + Signal ----------
chart_col, sig_col = st.columns([2.3, 1])
with chart_col:
    with st.container(border=True):
        st.markdown(f"##### {symbol} · price + indicators")
        if len(bars) >= strat.min_bars():
            e20, e50, e200 = ind.ema(closes, 20), ind.ema(closes, 50), ind.ema(closes, 200)
            bb_up, _, bb_lo = ind.bollinger(closes, 20, 2.0)
            x = list(range(len(bars)))
            fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                                row_heights=[0.76, 0.24], vertical_spacing=0.03)
            fig.add_trace(go.Candlestick(
                x=x, open=[b.open for b in bars], high=[b.high for b in bars],
                low=[b.low for b in bars], close=closes, name=symbol,
                increasing_line_color=PROFIT, decreasing_line_color=LOSS), row=1, col=1)
            fig.add_trace(go.Scatter(x=x, y=bb_up, name="BB", mode="lines",
                                     line=dict(width=1, color="#30363d")), row=1, col=1)
            fig.add_trace(go.Scatter(x=x, y=bb_lo, mode="lines", showlegend=False,
                                     line=dict(width=1, color="#30363d"),
                                     fill="tonexty", fillcolor="rgba(48,54,61,0.25)"), row=1, col=1)
            for s, nm, cc in ((e20, "EMA20", "#58a6ff"), (e50, "EMA50", "#bc8cff"),
                              (e200, "EMA200", "#d29922")):
                fig.add_trace(go.Scatter(x=x, y=s, name=nm, mode="lines",
                                         line=dict(width=1.4, color=cc)), row=1, col=1)
            vcol = [PROFIT if bars[i].close >= bars[i].open else LOSS for i in range(len(bars))]
            fig.add_trace(go.Bar(x=x, y=[b.volume for b in bars], marker_color=vcol,
                                 opacity=0.5, showlegend=False), row=2, col=1)
            fig.update_layout(template="plotly_dark", height=500, margin=dict(l=8, r=8, t=8, b=8),
                              paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                              xaxis_rangeslider_visible=False, bargap=0.1,
                              legend=dict(orientation="h", y=1.03, x=0, bgcolor="rgba(0,0,0,0)"))
            fig.update_xaxes(showgrid=False)
            fig.update_yaxes(gridcolor="#21262d", zeroline=False)
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
        else:
            st.info("Warming up indicators…")

with sig_col:
    with st.container(border=True):
        st.markdown("##### Signal")
        if len(bars) >= strat.min_bars():
            sig = strat.evaluate(bars)
            col = ACTION_COLOR[sig.action]
            st.markdown(
                f"<div style='text-align:center;padding:8px 0'>"
                f"<div style='font-size:2.6rem;font-weight:800;color:{col};line-height:1'>{sig.action}</div>"
                f"<div style='color:#8b949e;font-size:.78rem;margin-top:4px'>confidence</div>"
                f"<div style='font-size:1.35rem;font-weight:700;color:{col}'>{sig.strength*100:.0f}%</div>"
                f"</div>", unsafe_allow_html=True)
            st.progress(min(1.0, sig.strength))
            st.metric(f"{symbol} LTP", rupee(last), f"{chg:+.2f} ({chg_pct:+.2f}%)")
            with st.expander("Indicator breakdown"):
                votes = []
                for tok in sig.reason.split():
                    if "=" in tok:
                        kk, vv = tok.split("=", 1)
                        try:
                            votes.append((kk, float(vv)))
                        except ValueError:
                            pass
                if votes:
                    vfig = go.Figure(go.Bar(
                        x=[v for _, v in votes], y=[k for k, _ in votes], orientation="h",
                        marker_color=[PROFIT if v > 0 else LOSS if v < 0 else FLAT for _, v in votes],
                        text=[f"{v:+.2f}" for _, v in votes], textposition="auto"))
                    vfig.update_layout(template="plotly_dark", height=220,
                                       margin=dict(l=6, r=6, t=6, b=6),
                                       paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                                       xaxis=dict(range=[-1.1, 1.1], zeroline=True,
                                                  zerolinecolor="#30363d", gridcolor="#21262d"))
                    vfig.update_yaxes(autorange="reversed")
                    st.plotly_chart(vfig, use_container_width=True, config={"displayModeBar": False})
        else:
            st.caption("insufficient history")

# ---------- SECTION: Open Positions ----------
with st.container(border=True):
    st.markdown("##### Open positions")
    if positions:
        rows = []
        for p in positions:
            lp = feed.last_price(p.symbol) or p.avg_price
            rows.append({"Symbol": p.symbol, "Qty": p.quantity,
                         "Avg": round(p.avg_price, 2), "LTP": round(lp, 2),
                         "uPnL ₹": round((lp - p.avg_price) * p.quantity, 2)})
        st.dataframe(rows, use_container_width=True, hide_index=True)
    else:
        st.caption("no open positions — press ▶ Start")

# ---------- SECTION: Trade Log ----------
with st.container(border=True):
    st.markdown("##### Trade log")
    orders = dstore.get_orders(conn, DEMO_ID, limit=40)
    if orders:
        def badge(side):
            c = PROFIT if side == "BUY" else LOSS
            return (f"<span style='background:{c}22;color:{c};border:1px solid {c}55;"
                    f"padding:2px 9px;border-radius:999px;font-size:.72rem;font-weight:700'>{side}</span>")
        html = ("<table style='width:100%;border-collapse:collapse;font-family:ui-monospace,monospace;"
                "font-size:.82rem'><thead><tr style='color:#8b949e;text-align:left;"
                "border-bottom:1px solid #21262d'><th style='padding:6px'>#</th><th>Symbol</th>"
                "<th>Side</th><th style='text-align:right'>Qty</th>"
                "<th style='text-align:right'>Price ₹</th><th>Mode</th></tr></thead><tbody>")
        for o in orders:
            html += (f"<tr style='border-bottom:1px solid #161b22'>"
                     f"<td style='padding:6px;color:#6e7681'>{o['id']}</td>"
                     f"<td style='color:#e6edf3;font-weight:600'>{o['symbol']}</td>"
                     f"<td>{badge(o['side'])}</td><td style='text-align:right'>{o['quantity']}</td>"
                     f"<td style='text-align:right'>{o['price']:,.2f}</td>"
                     f"<td style='color:#6e7681'>{o['mode']}</td></tr>")
        html += "</tbody></table>"
        st.markdown(html, unsafe_allow_html=True)
    else:
        st.caption("no trades yet — press ▶ Start")

# ---------- loop ----------
if running:
    time.sleep(REFRESH_SECS)
    st.rerun()
