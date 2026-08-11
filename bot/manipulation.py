"""
تشخیص الگوهای دستکاری بازار: Fake Breakout, Bull Trap, Bear Trap
(Stop Hunt / Liquidity Sweep در ماژول smc.py پیاده‌سازی شده - اینجا رفرنس داده میشه)
"""
import pandas as pd
from bot.price_action import find_swings
from bot.smc import detect_liquidity_grab


def detect_fake_breakout(df: pd.DataFrame, left=3, right=3, confirm_candles=3):
    """
    Fake Breakout: قیمت از سطح مقاومت/حمایت مهمی عبور می‌کنه ولی ظرف چند کندل برمی‌گرده داخل رنج قبلی
    (یعنی breakout تایید نشده و تله بوده).
    """
    sw = find_swings(df, left, right)
    events = []
    recent_highs = sw.loc[sw["swing_high"]].tail(6)
    recent_lows = sw.loc[sw["swing_low"]].tail(6)
    n = len(df)

    for _, sh in recent_highs.iterrows():
        idx = sh.name
        if idx + confirm_candles >= n:
            continue
        breakout_candle = None
        for j in range(idx + 1, min(idx + 15, n)):
            if df["close"].iloc[j] > sh["high"]:
                breakout_candle = j
                break
        if breakout_candle is not None:
            after = df.iloc[breakout_candle + 1: breakout_candle + 1 + confirm_candles]
            if len(after) and (after["close"] < sh["high"]).all():
                events.append({"type": "bull_trap_fake_breakout", "level": float(sh["high"]), "index": breakout_candle})

    for _, sl in recent_lows.iterrows():
        idx = sl.name
        if idx + confirm_candles >= n:
            continue
        breakdown_candle = None
        for j in range(idx + 1, min(idx + 15, n)):
            if df["close"].iloc[j] < sl["low"]:
                breakdown_candle = j
                break
        if breakdown_candle is not None:
            after = df.iloc[breakdown_candle + 1: breakdown_candle + 1 + confirm_candles]
            if len(after) and (after["close"] > sl["low"]).all():
                events.append({"type": "bear_trap_fake_breakdown", "level": float(sl["low"]), "index": breakdown_candle})

    return events[-10:]


def full_manipulation_report(df: pd.DataFrame) -> dict:
    return {
        "fake_breakouts": detect_fake_breakout(df),
        "stop_hunts_liquidity_sweeps": detect_liquidity_grab(df),
    }
