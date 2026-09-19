"""
پرایس اکشن حرفه‌ای: حمایت/مقاومت، Swing High/Low، BOS، CHOCH، Order Block، FVG،
Equal High/Low، Premium/Discount Zone، Liquidity Zones.
منطق مبتنی بر تشخیص Swing Point با fractal ساده (n کندل قبل/بعد) است -
همون روشی که اکثر تریدرهای SMC/ICT دستی هم استفاده می‌کنن.
"""
import numpy as np
import pandas as pd


def find_swings(df: pd.DataFrame, left: int = 3, right: int = 3):
    """
    تشخیص Swing High/Low با روش fractal: کندلی swing high است اگه high آن
    از `left` کندل قبل و `right` کندل بعد بزرگتر باشه (و برعکس برای low).
    خروجی: دو ستون بولی swing_high, swing_low اضافه شده به DataFrame (کپی)
    """
    df = df.copy()
    highs = df["high"].values
    lows = df["low"].values
    n = len(df)
    swing_high = np.zeros(n, dtype=bool)
    swing_low = np.zeros(n, dtype=bool)
    for i in range(left, n - right):
        window_h = highs[i - left:i + right + 1]
        window_l = lows[i - left:i + right + 1]
        if highs[i] == window_h.max() and np.argmax(window_h) == left:
            swing_high[i] = True
        if lows[i] == window_l.min() and np.argmin(window_l) == left:
            swing_low[i] = True
    df["swing_high"] = swing_high
    df["swing_low"] = swing_low
    return df


def support_resistance_zones(df: pd.DataFrame, left=3, right=3, tolerance_pct=0.3, max_zones=6):
    """
    خوشه‌بندی Swing Point های نزدیک به هم برای ساخت زون‌های حمایت/مقاومت.
    """
    sw = find_swings(df, left, right)
    highs = sw.loc[sw["swing_high"], "high"].tolist()
    lows = sw.loc[sw["swing_low"], "low"].tolist()

    def cluster(levels):
        levels = sorted(levels)
        zones = []
        for lvl in levels:
            placed = False
            for z in zones:
                if abs(lvl - z["price"]) / z["price"] * 100 <= tolerance_pct:
                    z["touches"] += 1
                    z["price"] = (z["price"] * (z["touches"] - 1) + lvl) / z["touches"]
                    placed = True
                    break
            if not placed:
                zones.append({"price": lvl, "touches": 1})
        zones.sort(key=lambda z: z["touches"], reverse=True)
        return zones[:max_zones]

    return {"resistance": cluster(highs), "support": cluster(lows)}


def detect_bos_choch(df: pd.DataFrame, left=3, right=3):
    """
    Break of Structure (BOS): ادامه‌ی روند - شکست یک سقف/کف در جهت روند غالب.
    Change of Character (CHOCH): برعکس شدن روند - شکست ساختار در خلاف جهت روند قبلی.
    خروجی: لیست رویدادها با ایندکس، نوع و قیمت.
    """
    sw = find_swings(df, left, right)
    events = []
    last_swing_high = None
    last_swing_low = None
    trend = None  # 'up' or 'down'

    for i in range(len(sw)):
        row = sw.iloc[i]
        close = row["close"]

        if last_swing_high is not None and close > last_swing_high["price"]:
            event_type = "BOS_UP" if trend in (None, "up") else "CHOCH_UP"
            events.append({"index": i, "type": event_type, "price": float(close),
                            "broken_level": float(last_swing_high["price"])})
            trend = "up"
            last_swing_high = None

        if last_swing_low is not None and close < last_swing_low["price"]:
            event_type = "BOS_DOWN" if trend in (None, "down") else "CHOCH_DOWN"
            events.append({"index": i, "type": event_type, "price": float(close),
                            "broken_level": float(last_swing_low["price"])})
            trend = "down"
            last_swing_low = None

        if row["swing_high"]:
            last_swing_high = {"index": i, "price": float(row["high"])}
        if row["swing_low"]:
            last_swing_low = {"index": i, "price": float(row["low"])}

    return {"events": events, "current_trend": trend}


