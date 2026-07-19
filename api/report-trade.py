"""
اندپوینت گزارش دستی نتیجه‌ی معامله. مسیر: /api/report-trade
چون ربات خودش معامله نمی‌کنه (طبق درخواست کاربر)، خود کاربر بعد از بستن معامله در صرافی،
با یک POST request نتیجه (سود/ضرر درصدی) رو گزارش می‌ده تا Max Daily/Weekly Loss و Drawdown
به‌روز بمونه و در داشبورد و مدیریت ریسک لحاظ بشه.

نمونه‌ی درخواست:
curl -X POST https://YOUR-APP.vercel.app/api/report-trade \
  -H "Authorization: Bearer $CRON_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"pnl_pct": -1.2}'
"""
import json
import sys
import os
import datetime
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        from bot.config import CRON_SECRET
        from bot import storage

        if CRON_SECRET:
            auth_header = self.headers.get("Authorization", "")
            if auth_header != f"Bearer {CRON_SECRET}":
                self.send_response(401)
                self.end_headers()
                return

        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(body)
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

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "ok": True, "today_pnl_pct": today, "week_pnl_pct": week, "drawdown_pct": drawdown,
            }, ensure_ascii=False).encode("utf-8"))
        except Exception as e:
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}, ensure_ascii=False).encode("utf-8"))
