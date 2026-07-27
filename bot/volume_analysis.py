"""
تحلیل حجم معاملات. توجه: Delta Volume واقعی نیاز به دیتای Bid/Ask تفکیک‌شده (تیک به تیک) داره
که رایگان به صورت کامل در دسترس نیست؛ اینجا از یک "تخمین دلتا" مبتنی بر جهت و رنج کندل استفاده شده
(روش رایج در نبود دیتای تیک - taker buy/sell ratio از OHLCV تخمین زده میشه).
"""
import numpy as np
import pandas as pd


def volume_spike(df: pd.DataFrame, length: int = 20, z_threshold: float = 2.0):
    """Volume Spike: حجمی که به طور معناداری (z-score) از میانگین اخیر بیشتره"""
    vol = df["volume"]
    mean = vol.rolling(length).mean()
    std = vol.rolling(length).std()
    z = (vol - mean) / std.replace(0, np.nan)
    df = df.copy()
    df["volume_z"] = z
    spikes = df[df["volume_z"] >= z_threshold]
    return {
        "is_current_spike": bool(len(df) and df["volume_z"].iloc[-1] >= z_threshold),
        "current_z": float(df["volume_z"].iloc[-1]) if len(df) else 0.0,
        "recent_spikes_count": int(len(spikes.tail(20))),
    }


def estimated_delta_volume(df: pd.DataFrame):
    """
    تخمین Delta Volume از روی OHLCV (بدون دیتای تیک):
    فرض: نسبت close نسبت به رنج کندل، تقریب خوبی از فشار خرید/فروش در آن کندل است.
    delta_i = volume_i * ((close_i - low_i) - (high_i - close_i)) / (high_i - low_i)
    """
    rng = (df["high"] - df["low"]).replace(0, np.nan)
    buy_pressure = ((df["close"] - df["low"]) - (df["high"] - df["close"])) / rng
    delta = (buy_pressure * df["volume"]).fillna(0)
    cumulative_delta = delta.cumsum()
    return {
        "last_delta": float(delta.iloc[-1]) if len(delta) else 0.0,
        "cumulative_delta": float(cumulative_delta.iloc[-1]) if len(cumulative_delta) else 0.0,
        "delta_series_tail": delta.tail(20).tolist(),
    }


def volume_profile(df: pd.DataFrame, bins: int = 24, lookback: int = 200):
    """
    Volume Profile / VPVR: تقسیم رنج قیمتی به `bins` باکت و جمع حجم هر باکت.
    POC (Point of Control) = باکت با بیشترین حجم.
    HVN (High Volume Node) = باکت‌های با حجم بالا (بالای میانگین + انحراف معیار).
    LVN (Low Volume Node) = باکت‌های با حجم پایین (زیر میانگین - انحراف معیار) -> مناطق عبور سریع قیمت.
    """
    d = df.tail(lookback)
    price_min, price_max = d["low"].min(), d["high"].max()
    if price_max == price_min:
        return None
    edges = np.linspace(price_min, price_max, bins + 1)
    vol_per_bin = np.zeros(bins)

    for _, row in d.iterrows():
        low, high, vol = row["low"], row["high"], row["volume"]
        candle_range = high - low
        if candle_range <= 0:
            idx = np.searchsorted(edges, row["close"]) - 1
            idx = min(max(idx, 0), bins - 1)
            vol_per_bin[idx] += vol
            continue
        # پخش حجم کندل به نسبت هم‌پوشانی با هر باکت
        for b in range(bins):
            b_low, b_high = edges[b], edges[b + 1]
            overlap = max(0, min(high, b_high) - max(low, b_low))
            if overlap > 0:
                vol_per_bin[b] += vol * (overlap / candle_range)

    poc_idx = int(np.argmax(vol_per_bin))
    mean_v, std_v = vol_per_bin.mean(), vol_per_bin.std()
    hvn = [i for i in range(bins) if vol_per_bin[i] >= mean_v + 0.5 * std_v]
    lvn = [i for i in range(bins) if vol_per_bin[i] <= mean_v - 0.5 * std_v]

    def bin_mid(i):
        return float((edges[i] + edges[i + 1]) / 2)

    return {
        "poc_price": bin_mid(poc_idx),
        "hvn_prices": [bin_mid(i) for i in hvn],
        "lvn_prices": [bin_mid(i) for i in lvn],
    }


def full_volume_report(df: pd.DataFrame) -> dict:
    return {
        "volume_spike": volume_spike(df),
        "delta": estimated_delta_volume(df),
        "profile": volume_profile(df),
    }