def detect_order_blocks(df: pd.DataFrame, lookback=50):
    """
    Order Block ساده‌شده: آخرین کندل نزولی قبل از یک حرکت صعودی قوی (Bullish OB)
    یا آخرین کندل صعودی قبل از یک حرکت نزولی قوی (Bearish OB).
    'حرکت قوی' یعنی رنج کندل بعدی حداقل ۱.۵ برابر میانگین رنج اخیر باشه.
    """
    d = df.tail(lookback).reset_index(drop=True)
    avg_range = (d["high"] - d["low"]).mean()
    blocks = []
    for i in range(1, len(d) - 1):
        cur = d.iloc[i]
        nxt = d.iloc[i + 1]
        next_range = nxt["high"] - nxt["low"]
        is_strong_move = next_range >= 1.5 * avg_range

        if is_strong_move and nxt["close"] > nxt["open"] and cur["close"] < cur["open"]:
            blocks.append({"type": "bullish_ob", "top": float(cur["high"]), "bottom": float(cur["low"]),
                            "index": i})
        if is_strong_move and nxt["close"] < nxt["open"] and cur["close"] > cur["open"]:
            blocks.append({"type": "bearish_ob", "top": float(cur["high"]), "bottom": float(cur["low"]),
                            "index": i})
    return blocks[-10:]  # ۱۰ مورد اخیر کافیه


def detect_fvg(df: pd.DataFrame, lookback=100):
    """
    Fair Value Gap / Imbalance: فاصله‌ی بین high کندل i-1 و low کندل i+1
    (وقتی کندل وسط حرکت قوی یک‌طرفه داشته و گپی جا گذاشته).
    """
    d = df.tail(lookback).reset_index(drop=True)
    gaps = []
    for i in range(1, len(d) - 1):
        prev_c, next_c = d.iloc[i - 1], d.iloc[i + 1]
        if prev_c["high"] < next_c["low"]:
            gaps.append({"type": "bullish_fvg", "top": float(next_c["low"]), "bottom": float(prev_c["high"]),
                         "index": i})
        if prev_c["low"] > next_c["high"]:
            gaps.append({"type": "bearish_fvg", "top": float(prev_c["low"]), "bottom": float(next_c["high"]),
                         "index": i})
    return gaps[-10:]


def detect_equal_highs_lows(df: pd.DataFrame, left=3, right=3, tolerance_pct=0.15):
    """Equal High / Equal Low: دو یا چند سقف/کف تقریباً هم‌سطح -> نشونه‌ی Liquidity Pool"""
    sw = find_swings(df, left, right)
    highs = sw.loc[sw["swing_high"], ["high"]].values.flatten()
    lows = sw.loc[sw["swing_low"], ["low"]].values.flatten()

    def find_equal(levels):
        levels = sorted(levels)
        pairs = []
        for i in range(len(levels) - 1):
            if abs(levels[i] - levels[i + 1]) / levels[i] * 100 <= tolerance_pct:
                pairs.append((float(levels[i]), float(levels[i + 1])))
        return pairs

    return {"equal_highs": find_equal(highs), "equal_lows": find_equal(lows)}


def premium_discount_zone(df: pd.DataFrame, lookback=100):
    """
    Premium / Discount / Equilibrium بر اساس رنج اخیر (فیبوناچی ۰-۱۰۰٪):
    زیر ۴۰٪ = Discount (منطقه‌ی خرید مطلوب) | بالای ۶۰٪ = Premium (منطقه‌ی فروش مطلوب) | بین = Equilibrium
    """
    d = df.tail(lookback)
    high, low = d["high"].max(), d["low"].min()
    close = d["close"].iloc[-1]
    if high == low:
        position_pct = 50.0
    else:
        position_pct = (close - low) / (high - low) * 100
    if position_pct <= 40:
        zone = "discount"
    elif position_pct >= 60:
        zone = "premium"
    else:
        zone = "equilibrium"
    return {"zone": zone, "position_pct": round(float(position_pct), 1), "range_high": float(high), "range_low": float(low)}


