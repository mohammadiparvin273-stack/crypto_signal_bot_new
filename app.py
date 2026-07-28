"""
نقطه‌ی ورود اصلی سرورلس روی Vercel (روش فعلی Vercel: یک اپ Flask با چند route،
نه چند فایل جدا زیر api/). این فایل باید در ریشه‌ی پروژه (کنار vercel.json) باشه.

اندپوینت‌ها:
  GET/POST /api/scan            -> اسکن و ارسال سیگنال
  GET      /api/dashboard       -> آمار داشبورد
  POST     /api/report-trade    -> ثبت دستی نتیجه‌ی معامله
  POST     /api/reset-period    -> ریست شمارنده‌ی روزانه/هفتگی
  GET      /                    -> health check ساده
"""
import sys
import os
import datetime
from collections import defaultdict
from flask import Flask, request, jsonify

sys.path.insert(0, os.path.dirname(__file__))

from bot.config import CRON_SECRET, SYMBOLS, SIGNAL_COOLDOWN_HOURS, ACCOUNT_BALANCE_USDT, RISK_PER_TRADE_PCT  # noqa: E402
from bot.config import MAX_DAILY_LOSS_PCT, MAX_WEEKLY_LOSS_PCT, MAX_DRAWDOWN_PCT  # noqa: E402
from bot.signal_engine import scan_all_symbols  # noqa: E402
from bot.notifier import broadcast_signal, broadcast_news_text, format_news_digest  # noqa: E402
from bot.sentiment import full_sentiment_report  # noqa: E402
from bot.trade_tracker import check_open_trades  # noqa: E402
from bot.risk_management import check_risk_limits, compute_trade_plan  # noqa: E402
from bot import storage  # noqa: E402

app = Flask(__name__)


def _authorized() -> bool:
    if not CRON_SECRET:
        return True
    return request.headers.get("Authorization", "") == f"Bearer {CRON_SECRET}"


@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "crypto-signal-bot"})


