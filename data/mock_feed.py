"""Standalone mock market-data feed. Reuses Phase 1's synthetic OHLCV generator
(backtest.synthetic.generate) to pre-roll a long deterministic series per symbol,
then reveals bars one at a time so a running dashboard sees an evolving tape.

No API calls, no credentials. Exposes `quote_source` / `historical_source`
callables with the exact signatures the Broker interface's get_quote /
get_historical use, so the same feed can be injected into any PaperBroker (mock
today; the real adapter supplies its own source in a later live phase)."""
from __future__ import annotations
from brokers.base import Bar, Quote
from backtest.synthetic import generate

WARMUP = 220          # >= strategy.min_bars() (200) so signals are valid immediately
HORIZON = 5000        # pre-rolled future bars to reveal over the session


class MockFeed:
    def __init__(self, symbols=("DEMO",), seed: int = 11,
                 warmup: int = WARMUP, horizon: int = HORIZON):
        self.symbols = tuple(symbols)
        # one independent series per symbol (distinct seeds -> distinct tapes)
        self._series: dict[str, list[Bar]] = {
            s: generate(n=warmup + horizon, seed=seed + i)
            for i, s in enumerate(self.symbols)
        }
        self._cursor = warmup  # index of the next bar to reveal
        self._warmup = warmup

    def advance(self, steps: int = 1) -> None:
        """Reveal the next bar(s). Regenerates a fresh horizon if exhausted."""
        for _ in range(steps):
            self._cursor += 1
            if self._cursor >= len(next(iter(self._series.values()))):
                self._extend()

    def _extend(self) -> None:
        for i, s in enumerate(self.symbols):
            self._series[s] += generate(n=HORIZON, seed=self._cursor + i)

    def bars(self, symbol: str, limit: int | None = None) -> list[Bar]:
        revealed = self._series[symbol][: self._cursor]
        return revealed[-limit:] if limit else revealed

    # ---- Broker-interface-shaped sources (inject into build_broker) ----
    def historical_source(self, symbol: str, interval: str, limit: int) -> list[Bar]:
        return self.bars(symbol, limit)

    def quote_source(self, symbol: str) -> Quote | None:
        b = self.bars(symbol, 1)
        return Quote(symbol, b[-1].close, b[-1].ts) if b else None

    def last_price(self, symbol: str) -> float | None:
        b = self.bars(symbol, 1)
        return b[-1].close if b else None
