"""
اندپوینت سرورلس اصلی روی Vercel. مسیر: /api/scan
این تابع توسط GitHub Actions (هر ۱۵-۳۰ دقیقه، چون Vercel Cron رایگان فقط روزی یک‌بار مجازه)
یا توسط Vercel Cron روزانه (به عنوان فال‌بک) صدا زده میشه.

امنیت: هدر Authorization: Bearer <CRON_SECRET> باید مطابق env var CRON_SECRET باشه،
وگرنه هرکسی با دونستن URL می‌تونه صدها بار ربات رو صدا بزنه و Rate Limit صرافی‌ها رو بترکونه.
"""
import json
import logging
import sys
import os
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("api.scan")


def run_scan(symbols_override: list | None = None):
    from bot.config import SYMBOLS, CRON_SECRET  # noqa
    from bot.signal_engine import scan_all_symbols
    from bot.notifier import broadcast_signal
    from bot import storage

    symbols = symbols_override or SYMBOLS
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
            logger.error("خطا در ارسال سیگنال %s: %s", sig.get("symbol"), e)

    return {"scanned_symbols": len(symbols), "signals_found": len(signals), "signals_sent": sent}


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self._handle()

    def do_POST(self):
        self._handle()

    def _handle(self):
        from bot.config import CRON_SECRET

        if CRON_SECRET:
            auth_header = self.headers.get("Authorization", "")
            if auth_header != f"Bearer {CRON_SECRET}":
                self.send_response(401)
                self.end_headers()
                self.wfile.write(b'{"error": "unauthorized"}')
                return

        query = parse_qs(urlparse(self.path).query)
        symbols_param = query.get("symbols", [None])[0]
        symbols_override = [s.strip() for s in symbols_param.split(",")] if symbols_param else None

        try:
            result = run_scan(symbols_override)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(result, ensure_ascii=False).encode("utf-8"))
        except Exception as e:
            logger.exception("scan failed")
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}, ensure_ascii=False).encode("utf-8"))