@app.route("/api/scan", methods=["GET", "POST"])
def scan():
    if not _authorized():
        return jsonify({"error": "unauthorized"}), 401

    symbols_param = request.args.get("symbols")
    symbols = [s.strip() for s in symbols_param.split(",") if s.strip()] if symbols_param else SYMBOLS

    # چرخش نوبتی: هر اجرا از یه نقطه‌ی متفاوت لیست شروع میشه، تا اگه زمان کم آمد،
    # همیشه فقط ارزهای اول لیست بررسی نشن و بقیه هیچ‌وقت شانس نداشته باشن.
    start_idx = int(storage.get_value("scan_start_index", default=0) or 0)
    if start_idx >= len(symbols):
        start_idx = 0
    symbols = symbols[start_idx:] + symbols[:start_idx]
    storage.set_value("scan_start_index", (start_idx + 1) % max(len(symbols), 1))

    try:
        # قدم ۱: اول ببینیم سیگنال‌های قبلی به TP یا SL خوردن یا نه، و نتیجه رو گزارش کنیم
        closed_trades = []
        try:
            closed_trades = check_open_trades()
        except Exception as e:
            app.logger.error("بررسی نتیجه‌ی سیگنال‌های باز شکست خورد: %s", e)

        # قدم ۲: بررسی محدودیت‌های ریسک (Max Daily/Weekly Loss, Max Drawdown) - قبل از هر اسکنی
        today_pnl, week_pnl, drawdown = storage.get_daily_weekly_pnl_pct()
        risk_check = check_risk_limits(today_pnl, week_pnl, drawdown,
                                        MAX_DAILY_LOSS_PCT, MAX_WEEKLY_LOSS_PCT, MAX_DRAWDOWN_PCT)
        if not risk_check["trading_allowed"]:
            app.logger.warning("ارسال سیگنال متوقف شد به‌خاطر رسیدن به محدودیت ریسک: %s", risk_check["blocks"])
            return jsonify({
                "trading_allowed": False,
                "blocks": risk_check["blocks"],
                "previous_trades_closed_now": len(closed_trades),
                "message": "به یکی از حدهای ریسک (روزانه/هفتگی/Drawdown) رسیدیم؛ فعلاً سیگنال جدیدی ارسال نمیشه.",
            })

        # موجودی واقعی فعلی (سرمایه‌ی اولیه + سود/زیان انباشته) برای محاسبه‌ی دقیق‌تر Position Size
        cum_equity_pct = storage.get_value("cum_equity_pct", default=0.0) or 0.0
        current_balance = ACCOUNT_BALANCE_USDT * (1 + cum_equity_pct / 100)

        # قدم ۳: اسکن نمادها برای سیگنال‌های جدید
        signals, direction_tally, rejection_stats = scan_all_symbols(symbols)

        # محافظ مهم: اگه یه نماد توی چند تایم‌فریم هم‌زمان (مثلاً هم ۵m هم ۱۵m) واجد شرایط شد،
        # فقط بهترین (بالاترین امتیاز) یکیش نگه داشته میشه - تا کاربر با چند سیگنال هم‌زمان
        # از یک نماد (که گیج‌کننده و تکراریه) روبه‌رو نشه.
        best_per_symbol = {}
        for sig in signals:
            symbol = sig["symbol"]
            if symbol not in best_per_symbol or sig["score"]["total_score"] > best_per_symbol[symbol]["score"]["total_score"]:
                best_per_symbol[symbol] = sig
        deduped_signals = list(best_per_symbol.values())
        duplicates_skipped = len(signals) - len(deduped_signals)

        sent = []
        skipped_cooldown = []
        now = datetime.datetime.utcnow().timestamp()

        for sig in deduped_signals:
            symbol = sig["symbol"]
            cooldown_key = f"last_signal_ts:{symbol}"
            last_ts = storage.get_value(cooldown_key, default=0) or 0
            hours_since_last = (now - last_ts) / 3600 if last_ts else 999

            if hours_since_last < SIGNAL_COOLDOWN_HOURS:
                skipped_cooldown.append(f"{symbol} (هنوز {round(SIGNAL_COOLDOWN_HOURS - hours_since_last, 1)} ساعت مونده)")
                continue

            # محاسبه‌ی دوباره‌ی trade_plan با موجودی واقعی فعلی (نه عدد ثابت اولیه‌ی کانفیگ)
            atr_value = sig["trade_plan"].get("_atr_value")
            if atr_value:
                sig["trade_plan"] = compute_trade_plan(
                    sig["trade_plan"]["entry"], atr_value, sig["direction"],
                    account_balance=current_balance, risk_pct=RISK_PER_TRADE_PCT,
                )

            try:
                telegram_message_id = broadcast_signal(sig)
                if telegram_message_id is None:
                    app.logger.error("ارسال تلگرام برای %s شکست خورد - این سیگنال ثبت نشد.", symbol)
                    continue

                storage.push_trade_log({
                    "symbol": symbol, "timeframe": sig["timeframe"], "direction": sig["direction"],
                    "grade": sig["grade"], "score": sig["score"]["total_score"],
                    "entry": sig["trade_plan"]["entry"], "stop_loss": sig["trade_plan"]["stop_loss"],
                    "take_profit_1": sig["trade_plan"]["take_profit_1"],
                    "take_profit_2": sig["trade_plan"]["take_profit_2"],
                    "take_profit_3": sig["trade_plan"]["take_profit_3"],
                    "ai_probability": sig["ai_probability"]["probability_pct"],
                    "status": "open",
                    "telegram_message_id": telegram_message_id,
                    "score_breakdown": sig["score"]["breakdown"],
                    "extra_features": sig["extra_features"],
                })
                storage.set_value(cooldown_key, now)
                sent.append(f"{symbol} ({sig['timeframe']}, {sig['grade']})")
            except Exception as e:
                app.logger.error("خطا در ارسال سیگنال %s: %s", symbol, e)

        return jsonify({
            "scanned_symbols": len(symbols),
            "signals_found": len(signals),
            "duplicate_timeframe_signals_skipped": duplicates_skipped,
            "signals_sent": sent,
            "skipped_due_to_cooldown": skipped_cooldown,
            "previous_trades_closed_now": len(closed_trades),
            "diagnostic_direction_tally": direction_tally,
            "diagnostic_rejection_reasons": rejection_stats,
        })
    except Exception as e:
        app.logger.exception("scan failed")
        return jsonify({"error": str(e)}), 500


