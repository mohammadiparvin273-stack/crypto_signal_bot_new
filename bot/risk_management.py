"""
مدیریت سرمایه: محاسبه‌ی Entry/SL/TP1-3/Position Size/RR و بررسی محدودیت‌های
Max Daily Loss / Max Weekly Loss / Max Drawdown (با کمک storage.py برای وضعیت فعلی حساب)
"""
from bot.config import ACCOUNT_BALANCE_USDT, RISK_PER_TRADE_PCT, RR_TARGETS


def compute_trade_plan(entry_price: float, atr_value: float, direction: str,
                        account_balance: float = ACCOUNT_BALANCE_USDT,
                        risk_pct: float = RISK_PER_TRADE_PCT) -> dict:
    """
    Stop Loss بر مبنای ATR (استاندارد صنعتی: 1.5x ATR فاصله از نقطه‌ی ورود)
    Take Profit ها بر مبنای نسبت‌های RR تعریف‌شده در config.
    Position Size طوری محاسبه میشه که اگه SL بخوره، فقط risk_pct از سرمایه از دست بره.
    """
    sl_distance = atr_value * 1.5
    risk_amount = account_balance * (risk_pct / 100)

    if direction == "long":
        stop_loss = entry_price - sl_distance
        take_profits = [entry_price + sl_distance * rr for rr in RR_TARGETS]
    else:
        stop_loss = entry_price + sl_distance
        take_profits = [entry_price - sl_distance * rr for rr in RR_TARGETS]

    position_size_quote = risk_amount / (sl_distance / entry_price) if entry_price else 0
    position_size_units = position_size_quote / entry_price if entry_price else 0

    return {
        "entry": round(entry_price, 6),
        "stop_loss": round(stop_loss, 6),
        "take_profit_1": round(take_profits[0], 6),
        "take_profit_2": round(take_profits[1], 6),
        "take_profit_3": round(take_profits[2], 6),
        "risk_reward_ratios": RR_TARGETS,
        "risk_amount_usdt": round(risk_amount, 2),
        "position_size_usdt": round(position_size_quote, 2),
        "position_size_units": round(position_size_units, 6),
        "risk_pct_of_balance": risk_pct,
    }


def check_risk_limits(today_pnl_pct: float, week_pnl_pct: float, current_drawdown_pct: float,
                        max_daily_loss_pct: float, max_weekly_loss_pct: float, max_drawdown_pct: float) -> dict:
    """
    بررسی این‌که آیا مجاز به ارسال سیگنال جدید هستیم یا به یکی از حدهای ریسک رسیدیم.
    (امتیاز امروز/هفته/drawdown باید از storage.py خونده بشه و اینجا پاس داده بشه)
    """
    blocks = []
    if today_pnl_pct <= -abs(max_daily_loss_pct):
        blocks.append("max_daily_loss_reached")
    if week_pnl_pct <= -abs(max_weekly_loss_pct):
        blocks.append("max_weekly_loss_reached")
    if current_drawdown_pct >= abs(max_drawdown_pct):
        blocks.append("max_drawdown_reached")
    return {"trading_allowed": len(blocks) == 0, "blocks": blocks}
