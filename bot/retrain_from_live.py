"""
آموزش مدل AI با نتایج واقعی سیگنال‌هایی که تا الان ارسال و بسته شدن (به TP یا SL خوردن).
این مکمل بک‌تسته: بک‌تست روی داده‌ی تاریخی تقریبی کار می‌کنه، ولی این اسکریپت از خودِ
نتایج واقعی بازار (که ربات زنده جمع کرده) یاد می‌گیره - دقیق‌تره ولی معمولاً داده‌ش کمتره.

پیش‌نیاز: باید یه فایل .env کنار پروژه بسازی (کنار bot/) با همون مقادیر
UPSTASH_REDIS_REST_URL و UPSTASH_REDIS_REST_TOKEN که توی Vercel گذاشتی، تا این اسکریپت
بتونه به همون دیتابیس وصل بشه و تاریخچه‌ی واقعی رو بخونه.

اجرا (فقط با داده‌ی واقعی، اگه نمونه‌ی کافی جمع شده):
    python -m bot.retrain_from_live

اجرا (ترکیب با بک‌تست تاریخی - توصیه‌شده تا وقتی داده‌ی واقعی کمه):
    python -m bot.retrain_from_live --combine-backtest --symbol BTC/USDT --timeframe 4h --years 3
"""
import argparse
import logging
from bot import storage
from bot.ai_scorer import build_feature_vector
from bot.train_model import train_and_save

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("retrain_from_live")

MIN_SAMPLES_RECOMMENDED = 50


def build_training_records() -> list:
    if not storage.is_enabled():
        logger.error(
            "اتصال به Upstash برقرار نیست. یه فایل .env کنار پروژه بساز با "
            "UPSTASH_REDIS_REST_URL و UPSTASH_REDIS_REST_TOKEN (همونایی که توی Vercel گذاشتی)."
        )
        return []

    trades = storage.get_trade_log()
    closed = [t for t in trades if t.get("status") == "closed" and t.get("features")]

    records = []
    for t in closed:
        label = 1 if t.get("outcome", "").startswith("take_profit") else 0
        records.append({
            "features": t["features"],
            "label": label,
        })

    logger.info("تعداد کل سیگنال‌های ثبت‌شده: %d | تعداد بسته‌شده با جزئیات کامل: %d", len(trades), len(records))
    return records


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--combine-backtest", action="store_true",
                        help="داده‌ی واقعی رو با بک‌تست تاریخی ترکیب کن (توصیه‌شده تا وقتی داده‌ی واقعی کمه)")
    parser.add_argument("--symbols", default="BTC/USDT,ETH/USDT,SOL/USDT,DOGE/USDT,DOT/USDT",
                        help="لیست ارزها با کاما جدا شده، مثلا BTC/USDT,ETH/USDT")
    parser.add_argument("--timeframes", default="1h,4h",
                        help="لیست تایم‌فریم‌ها با کاما جدا شده، مثلا 1h,4h")
    parser.add_argument("--years", type=int, default=3)
    parser.add_argument("--live-weight", type=float, default=3.0,
                        help="وزن نسبی هر نمونه‌ی واقعی نسبت به نمونه‌ی بک‌تست (نه تکرار عینی - برای جلوگیری از بیش‌برازش)")
    args = parser.parse_args()

    live_records = build_training_records()
    logger.info("تعداد نتایج واقعی قابل‌استفاده: %d", len(live_records))

    combined = live_records
    sample_weight = [1.0] * len(live_records) if live_records else None

    if args.combine_backtest:
        from bot.backtester import fetch_historical_ohlcv, simulate_strategy
        symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
        timeframes = [t.strip() for t in args.timeframes.split(",") if t.strip()]

        backtest_records = []
        for symbol in symbols:
            for tf in timeframes:
                try:
                    logger.info("در حال دریافت داده‌ی تاریخی برای %s %s (%d سال)...", symbol, tf, args.years)
                    df = fetch_historical_ohlcv(symbol, tf, args.years)
                    recs = simulate_strategy(df)
                    logger.info("  -> %d نمونه از %s %s", len(recs), symbol, tf)
                    backtest_records.extend(recs)
                except Exception as e:
                    logger.warning("دریافت/تحلیل %s %s شکست خورد: %s", symbol, tf, e)
                    continue

        logger.info("مجموع نمونه‌ی بک‌تست (همه‌ی ارزها/تایم‌فریم‌ها): %d", len(backtest_records))
        # به‌جای تکرار عینی رکوردهای واقعی (که باعث بیش‌برازش می‌شد)، از sample_weight استفاده می‌کنیم:
        # هر نمونه‌ی بک‌تست وزن ۱، هر نمونه‌ی واقعی وزن live_weight (پیش‌فرض ۳) - بدون تکرار ردیف.
        combined = list(backtest_records) + list(live_records)
        sample_weight = ([1.0] * len(backtest_records)) + ([args.live_weight] * len(live_records))
        logger.info("مجموع نمونه‌های آموزش: %d (%d بک‌تست با وزن ۱ + %d واقعی با وزن %.1f)",
                    len(combined), len(backtest_records), len(live_records), args.live_weight)

    if not combined:
        logger.warning("هیچ داده‌ی قابل‌استفاده‌ای پیدا نشد. فعلاً صبر کن سیگنال‌های بیشتری بسته بشن، یا از --combine-backtest استفاده کن.")
        return

    if len(live_records) < MIN_SAMPLES_RECOMMENDED and not args.combine_backtest:
        logger.warning(
            "فقط %d نمونه‌ی واقعی پیدا شد. برای یادگیری قابل‌اعتماد، حداقل %d نمونه پیشنهاد میشه؛ "
            "پیشنهاد میشه فعلاً از --combine-backtest استفاده کنی تا داده‌ی بیشتری داشته باشیم.",
            len(live_records), MIN_SAMPLES_RECOMMENDED,
        )

    train_and_save(combined, sample_weight=sample_weight)


if __name__ == "__main__":
    main()
