"""
مفاهیم ICT: Judas Swing, Kill Zones, Power of Three, SMT Divergence (تقریبی),
Optimal Trade Entry (OTE), Dealing Range.
Kill Zone ها بر مبنای ساعت UTC هستند (استاندارد بین‌المللی ICT).
"""
import pandas as pd
from bot.price_action import find_swings

# بازه‌های Kill Zone به وقت UTC
KILL_ZONES_UTC = {
    "asian_kill_zone": (0, 3),      # نشست آسیا
    "london_kill_zone": (7, 10),    # نشست لندن (نقدینگی بالا)
    "ny_am_kill_zone": (12, 15),    # نشست نیویورک صبح (اوج نوسان معمولا هم‌زمان با باز شدن بازار سهام آمریکا)
    "ny_pm_kill_zone": (18, 20),    # نشست نیویورک بعدازظهر
}


def current_kill_zone(now_utc_hour: int) -> str | None:
    for name, (start, end) in KILL_ZONES_UTC.items():
        if start <= now_utc_hour < end:
            return name
    return None


def detect_judas_swing(df: pd.DataFrame, session_candles: int = 12):
    """
    Judas Swing: حرکت فریبنده در ابتدای نشست معاملاتی (معمولا لندن) که جهت واقعی روز رو
    برعکس نشون میده و سپس بازار جهت واقعی (Power of Three) رو دنبال می‌کنه.
    تشخیص ساده: اولین N کندل نشست در یک جهت حرکت کنه، بعد جهت برعکس بشه و از ابتدای رنج عبور کنه.
    """
    if len(df) < session_candles * 2:
        return None
    session = df.tail(session_candles * 2).reset_index(drop=True)
    first_half = session.iloc[:session_candles]
    second_half = session.iloc[session_candles:]

    first_move = first_half["close"].iloc[-1] - first_half["close"].iloc[0]
    second_move = second_half["close"].iloc[-1] - second_half["close"].iloc[0]

    if first_move > 0 and second_move < 0 and second_half["low"].min() < first_half["low"].min():
        return {"type": "judas_swing_bearish", "note": "حرکت اولیه‌ی صعودی فیک بود؛ روند واقعی نزولیه"}
    if first_move < 0 and second_move > 0 and second_half["high"].max() > first_half["high"].max():
        return {"type": "judas_swing_bullish", "note": "حرکت اولیه‌ی نزولی فیک بود؛ روند واقعی صعودیه"}
    return None


def detect_power_of_three(df: pd.DataFrame, session_candles: int = 24):
    """
    Power of Three (AMD): Accumulation (تثبیت) -> Manipulation (فریب/liquidity grab) -> Distribution (حرکت اصلی)
    تشخیص تقریبی بر مبنای: رنج فشرده در ابتدا -> شکست کوتاه یک طرف -> حرکت قوی طرف مقابل.
    """
    if len(df) < session_candles:
        return None
    d = df.tail(session_candles).reset_index(drop=True)
    third = session_candles // 3
    accumulation = d.iloc[:third]
    manipulation = d.iloc[third:2 * third]
    distribution = d.iloc[2 * third:]

    acc_range = accumulation["high"].max() - accumulation["low"].min()
    avg_range_all = (d["high"] - d["low"]).mean()
    is_accumulation_tight = acc_range <= avg_range_all * third * 0.6

    manip_break_up = manipulation["high"].max() > accumulation["high"].max()
    manip_break_down = manipulation["low"].min() < accumulation["low"].min()
    dist_move = distribution["close"].iloc[-1] - distribution["close"].iloc[0]

    if is_accumulation_tight and manip_break_up and dist_move < 0:
        return {"phase_detected": "AMD_bearish",
                "note": "تثبیت -> فریب صعودی (grab سقف) -> توزیع نزولی"}
    if is_accumulation_tight and manip_break_down and dist_move > 0:
        return {"phase_detected": "AMD_bullish",
                "note": "تثبیت -> فریب نزولی (grab کف) -> توزیع صعودی"}
    return None


