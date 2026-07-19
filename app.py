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

from bot.config import CRON_SECRET, SYMBOLS  # noqa: E402
from bot.signal_engine import scan_all_symbols  # noqa: E402
from bot.notifier import broadcast_signal  # noqa: E402
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

    try:
        signals = scan_all_symbols(symbols)
        sent = []
        for sig in signals:
            try:
                broadcast_signal(sig)
                storage.push_trade_log({
                    "symbol": sig["symbol"], "timeframe": sig["timeframe"], "direction": sig["direction"],
                    "grade": sig["grade"], "score": sig["score"]["total_score"],
                    "entry": sig["trade_plan"]["entry"], "stop_loss": sig["trade_plan"]["stop_loss"],
                    "take_profit_1": sig["trade_plan"]["take_profit_1"],
                    "ai_probability": sig["ai_probability"]["probability_pct"],
                })
                sent.append(f"{sig['symbol']} ({sig['timeframe']}, {sig['grade']})")
            except Exception as e:
                app.logger.error("خطا در ارسال سیگنال %s: %s", sig.get("symbol"), e)

        return jsonify({"scanned_symbols": len(symbols), "signals_found": len(signals), "signals_sent": sent})
    except Exception as e:
        app.logger.exception("scan failed")
        return jsonify({"error": str(e)}), 500


@app.route("/api/dashboard", methods=["GET"])
def dashboard():
    try:
        trades = storage.get_trade_log()
        if not trades:
            return jsonify({"message": "هنوز معامله‌ای ثبت نشده", "trades": 0})

        total = len(trades)
        by_symbol_score = defaultdict(list)
        for t in trades:
            by_symbol_score[t["symbol"]].append(t["score"])

        best_symbol = max(by_symbol_score, key=lambda s: sum(by_symbol_score[s]) / len(by_symbol_score[s]))
        worst_symbol = min(by_symbol_score, key=lambda s: sum(by_symbol_score[s]) / len(by_symbol_score[s]))
        today_pnl, week_pnl, drawdown = storage.get_daily_weekly_pnl_pct()

        return jsonify({
            "total_signals_sent": total,
            "today_pnl_pct": today_pnl,
            "week_pnl_pct": week_pnl,
            "current_drawdown_pct": drawdown,
            "average_score": round(sum(t["score"] for t in trades) / total, 1),
            "average_ai_probability": round(sum(t["ai_probability"] for t in trades) / total, 1),
            "best_symbol_by_avg_score": best_symbol,
            "worst_symbol_by_avg_score": worst_symbol,
            "note": "سود/زیان واقعی باید از طریق /api/report-trade گزارش دستی شود.",
            "recent_signals": trades[-10:],
        })
    except Exception as e:
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
