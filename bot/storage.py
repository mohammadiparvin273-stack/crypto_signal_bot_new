"""
چون توابع سرورلس روی Vercel هیچ حافظه‌ی پایدار (persistent disk) ندارن، برای ثبت تاریخچه‌ی
معاملات و آمار داشبورد باید از یک دیتابیس بیرونی استفاده کرد.
Upstash Redis انتخاب شده چون: کاملاً رایگان تا سقف مشخص، REST API ساده (بدون نیاز به TCP connection
که در سرورلس مشکل‌سازه)، و ست‌آپش دو دقیقه طول می‌کشه (upstash.com -> Create Database -> کپی URL/Token).

اگه UPSTASH تنظیم نشده باشه، این ماژول silently غیرفعال میشه (ربات کار می‌کنه ولی دشبورد/تاریخچه نداره).
"""
import json
import logging
import time
import requests
from bot.config import UPSTASH_REDIS_REST_URL, UPSTASH_REDIS_REST_TOKEN

logger = logging.getLogger("storage")

_ENABLED = bool(UPSTASH_REDIS_REST_URL and UPSTASH_REDIS_REST_TOKEN)


def is_enabled() -> bool:
    return _ENABLED


def _headers():
    return {"Authorization": f"Bearer {UPSTASH_REDIS_REST_TOKEN}"}


def _request(*parts):
    if not _ENABLED:
        return None
    url = f"{UPSTASH_REDIS_REST_URL}/{'/'.join(parts)}"
    try:
        resp = requests.get(url, headers=_headers(), timeout=10)
        resp.raise_for_status()
        return resp.json().get("result")
    except Exception as e:
        logger.warning("upstash request failed (%s): %s", parts, e)
        return None


def set_value(key: str, value) -> bool:
    if not _ENABLED:
        return False
    payload = json.dumps(value) if not isinstance(value, str) else value
    result = _request("set", key, requests.utils.quote(payload, safe=""))
    return result is not None


def get_value(key: str, default=None):
    result = _request("get", key)
    if result is None:
        return default
    try:
        return json.loads(result)
    except (json.JSONDecodeError, TypeError):
        return result


def push_trade_log(trade_record: dict):
    """ثبت هر سیگنال ارسالی در یک لیست (برای بک‌تست زنده و داشبورد)"""
    if not _ENABLED:
        return
    key = "trade_log"
    existing = get_value(key, default=[])
    if not isinstance(existing, list):
        existing = []
    trade_record["logged_at"] = int(time.time())
    existing.append(trade_record)
    existing = existing[-500:]  # فقط ۵۰۰ رکورد اخیر رو نگه می‌داریم (محدودیت حجم رایگان Upstash)
    set_value(key, existing)


def get_trade_log() -> list:
    return get_value("trade_log", default=[])


def get_daily_weekly_pnl_pct():
    """
    این تابع باید بر اساس نتایج واقعی معاملات (که کاربر خودش دستی انجام می‌ده) آپدیت بشه.
    چون ربات خودکار ترید نمی‌کنه، این مقدار باید از طریق یک endpoint جدا (مثلا /api/report-trade)
    توسط خود کاربر گزارش بشه. فعلا مقدار صفر برمی‌گردونه تا ربات بدون کرش کار کنه.
    """
    return get_value("today_pnl_pct", default=0.0), get_value("week_pnl_pct", default=0.0), get_value("current_drawdown_pct", default=0.0)


# ---------- کول‌داون بین سیگنال‌های یک ارز ----------

def get_last_signal_time(symbol: str) -> float:
    """آخرین زمانی (unix timestamp) که برای این نماد سیگنال فرستاده شده"""
    return get_value(f"last_signal_time:{symbol}", default=0.0)


def set_last_signal_time(symbol: str, ts: float):
    set_value(f"last_signal_time:{symbol}", ts)


# ---------- ردیابی معاملات باز (برای گزارش نتیجه‌ی TP/SL) ----------

def get_open_signals() -> list:
    return get_value("open_signals", default=[])


def add_open_signal(signal_record: dict):
    open_signals = get_open_signals()
    if not isinstance(open_signals, list):
        open_signals = []
    open_signals.append(signal_record)
    open_signals = open_signals[-200:]  # محدودیت حجم
    set_value("open_signals", open_signals)


def set_open_signals(open_signals: list):
    set_value("open_signals", open_signals[-200:])


def push_closed_trade(trade_record: dict):
    key = "closed_trades"
    existing = get_value(key, default=[])
    if not isinstance(existing, list):
        existing = []
    trade_record["closed_at"] = int(time.time())
    existing.append(trade_record)
    existing = existing[-500:]
    set_value(key, existing)


def get_closed_trades() -> list:
    return get_value("closed_trades", default=[])
