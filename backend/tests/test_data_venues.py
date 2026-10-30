"""Read-only venues: Tabdeal, Binance, Tetherland, Navasan."""

import httpx
import pytest

from saraf.venues import VenueError, VenueUnsupported
from saraf.venues.binance import BinanceClient
from saraf.venues.rates import NavasanClient, TetherlandClient
from saraf.venues.tabdeal import TabdealClient


def transport(routes: dict, seen: list | None = None):
    def handler(req):
        if seen is not None:
            seen.append(req)
        body = routes.get((req.url.host, req.url.path))
        if body is None:
            return httpx.Response(404, text="Not Found")
        return body if isinstance(body, httpx.Response) else httpx.Response(200, json=body)
    return httpx.MockTransport(handler)


async def test_tabdeal_markets_merge_info_and_prices():
    t = transport({
        ("api1.tabdeal.org", "/r/api/v1/exchangeInfo"): [
            {"symbol": "BTCIRT", "tabdealSymbol": "BTC_IRT", "status": "TRADING", "baseAsset": "BTC", "quoteAsset": "IRT",
             "filters": [{"filterType": "PRICE_FILTER", "tickSize": "1.0000"},
                         {"filterType": "LOT_SIZE", "minQty": "0.000001", "stepSize": "0.00000001"},
                         {"filterType": "MIN_NOTIONAL", "minNotional": "50000.00"}]},
            {"symbol": "XIRT", "tabdealSymbol": "X_IRT", "status": "BREAK", "baseAsset": "X", "quoteAsset": "IRT",
             "filters": []}],
        ("api-web.tabdeal.org", "/r/plots/market_information/"): [
            {"symbol": "BTC_IRT", "price": "21376940688", "high": "22214497905", "low": "20822354526",
             "open": "22051814260", "volume": "9.99", "symbol_fa": "بیت کوین-تومان"}]})
    m = await TabdealClient(transport=t).markets()
    assert list(m) == ["BTCTMN"]
    btc = m["BTCTMN"]
    assert btc["stats"]["lastPrice"] == 21376940688.0 and btc["minNotional"] == 50000.0
    assert btc["stepSize"] == 8 and btc["tickSize"] == 0 and btc["minQty"] == 0.000001
    assert btc["stats"]["24h_ch"] == pytest.approx(-3.06, abs=0.01)


async def test_binance_usdt_markets_only():
    t = transport({
        ("data-api.binance.vision", "/api/v3/ticker/24hr"): [
            {"symbol": "BTCUSDT", "lastPrice": "77400.01", "bidPrice": "77400", "askPrice": "77400.02",
             "priceChangePercent": "-4.7", "highPrice": "81000", "lowPrice": "76500", "volume": "100",
             "quoteVolume": "7740000"},
            {"symbol": "ETHBTC", "lastPrice": "0.028"}],
        ("data-api.binance.vision", "/api/v3/exchangeInfo"): {"symbols": [
            {"symbol": "BTCUSDT", "baseAsset": "BTC", "quoteAsset": "USDT", "status": "TRADING",
             "filters": [{"filterType": "PRICE_FILTER", "tickSize": "0.01000000"},
                         {"filterType": "LOT_SIZE", "stepSize": "0.00001000", "minQty": "0.00001"}]}]}})
    m = await BinanceClient(transport=t).markets()
    assert list(m) == ["BTCUSDT"] and m["BTCUSDT"]["tickSize"] == 2 and m["BTCUSDT"]["stepSize"] == 5
    assert m["BTCUSDT"]["stats"]["askPrice"] == 77400.02


async def test_tetherland_has_no_book():
    t = transport({("api.tetherland.com", "/currencies"): {"status": 200, "data": {"currencies": {"USDT": {
        "price": 274950, "sell_price": 274900, "buy_price": 275000, "diff24d": "1.40", "last24hMin": 270950,
        "last24hMax": 275950}}}}})
    v = TetherlandClient(transport=t)
    m = await v.markets()
    assert m["USDTTMN"]["stats"]["lastPrice"] == 274950.0 and m["USDTTMN"]["stats"]["24h_ch"] == 1.4
    assert v.capabilities.asset_class == "fx" and not v.capabilities.order_book
    with pytest.raises(VenueUnsupported):
        await v.depth("USDTTMN")


async def test_navasan_rates():
    seen = []
    t = transport({("api.navasan.tech", "/latest/"): {
        "usd_sell": {"value": "92500", "change": -500, "timestamp": 1793370000, "date": "1405-08-08 10:00:00"},
        "eur": {"value": "101000", "change": 0}, "sekkeh": {"value": "1050000000", "change": 2000000},
        "18ayar": {"value": "9300000", "change": 0}}}, seen)
    m = await NavasanClient("key1", transport=t).markets()
    assert seen[0].url.params["api_key"] == "key1"
    assert m["USDTMN"]["stats"]["lastPrice"] == 92500.0 and m["USDTMN"]["faBaseAsset"] == "دلار"
    assert m["USDTMN"]["stats"]["24h_ch"] == pytest.approx(-0.54, abs=0.01)
    assert {"EURTMN", "SEKKEHTMN", "GOLD18TMN"} <= set(m)


async def test_navasan_without_key_says_so():
    with pytest.raises(VenueError, match="API key"):
        await NavasanClient("").markets()
