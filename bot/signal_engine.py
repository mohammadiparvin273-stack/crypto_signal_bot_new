"""
موتور اصلی: برای یک نماد، همه‌ی تحلیل‌ها را روی چند تایم‌فریم اجرا می‌کند،
جهت معامله را از تایم‌فریم‌های بزرگ تشخیص می‌دهد (طبق درخواست: فقط هم‌جهت روند بزرگ)،
امتیاز نهایی را حساب می‌کند و در صورت عبور از آستانه، یک سیگنال کامل می‌سازد.
"""
import logging
import pandas as pd

from bot.config import TREND_TIMEFRAMES, ENTRY_TIMEFRAMES, CANDLES_LIMIT, PRIMARY_EXCHANGE
from bot.data_fetcher import fetch_multi_timeframe
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


def determine_big_trend(mtf_data: dict) -> dict:
    """
    روند بزرگ از تایم‌فریم‌های TREND_TIMEFRAMES با ساختار بازار (BOS/CHOCH - همون منطق
    Smart Money) تشخیص داده میشه، نه با EMA200 (که روی تایم‌فریم هفتگی یعنی ~۴ سال تاخیر
    و بیش‌ازحد کند بود). ساختار بازار به شکست واقعی سقف/کف واکنش نشون می‌ده، نه میانگین قدیمی.
    خروجی: {timeframe: 'up'/'down'/None}
    """
    trends = {}
    for tf in TREND_TIMEFRAMES:
        df = mtf_data.get(tf)
        if df is None or len(df) < 30:
            trends[tf] = None
            continue
        structure = detect_bos_choch(df, left=3, right=3)
        current_trend = structure["current_trend"]
        trends[tf] = current_trend if current_trend in ("up", "down") else None
    return trends


def build_entry_reason(direction: str, pa_report: dict, smc_report: dict, ict_report: dict) -> str:
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
    # جهت غالب (up/down) از تایم‌فریم‌های بزرگ -> تبدیل به جهت معامله (long/short)
    dominant_trend = max(set(dominant_directions), key=dominant_directions.count)
    direction = "long" if dominant_trend == "up" else "short"

    # دیتای مشترک (یک‌بار در هر اجرا برای این نماد، نه هر تایم‌فریم - برای صرفه‌جویی در rate limit)
    sentiment_report = full_sentiment_report(symbol_keyword=symbol.split("/")[0])
    onchain_report = onchain_summary(symbol)
    daily_df = mtf_data.get("1d")
    derivatives_report = full_derivatives_report(symbol, daily_df if daily_df is not None else list(mtf_data.values())[0])

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

        # فیلتر اضافه‌ی EMA20/EMA50 + RSI (استراتژی کلاسیک روند): قبلاً هر دو شرط الزامی بود که
        # خیلی سخت‌گیر شده بود (۷۲٪ رد میشدن همینجا). الان کافیه یکی از دو شرط برقرار باشه.
        ema20 = float(ind["ema_20"].iloc[-1])
        ema50 = float(ind["ema_50"].iloc[-1])
        ema_aligned = (ema20 > ema50) if direction == "long" else (ema20 < ema50)
        rsi_aligned = (rsi_value > 50) if direction == "long" else (rsi_value < 50)
        trend_confluence_ok = ema_aligned or rsi_aligned
        if not trend_confluence_ok:
            _reject("ema_rsi_filter")
            continue

        score = compute_final_score(direction, mtf_trends, volume_report, sentiment_report,
                                     smc_report, pa_report, rsi_value, derivatives_report,
                                     entry_price=float(df["close"].iloc[-1]))
        if not score["should_send"]:
            _reject(f"score_below_70 (بود: {score['total_score']})")
            continue
        _reject("passed_all_filters")

        entry_price = float(df["close"].iloc[-1])
        atr_value = float(ind["atr_14"].iloc[-1])
        trade_plan = compute_trade_plan(entry_price, atr_value, direction)
        trade_plan["_atr_value"] = atr_value  # برای محاسبه‌ی دوباره با موجودی واقعی در app.py

        extra_features = {
            "atr_pct": (atr_value / entry_price * 100) if entry_price else 0,
            "adx": float(ind["adx_14"].iloc[-1]),
            "rr_target_2": trade_plan["risk_reward_ratios"][1],
        }
        ai_prob = predict_success_probability(score["breakdown"], score["total_score"], extra_features)

        reason = build_entry_reason(direction, pa_report, smc_report, ict_report)

        signals.append({
            "symbol": symbol,
            "timeframe": tf,
            "direction": direction,
            "score": score,
            "trade_plan": trade_plan,
            "ai_probability": ai_prob,
            "extra_features": extra_features,
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
