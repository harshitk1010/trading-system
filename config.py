"""Config + .env loading and the broker factory. Zero external deps: a tiny YAML
subset reader (flat key: value + one simple list) and a KEY=VALUE .env reader.
The engine/strategy/risk code never imports this — only the entrypoints do, so
routing stays at the edge."""
from __future__ import annotations
import os
from pathlib import Path
from dataclasses import dataclass

ROOT = Path(__file__).parent
CONFIG_PATH = ROOT / "config.yaml"
ENV_PATH = ROOT / ".env"


def load_env(path: Path | str = ENV_PATH) -> None:
    """Read KEY=VALUE lines into os.environ (does not overwrite existing)."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        os.environ.setdefault(key, val)


def _coerce(v: str):
    v = v.strip().strip('"').strip("'")
    if v.lower() in ("true", "false"):
        return v.lower() == "true"
    try:
        return int(v)
    except ValueError:
        try:
            return float(v)
        except ValueError:
            return v


@dataclass
class Config:
    broker: str = "zerodha"
    mode: str = "paper"
    equity: float = 100_000.0
    interval: str = "day"
    watchlist: tuple = ("DEMO",)


def load_config(path: Path | str = CONFIG_PATH) -> Config:
    """Parse the flat config.yaml (key: value plus a `watchlist:` list)."""
    p = Path(path)
    data: dict = {}
    if p.exists():
        current_list_key = None
        for raw in p.read_text().splitlines():
            line = raw.split("#", 1)[0].rstrip()
            if not line.strip():
                continue
            if line.startswith("  - ") or line.startswith("- "):
                if current_list_key:
                    data.setdefault(current_list_key, []).append(_coerce(line.split("-", 1)[1]))
                continue
            if ":" in line and not line.startswith(" "):
                key, _, val = line.partition(":")
                key, val = key.strip(), val.strip()
                if val == "":
                    current_list_key = key
                    data[key] = []
                else:
                    current_list_key = None
                    data[key] = _coerce(val)
    wl = data.get("watchlist") or ["DEMO"]
    return Config(
        broker=str(data.get("broker", "zerodha")),
        mode=str(data.get("mode", "paper")),
        equity=float(data.get("equity", 100_000)),
        interval=str(data.get("interval", "day")),
        watchlist=tuple(wl),
    )


def build_broker(name: str, quote_source=None, historical_source=None):
    """Factory: map broker name -> adapter instance. New brokers register here."""
    from brokers.zerodha import ZerodhaBroker
    from brokers.upstox import UpstoxBroker
    from brokers.angelone import AngelOneBroker
    from brokers.alpaca import AlpacaBroker
    registry = {
        "zerodha": ZerodhaBroker,
        "upstox": UpstoxBroker,
        "angelone": AngelOneBroker,
        "alpaca": AlpacaBroker,
    }
    if name not in registry:
        raise ValueError(f"unknown broker '{name}' — choose one of {list(registry)}")
    return registry[name](quote_source=quote_source, historical_source=historical_source)
