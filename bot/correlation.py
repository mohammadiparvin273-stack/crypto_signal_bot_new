"""
تحلیل همبستگی BTC/ETH با NASDAQ, S&P500, DXY, Gold, Oil.
از yfinance استفاده میشه (رایگان، بدون کلید) - توجه: یاهو فایننس گاهی روی سرورلس محدودیت نرخ داره،
پس این ماژول کاملا try/except محافظت‌شده و در صورت شکست، None برمی‌گردونه (بدون کرش کل ربات).
"""
import logging
import pandas as pd

logger = logging.getLogger("correlation")

TICKERS = {
    "nasdaq": "^IXIC",
    "sp500": "^GSPC",
    "dxy": "DX-Y.NYB",
    "gold": "GC=F",
    "oil": "CL=F",
}


def fetch_correlation_asset(ticker: str, period="3mo", interval="1d"):
    try:
        import yfinance as yf
        data = yf.download(ticker, period=period, interval=interval, progress=False)
        if data is None or data.empty:
            return None
        return data["Close"]
    except Exception as e:
        logger.warning("yfinance fetch failed for %s: %s", ticker, e)
        return None


def compute_correlation(crypto_close: pd.Series, other_close: pd.Series) -> float | None:
    try:
        df = pd.DataFrame({"crypto": crypto_close.values[-len(other_close):] if len(crypto_close) > len(other_close) else crypto_close.values,
                            "other": other_close.values[-len(crypto_close):] if len(other_close) > len(crypto_close) else other_close.values})
        df = df.dropna()
        if len(df) < 10:
            return None
        return round(float(df["crypto"].pct_change().corr(df["other"].pct_change())), 3)
    except Exception as e:
        logger.warning("correlation compute failed: %s", e)
        return None


def full_correlation_report(crypto_daily_close: pd.Series) -> dict:
    report = {}
    for name, ticker in TICKERS.items():
        other = fetch_correlation_asset(ticker)
        report[name] = compute_correlation(crypto_daily_close, other) if other is not None else None
    return report
