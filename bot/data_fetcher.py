"""
دریافت داده‌ی قیمت/حجم از صرافی‌ها با ccxt (بدون نیاز به API Key چون فقط دیتای عمومی می‌خوایم)
همچنین دیتای مشتقه (Funding Rate / Open Interest / Long-Short Ratio) که رایگان در دسترسه.
"""
import time
import logging
import pandas as pd
import ccxt

logger = logging.getLogger("data_fetcher")

_EXCHANGE_CACHE = {}


def get_exchange(name: str):
    if name not in _EXCHANGE_CACHE:
        klass = getattr(ccxt, name)
        _EXCHANGE_CACHE[name] = klass({
            "enableRateLimit": True,
            "timeout": 15000,
        })
    return _EXCHANGE_CACHE[name]


def fetch_ohlcv(symbol: str, timeframe: str, limit: int = 300, exchange_name: str = "binance") -> pd.DataFrame:
    """
    برمی‌گردونه یک DataFrame با ستون‌های: timestamp, open, high, low, close, volume
    اگه صرافی اصلی جواب نداد، فال‌بک به صرافی‌های دیگه می‌زنه.
    """
    fallback_order = [exchange_name] + [e for e in ["binance", "bybit", "okx", "kucoin"] if e != exchange_name]
    last_err = None
    for ex_name in fallback_order:
        try:
            ex = get_exchange(ex_name)
            raw = ex.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
            if not raw or len(raw) < 10:
                continue
            df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
            df["exchange"] = ex_name
            return df
        except Exception as e:  # noqa
            last_err = e
            logger.warning("fetch_ohlcv failed on %s for %s %s: %s", ex_name, symbol, timeframe, e)
            time.sleep(0.2)
            continue
    raise RuntimeError(f"داده‌ای برای {symbol} {timeframe} از هیچ صرافی‌ای دریافت نشد: {last_err}")


def fetch_multi_timeframe(symbol: str, timeframes, limit: int = 300, exchange_name: str = "binance") -> dict:
    """دیکشنری {timeframe: DataFrame} برای تحلیل چندتایم‌فریمی"""
    result = {}
    for tf in timeframes:
        try:
            result[tf] = fetch_ohlcv(symbol, tf, limit=limit, exchange_name=exchange_name)
        except Exception as e:
            logger.error("timeframe %s skipped for %s: %s", tf, symbol, e)
    return result


def fetch_funding_rate(symbol: str, exchange_name: str = "binance") -> float | None:
    """Funding Rate فعلی (فقط بازار فیوچرز). رایگانه و از طریق ccxt در دسترسه."""
    try:
        ex_klass = getattr(ccxt, exchange_name)
        ex = ex_klass({"enableRateLimit": True, "options": {"defaultType": "future"}})
        fr = ex.fetch_funding_rate(symbol)
        return float(fr.get("fundingRate")) if fr and fr.get("fundingRate") is not None else None
    except Exception as e:
        logger.warning("funding rate unavailable for %s: %s", symbol, e)
        return None


def fetch_open_interest(symbol: str, exchange_name: str = "binance") -> float | None:
    """Open Interest فعلی. رایگانه."""
    try:
        ex_klass = getattr(ccxt, exchange_name)
        ex = ex_klass({"enableRateLimit": True, "options": {"defaultType": "future"}})
        oi = ex.fetch_open_interest(symbol)
        return float(oi.get("openInterestAmount") or oi.get("openInterestValue") or 0) if oi else None
    except Exception as e:
        logger.warning("open interest unavailable for %s: %s", symbol, e)
        return None


def fetch_long_short_ratio(symbol: str) -> float | None:
    """
    Binance Futures 'Top Trader Long/Short Ratio' - endpoint عمومی رایگان (بدون نیاز به کلید)
    """
    import requests
    try:
        base = symbol.split("/")[0]
        pair = f"{base}USDT"
        url = "https://fapi.binance.com/futures/data/topLongShortAccountRatio"
        resp = requests.get(url, params={"symbol": pair, "period": "1h", "limit": 1}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if data:
            return float(data[-1]["longShortRatio"])
    except Exception as e:
        logger.warning("long/short ratio unavailable for %s: %s", symbol, e)
    return None


def fetch_order_book(symbol: str, exchange_name: str = "binance", depth: int = 50):
    """برای تخمین عدم‌تعادل خرید/فروش (جایگزین رایگان Order Flow واقعی)"""
    try:
        ex = get_exchange(exchange_name)
        ob = ex.fetch_order_book(symbol, limit=depth)
        return ob
    except Exception as e:
        logger.warning("order book unavailable for %s: %s", symbol, e)
        return None
