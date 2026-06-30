import time
import logging
import urllib.request
import json

BASE_URL = "https://api.coingecko.com/api/v3"
_cache = {}
_cache_ts = {}

def _fetch(url, cache_sec=30):
    now = time.time()
    if url in _cache_ts and now - _cache_ts.get(url, 0) < cache_sec:
        return _cache[url]
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode())
        _cache[url] = data
        _cache_ts[url] = now
        return data
    except Exception as e:
        logging.error(f"CoinGecko error: {e}")
        return _cache.get(url)

def get_crypto_price(symbol):
    """Get price data for a crypto symbol (e.g. bitcoin, ethereum, solana)"""
    cg_id = _to_cg_id(symbol)
    data = _fetch(f"{BASE_URL}/simple/price?ids={cg_id}&vs_currencies=usd&include_24hr_change=true&include_market_cap=true&include_24hr_vol=true", cache_sec=15)
    coin = data.get(cg_id, {})
    if not coin:
        return None
    return {
        "symbol": symbol.upper(),
        "price": coin.get("usd"),
        "change_24h": coin.get("usd_24h_change"),
        "change_pct_24h": coin.get("usd_24h_change"),
        "market_cap": coin.get("usd_market_cap"),
        "volume_24h": coin.get("usd_24h_vol"),
        "source": "coingecko",
        "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

def get_top_crypto(limit=15):
    """Get top cryptocurrencies by market cap"""
    data = _fetch(f"{BASE_URL}/coins/markets?vs_currency=usd&order=market_cap_desc&per_page={limit}&page=1&sparkline=false&price_change_percentage=24h", cache_sec=30)
    if not data:
        return []
    coins = []
    for c in data:
        coins.append({
            "symbol": c.get("symbol", "").upper(),
            "name": c.get("name", ""),
            "price": c.get("current_price"),
            "change_pct_24h": c.get("price_change_percentage_24h"),
            "market_cap": c.get("market_cap"),
            "volume_24h": c.get("total_volume"),
            "image": c.get("image"),
        })
    return coins

def get_crypto_chart(symbol, days=7):
    """Get price history for chart"""
    cg_id = _to_cg_id(symbol)
    data = _fetch(f"{BASE_URL}/coins/{cg_id}/market_chart?vs_currency=usd&days={days}", cache_sec=120)
    if not data or "prices" not in data:
        return None
    prices = [{"time": p[0] / 1000, "price": p[1]} for p in data.get("prices", [])]
    return {"prices": prices}

def _to_cg_id(symbol):
    s = symbol.lower().strip()
    if s == "btc" or s == "bitcoin" or s == "btc-usd": return "bitcoin"
    if s == "eth" or s == "ethereum" or s == "eth-usd": return "ethereum"
    if s == "sol" or s == "solana" or s == "sol-usd": return "solana"
    if s == "xrp" or s == "xrp-usd": return "ripple"
    if s == "ada" or s == "cardano" or s == "ada-usd": return "cardano"
    if s == "doge" or s == "dogecoin" or s == "doge-usd": return "dogecoin"
    if s == "dot" or s == "polkadot" or s == "dot-usd": return "polkadot"
    if s == "matic" or s == "polygon" or s == "matic-usd": return "matic-network"
    if s == "bnb" or s == "binancecoin" or s == "bnb-usd": return "binancecoin"
    if s == "ltc" or s == "litecoin" or s == "ltc-usd": return "litecoin"
    if s == "link" or s == "chainlink" or s == "link-usd": return "chainlink"
    if s == "avax" or s == "avalanche" or s == "avax-usd": return "avalanche-2"
    if s == "uni" or s == "uniswap" or s == "uni-usd": return "uniswap"
    if s == "atom" or s == "cosmos" or s == "atom-usd": return "cosmos"
    if s == "trx" or s == "tron" or s == "trx-usd": return "tron"
    if s == "near" or s == "near-usd": return "near"
    return s.replace(" ", "-").lower()
