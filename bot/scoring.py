"""
سیستم امتیازدهی نهایی. هر بخش امتیاز 0 تا وزن مربوطه می‌گیره، جمع کل از 100.
Trend=20, Volume=15, News=10, Liquidity=20, Order Block=15, RSI=5, Open Interest=10, Funding=5
"""
from bot.config import SCORE_WEIGHTS, SCORE_GOLDEN, SCORE_GOOD, SCORE_MIN_SEND


def score_trend(mtf_trends: dict, direction: str) -> float:
    """
    mtf_trends: {timeframe: 'up'/'down'/None} برای تایم‌فریم‌های بزرگ (هفتگی/روزانه/۴ساعته)
    هرچی تایم‌فریم‌های بزرگ بیشتری هم‌جهت با `direction` باشن، امتیاز بیشتر.
    توجه: direction به صورت 'long'/'short' است ولی مقادیر mtf_trends به صورت 'up'/'down' هستند.
    """
    weight = SCORE_WEIGHTS["trend"]
    direction_as_trend = "up" if direction == "long" else "down"
    tfs = [v for v in mtf_trends.values() if v is not None]
    if not tfs:
        return 0.0
    aligned = sum(1 for t in tfs if t == direction_as_trend)
    return round(weight * (aligned / len(tfs)), 2)


def score_volume(volume_report: dict, direction: str = "long", entry_price: float | None = None) -> float:
    """
    وزن حجم بین سه بخش تقسیم میشه: Volume Spike (۴۰٪)، Delta تخمینی (۳۰٪)،
    و موقعیت نسبت به Volume Profile/POC (۳۰٪ - جای فیلتر سخت قبلی، حالا بونوس امتیازیه
    تا سیگنال‌های خوب رو کاملاً رد نکنه، ولی همچنان در نظر گرفته بشه).
    """
    weight = SCORE_WEIGHTS["volume"]
    score = 0.0
    if volume_report.get("volume_spike", {}).get("is_current_spike"):
        score += weight * 0.4
    delta = volume_report.get("delta", {}).get("last_delta", 0)
    if delta > 0:
        score += weight * 0.3 * min(1.0, abs(delta) / (abs(delta) + 1))

    vp_profile = volume_report.get("profile")
    if vp_profile and vp_profile.get("poc_price") and entry_price:
        poc = vp_profile["poc_price"]
        aligned = (entry_price <= poc * 1.01) if direction == "long" else (entry_price >= poc * 0.99)
        score += weight * 0.3 if aligned else weight * 0.1
    else:
        score += weight * 0.15  # داده‌ی کافی نیست - امتیاز خنثی/متوسط

    return round(min(score, weight), 2)


def score_news(sentiment_report: dict, direction: str) -> float:
    weight = SCORE_WEIGHTS["news"]
    news = sentiment_report.get("news_sentiment", {})
    label = news.get("label", "neutral")
    fg = sentiment_report.get("fear_greed") or {}
    fg_value = fg.get("value")

    score = 0.0
    if (direction == "long" and label == "positive") or (direction == "short" and label == "negative"):
        score += weight * 0.6
    elif label == "neutral":
        score += weight * 0.3
    if fg_value is not None:
        if direction == "long" and fg_value <= 30:  # ترس شدید -> فرصت خرید طبق تحلیل contrarian
            score += weight * 0.4
        elif direction == "short" and fg_value >= 70:  # طمع شدید -> فرصت فروش
            score += weight * 0.4
        else:
            score += weight * 0.2
    return round(min(score, weight), 2)


def score_liquidity(smc_report: dict, direction: str) -> float:
    weight = SCORE_WEIGHTS["liquidity"]
    grabs = smc_report.get("liquidity_grabs", [])
    if not grabs:
        return round(weight * 0.3, 2)  # حداقل امتیاز پایه
    relevant = [g for g in grabs if
                (direction == "long" and g["type"] == "buy_side_liquidity_grab") or
                (direction == "short" and g["type"] == "sell_side_liquidity_grab")]
    if relevant:
        return float(weight)
    return round(weight * 0.3, 2)


def score_order_block(price_action_report: dict, direction: str) -> float:
    weight = SCORE_WEIGHTS["order_block"]
    obs = price_action_report.get("order_blocks", [])
    wanted_type = "bullish_ob" if direction == "long" else "bearish_ob"
    relevant = [ob for ob in obs if ob["type"] == wanted_type]
    if relevant:
        return float(weight)
    return round(weight * 0.2, 2)


def score_rsi(rsi_value: float, direction: str) -> float:
    weight = SCORE_WEIGHTS["rsi"]
    if direction == "long" and rsi_value <= 40:
        return float(weight)
    if direction == "short" and rsi_value >= 60:
        return float(weight)
    if 40 < rsi_value < 60:
        return round(weight * 0.5, 2)
    return round(weight * 0.1, 2)


def score_open_interest(derivatives_report: dict, direction: str) -> float:
    weight = SCORE_WEIGHTS["open_interest"]
    state = derivatives_report.get("open_interest_trend", {}).get("state")
    if direction == "long" and state == "new_longs_entering":
        return float(weight)
    if direction == "short" and state == "new_shorts_entering":
        return float(weight)
    if state in ("short_covering", "long_liquidation_or_profit_taking"):
        return round(weight * 0.4, 2)
    return round(weight * 0.2, 2)


def score_funding(derivatives_report: dict, direction: str) -> float:
    weight = SCORE_WEIGHTS["funding"]
    bias = derivatives_report.get("funding", {}).get("bias")
    # فاندینگ extreme_short_crowded یعنی شورت‌ها شلوغن -> فرصت خوب برای long (short squeeze)
    if direction == "long" and bias == "extreme_short_crowded":
        return float(weight)
    if direction == "short" and bias == "extreme_long_crowded":
        return float(weight)
    if bias == "neutral":
        return round(weight * 0.5, 2)
    return round(weight * 0.2, 2)


def compute_final_score(direction: str, mtf_trends: dict, volume_report: dict, sentiment_report: dict,
                          smc_report: dict, price_action_report: dict, rsi_value: float,
                          derivatives_report: dict, entry_price: float | None = None) -> dict:
    breakdown = {
        "trend": score_trend(mtf_trends, direction),
        "volume": score_volume(volume_report, direction, entry_price),
        "news": score_news(sentiment_report, direction),
        "liquidity": score_liquidity(smc_report, direction),
        "order_block": score_order_block(price_action_report, direction),
        "rsi": score_rsi(rsi_value, direction),
        "open_interest": score_open_interest(derivatives_report, direction),
        "funding": score_funding(derivatives_report, direction),
    }
    total = round(sum(breakdown.values()), 2)

    if total >= SCORE_GOLDEN:
        grade = "golden"
    elif total >= SCORE_GOOD:
        grade = "good"
    elif total >= SCORE_MIN_SEND:
        grade = "acceptable"
    else:
        grade = "rejected"

    return {"total_score": total, "grade": grade, "breakdown": breakdown, "should_send": total >= SCORE_MIN_SEND}
