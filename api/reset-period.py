"""
اندپوینت ریست دوره‌ای. مسیر: /api/reset-period?period=daily یا ?period=weekly
باید توسط GitHub Actions هر روز نیمه‌شب UTC (daily) و هر دوشنبه (weekly) صدا زده بشه
تا شمارنده‌ی Max Daily/Weekly Loss درست کار کنه (وگرنه ضررهای قدیمی برای همیشه جمع می‌مونن).
"""
import json
import sys
import os
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

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

        query = parse_qs(urlparse(self.path).query)
        period = query.get("period", ["daily"])[0]

        if period == "daily":
            storage.set_value("today_pnl_pct", 0.0)
        elif period == "weekly":
            storage.set_value("week_pnl_pct", 0.0)

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"ok": True, "reset": period}, ensure_ascii=False).encode("utf-8"))
