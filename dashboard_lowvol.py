"""Streamlit dashboard for the LOW-VOLATILITY paper portfolio — the strategy with
a measured (India-specific) edge. Real NSE data, paper execution (no real money).

Reads/writes the `lowvol_paper` tenant via the same rebalancer the CLI runner
uses. Shows: risk-on/off (200d trend), portfolio value & P&L, allocation, current
holdings, the strategy's current target, and a NAV history that grows each time
you record it. Data source is the free feed today; swaps to a broker feed once
API keys exist — this file doesn't change.

Run: streamlit run dashboard_lowvol.py
"""
from __future__ import annotations
import json
import sqlite3
from datetime import datetime, timezone

import streamlit as st
import plotly.graph_objects as go

import config
from data import store as dstore
from data.yahoo_feed import YahooFeed, FeedError
from tenancy import store as tstore, service
from tenancy.models import RiskLimits
from strategies.lowvol_portfolio import LowVolPortfolio
from execution.rebalancer import rebalance, portfolio_value, portfolio_cash
from execution.live_runner import UNIVERSE, BENCH, CAPITAL, CUSTOMER

PROFIT, LOSS, ACCENT = "#3fb950", "#f85149", "#388bfd"

st.set_page_config(page_title="Low-Vol Paper Portfolio", layout="wide", page_icon="📊")
st.markdown("""<style>
#MainMenu,footer,header{visibility:hidden;}
.block-container{padding-top:1.1rem;max-width:1400px;}
div[data-testid="stMetricValue"]{font-variant-numeric:tabular-nums;font-weight:700;}
div[data-testid="stMetricLabel"] p{font-size:.72rem;letter-spacing:.05em;
 text-transform:uppercase;color:#8b949e;}
</style>""", unsafe_allow_html=True)