@app.route("/api/export-trades", methods=["GET"])
def export_trades():
    """خروجی کامل تاریخچه‌ی سیگنال‌ها (برای استفاده در آموزش مدل AI با bot/retrain_from_live.py)"""
    if not _authorized():
        return jsonify({"error": "unauthorized"}), 401
    trades = storage.get_trade_log()
    return jsonify({"trades": trades, "count": len(trades)})


@app.route("/api/dashboard", methods=["GET"])
def dashboard():
    try:
        trades = storage.get_trade_log()
        if not trades:
            return jsonify({"message": "هنوز معامله‌ای ثبت نشده", "trades": 0})

        total = len(trades)
        closed = [t for t in trades if t.get("status") == "closed"]
        wins = [t for t in closed if t.get("outcome", "").startswith("take_profit")]
        losses = [t for t in closed if t.get("outcome") == "stop_loss"]
        win_rate = round(len(wins) / len(closed) * 100, 1) if closed else None
        avg_pnl = round(sum(t.get("pnl_pct", 0) for t in closed) / len(closed), 2) if closed else None

        by_symbol_score = defaultdict(list)
        for t in trades:
            by_symbol_score[t["symbol"]].append(t["score"])

        best_symbol = max(by_symbol_score, key=lambda s: sum(by_symbol_score[s]) / len(by_symbol_score[s]))
        worst_symbol = min(by_symbol_score, key=lambda s: sum(by_symbol_score[s]) / len(by_symbol_score[s]))
        today_pnl, week_pnl, drawdown = storage.get_daily_weekly_pnl_pct()

        return jsonify({
            "total_signals_sent": total,
            "still_open": total - len(closed),
            "closed_trades": len(closed),
            "real_win_rate_pct": win_rate,
            "wins": len(wins),
            "losses": len(losses),
            "average_pnl_pct_per_closed_trade": avg_pnl,
            "today_pnl_pct": today_pnl,
            "week_pnl_pct": week_pnl,
            "current_drawdown_pct": drawdown,
            "average_score": round(sum(t["score"] for t in trades) / total, 1),
            "average_ai_probability": round(sum(t["ai_probability"] for t in trades) / total, 1),
            "best_symbol_by_avg_score": best_symbol,
            "worst_symbol_by_avg_score": worst_symbol,
            "recent_signals": trades[-10:],
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/news", methods=["GET", "POST"])
def news():
    if not _authorized():
        return jsonify({"error": "unauthorized"}), 401
    try:
        report = full_sentiment_report()
        text = format_news_digest(report)
        sent = broadcast_news_text(text)
        return jsonify({"sent": bool(sent), "text": text})
    except Exception as e:
        app.logger.exception("news broadcast failed")
        return jsonify({"error": str(e)}), 500


@app.route("/api/report-trade", methods=["POST"])
def report_trade():
    if not _authorized():
        return jsonify({"error": "unauthorized"}), 401
    try:
        payload = request.get_json(force=True)
        pnl_pct = float(payload["pnl_pct"])

        today = storage.get_value("today_pnl_pct", default=0.0) + pnl_pct
        week = storage.get_value("week_pnl_pct", default=0.0) + pnl_pct
        peak_equity = storage.get_value("peak_equity_pct", default=0.0)
        cum_equity = storage.get_value("cum_equity_pct", default=0.0) + pnl_pct
        peak_equity = max(peak_equity, cum_equity)
        drawdown = max(0.0, peak_equity - cum_equity)

        storage.set_value("today_pnl_pct", today)
        storage.set_value("week_pnl_pct", week)
        storage.set_value("cum_equity_pct", cum_equity)
        storage.set_value("peak_equity_pct", peak_equity)
        storage.set_value("current_drawdown_pct", drawdown)
        storage.set_value("last_report_at", datetime.datetime.utcnow().isoformat())

        return jsonify({"ok": True, "today_pnl_pct": today, "week_pnl_pct": week, "drawdown_pct": drawdown})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/reset-period", methods=["POST"])
def reset_period():
    if not _authorized():
        return jsonify({"error": "unauthorized"}), 401
    period = request.args.get("period", "daily")
    if period == "daily":
        storage.set_value("today_pnl_pct", 0.0)
    elif period == "weekly":
        storage.set_value("week_pnl_pct", 0.0)
    return jsonify({"ok": True, "reset": period})


# برای اجرای لوکال: python app.py
if __name__ == "__main__":
    app.run(debug=True, port=3000)