def detect_smt_divergence(df_a: pd.DataFrame, df_b: pd.DataFrame, left=3, right=3):
    """
    SMT Divergence: وقتی دو دارایی همبسته (مثلا BTC و ETH) یکی سقف/کف جدید می‌زنه ولی اون‌یکی نمی‌زنه
    -> نشونه‌ی ضعف روند و احتمال برگشت.
    df_a, df_b باید هم‌طول و هم‌بازه باشند (مثلا هر دو تایم‌فریم ۱ ساعته).
    """
    sw_a = find_swings(df_a, left, right)
    sw_b = find_swings(df_b, left, right)
    n = min(len(sw_a), len(sw_b))
    sw_a, sw_b = sw_a.tail(n).reset_index(drop=True), sw_b.tail(n).reset_index(drop=True)

    a_high_idx = sw_a[sw_a["swing_high"]].index
    b_high_idx = sw_b[sw_b["swing_high"]].index
    if len(a_high_idx) >= 2 and len(b_high_idx) >= 2:
        a_new_high = sw_a.loc[a_high_idx[-1], "high"] > sw_a.loc[a_high_idx[-2], "high"]
        b_new_high = sw_b.loc[b_high_idx[-1], "high"] > sw_b.loc[b_high_idx[-2], "high"]
        if a_new_high and not b_new_high:
            return {"type": "smt_bearish_divergence", "note": "دارایی اول سقف جدید زد ولی دوم نه -> ضعف روند صعودی"}
        if b_new_high and not a_new_high:
            return {"type": "smt_bearish_divergence_b", "note": "دارایی دوم سقف جدید زد ولی اول نه -> ضعف روند صعودی"}

    a_low_idx = sw_a[sw_a["swing_low"]].index
    b_low_idx = sw_b[sw_b["swing_low"]].index
    if len(a_low_idx) >= 2 and len(b_low_idx) >= 2:
        a_new_low = sw_a.loc[a_low_idx[-1], "low"] < sw_a.loc[a_low_idx[-2], "low"]
        b_new_low = sw_b.loc[b_low_idx[-1], "low"] < sw_b.loc[b_low_idx[-2], "low"]
        if a_new_low and not b_new_low:
            return {"type": "smt_bullish_divergence", "note": "دارایی اول کف جدید زد ولی دوم نه -> ضعف روند نزولی"}
        if b_new_low and not a_new_low:
            return {"type": "smt_bullish_divergence_b", "note": "دارایی دوم کف جدید زد ولی اول نه -> ضعف روند نزولی"}
    return None


def optimal_trade_entry_zone(swing_low: float, swing_high: float):
    """
    OTE: منطقه‌ی بازگشت فیبوناچی ۶۲٪ تا ۷۹٪ یک حرکت impulsive - بهترین ناحیه‌ی ورود هم‌جهت با روند.
    """
    diff = swing_high - swing_low
    return {
        "ote_62": round(swing_high - diff * 0.62, 6),
        "ote_705": round(swing_high - diff * 0.705, 6),
        "ote_79": round(swing_high - diff * 0.79, 6),
    }


def dealing_range(df: pd.DataFrame, left=5, right=5):
    """
    Dealing Range: رنج بین آخرین سوئینگ های مهم High/Low که قیمت درونش در حال معامله‌ست.
    """
    sw = find_swings(df, left, right)
    highs = sw.loc[sw["swing_high"], "high"]
    lows = sw.loc[sw["swing_low"], "low"]
    if highs.empty or lows.empty:
        return None
    return {"range_high": float(highs.iloc[-1]), "range_low": float(lows.iloc[-1])}


def full_ict_report(df: pd.DataFrame, now_utc_hour: int, correlated_df: pd.DataFrame | None = None) -> dict:
    report = {
        "kill_zone": current_kill_zone(now_utc_hour),
        "judas_swing": detect_judas_swing(df),
        "power_of_three": detect_power_of_three(df),
        "dealing_range": dealing_range(df),
    }
    dr = report["dealing_range"]
    if dr:
        report["ote_zone"] = optimal_trade_entry_zone(dr["range_low"], dr["range_high"])
    if correlated_df is not None:
        report["smt_divergence"] = detect_smt_divergence(df, correlated_df)
    return report
