"""FastAPI surface: customer compliance endpoints + kill switch, and a minimal
server-rendered admin dashboard. All state changes go through tenancy.service, so
the compliance gate and risk/kill rules are enforced in code, not the UI.

No copy in this file promises returns or accuracy — backtest figures are shown as
measured, historical, and explicitly non-indicative of future results."""
from __future__ import annotations
import html
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI, Depends, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from tenancy import store as tstore
from tenancy import service
from tenancy.models import RiskLimits, TOS_VERSION

app = FastAPI(title="Trading Platform — Admin & Compliance")

DISCLAIMER = (
    "Trading involves risk of loss. Backtested figures are historical simulation "
    "results only and are not indicative of future performance. You are solely "
    "responsible for your own trading decisions."
)


def get_db():
    conn = tstore.connect()
    try:
        yield conn
    finally:
        conn.close()


# ---------------- request models ----------------
class CreateCustomer(BaseModel):
    id: str
    name: str
    email: str
    broker: str = "zerodha"
    equity: float = 100_000.0
    watchlist: list[str] = ["DEMO"]


class ModeToggle(BaseModel):
    mode: str


class KillSwitch(BaseModel):
    flatten: bool = False


class RiskUpdate(BaseModel):
    risk_per_trade: float
    stop_loss_pct: float
    take_profit_pct: float
    max_daily_loss_pct: float
    max_position_pct: float


# ---------------- customer / compliance API ----------------
@app.post("/customers")
def create_customer(body: CreateCustomer, conn=Depends(get_db)):
    service.create_customer(conn, body.id, body.name, body.email, body.broker,
                            body.equity, tuple(body.watchlist))
    return {"id": body.id, "mode": "paper", "disclaimer": DISCLAIMER}


@app.post("/customers/{customer_id}/consent")
def accept_tos(customer_id: str, conn=Depends(get_db)):
    if not service.get_customer(conn, customer_id):
        raise HTTPException(404, "unknown customer")
    ts = service.record_consent(conn, customer_id, TOS_VERSION)
    return {"tos_version": TOS_VERSION, "accepted_at": ts}


@app.post("/customers/{customer_id}/backtest-view")
def view_backtest(customer_id: str, conn=Depends(get_db)):
    if not service.get_customer(conn, customer_id):
        raise HTTPException(404, "unknown customer")
    metrics = service.run_backtest(conn, customer_id)
    service.record_backtest_view(conn, customer_id, metrics)
    return {"metrics": metrics, "note": "Historical simulation, not indicative of future results."}


@app.post("/customers/{customer_id}/mode")
def set_mode(customer_id: str, body: ModeToggle, conn=Depends(get_db)):
    if not service.get_customer(conn, customer_id):
        raise HTTPException(404, "unknown customer")
    ok, reason = service.set_mode(conn, customer_id, body.mode, actor="customer")
    if not ok:
        raise HTTPException(status_code=403, detail=f"live mode blocked: {reason}")
    return {"mode": body.mode}


@app.post("/customers/{customer_id}/kill-switch")
def kill_switch(customer_id: str, body: KillSwitch, conn=Depends(get_db)):
    if not service.get_customer(conn, customer_id):
        raise HTTPException(404, "unknown customer")
    flattened = service.kill_switch(conn, customer_id, flatten=body.flatten)
    return {"killed": True, "flattened_positions": flattened}


# ---------------- admin dashboard (server-rendered) ----------------
def _page(title: str, body: str) -> str:
    return (f"<!doctype html><meta charset=utf-8><title>{html.escape(title)}</title>"
            "<style>body{font-family:system-ui,sans-serif;margin:2rem;max-width:960px}"
            "table{border-collapse:collapse;width:100%}th,td{border:1px solid #ccc;"
            "padding:.4rem .6rem;text-align:left;font-size:.9rem}th{background:#f4f4f4}"
            "code{background:#f4f4f4;padding:.1rem .3rem}.muted{color:#666;font-size:.8rem}"
            f"</style><h1>{html.escape(title)}</h1>{body}"
            f"<p class=muted>{html.escape(DISCLAIMER)}</p>")


@app.get("/admin", response_class=HTMLResponse)
def admin_home(conn=Depends(get_db)):
    rows = ["<tr><th>ID</th><th>Name</th><th>Broker</th><th>Mode</th><th>Status</th>"
            "<th>Errors</th><th>ToS</th><th>Backtest</th><th>Audit</th></tr>"]
    for c in service.list_customers(conn):
        rows.append(
            f"<tr><td>{html.escape(c.id)}</td><td>{html.escape(c.name)}</td>"
            f"<td>{html.escape(c.broker)}</td><td>{html.escape(c.mode)}</td>"
            f"<td>{html.escape(c.status)}</td><td>{c.error_count}</td>"
            f"<td>{'yes' if c.tos_accepted_at else 'no'}</td>"
            f"<td>{'yes' if c.backtest_viewed_at else 'no'}</td>"
            f"<td><a href='/admin/customers/{html.escape(c.id)}'>log</a></td></tr>")
    return _page("Customers", f"<table>{''.join(rows)}</table>")


@app.get("/admin/customers/{customer_id}", response_class=HTMLResponse)
def admin_audit(customer_id: str, conn=Depends(get_db)):
    c = service.get_customer(conn, customer_id)
    if not c:
        raise HTTPException(404, "unknown customer")
    rows = ["<tr><th>ID</th><th>Time</th><th>Kind</th><th>Symbol</th><th>Detail</th></tr>"]
    for r in service.get_audit(conn, customer_id, limit=500):
        rows.append(
            f"<tr><td>{r['id']}</td><td>{html.escape(r['ts'])}</td>"
            f"<td>{html.escape(r['kind'])}</td><td>{html.escape(r['symbol'] or '')}</td>"
            f"<td><code>{html.escape(r['detail'] or '')}</code></td></tr>")
    head = (f"<p><b>{html.escape(c.name)}</b> — broker {html.escape(c.broker)}, "
            f"mode {html.escape(c.mode)}, status {html.escape(c.status)}</p>"
            "<form method=post action='suspend' style='display:inline'>"
            f"<button formaction='/admin/customers/{html.escape(customer_id)}/suspend'>Suspend</button></form> "
            "<form method=post style='display:inline'>"
            f"<button formaction='/admin/customers/{html.escape(customer_id)}/resume'>Resume</button></form>")
    return _page(f"Audit — {customer_id}", head + f"<table>{''.join(rows)}</table>")


@app.post("/admin/customers/{customer_id}/suspend")
def admin_suspend(customer_id: str, conn=Depends(get_db)):
    if not service.get_customer(conn, customer_id):
        raise HTTPException(404, "unknown customer")
    service.suspend(conn, customer_id)
    return JSONResponse({"status": "suspended"})


@app.post("/admin/customers/{customer_id}/resume")
def admin_resume(customer_id: str, conn=Depends(get_db)):
    if not service.get_customer(conn, customer_id):
        raise HTTPException(404, "unknown customer")
    service.resume(conn, customer_id)
    return JSONResponse({"status": "active"})


@app.post("/admin/customers/{customer_id}/risk")
def admin_risk(customer_id: str, body: RiskUpdate, conn=Depends(get_db)):
    ok, reason = service.admin_update_risk(conn, customer_id, RiskLimits(**body.model_dump()))
    if not ok:
        raise HTTPException(status_code=403, detail=reason)
    return {"status": "risk_updated"}
