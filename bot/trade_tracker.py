"""
ردیابی نتیجه‌ی سیگنال‌های قبلاً ارسال‌شده: هر بار که ربات اجرا میشه، قیمت فعلی هر سیگنال
«باز» (هنوز به TP یا SL نخورده) رو چک می‌کنه؛ اگه برخورد کرده، نتیجه (چند درصد سود/ضرر) رو
به کانال گزارش می‌کنه و وضعیتش رو توی حافظه به‌روزرسانی می‌کنه (برای دقیق شدن Win Rate داشبورد).
"""
import logging
from bot import storage
from bot.data_fetcher import fetch_last_price
from bot.notifier import send_telegram_message
from bot.config import TELEGRAM_CHANNEL_ID

logger = logging.getLogger("trade_tracker")


def _check_single_trade(trade: dict) -> dict | None:
    price = fetch_last_price(trade["symbol"])
    if price is None:
        return None

    direction = trade["direction"]
    sl = trade["stop_loss"]
    tp1 = trade["take_profit_1"]
    tp2 = trade.get("take_profit_2")
    tp3 = trade.get("take_profit_3")
    entry = trade["entry"]

    hit = None
    if direction == "long":
        if price <= sl:
            hit = "stop_loss"
        elif tp3 and price >= tp3:
            hit = "take_profit_3"
        elif tp2 and price >= tp2:
            hit = "take_profit_2"
        elif price >= tp1:
            hit = "take_profit_1"
    else:
        if price >= sl:
            hit = "stop_loss"
        elif tp3 and price <= tp3:
            hit = "take_profit_3"
        elif tp2 and price <= tp2:
            hit = "take_profit_2"
        elif price <= tp1:
            hit = "take_profit_1"

    if not hit:
        return None

    pnl_pct = ((price - entry) / entry * 100) if direction == "long" else ((entry - price) / entry * 100)
    return {"hit": hit, "price": price, "pnl_pct": round(pnl_pct, 2)}


def check_open_trades() -> list:
    """
    همه‌ی سیگنال‌های «باز» رو چک می‌کنه. اگه به TP یا SL خورده باشن، نتیجه رو به تلگرام
    می‌فرسته و رکورد رو به‌روزرسانی می‌کنه. خروجی: لیست نتایجی که همین الان بسته شدن.
    """
    trades = storage.get_trade_log()
    if not trades:
        return []

    updated = False
    closed_now = []
    hit_labels = {
        "stop_loss": ("❌", "حد ضرر (SL)"),
        "take_profit_1": ("✅", "هدف اول (TP1)"),
        "take_profit_2": ("✅✅", "هدف دوم (TP2)"),
        "take_profit_3": ("🎯", "هدف سوم (TP3)"),
    }

    for t in trades:
        if t.get("status", "open") != "open":
            continue
        outcome = _check_single_trade(t)
        if not outcome:
            continue

        t["status"] = "closed"
        t["outcome"] = outcome["hit"]
        t["exit_price"] = outcome["price"]
        t["pnl_pct"] = outcome["pnl_pct"]
        updated = True
        closed_now.append(t)

        emoji, label = hit_labels[outcome["hit"]]
        msg = (
            f"{emoji} نتیجه‌ی سیگنال {t['symbol']} ({t['timeframe']})\n\n"
            f"{label} فعال شد\n"
            f"قیمت ورود: {t['entry']}\n"
            f"قیمت خروج: {outcome['price']}\n"
            f"سود/ضرر این معامله: {outcome['pnl_pct']}%"
        )
        send_telegram_message(msg, channel_id=TELEGRAM_CHANNEL_ID, reply_to_message_id=t.get("telegram_message_id"))

    if updated:
        storage.set_value("trade_log", trades)

    return closed_now