def detect_liquidity_zones(df: pd.DataFrame, left=3, right=3):
    """
    Liquidity Zones: نواحی بالای سقف‌های اخیر (buy-side liquidity) و پایین کف‌های اخیر (sell-side liquidity)
    جایی که استاپ‌لاس‌های زیادی معمولاً چیده شده‌اند.
    """
    sw = find_swings(df, left, right)
    recent_highs = sw.loc[sw["swing_high"], "high"].tail(5).tolist()
    recent_lows = sw.loc[sw["swing_low"], "low"].tail(5).tolist()
    return {
        "buy_side_liquidity": [float(x) for x in recent_highs],  # بالای این‌ها = liquidity برای short ها
        "sell_side_liquidity": [float(x) for x in recent_lows],  # پایین این‌ها = liquidity برای long ها
    }


def detect_breakout_retest(df: pd.DataFrame, left=3, right=3, lookback=60, retest_tolerance_pct=0.4):
    """
    Breakout + Retest: یکی از باکیفیت‌ترین الگوهای پرایس‌اکشن.
    ۱. یه سطح حمایت/مقاومت مهم شکسته میشه (با یه حرکت قدرتمند - بسته‌شدن واضح فراتر از سطح)
    ۲. قیمت برمی‌گرده و همون سطح رو "تست" می‌کنه (نزدیک میشه، ولی رد نمیشه)
    ۳. از همونجا دوباره در جهت شکست ادامه می‌ده
    این تابع آخرین Breakout+Retest معتبر (اگه باشه) رو برمی‌گردونه.
    """
    sw = find_swings(df, left, right)
    d = df.tail(lookback).reset_index(drop=True)
    sw_recent = sw.tail(lookback).reset_index(drop=True)

    recent_highs = sw_recent.loc[sw_recent["swing_high"]]
    recent_lows = sw_recent.loc[sw_recent["swing_low"]]

    result = None

    # شکست مقاومت به سمت بالا + ریتست از بالا (bullish)
    for idx, row in recent_highs.iterrows():
        level = row["high"]
        after = d.iloc[idx + 1:]
        breakout_idx = None
        for j in after.index:
            if d.loc[j, "close"] > level * 1.001:  # شکست واضح، نه فقط نوسان جزئی
                breakout_idx = j
                break
        if breakout_idx is None:
            continue
        retest_window = d.iloc[breakout_idx + 1:]
        for j in retest_window.index:
            touched = abs(d.loc[j, "low"] - level) / level * 100 <= retest_tolerance_pct
            held_above = d.loc[j, "close"] >= level * 0.999
            if touched and held_above:
                result = {"type": "bullish_breakout_retest", "level": float(level), "retest_index": j}
                break

    # شکست حمایت به سمت پایین + ریتست از پایین (bearish)
    for idx, row in recent_lows.iterrows():
        level = row["low"]
        after = d.iloc[idx + 1:]
        breakout_idx = None
        for j in after.index:
            if d.loc[j, "close"] < level * 0.999:
                breakout_idx = j
                break
        if breakout_idx is None:
            continue
        retest_window = d.iloc[breakout_idx + 1:]
        for j in retest_window.index:
            touched = abs(d.loc[j, "high"] - level) / level * 100 <= retest_tolerance_pct
            held_below = d.loc[j, "close"] <= level * 1.001
            if touched and held_below:
                candidate = {"type": "bearish_breakout_retest", "level": float(level), "retest_index": j}
                if result is None or candidate["retest_index"] > result["retest_index"]:
                    result = candidate

    return result


def full_price_action_report(df: pd.DataFrame) -> dict:
    """جمع‌بندی کامل همه‌ی تحلیل‌های پرایس‌اکشن برای یک تایم‌فریم"""
    return {
        "structure": detect_bos_choch(df),
        "order_blocks": detect_order_blocks(df),
        "fvg": detect_fvg(df),
        "equal_levels": detect_equal_highs_lows(df),
        "premium_discount": premium_discount_zone(df),
        "liquidity_zones": detect_liquidity_zones(df),
        "support_resistance": support_resistance_zones(df),
        "breakout_retest": detect_breakout_retest(df),
    }
