"""
پیاده‌سازی اندیکاتورهای درخواستی. همه با pandas/numpy خالص نوشته شدن (بدون pandas-ta)
تا وابستگی شکننده نداشته باشیم و روی Vercel هم بدون مشکل نصب/اجرا بشه.
ورودی همه‌ی توابع: DataFrame با ستون‌های open/high/low/close/volume
"""
import numpy as np
import pandas as pd


def ema(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(span=length, adjust=False).mean()


def sma(series: pd.Series, length: int) -> pd.Series:
    return series.rolling(length).mean()


def rsi(series: pd.Series, length: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / length, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / length, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100 - (100 / (1 + rs))
    return out.fillna(50)


def macd(series: pd.Series, fast=12, slow=26, signal=9):
    ema_fast = ema(series, fast)
    ema_slow = ema(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = ema(macd_line, signal)
    hist = macd_line - signal_line
    return pd.DataFrame({"macd": macd_line, "signal": signal_line, "hist": hist})


def atr(df: pd.DataFrame, length: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        (high - low),
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / length, adjust=False).mean()


def bollinger_bands(series: pd.Series, length: int = 20, std_mult: float = 2.0):
    mid = sma(series, length)
    std = series.rolling(length).std()
    upper = mid + std_mult * std
    lower = mid - std_mult * std
    return pd.DataFrame({"upper": upper, "mid": mid, "lower": lower})


def supertrend(df: pd.DataFrame, length: int = 10, mult: float = 3.0):
    hl2 = (df["high"] + df["low"]) / 2
    atr_val = atr(df, length)
    upper_band = hl2 + mult * atr_val
    lower_band = hl2 - mult * atr_val
    close = df["close"]

    n = len(df)
    trend = np.ones(n)
    final_upper = upper_band.copy()
    final_lower = lower_band.copy()

    for i in range(1, n):
        if close.iloc[i - 1] > final_upper.iloc[i - 1]:
            trend[i] = 1
        elif close.iloc[i - 1] < final_lower.iloc[i - 1]:
            trend[i] = -1
        else:
            trend[i] = trend[i - 1]
            if trend[i] == 1 and lower_band.iloc[i] < final_lower.iloc[i - 1]:
                final_lower.iloc[i] = final_lower.iloc[i - 1]
            if trend[i] == -1 and upper_band.iloc[i] > final_upper.iloc[i - 1]:
                final_upper.iloc[i] = final_upper.iloc[i - 1]

    direction = pd.Series(trend, index=df.index)
    line = pd.Series(np.where(direction == 1, final_lower, final_upper), index=df.index)
    return pd.DataFrame({"supertrend": line, "direction": direction})


def adx(df: pd.DataFrame, length: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    tr = atr(df, length) * length  # true range تقریبی معادل با روش وایلدر
    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(alpha=1 / length, adjust=False).mean() / tr.replace(0, np.nan)
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=1 / length, adjust=False).mean() / tr.replace(0, np.nan)
    dx = (abs(plus_di - minus_di) / (plus_di + minus_di).replace(0, np.nan)) * 100
    return dx.ewm(alpha=1 / length, adjust=False).mean().fillna(0)


def ichimoku(df: pd.DataFrame, tenkan=9, kijun=26, senkou_b=52):
    high, low, close = df["high"], df["low"], df["close"]
    tenkan_sen = (high.rolling(tenkan).max() + low.rolling(tenkan).min()) / 2
    kijun_sen = (high.rolling(kijun).max() + low.rolling(kijun).min()) / 2
    senkou_span_a = ((tenkan_sen + kijun_sen) / 2).shift(kijun)
    senkou_span_b = ((high.rolling(senkou_b).max() + low.rolling(senkou_b).min()) / 2).shift(kijun)
    chikou = close.shift(-kijun)
    return pd.DataFrame({
        "tenkan": tenkan_sen, "kijun": kijun_sen,
        "senkou_a": senkou_span_a, "senkou_b": senkou_span_b, "chikou": chikou,
    })


def obv(df: pd.DataFrame) -> pd.Series:
    direction = np.sign(df["close"].diff()).fillna(0)
    return (direction * df["volume"]).cumsum()


def cmf(df: pd.DataFrame, length: int = 20) -> pd.Series:
    mf_multiplier = ((df["close"] - df["low"]) - (df["high"] - df["close"])) / (df["high"] - df["low"]).replace(0, np.nan)
    mf_volume = mf_multiplier * df["volume"]
    return (mf_volume.rolling(length).sum() / df["volume"].rolling(length).sum()).fillna(0)


def stochastic_rsi(series: pd.Series, rsi_length=14, stoch_length=14, k=3, d=3):
    rsi_val = rsi(series, rsi_length)
    min_rsi = rsi_val.rolling(stoch_length).min()
    max_rsi = rsi_val.rolling(stoch_length).max()
    stoch = ((rsi_val - min_rsi) / (max_rsi - min_rsi).replace(0, np.nan)) * 100
    k_line = stoch.rolling(k).mean()
    d_line = k_line.rolling(d).mean()
    return pd.DataFrame({"k": k_line.fillna(50), "d": d_line.fillna(50)})


def vwap(df: pd.DataFrame) -> pd.Series:
    typical = (df["high"] + df["low"] + df["close"]) / 3
    cum_vol = df["volume"].cumsum().replace(0, np.nan)
    return (typical * df["volume"]).cumsum() / cum_vol


def compute_all_indicators(df: pd.DataFrame) -> dict:
    """
    خروجی: دیکشنری شامل آخرین مقدار همه‌ی اندیکاتورها + چند مقدار قبلی برای تشخیص کراس‌اوور
    """
    close = df["close"]
    out = {}
    out["ema_20"] = ema(close, 20)
    out["ema_50"] = ema(close, 50)
    out["ema_200"] = ema(close, 200)
    out["sma_50"] = sma(close, 50)
    out["rsi_14"] = rsi(close, 14)
    macd_df = macd(close)
    out["macd"] = macd_df["macd"]
    out["macd_signal"] = macd_df["signal"]
    out["macd_hist"] = macd_df["hist"]
    out["atr_14"] = atr(df, 14)
    bb = bollinger_bands(close)
    out["bb_upper"] = bb["upper"]
    out["bb_lower"] = bb["lower"]
    st = supertrend(df)
    out["supertrend"] = st["supertrend"]
    out["supertrend_dir"] = st["direction"]
    out["adx_14"] = adx(df, 14)
    ich = ichimoku(df)
    out["ichimoku_tenkan"] = ich["tenkan"]
    out["ichimoku_kijun"] = ich["kijun"]
    out["ichimoku_senkou_a"] = ich["senkou_a"]
    out["ichimoku_senkou_b"] = ich["senkou_b"]
    out["obv"] = obv(df)
    out["cmf_20"] = cmf(df, 20)
    srsi = stochastic_rsi(close)
    out["stoch_rsi_k"] = srsi["k"]
    out["stoch_rsi_d"] = srsi["d"]
    out["vwap"] = vwap(df)
    return out
