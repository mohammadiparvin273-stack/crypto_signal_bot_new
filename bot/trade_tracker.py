"""
ردیابی نتیجه‌ی سیگنال‌های قبلاً ارسال‌شده، با پشتیبانی از «حد ضرر بی‌ریسک» (Break-even):
- وقتی TP1 فعال بشه: معامله بسته نمیشه (چون کاربر دستی معامله می‌کنه، نه ربات)، بلکه یه پیام
  می‌فرستیم که بگه "بخشی از سود رو ذخیره کن و SL رو ببر روی نقطه‌ی ورود (بی‌ریسک)".
- وقتی TP2 فعال بشه: SL داخلی رو می‌بریم روی TP1 (trailing) و پیام می‌فرستیم.
- وقتی TP3 فعال بشه یا SL/Break-even بخوره: معامله «بسته» ثبت میشه و نتیجه‌ی نهایی گزارش میشه.
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
    stage = trade.get("stage", 0)  # 0 = هیچ‌کدوم نخورده، 1 = TP1 خورده، 2 = TP2 خورده
    current_sl = trade["stop_loss"]  # ممکنه از قبل به breakeven/TP1 آپدیت شده باشه
    tp1 = trade["take_profit_1"]
    tp2 = trade.get("take_profit_2")
    tp3 = trade.get("take_profit_3")
    entry = trade["entry"]

    def hit_sl():
        return (price <= current_sl) if direction == "long" else (price >= current_sl)

    # اول چک کن SL (یا breakeven/trailing stop) خورده یا نه - همیشه اولویت با محافظت از سرمایه‌ست
    if hit_sl():
        outcome = "stop_loss" if stage == 0 else ("breakeven_stop" if stage == 1 else "trailing_stop_after_tp2")
        pnl_pct = ((price - entry) / entry * 100) if direction == "long" else ((entry - price) / entry * 100)
        return {"type": "closed", "outcome": outcome, "price": price, "pnl_pct": round(pnl_pct, 2)}

    # بعد چک کن آیا به هدف بعدی رسیده
    if stage == 0:
        reached = (price >= tp1) if direction == "long" else (price <= tp1)
        if reached:
            return {"type": "partial", "stage": 1, "hit": "take_profit_1", "price": price, "new_sl": entry}
    elif stage == 1 and tp2:
        reached = (price >= tp2) if direction == "long" else (price <= tp2)
        if reached:
            return {"type": "partial", "stage": 2, "hit": "take_profit_2", "price": price, "new_sl": tp1}
    elif stage >= 1 and tp3:
        reached = (price >= tp3) if direction == "long" else (price <= tp3)
        if reached:
            pnl_pct = ((price - entry) / entry * 100) if direction == "long" else ((entry - price) / entry * 100)
            return {"type": "closed", "outcome": "take_profit_3", "price": price, "pnl_pct": round(pnl_pct, 2)}

    return None


def check_open_trades() -> list:
    """
    همه‌ی سیگنال‌های «باز» رو چک می‌کنه. سه نوع اتفاق ممکنه بیفته:
    ۱) هدف جزئی (TP1/TP2) خورده -> پیام «بی‌ریسک کن / trail کن» می‌فرستیم، معامله باز می‌مونه
    ۲) SL/Break-even/Trailing-stop خورده -> معامله می‌بندیم، نتیجه‌ی نهایی گزارش میشه
    ۳) TP3 خورده -> معامله می‌بندیم، نتیجه‌ی نهایی گزارش میشه
    خروجی: لیست معاملاتی که کاملاً بسته شدن (برای آمار Win Rate).
    """
    trades = storage.get_trade_log()
    if not trades:
        return []

    updated = False
    closed_now = []
    close_labels = {
        "stop_loss": ("❌", "حد ضرر (SL)"),
        "breakeven_stop": ("⚪", "برگشت به نقطه‌ی ورود (بی‌ریسک - نه سود نه ضرر)"),
        "trailing_stop_after_tp2": ("✅", "برگشت به سطح TP1 بعد از رسیدن به TP2 (سود جزئی قفل‌شده)"),
        "take_profit_3": ("🎯", "هدف سوم (TP3) - کامل"),
    }

    for t in trades:
        if t.get("status", "open") != "open":
            continue
        result = _check_single_trade(t)
        if not result:
            continue
        updated = True

        if result["type"] == "partial":
            t["stage"] = result["stage"]
            t["stop_loss"] = result["new_sl"]
            if result["hit"] == "take_profit_1":
                msg = (
                    f"✅ هدف اول (TP1) فعال شد — {t['symbol']} ({t['timeframe']})\n\n"
                    f"قیمت فعلی: {result['price']}\n\n"
                    f"🔒 پیشنهاد: بخشی از پوزیشن رو ببند (مثلاً نصف) و حد ضرر باقی‌مانده رو ببر روی "
                    f"نقطه‌ی ورود ({t['entry']}) — این‌جوری از این‌جا به بعد، بدترین حالت برات "
                    f"«نه سود نه ضرر»ه، نه ضرر کامل."
                )
            else:  # take_profit_2
                msg = (
                    f"✅✅ هدف دوم (TP2) فعال شد — {t['symbol']} ({t['timeframe']})\n\n"
                    f"قیمت فعلی: {result['price']}\n\n"
                    f"🔒 پیشنهاد: حد ضرر باقی‌مانده رو ببر روی سطح TP1 ({t['take_profit_1']}) — "
                    f"سود بخش باقی‌مانده رو قفل کن و بذار تا TP3 هم فرصت داشته باشه."
                )
            send_telegram_message(msg, channel_id=TELEGRAM_CHANNEL_ID, reply_to_message_id=t.get("telegram_message_id"))

        else:  # closed
            t["status"] = "closed"
            t["outcome"] = result["outcome"]
            t["exit_price"] = result["price"]
            t["pnl_pct"] = result["pnl_pct"]
            closed_now.append(t)

            emoji, label = close_labels[result["outcome"]]
            msg = (
                f"{emoji} نتیجه‌ی نهایی سیگنال {t['symbol']} ({t['timeframe']})\n\n"
                f"{label}\n"
                f"قیمت ورود: {t['entry']}\n"
                f"قیمت خروج: {result['price']}\n"
                f"سود/ضرر کل معامله: {result['pnl_pct']}%"
            )
            send_telegram_message(msg, channel_id=TELEGRAM_CHANNEL_ID, reply_to_message_id=t.get("telegram_message_id"))

    if updated:
        storage.set_value("trade_log", trades)

    return closed_now
