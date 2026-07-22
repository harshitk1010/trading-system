"""Per-broker credential loading from environment (.env). Paper mode does not
require any of these — they are loaded if present and validated only when a live
phase actually needs them. Names follow each vendor SDK's own docs."""
from __future__ import annotations
import os
from dataclasses import dataclass


def _get(*keys: str) -> dict:
    return {k: os.environ.get(k, "") for k in keys}


@dataclass
class Creds:
    broker: str
    values: dict

    def missing(self) -> list[str]:
        return [k for k, v in self.values.items() if not v]


# KiteConnect: api_key + api_secret -> access_token (daily, via login flow)
def zerodha() -> Creds:
    return Creds("zerodha", _get("KITE_API_KEY", "KITE_API_SECRET", "KITE_ACCESS_TOKEN"))


# upstox-python-sdk: OAuth2; api_key(client id)+secret+redirect -> access_token (daily)
def upstox() -> Creds:
    return Creds("upstox", _get(
        "UPSTOX_API_KEY", "UPSTOX_API_SECRET", "UPSTOX_REDIRECT_URI", "UPSTOX_ACCESS_TOKEN"))


# SmartApi-python (Angel One): api_key + client_code + mpin + TOTP secret -> session
def angelone() -> Creds:
    return Creds("angelone", _get(
        "ANGELONE_API_KEY", "ANGELONE_CLIENT_CODE", "ANGELONE_MPIN", "ANGELONE_TOTP_SECRET"))


# alpaca-py: api_key_id + secret_key + paper flag (base url)
def alpaca() -> Creds:
    return Creds("alpaca", _get("ALPACA_API_KEY", "ALPACA_SECRET_KEY", "ALPACA_PAPER"))


LOADERS = {
    "zerodha": zerodha,
    "upstox": upstox,
    "angelone": angelone,
    "alpaca": alpaca,
}


def load(broker: str) -> Creds:
    if broker not in LOADERS:
        raise ValueError(f"unknown broker: {broker}")
    return LOADERS[broker]()
