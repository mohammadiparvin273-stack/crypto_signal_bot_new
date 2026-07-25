"""
بک‌تست استراتژی روی داده‌ی تاریخی (تا چند سال، بسته به عمر جفت‌ارز روی صرافی).
توجه: صرافی‌ها معمولا هر درخواست را به ۱۰۰۰-۱۵۰۰ کندل محدود می‌کنند، پس برای گرفتن چند سال
داده‌ی ۱ ساعته/۴ ساعته باید paginate کنیم (این تابع خودش این کار رو انجام می‌ده).

خروجی: Win Rate, Profit Factor, Sharpe Ratio, Max Drawdown + دیتاست فیچر/نتیجه برای آموزش مدل AI.
این اسکریپت سنگینه (چند هزار کندل + محاسبه‌ی همه‌ی تحلیل‌ها) و باید **لوکال** اجرا بشه، نه داخل
تابع سرورلس Vercel (که محدودیت زمانی ۱۰-۶۰ ثانیه‌ای داره).
اجرا: `python -m bot.backtester --symbol BTC/USDT --timeframe 4h --years 3`
"""
import argparse
import time
import logging
import numpy as np
import pandas as pd
import ccxt

from bot.indicators import rsi, atr, adx
from bot.price_action import full_price_action_report, detect_bos_choch
from bot.smc import full_smc_report
from bot.volume_analysis import full_volume_report
from bot.scoring import compute_final_score

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("backtester")


