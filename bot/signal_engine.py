"""
موتور اصلی: برای یک نماد، همه‌ی تحلیل‌ها را روی چند تایم‌فریم اجرا می‌کند،
جهت معامله را از تایم‌فریم‌های بزرگ تشخیص می‌دهد (طبق درخواست: فقط هم‌جهت روند بزرگ)،
امتیاز نهایی را حساب می‌کند و در صورت عبور از آستانه، یک سیگنال کامل می‌سازد.
"""
import logging
import pandas as pd

from bot.config import (TREND_TIMEFRAMES, ENTRY_TIMEFRAMES, CANDLES_LIMIT, PRIMARY_EXCHANGE,
                         MIN_AI_PROBABILITY_PCT, ADX_HARD_FLOOR, TREND_TF_WEIGHTS)
from bot.data_fetcher import fetch_multi_timeframe, compute_order_book_imbalance
from bot.indicators import compute_all_indicators
from bot.price_action import full_price_action_report, detect_bos_choch
from bot.smc import full_smc_report
from bot.ict import full_ict_report
from bot.volume_analysis import full_volume_report
from bot.derivatives import full_derivatives_report
from bot.sentiment import full_sentiment_report
from bot.onchain import onchain_summary
from bot.manipulation import full_manipulation_report
from bot.scoring import compute_final_score
from bot.risk_management import compute_trade_plan
from bot.ai_scorer import predict_success_probability

logger = logging.getLogger("signal_engine")


def _ema_slope_fallback(df) -> str | None:
    """
    وقتی BOS/CHOCH روی یه تایم‌فریم هنوز هیچ رویدادی ثبت نکرده (مثلاً چون کندل کافی برای
    تایید Swing Point فراهم نشده)، به‌جای برگردوندن None (که باعث نادیده گرفتن کامل اون
    تایم‌فریم در رای‌گیری روند میشه)، از شیب EMA50 نسبت به EMA200 به‌عنوان یه تخمین کمکی
    و ساده‌تر استفاده می‌کنیم - فقط برای این‌که رای این تایم‌فریم کامل از دست نره.
    """
    close = df["close"]
    if len(close) < 60:
        return None
    ema50 = close.ewm(span=50, adjust=False).mean()
    ema200_len = min(200, max(50, len(close) - 1))
    ema_slow = close.ewm(span=ema200_len, adjust=False).mean()
    if ema50.iloc[-1] > ema_slow.iloc[-1]:
        return "up"
    if ema50.iloc[-1] < ema_slow.iloc[-1]:
        return "down"
    return None


def determine_big_trend(mtf_data: dict) -> dict:
    """
    روند بزرگ از تایم‌فریم‌های TREND_TIMEFRAMES با ساختار بازار (BOS/CHOCH - همون منطق
    Smart Money) تشخیص داده میشه. اگه ساختار روی یه تایم‌فریم هنوز رویدادی نداشت، به‌جای
    نادیده گرفتن کامل اون تایم‌فریم، از شیب EMA به‌عنوان تخمین کمکی استفاده میشه (تا رای‌گیری
    وزن‌دار زیر کاملاً بی‌اثر نشه). خروجی: {timeframe: 'up'/'down'/None}
    """
    trends = {}
    for tf in TREND_TIMEFRAMES:
        df = mtf_data.get(tf)
        if df is None or len(df) < 30:
            trends[tf] = None
            continue
        structure = detect_bos_choch(df, left=3, right=3)
        current_trend = structure["current_trend"]
        if current_trend not in ("up", "down"):
            current_trend = _ema_slope_fallback(df)
        trends[tf] = current_trend if current_trend in ("up", "down") else None
    return trends


