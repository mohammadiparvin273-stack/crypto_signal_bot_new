"""
اندپوینت داشبورد. مسیر: /api/dashboard
نمایش JSON از آمار: سود امروز/هفته/ماه (اگه از /api/report-trade گزارش شده باشه)،
Win Rate، تعداد معاملات، Average RR، Drawdown، بهترین/بدترین استراتژی.
برای نمایش گرافیکی، این JSON رو می‌تونی به یک صفحه‌ی ساده‌ی HTML/React وصل کنی.
"""
import json
import sys
import os
from http.server import BaseHTTPRequestHandler
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def build_dashboard():
    from bot import storage
    trades = storage.get_trade_log()
    if not trades:
        return {"message": "هنوز معامله‌ای ثبت نشده", "trades": 0}

    total = len(trades)
    by_symbol_score = defaultdict(list)
    for t in trades:
        by_symbol_score[t["symbol"]].append(t["score"])

    best_symbol = max(by_symbol_score, key=lambda s: sum(by_symbol_score[s]) / len(by_symbol_score[s]))
    worst_symbol = min(by_symbol_score, key=lambda s: sum(by_symbol_score[s]) / len(by_symbol_score[s]))

    today_pnl, week_pnl, drawdown = storage.get_daily_weekly_pnl_pct()

    return {
        "total_signals_sent": total,
        "today_pnl_pct": today_pnl,
        "week_pnl_pct": week_pnl,
        "current_drawdown_pct": drawdown,
        "average_score": round(sum(t["score"] for t in trades) / total, 1),
        "average_ai_probability": round(sum(t["ai_probability"] for t in trades) / total, 1),
        "best_symbol_by_avg_score": best_symbol,
        "worst_symbol_by_avg_score": worst_symbol,
        "note": "سود/زیان واقعی (win rate واقعی) باید از طریق endpoint گزارش دستی معاملات "
                "(چون ربات خودکار ترید نمی‌کند) تکمیل شود.",
        "recent_signals": trades[-10:],
    }


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            data = build_dashboard()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"))
        except Exception as e:
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}, ensure_ascii=False).encode("utf-8"))