def fetch_historical_ohlcv(symbol: str, timeframe: str, years: int, exchange_name="binance") -> pd.DataFrame:
    ex = getattr(ccxt, exchange_name)({"enableRateLimit": True})
    ms_per_candle = ex.parse_timeframe(timeframe) * 1000
    now = ex.milliseconds()
    since = now - years * 365 * 24 * 60 * 60 * 1000
    all_candles = []
    cursor = since
    while cursor < now:
        batch = ex.fetch_ohlcv(symbol, timeframe=timeframe, since=cursor, limit=1000)
        if not batch:
            break
        all_candles += batch
        last_ts = batch[-1][0]
        if last_ts == cursor:
            break
        cursor = last_ts + ms_per_candle
        time.sleep(ex.rateLimit / 1000)
        if len(batch) < 2:
            break
    df = pd.DataFrame(all_candles, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df = df.drop_duplicates(subset="timestamp").reset_index(drop=True)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    logger.info("دریافت شد: %d کندل برای %s %s", len(df), symbol, timeframe)
    return df


def simulate_strategy(df: pd.DataFrame, window: int = 250, rr_target_index: int = 1, holding_max: int = 50):
    """
    شبیه‌سازی ساده: در هر نقطه با یک پنجره‌ی گذشته (window کندل) امتیاز محاسبه میشه.
    اگه امتیاز کافی بود، فرض می‌کنیم وارد معامله می‌شیم (جهت بر اساس روند EMA200 در همون نقطه)،
    و نتیجه (برخورد به TP قبل از SL در `holding_max` کندل آینده) را برچسب می‌زنیم.
    خروجی: لیست رکوردهای {features, label, rr}
    """
    records = []
    close = df["close"]
    ema20_series = close.ewm(span=20, adjust=False).mean()
    ema50_series = close.ewm(span=50, adjust=False).mean()
    rsi_series = rsi(close)
    atr_series = atr(df)
    adx_series = adx(df)

    for i in range(window, len(df) - holding_max):
        sub = df.iloc[i - window:i + 1].reset_index(drop=True)

        # تعیین جهت با ساختار بازار (BOS/CHOCH) به‌جای EMA200 تاخیردار - هماهنگ با ربات زنده
        structure = detect_bos_choch(sub, left=3, right=3)
        current_trend = structure["current_trend"]
        if current_trend not in ("up", "down"):
            continue
        direction = "long" if current_trend == "up" else "short"

        # همون فیلتر EMA20/EMA50 + RSI که در ربات زنده هم اعمال میشه (برای تست منصفانه)
        rsi_now = float(rsi_series.iloc[i])
        ema20_now, ema50_now = float(ema20_series.iloc[i]), float(ema50_series.iloc[i])
        trend_confluence_ok = (
            (ema20_now > ema50_now and rsi_now > 50) if direction == "long"
            else (ema20_now < ema50_now and rsi_now < 50)
        )
        if not trend_confluence_ok:
            continue

        pa_report = full_price_action_report(sub)
        smc_report = full_smc_report(sub)
        # در بک‌تست ساده، تایید mtf واقعی (چند تایم‌فریم جدا) گرون است؛ این یک تقریب است.
        # مقدار باید 'up'/'down' باشه (نه 'long'/'short') چون score_trend همین قالب رو انتظار داره.
        mtf_trends = {"synthetic": "up" if direction == "long" else "down"}
        volume_report = full_volume_report(sub)

        # فیلتر Volume Profile (همون منطقی که در ربات زنده هست)
        vp_profile = volume_report.get("profile")
        if vp_profile and vp_profile.get("poc_price"):
            poc = vp_profile["poc_price"]
            entry_check = float(close.iloc[i])
            volume_profile_ok = (entry_check <= poc * 1.01) if direction == "long" else (entry_check >= poc * 0.99)
            if not volume_profile_ok:
                continue

        sentiment_report = {"news_sentiment": {"label": "neutral"}, "fear_greed": None}
        derivatives_report = {"open_interest_trend": {"state": "insufficient_data"}, "funding": {"bias": "neutral"}}

        score = compute_final_score(direction, mtf_trends, volume_report, sentiment_report,
                                     smc_report, pa_report, rsi_now, derivatives_report)
        if not score["should_send"]:
            continue

        entry = close.iloc[i]
        sl_dist = atr_series.iloc[i] * 1.5
        rr = [1.5, 2.5, 4.0][rr_target_index]
        if direction == "long":
            sl_price = entry - sl_dist
            tp_price = entry + sl_dist * rr
            future = df.iloc[i + 1:i + 1 + holding_max]
            hit_tp = (future["high"] >= tp_price).any()
            hit_sl = (future["low"] <= sl_price).any()
        else:
            sl_price = entry + sl_dist
            tp_price = entry - sl_dist * rr
            future = df.iloc[i + 1:i + 1 + holding_max]
            hit_tp = (future["low"] <= tp_price).any()
            hit_sl = (future["high"] >= sl_price).any()

        if hit_tp and hit_sl:
            tp_idx = future[future["high"] >= tp_price].index.min() if direction == "long" else future[future["low"] <= tp_price].index.min()
            sl_idx = future[future["low"] <= sl_price].index.min() if direction == "long" else future[future["high"] >= sl_price].index.min()
            label = 1 if tp_idx <= sl_idx else 0
        elif hit_tp:
            label = 1
        elif hit_sl:
            label = 0
        else:
            continue  # نه TP نه SL خورده - نادیده می‌گیریم

        records.append({
            "features": {
                "trend": score["breakdown"]["trend"], "volume": score["breakdown"]["volume"],
                "news": score["breakdown"]["news"], "liquidity": score["breakdown"]["liquidity"],
                "order_block": score["breakdown"]["order_block"], "rsi": score["breakdown"]["rsi"],
                "open_interest": score["breakdown"]["open_interest"], "funding": score["breakdown"]["funding"],
                "atr_pct": float(atr_series.iloc[i] / entry * 100) if entry else 0,
                "adx": float(adx_series.iloc[i]),
                "rr_target_2": rr,
            },
            "label": label,
            "rr": rr,
            "direction": direction,
        })
    return records


def compute_backtest_metrics(records: list, fee_slippage_r: float = 0.1) -> dict:
    """
    fee_slippage_r: هزینه‌ی تقریبی کارمزد صرافی + اسلیپیج اجرای دستی، به واحد R (ریسک هر معامله).
    مقدار پیش‌فرض ۰.۱R یعنی تقریباً معادل ۰.۱-۰.۲٪ قیمت (بسته به فاصله‌ی SL) از هر معامله کم میشه -
    چون این ربات معامله رو خودکار انجام نمی‌ده و بین تولید سیگنال و اجرای دستی، قیمت جابه‌جا میشه.
    """
    if not records:
        return {"trades": 0}

    def _metrics_for(subset):
        if not subset:
            return {"trades": 0}
        returns = [(r["rr"] - fee_slippage_r) if r["label"] == 1 else (-1 - fee_slippage_r) for r in subset]
        wins = [r for r in returns if r > 0]
        losses = [r for r in returns if r <= 0]
        win_rate = len(wins) / len(subset) * 100
        gross_profit = sum(wins)
        gross_loss = abs(sum(losses))
        profit_factor = (gross_profit / gross_loss) if gross_loss else float("inf")
        mean_r, std_r = np.mean(returns), np.std(returns)
        sharpe = (mean_r / std_r) * np.sqrt(len(returns)) if std_r else 0
        equity = np.cumsum(returns)
        running_max = np.maximum.accumulate(equity) if len(equity) else np.array([0])
        drawdown = running_max - equity
        max_drawdown = float(drawdown.max()) if len(drawdown) else 0
        return {
            "trades": len(subset),
            "win_rate_pct": round(win_rate, 2),
            "profit_factor": round(profit_factor, 2) if profit_factor != float("inf") else "inf",
            "sharpe_ratio": round(float(sharpe), 2),
            "max_drawdown_R": round(max_drawdown, 2),
        }

    longs = [r for r in records if r.get("direction") == "long"]
    shorts = [r for r in records if r.get("direction") == "short"]

    return {
        "overall": _metrics_for(records),
        "long_only": _metrics_for(longs),
        "short_only": _metrics_for(shorts),
        "note": f"در این محاسبه {fee_slippage_r}R هزینه‌ی کارمزد+اسلیپیج از هر معامله کم شده (عدد واقع‌بینانه‌تر)",
    }


def compute_factor_correlations(records: list) -> dict:
    """
    همبستگی (Pearson correlation) هر فاکتور امتیازدهی با نتیجه‌ی واقعی معامله (برد=۱ / باخت=۰).
    عدد نزدیک به +۱ یعنی هرچی اون فاکتور بیشتر بوده، برد بیشتر بوده (فاکتور مفید).
    عدد نزدیک به ۰ یعنی اون فاکتور تقریباً هیچ ربطی به نتیجه نداشته (کاندیدای کم‌کردن وزن).
    عدد منفی یعنی رابطه‌ی معکوس داشته (جای تعجب داره و باید بررسی بشه).
    """
    if not records:
        return {}
    factor_names = ["trend", "volume", "news", "liquidity", "order_block", "rsi", "open_interest", "funding"]
    labels = np.array([r["label"] for r in records])
    correlations = {}
    for factor in factor_names:
        values = np.array([r["features"].get(factor, 0) for r in records])
        if values.std() == 0:
            correlations[factor] = None
            continue
        corr = np.corrcoef(values, labels)[0, 1]
        correlations[factor] = round(float(corr), 3) if not np.isnan(corr) else None
    return dict(sorted(correlations.items(), key=lambda kv: (kv[1] is None, -(kv[1] or -999))))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="BTC/USDT")
    parser.add_argument("--timeframe", default="4h")
    parser.add_argument("--years", type=int, default=3)
    parser.add_argument("--train-model", action="store_true", help="بعد از بک‌تست، مدل AI را هم آموزش بده")
    args = parser.parse_args()

    df = fetch_historical_ohlcv(args.symbol, args.timeframe, args.years)
    records = simulate_strategy(df)
    metrics = compute_backtest_metrics(records)
    print("نتایج بک‌تست:", metrics)

    correlations = compute_factor_correlations(records)
    print("\nهمبستگی هر فاکتور با نتیجه‌ی واقعی معامله (برد/باخت):")
    print("(نزدیک +1 = فاکتور مفید | نزدیک 0 = بی‌تاثیر | منفی = رابطه‌ی معکوس)")
    for factor, corr in correlations.items():
        print(f"  {factor}: {corr}")

    if args.train_model and records:
        from bot.train_model import train_and_save
        train_and_save(records)


if __name__ == "__main__":
    main()