def weighted_dominant_trend(mtf_trends: dict) -> str | None:
    """
    رای‌گیری وزن‌دار روی روند تایم‌فریم‌های بزرگ: تایم‌فریم‌های نزدیک‌تر به لحظه‌ی حال (۴ساعته)
    وزن بیشتری نسبت به هفتگی/روزانه دارن، چون سریع‌تر به تغییر واقعی جهت واکنش نشون می‌دن.
    قبلاً رای مساوی بود که باعث می‌شد وقتی ۴ساعته برگشته ولی هفتگی/روزانه هنوز آپدیت نشده،
    جهت غالب اشتباه یا خنثی تشخیص داده بشه و کل نماد رد بشه.
    """
    scores = {"up": 0, "down": 0}
    for tf, trend in mtf_trends.items():
        if trend in ("up", "down"):
            scores[trend] += TREND_TF_WEIGHTS.get(tf, 1)
    if scores["up"] == 0 and scores["down"] == 0:
        return None
    return "up" if scores["up"] >= scores["down"] else "down"


def build_entry_reason(direction: str, pa_report: dict, smc_report: dict, ict_report: dict,
                        vwap_aligned: bool | None = None, order_book_imbalance: float | None = None) -> str:
    reasons = []
    struct = smc_report["market_structure"]
    if struct["aligned"]:
        reasons.append(f"ساختار داخلی و بیرونی هم‌جهت ({struct['external_trend']})")

    # Liquidity Grab: فقط اگه نوعش با جهت سیگنال هم‌خونی داره اضافه بشه
    wanted_grab = "buy_side_liquidity_grab" if direction == "long" else "sell_side_liquidity_grab"
    if any(g["type"] == wanted_grab for g in smc_report.get("liquidity_grabs", [])):
        reasons.append("Liquidity Grab هم‌جهت اخیر شناسایی شد (نقدینگی گرفته شده)")

    if pa_report["order_blocks"]:
        wanted_ob = "bullish_ob" if direction == "long" else "bearish_ob"
        if any(ob["type"] == wanted_ob for ob in pa_report["order_blocks"]):
            reasons.append("قیمت نزدیک یک Order Block هم‌جهت است")

    # Breakout+Retest: فقط اگه نوعش با جهت سیگنال هم‌خونی داره
    br = pa_report.get("breakout_retest")
    if br:
        wanted_br = "bullish_breakout_retest" if direction == "long" else "bearish_breakout_retest"
        if br["type"] == wanted_br:
            reasons.append(f"الگوی Breakout+Retest معتبر روی سطح {br['level']}")

    if vwap_aligned:
        reasons.append("قیمت هم‌جهت با VWAP است")

    if order_book_imbalance is not None:
        aligned_ob = (order_book_imbalance > 0.05) if direction == "long" else (order_book_imbalance < -0.05)
        if aligned_ob:
            reasons.append(f"دفتر سفارش فشار {'خرید' if direction == 'long' else 'فروش'} هم‌جهت نشون می‌ده")

    if pa_report["premium_discount"]["zone"] in ("discount", "premium"):
        reasons.append(f"قیمت در ناحیه‌ی {pa_report['premium_discount']['zone']} قرار دارد")

    # Judas Swing: فقط اگه نوعش (bullish/bearish) با جهت سیگنال هم‌خونی داره
    judas = ict_report.get("judas_swing")
    if judas:
        wanted_judas = "judas_swing_bullish" if direction == "long" else "judas_swing_bearish"
        if judas["type"] == wanted_judas:
            reasons.append(judas["note"])

    # Power of Three (AMD): فقط اگه نوعش (bullish/bearish) با جهت سیگنال هم‌خونی داره
    p3 = ict_report.get("power_of_three")
    if p3:
        wanted_p3 = "AMD_bullish" if direction == "long" else "AMD_bearish"
        if p3["phase_detected"] == wanted_p3:
            reasons.append(p3["note"])

    if ict_report.get("kill_zone"):
        reasons.append(f"در بازه‌ی {ict_report['kill_zone']} هستیم (نقدینگی بالا)")
    return " | ".join(reasons) if reasons else "ترکیب امتیازهای تکنیکال از آستانه عبور کرد"


