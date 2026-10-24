"""Shared fixtures. `FakeClient` is a deterministic stand-in venue (shaped like Wallex's market data) whose
prices tests can move, so fills, TP/SL and liquidations are exactly reproducible."""

import pytest

from saraf.agent.context import AppContext
from saraf.alerts import AlertStore
from saraf.brokers import PaperFuturesBroker, PaperSpotBroker
from saraf.config import Settings
from saraf.venues import Capabilities, Venue


def make_settings(tmp_path, **over) -> Settings:
    # _env_file=None: tests must never inherit the user's .env (e.g. SPOT_MODE=live)
    base = dict(_env_file=None, llm_api_key="test", llm_base_url="http://fake/v1", data_dir=tmp_path,
                spot_mode="paper", futures_mode="paper", require_confirmation=True, llm_compat_schemas=False,
                max_order_notional_usdt=500, max_order_notional_tmn=50_000_000, max_leverage=10,
                max_open_positions=10, symbol_allowlist="", paper_usdt=1000, paper_tmn=100_000_000)
    base.update(over)
    return Settings(**base)


class FakeClient(Venue):
    name = "wallex"   # stands in for the default venue
    title = "Fake"
    capabilities = Capabilities(asset_class="crypto", futures=True)
    has_key = False   # tests flip it per instance
    SPREAD = 0.002  # 0.2%

    def __init__(self):
        self.prices = {"BTCUSDT": 100.0, "ETHUSDT": 10.0, "BTCTMN": 30_000_000.0, "XYZUSDT": 1.0}
        self.leverage = {"BTCUSDT": 10, "ETHUSDT": 5, "BTCTMN": 10}
        self.books: dict[str, dict] = {}  # per-symbol order book override
        self.has_key = False

    def set_price(self, symbol: str, price: float) -> None:
        self.prices[symbol] = price

    def _bid_ask(self, sym):
        p = self.prices[sym]
        return p * (1 - self.SPREAD / 2), p * (1 + self.SPREAD / 2)

    async def markets(self):
        out = {}
        for sym, p in self.prices.items():
            quote = "TMN" if sym.endswith("TMN") else "USDT"
            bid, ask = self._bid_ask(sym)
            out[sym] = {
                "symbol": sym, "baseAsset": sym[: -len(quote)], "quoteAsset": quote,
                "enBaseAsset": sym[: -len(quote)], "faBaseAsset": "فارسی",
                "stepSize": 6 if quote == "USDT" else 8, "tickSize": 2 if quote == "USDT" else 0,
                "minQty": 1e-6, "minNotional": 1 if quote == "USDT" else 100_000,
                "stats": {"bidPrice": str(bid), "askPrice": str(ask), "lastPrice": str(p), "24h_ch": 1.5,
                          "7d_ch": -2.0, "24h_quoteVolume": str(1000 * p), "24h_highPrice": str(p * 1.05),
                          "24h_lowPrice": str(p * 0.95), "direction": {"BUY": 55, "SELL": 45}},
            }
        return out

    async def futures_markets(self):
        return [{"symbol": s, "max_leverage": self.leverage[s], "price": str(p)}
                for s, p in self.prices.items() if s in self.leverage]

    async def depth(self, symbol):
        if symbol in self.books:
            return self.books[symbol]
        bid, ask = self._bid_ask(symbol)
        return {"ask": [{"price": ask * (1 + i * 0.001), "quantity": 5.0} for i in range(10)],
                "bid": [{"price": bid * (1 - i * 0.001), "quantity": 5.0} for i in range(10)]}

    async def candles(self, symbol, resolution, start, end=None):
        p = self.prices[symbol]
        step = {"1": 60, "60": 3600, "180": 10800, "360": 21600, "720": 43200, "1D": 86400}[resolution]
        end = end or start + 200 * step
        n = min(int((end - start) // step), 500)
        out = []
        for i in range(n):
            c = p * (0.9 + 0.1 * i / max(n - 1, 1))  # steady climb to the current price
            out.append({"t": start + i * step, "o": c * 0.999, "h": c * 1.004, "l": c * 0.996, "c": c, "v": 10.0})
        return out

    async def trades(self, symbol):
        p = self.prices[symbol]
        return [{"symbol": symbol, "quantity": "1", "price": str(p), "sum": str(p), "isBuyOrder": i % 2 == 0,
                 "timestamp": "2026-10-21T10:00:00Z"} for i in range(10)]

    async def aclose(self):
        pass


def make_ctx(settings: Settings, client: FakeClient | None = None) -> AppContext:
    client = client or FakeClient()
    return AppContext(settings, client, PaperSpotBroker(client, settings), PaperFuturesBroker(client, settings),
                      AlertStore(client, settings.data_dir))


@pytest.fixture
def fake():
    return FakeClient()


@pytest.fixture
def ctx(tmp_path, fake):
    return make_ctx(make_settings(tmp_path), fake)
