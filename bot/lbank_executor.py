"""
اجرای واقعی معامله روی LBank (فیوچرز). این ماژول با احتیاط کامل نوشته شده چون پول واقعیه:

۱. پیش‌فرض DRY_RUN=true: هیچ سفارش واقعی زده نمیشه، فقط محاسبات (مقدار پوزیشن، مارجین، SL/TP)
   دقیقاً همون‌جوری که قراره واقعی زده بشه محاسبه و برگردونده میشه تا بتونی تاییدشون کنی.
۲. فقط با تایید صریح کاربر (زدن دکمه‌ی Confirm در تلگرام) سفارش زده میشه - هیچ‌وقت خودکار نیست.
۳. هر خطا/شکست به‌وضوح گزارش میشه و چیزی silently نادیده گرفته نمیشه.
"""
import logging
import ccxt

from bot.config import LBANK_API_KEY, LBANK_API_SECRET, LBANK_DRY_RUN

logger = logging.getLogger("lbank_executor")

_exchange = None


def get_lbank_exchange():
    global _exchange
    if _exchange is None:
        if not LBANK_API_KEY or not LBANK_API_SECRET:
            raise RuntimeError("LBANK_API_KEY / LBANK_API_SECRET تنظیم نشده - اول اینا رو توی Vercel بذار.")
        _exchange = ccxt.lbank2({
            "apiKey": LBANK_API_KEY,
            "secret": LBANK_API_SECRET,
            "enableRateLimit": True,
            "options": {"defaultType": "swap"},  # فیوچرز/پرپچوال
        })
    return _exchange


def compute_order_plan(symbol: str, direction: str, usd_amount: float, leverage: int,
                        entry: float, stop_loss: float, take_profit_1: float,
                        take_profit_2: float, take_profit_3: float) -> dict:
    """
    محاسبه‌ی دقیق مقدار پوزیشن و مارجین لازم - این تابع همیشه اجرا میشه (چه DRY_RUN چه واقعی)
    تا بشه قبل از ارسال سفارش واقعی، اعداد رو به کاربر نشون داد و تاییدش رو گرفت.
    """
    position_size_usdt = usd_amount * leverage  # ارزش کل پوزیشن با اهرم
    position_size_units = position_size_usdt / entry if entry else 0
    margin_required = usd_amount  # همون مبلغی که کاربر انتخاب کرده، مارجین اولیه‌ست

    liquidation_distance_pct = (1 / leverage) * 0.9  # تقریبی، با کمی حاشیه‌ی اطمینان
    liquidation_price = (
        entry * (1 - liquidation_distance_pct) if direction == "long"
        else entry * (1 + liquidation_distance_pct)
    )

    return {
        "symbol": symbol, "direction": direction, "leverage": leverage,
        "margin_usdt": round(margin_required, 2),
        "position_size_usdt": round(position_size_usdt, 2),
        "position_size_units": round(position_size_units, 6),
        "entry": entry, "stop_loss": stop_loss,
        "take_profit_1": take_profit_1, "take_profit_2": take_profit_2, "take_profit_3": take_profit_3,
        "estimated_liquidation_price": round(liquidation_price, 6),
    }


def execute_trade(plan: dict) -> dict:
    """
    اجرای واقعی (یا شبیه‌سازی‌شده اگه DRY_RUN باشه): باز کردن پوزیشن + گذاشتن SL + گذاشتن TP1.
    (TP2/TP3 و بی‌ریسک‌سازی بعداً توسط trade_tracker مدیریت میشه)
    """
    if LBANK_DRY_RUN:
        logger.info("DRY_RUN فعاله - سفارش واقعی زده نشد. پلن: %s", plan)
        return {"ok": True, "dry_run": True, "plan": plan, "message": "این فقط شبیه‌سازی بود، سفارش واقعی زده نشد."}

    try:
        ex = get_lbank_exchange()
        symbol = plan["symbol"]
        side = "buy" if plan["direction"] == "long" else "sell"
        opposite_side = "sell" if plan["direction"] == "long" else "buy"

        ex.set_leverage(plan["leverage"], symbol)

        entry_order = ex.create_order(symbol, "market", side, plan["position_size_units"])

        sl_order = ex.create_order(
            symbol, "stop_market", opposite_side, plan["position_size_units"],
            params={"stopPrice": plan["stop_loss"], "reduceOnly": True},
        )

        tp1_order = ex.create_order(
            symbol, "limit", opposite_side, plan["position_size_units"] / 2,  # نصف پوزیشن روی TP1
            plan["take_profit_1"], params={"reduceOnly": True},
        )

        return {
            "ok": True, "dry_run": False,
            "entry_order_id": entry_order.get("id"),
            "sl_order_id": sl_order.get("id"),
            "tp1_order_id": tp1_order.get("id"),
            "plan": plan,
        }
    except Exception as e:
        logger.error("اجرای معامله روی LBank شکست خورد: %s", e)
        return {"ok": False, "error": str(e), "plan": plan}


def move_stop_to_breakeven(symbol: str, direction: str, sl_order_id: str, entry_price: float, remaining_units: float) -> dict:
    """لغو SL قبلی و گذاشتن SL جدید روی نقطه‌ی ورود (بی‌ریسک‌سازی بعد از TP1)"""
    if LBANK_DRY_RUN:
        logger.info("DRY_RUN: شبیه‌سازی انتقال SL به %s برای %s", entry_price, symbol)
        return {"ok": True, "dry_run": True}
    try:
        ex = get_lbank_exchange()
        opposite_side = "sell" if direction == "long" else "buy"
        try:
            ex.cancel_order(sl_order_id, symbol)
        except Exception as e:
            logger.warning("لغو SL قبلی شکست خورد (شاید قبلاً اجرا شده): %s", e)
        new_sl = ex.create_order(
            symbol, "stop_market", opposite_side, remaining_units,
            params={"stopPrice": entry_price, "reduceOnly": True},
        )
        return {"ok": True, "new_sl_order_id": new_sl.get("id")}
    except Exception as e:
        logger.error("انتقال SL به breakeven شکست خورد: %s", e)
        return {"ok": False, "error": str(e)}


def close_partial_position(symbol: str, direction: str, units: float) -> dict:
    """بستن بخشی از پوزیشن (مثلاً نصف، وقتی TP1 می‌خوره)"""
    if LBANK_DRY_RUN:
        logger.info("DRY_RUN: شبیه‌سازی بستن %s واحد از %s", units, symbol)
        return {"ok": True, "dry_run": True}
    try:
        ex = get_lbank_exchange()
        opposite_side = "sell" if direction == "long" else "buy"
        order = ex.create_order(symbol, "market", opposite_side, units, params={"reduceOnly": True})
        return {"ok": True, "order_id": order.get("id")}
    except Exception as e:
        logger.error("بستن جزئی پوزیشن شکست خورد: %s", e)
        return {"ok": False, "error": str(e)}
