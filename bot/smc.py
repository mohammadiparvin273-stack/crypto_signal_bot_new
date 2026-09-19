"""
Smart Money Concept: Liquidity Grab, Stop Hunt, Mitigation Block, Breaker Block,
Market Structure (Internal/External).
این‌ها روی خروجی price_action.py (به‌خصوص swing ها و order block ها) سوارن.
"""
import pandas as pd
from bot.price_action import find_swings, detect_order_blocks


def detect_liquidity_grab(df: pd.DataFrame, left=3, right=3, wick_ratio=0.6, confirm_candles=2):
    """
    Liquidity Grab / Stop Hunt: کندلی که فیتیله‌ی بلندی بالاتر از یک سقف (یا پایین‌تر از یک کف) اخیر
    می‌زنه ولی close داخل رنج قبلی برمی‌گرده (یعنی نقدینگی گرفته شده و برگشته - تله برای معامله‌گران خرد).

    نکته‌ی مهم (اصلاح‌شده بعد از بک‌تست): نسخه‌ی قبلی این تابع فقط به یک کندل با فیتیله‌ی بلند
    تکیه می‌کرد که باعث می‌شد بریک‌اوت‌های واقعی (که قیمت واقعاً ادامه می‌ده) به‌اشتباه به‌عنوان
    "تله" تشخیص داده بشن. حالا علاوه بر فیتیله، چند کندل بعدی هم باید واقعاً در جهت برگشت
    حرکت کرده باشن (نه فقط یک کندل)، تا false positive کمتر بشه.
    """
    sw = find_swings(df, left, right)
    grabs = []
    recent_highs = sw.loc[sw["swing_high"]].tail(8)
    recent_lows = sw.loc[sw["swing_low"]].tail(8)
    n = len(df)

    for i in range(len(df) - confirm_candles):
        row = df.iloc[i]
        candle_range = row["high"] - row["low"]
        if candle_range <= 0:
            continue
        upper_wick = row["high"] - max(row["close"], row["open"])
        lower_wick = min(row["close"], row["open"]) - row["low"]
        after = df.iloc[i + 1:i + 1 + confirm_candles]
        if len(after) < confirm_candles:
            continue

        for _, sh in recent_highs.iterrows():
            if sh.name >= i:
                continue
            wick_ok = row["high"] > sh["high"] and row["close"] < sh["high"] and (upper_wick / candle_range) >= wick_ratio
            # تایید: کندل‌های بعدی باید واقعاً پایین‌تر بسته بشن (روند برگشت واقعی، نه فقط یک وقفه)
            confirmed = wick_ok and (after["close"] < row["close"]).all()
            if confirmed:
                grabs.append({"type": "sell_side_liquidity_grab", "index": i, "swept_level": float(sh["high"]),
                              "note": "Stop Hunt بالای سقف قبلی با تایید برگشت - احتمال ادامه‌ی نزول"})

        for _, sl in recent_lows.iterrows():
            if sl.name >= i:
                continue
            wick_ok = row["low"] < sl["low"] and row["close"] > sl["low"] and (lower_wick / candle_range) >= wick_ratio
            confirmed = wick_ok and (after["close"] > row["close"]).all()
            if confirmed:
                grabs.append({"type": "buy_side_liquidity_grab", "index": i, "swept_level": float(sl["low"]),
                              "note": "Stop Hunt پایین کف قبلی با تایید برگشت - احتمال ادامه‌ی صعود"})

    return grabs[-10:]


def detect_breaker_and_mitigation_blocks(df: pd.DataFrame, lookback=80):
    """
    Breaker Block: یک Order Block که شکسته شده و بعداً به عنوان سطح مخالف (نقش عوض‌شده) عمل می‌کنه.
    Mitigation Block: ناحیه‌ای که قیمت برای "جبران" یک حرکت ناگهانی به آن برمی‌گردد قبل از ادامه‌ی روند.
    پیاده‌سازی ساده‌شده: از order block هایی که بعداً قیمت از آن‌ها عبور کرده استفاده می‌کنیم.
    """
    obs = detect_order_blocks(df, lookback=lookback)
    d = df.tail(lookback).reset_index(drop=True)
    breakers, mitigations = [], []

    for ob in obs:
        idx = ob["index"]
        after = d.iloc[idx + 2:] if idx + 2 < len(d) else pd.DataFrame()
        if after.empty:
            continue
        if ob["type"] == "bullish_ob" and (after["close"] < ob["bottom"]).any():
            breakers.append({"type": "bearish_breaker", "top": ob["top"], "bottom": ob["bottom"]})
        elif ob["type"] == "bearish_ob" and (after["close"] > ob["top"]).any():
            breakers.append({"type": "bullish_breaker", "top": ob["top"], "bottom": ob["bottom"]})
        else:
            mitigations.append(ob)

    return {"breaker_blocks": breakers[-5:], "mitigation_blocks": mitigations[-5:]}


def market_structure(df: pd.DataFrame, internal_left=2, internal_right=2, external_left=5, external_right=5):
    """
    External Structure: ساختار روی سوئینگ‌های بزرگ (روند اصلی بازار).
    Internal Structure: ساختار روی سوئینگ‌های کوچک داخل روند اصلی (نوسانات جزئی/entry timing).
    """
    from bot.price_action import detect_bos_choch
    external = detect_bos_choch(df, left=external_left, right=external_right)
    internal = detect_bos_choch(df, left=internal_left, right=internal_right)
    return {
        "external_trend": external["current_trend"],
        "external_last_events": external["events"][-3:],
        "internal_trend": internal["current_trend"],
        "internal_last_events": internal["events"][-3:],
        "aligned": external["current_trend"] == internal["current_trend"],
    }


def full_smc_report(df: pd.DataFrame) -> dict:
    return {
        "liquidity_grabs": detect_liquidity_grab(df),
        "blocks": detect_breaker_and_mitigation_blocks(df),
        "market_structure": market_structure(df),
    }
