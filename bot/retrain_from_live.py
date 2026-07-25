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
    closed = [t for t in trades if t.get("status") == "closed" and t.get("score_breakdown") and t.get("extra_features")]

    records = []
    for t in closed:
        label = 1 if t.get("outcome", "").startswith("take_profit") else 0
        records.append({
            "features": {**t["score_breakdown"], **t["extra_features"]},
            "label": label,
        })

    logger.info("تعداد کل سیگنال‌های ثبت‌شده: %d | تعداد بسته‌شده با جزئیات کامل: %d", len(trades), len(records))
    return records


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--combine-backtest", action="store_true",
                        help="داده‌ی واقعی رو با بک‌تست تاریخی ترکیب کن (توصیه‌شده تا وقتی داده‌ی واقعی کمه)")
    parser.add_argument("--symbol", default="BTC/USDT")
    parser.add_argument("--timeframe", default="4h")
    parser.add_argument("--years", type=int, default=3)
    parser.add_argument("--live-weight", type=int, default=5,
                        help="هر نمونه‌ی واقعی چند بار تکرار بشه توی دیتاست (چون کم‌تعدادن ولی دقیق‌ترن)")
    args = parser.parse_args()

    live_records = build_training_records()
    logger.info("تعداد نتایج واقعی قابل‌استفاده: %d", len(live_records))

    combined = live_records

    if args.combine_backtest:
        from bot.backtester import fetch_historical_ohlcv, simulate_strategy
        logger.info("در حال دریافت داده‌ی تاریخی برای بک‌تست (%s %s، %d سال)...", args.symbol, args.timeframe, args.years)
        df = fetch_historical_ohlcv(args.symbol, args.timeframe, args.years)
        backtest_records = simulate_strategy(df)
        logger.info("تعداد نمونه‌ی بک‌تست: %d", len(backtest_records))
        combined = list(backtest_records) + (live_records * args.live_weight)
        logger.info("مجموع نمونه‌های آموزش: %d (شامل %d واقعی با وزن %dx)",
                    len(combined), len(live_records), args.live_weight)

    if not combined:
        logger.warning("هیچ داده‌ی قابل‌استفاده‌ای پیدا نشد. فعلاً صبر کن سیگنال‌های بیشتری بسته بشن، یا از --combine-backtest استفاده کن.")
        return

    if len(live_records) < MIN_SAMPLES_RECOMMENDED and not args.combine_backtest:
        logger.warning(
            "فقط %d نمونه‌ی واقعی پیدا شد. برای یادگیری قابل‌اعتماد، حداقل %d نمونه پیشنهاد میشه؛ "
            "پیشنهاد میشه فعلاً از --combine-backtest استفاده کنی تا داده‌ی بیشتری داشته باشیم.",
            len(live_records), MIN_SAMPLES_RECOMMENDED,
        )

    train_and_save(combined)


if __name__ == "__main__":
    main()