def open_conn():
    dstore.connect().close(); tstore.connect().close()
    c = sqlite3.connect(str(dstore.DB_PATH), check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def ensure_customer(conn):
    c = service.get_customer(conn, CUSTOMER)
    if c is None:
        c = service.create_customer(conn, id=CUSTOMER, name="Low-Vol Paper",
                                    email="lowvol@example.com", broker="mock",
                                    equity=CAPITAL, watchlist=tuple(UNIVERSE), risk=RiskLimits())
    return c


@st.cache_data(show_spinner="Loading real NSE data…", ttl=1800)
def load_market():
    feed = YahooFeed(interval="day", rng="2y")
    history, dropped = {}, []
    for s in UNIVERSE:
        try:
            b = feed.bars(s)
            history[s] = b if len(b) > 210 else dropped.append(s)
        except FeedError:
            dropped.append(s)
    history = {s: b for s, b in history.items() if b}
    bench = feed.bars(BENCH)
    prices = {s: b[-1].close for s, b in history.items()}
    return history, bench, prices, [d for d in dropped if d], (bench[-1].ts if bench else "?")


def record_nav(conn, value):
    """Snapshot portfolio value once per day so the NAV curve builds over time."""
    today = datetime.now(timezone.utc).date().isoformat()
    rows = service.get_audit(conn, CUSTOMER, limit=2000)
    if any(r["kind"] == "nav" and json.loads(r["detail"])["date"] == today for r in rows):
        return
    service.audit(conn, CUSTOMER, "nav", None, {"date": today, "value": round(value, 2)})


def nav_history(conn):
    pts = []
    for r in reversed(service.get_audit(conn, CUSTOMER, limit=2000)):
        if r["kind"] == "nav":
            d = json.loads(r["detail"])
            pts.append((d["date"], d["value"]))
    return pts


# ---------- state ----------
if "conn" not in st.session_state:
    st.session_state.conn = open_conn()
conn = st.session_state.conn
ensure_customer(conn)
try:
    history, bench, prices, dropped, asof = load_market()
except Exception as e:
    st.error(f"⚠️ Could not load market data: {e}")
    st.stop()

# ---------- controls ----------
st.markdown(
    f"<div style='background:{ACCENT};color:#0d1117;padding:9px 16px;border-radius:10px;"
    f"font-weight:800;letter-spacing:.06em;display:flex;justify-content:space-between'>"
    f"<span>📊 LOW-VOLATILITY PAPER PORTFOLIO · REAL NSE DATA (EOD)</span>"
    f"<span style='font-size:.85rem'>NO REAL ORDERS · data through {asof}</span></div>",
    unsafe_allow_html=True)

cc = st.columns([1.2, 1.2, 1, 2.6])
top_k = cc[0].number_input("Holdings (K)", 5, 20, 10)
use_trend = cc[1].checkbox("200d trend filter", value=True,
                           help="Step to cash when Nifty is below its 200-day average")
rebalance_clicked = cc[2].button("🔄 Rebalance", use_container_width=True, type="primary")

strat = LowVolPortfolio(lookback=126, top_k=int(top_k), trend_filter=use_trend)
targets = strat.target_weights(history, bench)
risk_on = bool(targets)

broker = config.build_broker("mock", customer_id=CUSTOMER)
broker.connect()

if rebalance_clicked:
    res = rebalance(broker, conn, CUSTOMER, targets, prices, CAPITAL,
                    on_event=lambda k, s, d: service.audit(conn, CUSTOMER, k, s, d))
    record_nav(conn, res["value"])
    st.success(f"Rebalanced: {len(res['orders'])} paper orders "
               f"({'risk-off → cash' if not targets else f'{len(targets)} names'}).")

# ---------- computed ----------
positions = [p for p in broker.get_positions() if p.quantity != 0]
value = portfolio_value(broker, conn, CUSTOMER, CAPITAL, prices)
cash = portfolio_cash(conn, CUSTOMER, CAPITAL)
invested = sum(p.quantity * prices.get(p.symbol, p.avg_price) for p in positions)
pnl = value - CAPITAL
upnl = sum((prices.get(p.symbol, p.avg_price) - p.avg_price) * p.quantity for p in positions)
record_nav(conn, value)

trend_badge = (f"<span style='color:{PROFIT};font-weight:700'>● RISK-ON</span>" if risk_on
               else f"<span style='color:{LOSS};font-weight:700'>● RISK-OFF (cash)</span>")
st.markdown(f"Strategy status: {trend_badge} &nbsp;·&nbsp; universe {len(history)} names"
            + (f" &nbsp;·&nbsp; {len(dropped)} skipped" if dropped else ""),
            unsafe_allow_html=True)

# ---------- KPIs ----------
k = st.columns(5)
k[0].metric("Portfolio value", f"₹{value:,.0f}", f"{pnl:+,.0f}")
k[1].metric("Total P&L", f"₹{pnl:,.0f}", f"{pnl/CAPITAL*100:+.2f}%")
k[2].metric("Unrealized P&L", f"₹{upnl:,.0f}")
k[3].metric("Cash", f"₹{cash:,.0f}")
k[4].metric("Holdings", str(len(positions)))

left, right = st.columns([1, 1])

# ---------- allocation ----------
with left:
    st.markdown("##### Allocation")
    if positions:
        labels = [p.symbol for p in positions]
        vals = [p.quantity * prices.get(p.symbol, p.avg_price) for p in positions]
        fig = go.Figure(go.Pie(labels=labels, values=vals, hole=0.55,
                               textinfo="label+percent", textposition="inside"))
        fig.update_layout(template="plotly_dark", height=340, showlegend=False,
                          margin=dict(l=8, r=8, t=8, b=8),
                          paper_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
    else:
        st.info("In cash — no holdings (risk-off, or not rebalanced yet).")

# ---------- NAV history ----------
with right:
    st.markdown("##### Portfolio value history (NAV)")
    nav = nav_history(conn)
    if len(nav) >= 2:
        ys = [v for _, v in nav]
        col = PROFIT if ys[-1] >= ys[0] else LOSS
        fig = go.Figure(go.Scatter(x=[d for d, _ in nav], y=ys, mode="lines+markers",
                                   line=dict(color=col, width=2)))
        fig.update_layout(template="plotly_dark", height=340, margin=dict(l=8, r=8, t=8, b=8),
                          paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                          yaxis=dict(gridcolor="#21262d"))
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
    else:
        st.caption("NAV history builds as you run this daily — one point recorded per day.")

# ---------- holdings ----------
st.markdown("##### Current holdings")
if positions:
    rows = []
    for p in sorted(positions, key=lambda z: -z.quantity * prices.get(z.symbol, 0)):
        ltp = prices.get(p.symbol, p.avg_price)
        mv = p.quantity * ltp
        rows.append({"Symbol": p.symbol, "Qty": p.quantity, "Avg": round(p.avg_price, 1),
                     "LTP": round(ltp, 1), "Value ₹": round(mv), "Weight %": round(mv / value * 100, 1),
                     "uPnL ₹": round((ltp - p.avg_price) * p.quantity)})
    st.dataframe(rows, use_container_width=True, hide_index=True)
else:
    st.caption("no holdings")

# ---------- strategy target ----------
st.markdown("##### Strategy target now")
if targets:
    st.caption(f"Low-vol wants these {len(targets)} names, equal-weight "
               f"({list(targets.values())[0]*100:.1f}% each) — press 🔄 Rebalance to match:")
    st.write("  ·  ".join(sorted(targets)))
else:
    st.caption("Trend filter is RISK-OFF (Nifty below 200-day) — target is cash. "
               "Uncheck the trend filter to see the low-vol picks anyway.")
