"""
تحلیل بازار مشتقه. Open Interest / Funding Rate / Long-Short Ratio از API های عمومی و رایگان
Binance Futures گرفته میشه. Liquidation Heatmap واقعی (صف سفارش‌های لیکوئید) پولیه (CoinGlass Pro)،
اینجا یک نسخه‌ی *تقریبی* بر مبنای تغییرات ناگهانی OI + حرکت شدید قیمت پیاده شده (مستند و شفاف).
Whale Positions هم به‌صورت واقعی نیاز به دیتای صرافی (پولی) داره؛ اینجا از OI بزرگ + Funding extreme
به عنوان "نشانه‌ی احتمالی حضور پوزیشن‌های بزرگ" استفاده می‌کنیم - این یک تخمین است، نه ردیابی مستقیم.
"""
import logging
import pandas as pd
from bot.data_fetcher import fetch_funding_rate, fetch_open_interest, fetch_long_short_ratio

logger = logging.getLogger("derivatives")


def funding_rate_signal(funding_rate: float | None):
    if funding_rate is None:
        return {"value": None, "bias": "unknown"}
    # فاندینگ خیلی مثبت -> لانگ‌ها زیادن -> ریسک فشار فروش (احتمال Long Squeeze)
    # فاندینگ خیلی منفی -> شورت‌ها زیادن -> ریسک فشار خرید (احتمال Short Squeeze)
    if funding_rate > 0.0005:
        bias = "extreme_long_crowded"
    elif funding_rate < -0.0005:
        bias = "extreme_short_crowded"
    else:
        bias = "neutral"
    return {"value": funding_rate, "bias": bias}


def open_interest_trend(df_price: pd.DataFrame, oi_current: float | None, oi_history: list | None = None):
    """
    ترکیب تغییرات قیمت با تغییرات OI برای تشخیص نوع حرکت:
    قیمت بالا + OI بالا  -> ورود پول جدید (روند قوی، صعودی سالم)
    قیمت بالا + OI پایین -> بسته شدن پوزیشن شورت (short covering، ممکنه ادامه‌دار نباشه)
    قیمت پایین + OI بالا -> ورود پول جدید شورت (روند نزولی قوی)
    قیمت پایین + OI پایین -> بسته شدن پوزیشن لانگ (long liquidation/profit taking)
    """
    if oi_current is None or not oi_history or len(oi_history) < 2:
        return {"state": "insufficient_data"}
    price_change = df_price["close"].iloc[-1] - df_price["close"].iloc[-2]
    oi_change = oi_current - oi_history[-2]
    if price_change > 0 and oi_change > 0:
        state = "new_longs_entering"
    elif price_change > 0 and oi_change <= 0:
        state = "short_covering"
    elif price_change <= 0 and oi_change > 0:
        state = "new_shorts_entering"
    else:
        state = "long_liquidation_or_profit_taking"
    return {"state": state, "oi_current": oi_current, "price_change": float(price_change)}


def approximate_liquidation_heatmap(df: pd.DataFrame, funding_rate: float | None):
    """
    تخمین نواحی احتمالی تراکم لیکوئیدیشن بر مبنای:
    فاصله‌ی قیمتی معمول برای لیکوئید شدن لوریج‌های رایج (x10, x20, x50, x100) از قیمت فعلی.
    این یک مدل ساده‌ی ریاضی است، نه دیتای واقعی صف سفارش صرافی‌ها.
    """
    price = float(df["close"].iloc[-1])
    leverages = [10, 20, 50, 100]
    # فرمول تقریبی فاصله‌ی لیکوئید (بدون در نظر گرفتن مارجین اضافه): 1/leverage
    long_liq_levels = [round(price * (1 - 1 / lv), 6) for lv in leverages]
    short_liq_levels = [round(price * (1 + 1 / lv), 6) for lv in leverages]
    crowded_side = None
    if funding_rate is not None:
        crowded_side = "longs" if funding_rate > 0.0003 else ("shorts" if funding_rate < -0.0003 else None)
    return {
        "approx_long_liquidation_zones": long_liq_levels,
        "approx_short_liquidation_zones": short_liq_levels,
        "crowded_side_estimate": crowded_side,
        "note": "این محاسبه‌ی ریاضی تقریبی است، نه دیتای واقعی heatmap صرافی (که پولی است)",
    }


def full_derivatives_report(symbol: str, df_price: pd.DataFrame, oi_history: list | None = None) -> dict:
    funding = fetch_funding_rate(symbol)
    oi = fetch_open_interest(symbol)
    ls_ratio = fetch_long_short_ratio(symbol)
    return {
        "funding": funding_rate_signal(funding),
        "open_interest_trend": open_interest_trend(df_price, oi, oi_history),
        "long_short_ratio": ls_ratio,
        "liquidation_heatmap_approx": approximate_liquidation_heatmap(df_price, funding),
    }
