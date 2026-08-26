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
        ex = klass({
            "enableRateLimit": True,
            "timeout": 15000,
        })
        _EXCHANGE_CACHE[name] = ex
    return _EXCHANGE_CACHE[name]


def _ensure_markets_loaded(ex):
    """
    برخی صرافی‌ها (مثلاً OKX) اگه قبل از fetch_ohlcv مارکت‌ها لود نشده باشن،
    با خطای داخلی مبهم (NoneType) مواجه میشن. اینجا صریحاً لودشون می‌کنیم.
    """
    try:
        if not ex.markets:
            ex.load_markets()
    except Exception as e:  # noqa
        logger.warning("load_markets failed for %s: %s", ex.id, e)
        raise


def fetch_ohlcv(symbol: str, timeframe: str, limit: int = 300, exchange_name: str = "kucoin") -> pd.DataFrame:
    """
    برمی‌گردونه یک DataFrame با ستون‌های: timestamp, open, high, low, close, volume
    اگه صرافی اصلی جواب نداد، فال‌بک به صرافی‌های دیگه می‌زنه.
    نکته: بایننس/بایبیت دسترسی از IP آمریکا رو مسدود می‌کنن (به‌خاطر قوانین خودشون)؛
    با تنظیم region روی fra1 در vercel.json این مشکل معمولاً رفع میشه، ولی fallback
    به okx/kucoin هم به‌عنوان محافظ اضافه نگه داشته شده.
    """
    fallback_order = [exchange_name] + [e for e in ["kucoin", "okx", "binance", "bybit"] if e != exchange_name]
    last_err = None
    for ex_name in fallback_order:
        try:
            ex = get_exchange(ex_name)
            _ensure_markets_loaded(ex)
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


def fetch_multi_timeframe(symbol: str, timeframes, limit: int = 300, exchange_name: str = "kucoin") -> dict:
    """دیکشنری {timeframe: DataFrame} برای تحلیل چندتایم‌فریمی"""
    result = {}
    for tf in timeframes:
        try:
            result[tf] = fetch_ohlcv(symbol, tf, limit=limit, exchange_name=exchange_name)
        except Exception as e:
            logger.error("timeframe %s skipped for %s: %s", tf, symbol, e)
    return result


def fetch_funding_rate(symbol: str, exchange_name: str = "okx") -> float | None:
    """Funding Rate فعلی (فقط بازار فیوچرز). چون بایننس از IP آمریکا بلاکه، چند صرافی امتحان میشه."""
    for ex_name in [exchange_name, "okx", "bybit", "binance"]:
        try:
            ex_klass = getattr(ccxt, ex_name)
            ex = ex_klass({"enableRateLimit": True, "timeout": 15000, "options": {"defaultType": "future"}})
            fr = ex.fetch_funding_rate(symbol)
            if fr and fr.get("fundingRate") is not None:
                return float(fr.get("fundingRate"))
        except Exception as e:
            logger.warning("funding rate unavailable on %s for %s: %s", ex_name, symbol, e)
            continue
    return None


def fetch_open_interest(symbol: str, exchange_name: str = "okx") -> float | None:
    """Open Interest فعلی. چون بایننس از IP آمریکا بلاکه، چند صرافی امتحان میشه."""
    for ex_name in [exchange_name, "okx", "bybit", "binance"]:
        try:
            ex_klass = getattr(ccxt, ex_name)
            ex = ex_klass({"enableRateLimit": True, "timeout": 15000, "options": {"defaultType": "future"}})
            oi = ex.fetch_open_interest(symbol)
            if oi:
                return float(oi.get("openInterestAmount") or oi.get("openInterestValue") or 0)
        except Exception as e:
            logger.warning("open interest unavailable on %s for %s: %s", ex_name, symbol, e)
            continue
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


def fetch_current_price(symbol: str) -> float | None:
    """قیمت لحظه‌ای (برای چک کردن اینکه TP یا SL خورده یا نه)"""
    for ex_name in ["kucoin", "okx", "binance", "bybit"]:
        try:
            ex = get_exchange(ex_name)
            _ensure_markets_loaded(ex)
            ticker = ex.fetch_ticker(symbol)
            if ticker and ticker.get("last"):
                return float(ticker["last"])
        except Exception as e:
            logger.warning("fetch_ticker failed on %s for %s: %s", ex_name, symbol, e)
            continue
    return None


def fetch_last_price(symbol: str, exchange_name: str = "kucoin") -> float | None:
    """دریافت سریع آخرین قیمت (بدون کندل کامل) - برای بررسی برخورد به TP/SL"""
    for ex_name in [exchange_name, "okx", "binance", "bybit"]:
        try:
            ex = get_exchange(ex_name)
            ticker = ex.fetch_ticker(symbol)
            if ticker and ticker.get("last"):
                return float(ticker["last"])
        except Exception as e:
            logger.warning("fetch_ticker failed on %s for %s: %s", ex_name, symbol, e)
            continue
    return None


def fetch_order_book(symbol: str, exchange_name: str = "kucoin", depth: int = 50):
    """برای تخمین عدم‌تعادل خرید/فروش (جایگزین رایگان Order Flow واقعی)"""
    try:
        ex = get_exchange(exchange_name)
        ob = ex.fetch_order_book(symbol, limit=depth)
        return ob
    except Exception as e:
        logger.warning("order book unavailable for %s: %s", symbol, e)
        return None


def compute_order_book_imbalance(symbol: str, exchange_name: str = "kucoin", depth: int = 50) -> float | None:
    """
    عدم‌تعادل دفتر سفارش: نسبت حجم سفارش‌های خرید (bid) به کل حجم (bid+ask) در یک عمق مشخص.
    خروجی بین -1 (فشار فروش شدید) تا +1 (فشار خرید شدید). این نسخه‌ی رایگان Order Flow واقعیه
    (که پولیه) - یه عکس لحظه‌ای از دفتر سفارش، نه جریان تیک‌به‌تیک واقعی.
    """
    ob = fetch_order_book(symbol, exchange_name, depth)
    if not ob or not ob.get("bids") or not ob.get("asks"):
        return None
    try:
        bid_volume = sum(bid[1] for bid in ob["bids"])
        ask_volume = sum(ask[1] for ask in ob["asks"])
        total = bid_volume + ask_volume
        if total == 0:
            return None
        return round((bid_volume - ask_volume) / total, 3)
    except Exception as e:
        logger.warning("order book imbalance calc failed for %s: %s", symbol, e)
        return None