def analyze_symbol(symbol: str, direction_tally: dict | None = None, rejection_stats: dict | None = None) -> list:
    """
    تحلیل کامل یک نماد روی همه‌ی تایم‌فریم‌های ورود. خروجی: لیست سیگنال‌های واجدشرایط (ممکنه خالی باشه)
    """
    def _reject(reason):
        if rejection_stats is not None:
            rejection_stats[reason] = rejection_stats.get(reason, 0) + 1

    all_timeframes = list(dict.fromkeys(TREND_TIMEFRAMES + ENTRY_TIMEFRAMES))
    mtf_data = fetch_multi_timeframe(symbol, all_timeframes, limit=CANDLES_LIMIT, exchange_name=PRIMARY_EXCHANGE)

    if not mtf_data:
        logger.warning("داده‌ای برای %s دریافت نشد", symbol)
        _reject("no_data")
        return []

    mtf_trends = determine_big_trend(mtf_data)
    dominant_directions = [v for v in mtf_trends.values() if v]
    logger.info("تشخیص روند برای %s: %s", symbol, mtf_trends)
    if direction_tally is not None:
        for v in dominant_directions:
            direction_tally[v] = direction_tally.get(v, 0) + 1
    if not dominant_directions:
        _reject("no_clear_trend")
        return []
    # جهت غالب (up/down) از تایم‌فریم‌های بزرگ -> با رای‌گیری وزن‌دار (نزدیک‌تر به الان = وزن بیشتر)
    # تا در حرکت‌های تازه‌شروع‌شده که هنوز هفتگی/روزانه آپدیت نشدن، جهت درست زودتر تشخیص داده بشه.
    dominant_trend = weighted_dominant_trend(mtf_trends)
    if dominant_trend is None:
        _reject("no_clear_trend")
        return []
    direction = "long" if dominant_trend == "up" else "short"

    # دیتای مشترک (یک‌بار در هر اجرا برای این نماد، نه هر تایم‌فریم - برای صرفه‌جویی در rate limit)
    sentiment_report = full_sentiment_report(symbol_keyword=symbol.split("/")[0])
    onchain_report = onchain_summary(symbol)
    daily_df = mtf_data.get("1d")
    derivatives_report = full_derivatives_report(symbol, daily_df if daily_df is not None else list(mtf_data.values())[0])
    order_book_imbalance = compute_order_book_imbalance(symbol)

    signals = []
    for tf in ENTRY_TIMEFRAMES:
        df = mtf_data.get(tf)
        if df is None or len(df) < 60:
            _reject("insufficient_candles")
            continue

        pa_report = full_price_action_report(df)
        smc_report = full_smc_report(df)
        manipulation_report = full_manipulation_report(df)
        volume_report = full_volume_report(df)
        ind = compute_all_indicators(df)
        now_hour = pd.Timestamp.utcnow().hour
        ict_report = full_ict_report(df, now_hour)

        rsi_value = float(ind["rsi_14"].iloc[-1])

        # هم‌جهتی EMA20/EMA50 + RSI: قبلاً فیلتر سخت بود (رد کامل اگه هر دو هم‌زمان برقرار نبودن).
        # الان به‌عنوان یه امتیاز نرم (score_momentum توی scoring.py) حساب میشه، نه رد کامل -
        # چون این ترکیب دقیقاً همون چیزی بود که باعث می‌شد در ابتدای حرکت‌های بزرگ (وقتی هنوز
        # EMA20/EMA50 کراس نکرده یا RSI رد نکرده ۵۰ رو) سیگنال به‌کلی از دست بره.
        ema20 = float(ind["ema_20"].iloc[-1])
        ema50 = float(ind["ema_50"].iloc[-1])
        ema_aligned = (ema20 > ema50) if direction == "long" else (ema20 < ema50)
        rsi_aligned = (rsi_value > 50) if direction == "long" else (rsi_value < 50)

        # ADX: قبلاً فیلتر سخت بود (رد کامل زیر ۲۰). الان فقط زیر ADX_HARD_FLOOR (بازار واقعاً
        # کاملاً بی‌روند) رد میشه؛ بین این کف و آستانه‌ی هدف، به‌صورت امتیاز نرم لحاظ میشه تا
        # حرکت‌های تازه‌شروع‌شده (که ADX هنوز کامل بالا نیومده) هم شانس دیده‌شدن داشته باشن.
        adx_value = float(ind["adx_14"].iloc[-1])
        if adx_value < ADX_HARD_FLOOR:
            _reject(f"adx_hard_floor (بود: {round(adx_value,1)})")
            continue

        # VWAP: قیمت هم‌جهت با VWAP (بالای VWAP برای لانگ / زیر VWAP برای شورت) به‌عنوان
        # یه تاییدیه‌ی اضافه‌ی معامله‌گران روزانه/موسسات در نظر گرفته میشه.
        vwap_value = float(ind["vwap"].iloc[-1]) if "vwap" in ind and not pd.isna(ind["vwap"].iloc[-1]) else None
        entry_now = float(df["close"].iloc[-1])
        vwap_aligned = None
        if vwap_value:
            vwap_aligned = (entry_now > vwap_value) if direction == "long" else (entry_now < vwap_value)

        score = compute_final_score(direction, mtf_trends, volume_report, sentiment_report,
                                     smc_report, pa_report, rsi_value, derivatives_report,
                                     entry_price=float(df["close"].iloc[-1]),
                                     vwap_aligned=vwap_aligned, order_book_imbalance=order_book_imbalance,
                                     adx_value=adx_value, ema_aligned=ema_aligned, rsi_aligned=rsi_aligned)
        if not score["should_send"]:
            _reject(f"score_below_min (بود: {score['total_score']})")
            continue
        _reject("passed_all_filters")

        entry_price = float(df["close"].iloc[-1])
        atr_value = float(ind["atr_14"].iloc[-1])

        # SL ساختاری: نزدیک‌ترین Order Block هم‌جهت رو پیدا می‌کنیم و یه بافر کوچیک (۱۰٪ ATR)
        # پشتش می‌ذاریم - اگه قیمت تا اونجا برگرده، یعنی واقعاً تحلیل غلط بوده.
        wanted_ob_type = "bullish_ob" if direction == "long" else "bearish_ob"
        matching_obs = [ob for ob in pa_report.get("order_blocks", []) if ob["type"] == wanted_ob_type]
        structural_sl_candidate = None
        if matching_obs:
            nearest_ob = min(matching_obs, key=lambda ob: abs(entry_price - (ob["bottom"] if direction == "long" else ob["top"])))
            buffer = atr_value * 0.1
            structural_sl_candidate = (nearest_ob["bottom"] - buffer) if direction == "long" else (nearest_ob["top"] + buffer)

        trade_plan = compute_trade_plan(entry_price, atr_value, direction, structural_sl_candidate=structural_sl_candidate)
        trade_plan["_atr_value"] = atr_value  # برای محاسبه‌ی دوباره با موجودی واقعی در app.py

        # فیچرهای خام برای مدل AI (به‌جای امتیاز خلاصه‌شده) - همه از دیتای واقعی همین لحظه
        wanted_grab = "buy_side_liquidity_grab" if direction == "long" else "sell_side_liquidity_grab"
        liquidity_grab_match_count = sum(1 for g in smc_report.get("liquidity_grabs", []) if g["type"] == wanted_grab)
        wanted_ob = "bullish_ob" if direction == "long" else "bearish_ob"
        order_block_match_count = sum(1 for ob in pa_report.get("order_blocks", []) if ob["type"] == wanted_ob)
        wanted_br = "bullish_breakout_retest" if direction == "long" else "bearish_breakout_retest"
        br = pa_report.get("breakout_retest")
        breakout_retest_match = 1 if (br and br.get("type") == wanted_br) else 0
        funding_val = derivatives_report.get("funding", {}).get("value")
        news_sent = sentiment_report.get("news_sentiment", {})
        fg = sentiment_report.get("fear_greed")

        raw_features = {
            "rsi_value": rsi_value,
            "ema_diff_pct": ((ema20 - ema50) / ema50 * 100) if ema50 else 0,
            "adx_value": adx_value,
            "atr_pct": (atr_value / entry_price * 100) if entry_price else 0,
            "vwap_aligned": 1 if vwap_aligned is True else (0 if vwap_aligned is False else 0.5),
            "liquidity_grab_match_count": liquidity_grab_match_count,
            "order_block_match_count": order_block_match_count,
            "fvg_count": len(pa_report.get("fvg", [])),
            "breakout_retest_match": breakout_retest_match,
            "premium_discount_position_pct": pa_report.get("premium_discount", {}).get("position_pct", 50),
            "volume_z": volume_report.get("volume_spike", {}).get("current_z", 0),
            "rr_target_2": trade_plan["risk_reward_ratios"][1],
            "funding_rate_x1000": (funding_val * 1000) if funding_val else 0,
            "fear_greed_value": fg["value"] if fg else 50,
            "news_sentiment_score": news_sent.get("score", 0),
            "hour_utc": now_hour,
            "direction_encoded": 1 if direction == "long" else 0,
            "order_book_imbalance": order_book_imbalance if order_book_imbalance is not None else 0,
        }
        ai_prob = predict_success_probability(raw_features, score["total_score"])

        # گیت نهایی: اگه مدل AI واقعی (نه fallback قانون‌محور) آموزش دیده باشه، خودِ AI هم باید
        # این معامله رو با احتمال قابل‌قبول تایید کنه - فارغ از این‌که لانگه یا شورت. این دقیقاً
        # همون چیزیه که باعث میشه تصمیم‌گیری جهت‌گرا/گزینشی نباشه: AI بر اساس نتایج واقعی یاد گرفته
        # کدوم الگوها (نه کدوم جهت) واقعاً جواب می‌دن.
        if ai_prob.get("source") == "ml_model" and ai_prob["probability_pct"] < MIN_AI_PROBABILITY_PCT:
            _reject(f"ai_model_rejected (احتمال: {ai_prob['probability_pct']}%)")
            continue

        reason = build_entry_reason(direction, pa_report, smc_report, ict_report, vwap_aligned, order_book_imbalance)

        signals.append({
            "symbol": symbol,
            "timeframe": tf,
            "direction": direction,
            "score": score,
            "trade_plan": trade_plan,
            "ai_probability": ai_prob,
            "features": raw_features,
            "reason": reason,
            "grade": score["grade"],
            "mtf_trends": mtf_trends,
            "price_action": pa_report,
            "smc": smc_report,
            "ict": ict_report,
            "manipulation": manipulation_report,
            "volume": volume_report,
            "derivatives": derivatives_report,
            "sentiment": sentiment_report,
            "onchain": onchain_report,
        })

    return signals


def scan_all_symbols(symbols: list) -> tuple:
    import time
    from bot.config import MAX_SCAN_SECONDS

    start = time.time()
    all_signals = []
    direction_tally = {"up": 0, "down": 0}
    rejection_stats = {}

    for symbol in symbols:
        elapsed = time.time() - start
        if elapsed > MAX_SCAN_SECONDS:
            logger.warning(
                "زمان مجاز اسکن (%s ثانیه) به پایان رسید؛ %d ارز از %d بررسی نشد (برای جلوگیری از timeout متوقف شد)",
                MAX_SCAN_SECONDS, len(symbols) - symbols.index(symbol), len(symbols),
            )
            break
        try:
            sigs = analyze_symbol(symbol, direction_tally=direction_tally, rejection_stats=rejection_stats)
            all_signals.extend(sigs)
        except Exception as e:
            logger.error("خطا در تحلیل %s: %s", symbol, e)
            continue

    logger.info("خلاصه‌ی تشخیص روند این اجرا: %s (اگه شدیداً یک‌طرفه بود، یا بازار واقعاً یک‌طرفه‌ست یا باگیه)",
                direction_tally)
    logger.info("خلاصه‌ی دلایل رد شدن: %s", rejection_stats)
    return all_signals, direction_tally, rejection_stats
