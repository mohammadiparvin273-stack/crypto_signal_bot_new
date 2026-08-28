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
import requests
from collections import defaultdict
from flask import Flask, request, jsonify

sys.path.insert(0, os.path.dirname(__file__))

from bot.config import CRON_SECRET, SYMBOLS, SIGNAL_COOLDOWN_HOURS, ACCOUNT_BALANCE_USDT, RISK_PER_TRADE_PCT  # noqa: E402
from bot.config import MAX_DAILY_LOSS_PCT, MAX_WEEKLY_LOSS_PCT, MAX_DRAWDOWN_PCT, MAX_PORTFOLIO_OPEN_RISK_PCT, MAX_SAME_DIRECTION_OPEN  # noqa: E402
from bot.config import TELEGRAM_PERSONAL_CHAT_ID, TELEGRAM_WEBHOOK_SECRET, LEVERAGE_PRESETS  # noqa: E402
from bot.signal_engine import scan_all_symbols  # noqa: E402
from bot.notifier import broadcast_signal, broadcast_news_text, format_news_digest, send_trade_entry_prompt  # noqa: E402
from bot.sentiment import full_sentiment_report  # noqa: E402
from bot.trade_tracker import check_open_trades  # noqa: E402
from bot.risk_management import check_risk_limits, compute_trade_plan  # noqa: E402
from bot import storage  # noqa: E402

app = Flask(__name__)


def _authorized() -> bool:
    if not CRON_SECRET:
        return True
    return request.headers.get("Authorization", "") == f"Bearer {CRON_SECRET}"


def _is_win(outcome: str) -> bool:
    """برد شامل: رسیدن به هر TP، یا trailing-stop بعد از TP2 (چون سود جزئی قفل‌شده)"""
    outcome = outcome or ""
    return outcome.startswith("take_profit") or outcome == "trailing_stop_after_tp2"


@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "crypto-signal-bot"})


DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>داشبورد ربات سیگنال</title>
<style>
  body { font-family: Tahoma, sans-serif; background: #0f1117; color: #e6e6e6; margin: 0; padding: 16px; }
  h1 { font-size: 20px; margin-bottom: 8px; }
  h2 { font-size: 15px; margin: 22px 0 8px; color: #cfd3da; }
  .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 10px; }
  .card { background: #1a1d29; border-radius: 10px; padding: 12px; text-align: center; }
  .card .label { font-size: 11px; color: #9aa0ac; margin-bottom: 6px; }
  .card .value { font-size: 20px; font-weight: bold; }
  .positive { color: #4caf50; }
  .negative { color: #f44336; }
  table { width: 100%; border-collapse: collapse; font-size: 12.5px; margin-top: 6px; }
  th, td { padding: 7px 5px; text-align: right; border-bottom: 1px solid #2a2e3d; white-space: nowrap; }
  th { color: #9aa0ac; font-weight: normal; position: sticky; top: 0; background: #0f1117; }
  .badge { padding: 2px 7px; border-radius: 6px; font-size: 10.5px; }
  .badge.long { background: #1b3a2a; color: #4caf50; }
  .badge.short { background: #3a1b1b; color: #f44336; }
  .badge.open { background: #2a2e3d; color: #9aa0ac; }
  .refresh-note { font-size: 11px; color: #666; margin-top: 16px; text-align: center; }
  .loading { text-align: center; padding: 40px; color: #9aa0ac; }
  .table-wrap { max-height: 480px; overflow-y: auto; border: 1px solid #2a2e3d; border-radius: 8px; }
  .filters { display: flex; gap: 6px; margin: 10px 0; flex-wrap: wrap; }
  .filters button { background: #1a1d29; color: #e6e6e6; border: 1px solid #2a2e3d; border-radius: 6px; padding: 6px 12px; font-size: 12px; cursor: pointer; }
  .filters button.active { background: #2b5fd9; border-color: #2b5fd9; }
  .two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
</style>
</head>
<body>
  <h1>📊 داشبورد ربات سیگنال کریپتو</h1>
  <div id="content" class="loading">در حال بارگذاری...</div>
  <div class="refresh-note">هر ۶۰ ثانیه خودکار به‌روزرسانی میشه</div>

<script>
let allSignals = [];
let currentFilter = 'all';

function fmt(v, suffix) { return (v === null || v === undefined) ? '-' : v + (suffix || ''); }
function pnlClass(v) { return (v > 0) ? 'positive' : (v < 0 ? 'negative' : ''); }

function renderTable() {
  const wrap = document.getElementById('tableWrap');
  let rows = allSignals;
  if (currentFilter === 'open') rows = rows.filter(t => t.status === 'open');
  if (currentFilter === 'closed') rows = rows.filter(t => t.status === 'closed');
  if (currentFilter === 'long') rows = rows.filter(t => t.direction === 'long');
  if (currentFilter === 'short') rows = rows.filter(t => t.direction === 'short');

  let html = '<div class="table-wrap"><table><thead><tr><th>ارز</th><th>جهت</th><th>تایم‌فریم</th><th>امتیاز</th><th>وضعیت</th><th>سود/ضرر</th></tr></thead><tbody>';
  for (const t of rows) {
    const dirBadge = t.direction === 'long' ? '<span class="badge long">لانگ</span>' : '<span class="badge short">شورت</span>';
    let statusCell;
    if (t.status === 'open') {
      const stageNote = t.stage === 1 ? ' (بی‌ریسک)' : (t.stage === 2 ? ' (Trail شده)' : '');
      statusCell = `<span class="badge open">باز${stageNote}</span>`;
    } else if (t.outcome && t.outcome.startsWith('take_profit')) {
      statusCell = '<span class="badge long">✅ TP</span>';
    } else if (t.outcome === 'trailing_stop_after_tp2') {
      statusCell = '<span class="badge long">✅ Trail</span>';
    } else if (t.outcome === 'breakeven_stop') {
      statusCell = '<span class="badge open">⚪ بی‌ریسک</span>';
    } else {
      statusCell = '<span class="badge short">❌ SL</span>';
    }
    const pnlCell = (t.pnl_pct !== undefined) ? `<span class="${pnlClass(t.pnl_pct)}">${t.pnl_pct}%</span>` : '-';
    html += `<tr><td>${t.symbol}</td><td>${dirBadge}</td><td>${t.timeframe}</td><td>${t.score}</td><td>${statusCell}</td><td>${pnlCell}</td></tr>`;
  }
  html += '</tbody></table></div>';
  wrap.innerHTML = html;
}

function setFilter(f) {
  currentFilter = f;
  document.querySelectorAll('.filters button').forEach(b => b.classList.toggle('active', b.dataset.f === f));
  renderTable();
}

async function loadDashboard() {
  const el = document.getElementById('content');
  try {
    const res = await fetch('/api/dashboard');
    const data = await res.json();

    if (data.trades === 0 || !data.total_signals_sent) {
      el.innerHTML = '<div class="loading">هنوز معامله‌ای ثبت نشده</div>';
      return;
    }
    allSignals = data.all_signals || [];

    let html = '<div class="cards">';
    html += `<div class="card"><div class="label">کل سیگنال‌ها</div><div class="value">${fmt(data.total_signals_sent)}</div></div>`;
    html += `<div class="card"><div class="label">در حال اجرا</div><div class="value">${fmt(data.still_open)}</div></div>`;
    html += `<div class="card"><div class="label">Win Rate واقعی</div><div class="value ${pnlClass((data.real_win_rate_pct||0) - 50)}">${fmt(data.real_win_rate_pct, '%')}</div></div>`;
    html += `<div class="card"><div class="label">میانگین سود/معامله</div><div class="value ${pnlClass(data.average_pnl_pct_per_closed_trade)}">${fmt(data.average_pnl_pct_per_closed_trade, '%')}</div></div>`;
    html += `<div class="card"><div class="label">برد / باخت</div><div class="value">${fmt(data.wins)} / ${fmt(data.losses)}</div></div>`;
    html += `<div class="card"><div class="label">Drawdown فعلی</div><div class="value negative">${fmt(data.current_drawdown_pct, '%')}</div></div>`;
    html += '</div>';

    html += '<h2>تفکیک لانگ / شورت</h2><div class="two-col">';
    const ls = data.long_stats || {}, ss = data.short_stats || {};
    html += `<div class="card"><div class="label">لانگ (${fmt(ls.count)} معامله)</div><div class="value ${pnlClass((ls.win_rate_pct||0)-50)}">${fmt(ls.win_rate_pct,'%')}</div><div class="label">${fmt(ls.wins)} برد / ${fmt(ls.losses)} باخت</div></div>`;
    html += `<div class="card"><div class="label">شورت (${fmt(ss.count)} معامله)</div><div class="value ${pnlClass((ss.win_rate_pct||0)-50)}">${fmt(ss.win_rate_pct,'%')}</div><div class="label">${fmt(ss.wins)} برد / ${fmt(ss.losses)} باخت</div></div>`;
    html += '</div>';

    html += '<h2>تفکیک به ارز</h2><div class="table-wrap"><table><thead><tr><th>ارز</th><th>تعداد</th><th>Win Rate</th><th>میانگین سود</th></tr></thead><tbody>';
    for (const s of (data.per_symbol_stats || [])) {
      html += `<tr><td>${s.symbol}</td><td>${s.total_signals} (${s.closed} بسته)</td><td class="${pnlClass((s.win_rate_pct||0)-50)}">${fmt(s.win_rate_pct,'%')}</td><td class="${pnlClass(s.avg_pnl_pct)}">${fmt(s.avg_pnl_pct,'%')}</td></tr>`;
    }
    html += '</tbody></table></div>';

    html += '<h2>همه‌ی معاملات</h2>';
    html += '<div class="filters">' +
      '<button data-f="all" class="active" onclick="setFilter(\\'all\\')">همه</button>' +
      '<button data-f="open" onclick="setFilter(\\'open\\')">باز</button>' +
      '<button data-f="closed" onclick="setFilter(\\'closed\\')">بسته</button>' +
      '<button data-f="long" onclick="setFilter(\\'long\\')">لانگ</button>' +
      '<button data-f="short" onclick="setFilter(\\'short\\')">شورت</button>' +
      '</div><div id="tableWrap"></div>';

    el.innerHTML = html;
    renderTable();
  } catch (e) {
    el.innerHTML = '<div class="loading">خطا در بارگذاری داده</div>';
  }
}
loadDashboard();
setInterval(loadDashboard, 60000);
</script>
</body>
</html>
"""


@app.route("/dashboard-view", methods=["GET"])
def dashboard_view():
    """داشبورد وبی خوانا (نه JSON خام) - برای دیدن راحت‌تر از گوشی/کامپیوتر"""
    return DASHBOARD_HTML


@app.route("/api/scan", methods=["GET", "POST"])
def scan():
    if not _authorized():
        return jsonify({"error": "unauthorized"}), 401

    # قفل اتمیک: اگه یه اجرای دیگه در حال انجامه (مثلاً به‌خاطر همپوشانی زمان‌بندی/اجرای دستی)،
    # این اجرا فوراً متوقف میشه - تا از سیگنال تکراری با قیمت‌های متفاوت جلوگیری بشه.
    if not storage.acquire_lock("scan_lock", ttl_seconds=600):
        return jsonify({"skipped": True, "reason": "اجرای دیگه‌ای در حال انجامه (قفل فعاله)"}), 200

    try:
        symbols_param = request.args.get("symbols")
        symbols = [s.strip() for s in symbols_param.split(",") if s.strip()] if symbols_param else SYMBOLS

        # چرخش نوبتی: هر اجرا از یه نقطه‌ی متفاوت لیست شروع میشه، تا اگه زمان کم آمد،
        # همیشه فقط ارزهای اول لیست بررسی نشن و بقیه هیچ‌وقت شانس نداشته باشن.
        start_idx = int(storage.get_value("scan_start_index", default=0) or 0)
        if start_idx >= len(symbols):
            start_idx = 0
        symbols = symbols[start_idx:] + symbols[:start_idx]
        storage.set_value("scan_start_index", (start_idx + 1) % max(len(symbols), 1))

        return _run_scan_locked(symbols)
    finally:
        storage.release_lock("scan_lock")


def _run_scan_locked(symbols):
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
        skipped_portfolio_risk = []
        now = datetime.datetime.utcnow().timestamp()

        # مجموع ریسک معاملات باز فعلی (برای محدودیت ریسک کل پرتفوی)
        open_trades_now = [t for t in storage.get_trade_log() if t.get("status") == "open"]
        current_open_risk_pct = len(open_trades_now) * RISK_PER_TRADE_PCT
        open_long_count = sum(1 for t in open_trades_now if t.get("direction") == "long")
        open_short_count = sum(1 for t in open_trades_now if t.get("direction") == "short")

        for sig in deduped_signals:
            symbol = sig["symbol"]
            cooldown_key = f"last_signal_ts:{symbol}"
            last_ts = storage.get_value(cooldown_key, default=0) or 0
            hours_since_last = (now - last_ts) / 3600 if last_ts else 999

            if hours_since_last < SIGNAL_COOLDOWN_HOURS:
                skipped_cooldown.append(f"{symbol} (هنوز {round(SIGNAL_COOLDOWN_HOURS - hours_since_last, 1)} ساعت مونده)")
                continue

            # محدودیت ریسک کل پرتفوی: اگه اضافه‌کردن این معامله از حداکثر مجموع ریسک هم‌زمان
            # عبور کنه، نادیده گرفته میشه (حتی اگه سیگنال خودش خوب باشه) - جلوگیری از قرارگرفتن
            # بیش‌ازحد در معرض ریسک وقتی چند معامله هم‌زمان باز می‌مونن.
            if current_open_risk_pct + RISK_PER_TRADE_PCT > MAX_PORTFOLIO_OPEN_RISK_PCT:
                skipped_portfolio_risk.append(f"{symbol} (سقف ریسک کل پرتفوی)")
                continue

            # محدودیت هم‌بستگی: بیش از حد مجاز معامله‌ی هم‌جهت (همه لانگ یا همه شورت) هم‌زمان باز نشه
            same_dir_count = open_long_count if sig["direction"] == "long" else open_short_count
            if same_dir_count >= MAX_SAME_DIRECTION_OPEN:
                skipped_portfolio_risk.append(f"{symbol} (سقف معاملات هم‌جهت {sig['direction']})")
                continue

            # محاسبه‌ی دوباره‌ی trade_plan با موجودی واقعی فعلی (نه عدد ثابت اولیه‌ی کانفیگ)
            # نکته: همون SL قبلی (ساختاری یا ATR) رو دوباره پاس می‌دیم تا عوض نشه، فقط Position Size آپدیت بشه
            atr_value = sig["trade_plan"].get("_atr_value")
            if atr_value:
                sig["trade_plan"] = compute_trade_plan(
                    sig["trade_plan"]["entry"], atr_value, sig["direction"],
                    account_balance=current_balance, risk_pct=RISK_PER_TRADE_PCT,
                    structural_sl_candidate=sig["trade_plan"]["stop_loss"],
                )

            try:
                telegram_message_id = broadcast_signal(sig)
                if telegram_message_id is None:
                    app.logger.error("ارسال تلگرام برای %s شکست خورد - این سیگنال ثبت نشد.", symbol)
                    continue

                # ساخت یه شناسه‌ی کوتاه برای این سیگنال (برای دکمه‌های تعاملی)
                signal_id = f"{symbol.replace('/', '')}{int(now)}"[-20:]

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
                    "features": sig["features"],
                    "signal_id": signal_id,
                })

                # پیام تعاملی «ورود به معامله» - فقط اگه چت شخصی تنظیم شده باشه
                if TELEGRAM_PERSONAL_CHAT_ID:
                    storage.set_value(f"pending_trade:{signal_id}", {
                        "symbol": symbol, "direction": sig["direction"],
                        "entry": sig["trade_plan"]["entry"], "stop_loss": sig["trade_plan"]["stop_loss"],
                        "take_profit_1": sig["trade_plan"]["take_profit_1"],
                        "take_profit_2": sig["trade_plan"]["take_profit_2"],
                        "take_profit_3": sig["trade_plan"]["take_profit_3"],
                        "chat_id": TELEGRAM_PERSONAL_CHAT_ID,
                    })
                    send_trade_entry_prompt(sig, signal_id, TELEGRAM_PERSONAL_CHAT_ID)

                storage.set_value(cooldown_key, now)
                current_open_risk_pct += RISK_PER_TRADE_PCT
                if sig["direction"] == "long":
                    open_long_count += 1
                else:
                    open_short_count += 1
                sent.append(f"{symbol} ({sig['timeframe']}, {sig['grade']})")
            except Exception as e:
                app.logger.error("خطا در ارسال سیگنال %s: %s", symbol, e)

        return jsonify({
            "scanned_symbols": len(symbols),
            "signals_found": len(signals),
            "duplicate_timeframe_signals_skipped": duplicates_skipped,
            "signals_sent": sent,
            "skipped_due_to_cooldown": skipped_cooldown,
            "skipped_due_to_portfolio_risk_limit": skipped_portfolio_risk,
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


@app.route("/api/telegram-webhook", methods=["POST"])
def telegram_webhook():
    """دریافت آپدیت‌های تلگرام (دکمه‌ها و پیام‌های متنی) برای جریان تعاملی ورود به معامله"""
    if TELEGRAM_WEBHOOK_SECRET:
        incoming_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if incoming_secret != TELEGRAM_WEBHOOK_SECRET:
            return jsonify({"error": "unauthorized"}), 401

    update = request.get_json(force=True, silent=True) or {}
    try:
        if "callback_query" in update:
            _handle_callback_query(update["callback_query"])
        elif "message" in update:
            _handle_plain_message(update["message"])
    except Exception:
        app.logger.exception("پردازش webhook تلگرام شکست خورد")

    return jsonify({"ok": True})


def _handle_callback_query(cq):
    from bot.notifier import answer_callback_query, edit_message_with_keyboard
    from bot.lbank_executor import compute_order_plan, execute_trade

    callback_id = cq["id"]
    data = cq.get("data", "")
    chat_id = str(cq["message"]["chat"]["id"])
    message_id = cq["message"]["message_id"]
    answer_callback_query(callback_id)

    parts = data.split(":")
    action = parts[0]
    signal_id = parts[1] if len(parts) > 1 else None
    pending = storage.get_value(f"pending_trade:{signal_id}") if signal_id else None

    if not pending:
        edit_message_with_keyboard(chat_id, message_id, "⌛ این سیگنال منقضی شده یا قبلاً پردازش شده.")
        return

    if action == "cancel":
        storage.delete_value(f"pending_trade:{signal_id}")
        edit_message_with_keyboard(chat_id, message_id, "❌ این معامله رد شد.")
        return

    if action == "amt":
        amount = float(parts[2])
        pending["amount"] = amount
        storage.set_value(f"pending_trade:{signal_id}", pending)
        rows = [[{"text": f"{lv}x", "callback_data": f"lev:{signal_id}:{lv}"} for lv in LEVERAGE_PRESETS]]
        rows.append([{"text": "❌ لغو", "callback_data": f"cancel:{signal_id}"}])
        edit_message_with_keyboard(chat_id, message_id, f"مبلغ: ${amount}\n\nحالا اهرم رو انتخاب کن:", rows)
        return

    if action == "amtcustom":
        storage.set_value(f"awaiting_custom_amount:{chat_id}", signal_id)
        edit_message_with_keyboard(chat_id, message_id, "عدد مبلغ دلاری رو تایپ کن و بفرست (مثلاً 75)")
        return

    if action == "lev":
        leverage = int(parts[2])
        plan = compute_order_plan(
            pending["symbol"], pending["direction"], pending["amount"], leverage,
            pending["entry"], pending["stop_loss"], pending["take_profit_1"],
            pending["take_profit_2"], pending["take_profit_3"],
        )
        pending["leverage"] = leverage
        pending["plan"] = plan
        storage.set_value(f"pending_trade:{signal_id}", pending)
        text = (
            f"📋 خلاصه‌ی معامله:\n\n"
            f"{plan['symbol']} - {plan['direction']}\n"
            f"مارجین: ${plan['margin_usdt']} | اهرم: {plan['leverage']}x\n"
            f"حجم پوزیشن: {plan['position_size_units']} (≈ ${plan['position_size_usdt']})\n"
            f"ورود: {plan['entry']} | SL: {plan['stop_loss']}\n"
            f"TP1: {plan['take_profit_1']} | TP2: {plan['take_profit_2']} | TP3: {plan['take_profit_3']}\n"
            f"قیمت لیکوئید تقریبی: {plan['estimated_liquidation_price']}\n\n"
            f"⚠️ با تایید، این معامله واقعاً روی LBank باز میشه."
        )
        rows = [[{"text": "✅ تایید و ورود", "callback_data": f"confirm:{signal_id}"},
                 {"text": "❌ لغو", "callback_data": f"cancel:{signal_id}"}]]
        edit_message_with_keyboard(chat_id, message_id, text, rows)
        return

    if action == "confirm":
        plan = pending.get("plan")
        if not plan:
            edit_message_with_keyboard(chat_id, message_id, "خطا: پلن معامله پیدا نشد، از اول امتحان کن.")
            return
        result = execute_trade(plan)
        storage.delete_value(f"pending_trade:{signal_id}")
        if result.get("dry_run"):
            edit_message_with_keyboard(
                chat_id, message_id,
                f"🧪 حالت آزمایشی (DRY_RUN) فعاله - سفارش واقعی زده نشد.\n\n{result['message']}\n\n"
                f"وقتی مطمئن شدی همه‌چی درسته، LBANK_DRY_RUN رو توی Vercel بذار false.",
            )
        elif result.get("ok"):
            trades = storage.get_trade_log()
            for t in trades:
                if t.get("signal_id") == signal_id:
                    t["lbank_managed"] = True
                    t["lbank_sl_order_id"] = result.get("sl_order_id")
                    t["lbank_position_units"] = plan["position_size_units"]
                    break
            storage.set_value("trade_log", trades)
            edit_message_with_keyboard(chat_id, message_id, "✅ معامله با موفقیت روی LBank باز شد!")
        else:
            edit_message_with_keyboard(chat_id, message_id, f"❌ خطا در اجرای معامله: {result.get('error')}")
        return


def _handle_plain_message(msg):
    from bot.notifier import send_message_with_keyboard

    chat_id = str(msg["chat"]["id"])
    text = (msg.get("text") or "").strip()
    signal_id = storage.get_value(f"awaiting_custom_amount:{chat_id}")
    if not signal_id:
        return
    try:
        amount = float(text)
    except ValueError:
        send_message_with_keyboard(chat_id, "عدد معتبر نبود، دوباره فقط عدد بفرست (مثلاً 75)", [])
        return
    storage.delete_value(f"awaiting_custom_amount:{chat_id}")
    pending = storage.get_value(f"pending_trade:{signal_id}")
    if not pending:
        return
    pending["amount"] = amount
    storage.set_value(f"pending_trade:{signal_id}", pending)
    rows = [[{"text": f"{lv}x", "callback_data": f"lev:{signal_id}:{lv}"} for lv in LEVERAGE_PRESETS]]
    rows.append([{"text": "❌ لغو", "callback_data": f"cancel:{signal_id}"}])
    send_message_with_keyboard(chat_id, f"مبلغ: ${amount}\n\nحالا اهرم رو انتخاب کن:", rows)


@app.route("/api/setup-webhook", methods=["GET"])
def setup_webhook():
    """اندپوینت کمکی: خودش وب‌هوک تلگرام رو روی همین آدرس تنظیم می‌کنه (فقط یه‌بار لازمه)"""
    if not _authorized():
        return jsonify({"error": "unauthorized"}), 401
    from bot.config import TELEGRAM_BOT_TOKEN
    base_url = request.url_root.rstrip("/")
    webhook_url = f"{base_url}/api/telegram-webhook"
    payload = {"url": webhook_url}
    if TELEGRAM_WEBHOOK_SECRET:
        payload["secret_token"] = TELEGRAM_WEBHOOK_SECRET
    resp = requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/setWebhook", json=payload, timeout=10)
    return jsonify(resp.json())


@app.route("/api/model-status", methods=["GET"])
def model_status():
    """اندپوینت تشخیصی: دقیقاً می‌گه مدل AI لود شده یا نه، و اگه نشده چرا"""
    import os
    from bot.ai_scorer import MODEL_PATH
    result = {
        "model_path": os.path.abspath(MODEL_PATH),
        "file_exists": os.path.exists(MODEL_PATH),
    }
    if result["file_exists"]:
        result["file_size_bytes"] = os.path.getsize(MODEL_PATH)
        try:
            import joblib
            model = joblib.load(MODEL_PATH)
            result["load_success"] = True
            result["model_type"] = str(type(model))
        except Exception as e:
            result["load_success"] = False
            result["load_error"] = f"{type(e).__name__}: {e}"
    else:
        # لیست فایل‌های ریشه‌ی پروژه رو هم نشون بده تا ببینیم واقعاً کجاست
        try:
            root_dir = os.path.dirname(os.path.abspath(MODEL_PATH))
            result["root_directory_contents"] = os.listdir(root_dir)
        except Exception as e:
            result["list_dir_error"] = str(e)
    return jsonify(result)


@app.route("/api/weekly-report", methods=["GET", "POST"])
def weekly_report():
    """گزارش هفتگی عملکرد: خلاصه‌ی سیگنال‌های ۷ روز اخیر، به کانال تلگرام فرستاده میشه"""
    if not _authorized():
        return jsonify({"error": "unauthorized"}), 401
    try:
        trades = storage.get_trade_log()
        seven_days_ago = (datetime.datetime.utcnow() - datetime.timedelta(days=7)).timestamp()
        recent = [t for t in trades if t.get("logged_at", 0) >= seven_days_ago]

        if not recent:
            text = "📊 گزارش هفتگی\n\nاین هفته هیچ سیگنالی ارسال نشده."
            sent = broadcast_news_text(text)
            return jsonify({"sent": bool(sent), "text": text})

        closed = [t for t in recent if t.get("status") == "closed"]
        wins = [t for t in closed if _is_win(t.get("outcome"))]
        losses = [t for t in closed if t.get("outcome") == "stop_loss"]
        win_rate = round(len(wins) / len(closed) * 100, 1) if closed else None
        avg_pnl = round(sum(t.get("pnl_pct", 0) for t in closed) / len(closed), 2) if closed else None

        by_symbol = defaultdict(lambda: {"count": 0, "wins": 0, "losses": 0})
        for t in recent:
            by_symbol[t["symbol"]]["count"] += 1
            if _is_win(t.get("outcome")):
                by_symbol[t["symbol"]]["wins"] += 1
            elif t.get("outcome") == "stop_loss":
                by_symbol[t["symbol"]]["losses"] += 1

        best_symbol, worst_symbol = None, None
        closed_symbols = {s: v for s, v in by_symbol.items() if (v["wins"] + v["losses"]) > 0}
        if closed_symbols:
            best_symbol = max(closed_symbols, key=lambda s: closed_symbols[s]["wins"] - closed_symbols[s]["losses"])
            worst_symbol = min(closed_symbols, key=lambda s: closed_symbols[s]["wins"] - closed_symbols[s]["losses"])

        lines = [
            "📊 گزارش هفتگی عملکرد ربات",
            "",
            f"تعداد کل سیگنال‌های این هفته: {len(recent)}",
            f"بسته‌شده (نتیجه مشخص): {len(closed)} | هنوز باز: {len(recent) - len(closed)}",
        ]
        if win_rate is not None:
            lines.append(f"Win Rate هفتگی: {win_rate}% ({len(wins)} برد / {len(losses)} باخت)")
            lines.append(f"میانگین سود/ضرر هر معامله: {avg_pnl}%")
        if best_symbol:
            lines.append(f"بهترین ارز: {best_symbol} ({closed_symbols[best_symbol]['wins']} برد / {closed_symbols[best_symbol]['losses']} باخت)")
        if worst_symbol and worst_symbol != best_symbol:
            lines.append(f"بدترین ارز: {worst_symbol} ({closed_symbols[worst_symbol]['wins']} برد / {closed_symbols[worst_symbol]['losses']} باخت)")
        lines.append("")
        lines.append("⚠️ این گزارش صرفاً جهت اطلاع‌رسانیه، نه توصیه‌ی مالی.")
        text = "\n".join(lines)

        sent = broadcast_news_text(text)
        return jsonify({"sent": bool(sent), "text": text})
    except Exception as e:
        app.logger.exception("weekly report failed")
        return jsonify({"error": str(e)}), 500


@app.route("/api/dashboard", methods=["GET"])
def dashboard():
    try:
        trades = storage.get_trade_log()
        if not trades:
            return jsonify({"message": "هنوز معامله‌ای ثبت نشده", "trades": 0})

        total = len(trades)
        closed = [t for t in trades if t.get("status") == "closed"]
        wins = [t for t in closed if _is_win(t.get("outcome"))]
        losses = [t for t in closed if t.get("outcome") == "stop_loss"]
        win_rate = round(len(wins) / len(closed) * 100, 1) if closed else None
        avg_pnl = round(sum(t.get("pnl_pct", 0) for t in closed) / len(closed), 2) if closed else None

        # آمار تفکیکی به‌تفکیک جهت (لانگ/شورت)
        def _direction_stats(direction):
            d_closed = [t for t in closed if t.get("direction") == direction]
            d_wins = [t for t in d_closed if _is_win(t.get("outcome"))]
            return {
                "count": len(d_closed),
                "wins": len(d_wins),
                "losses": len(d_closed) - len(d_wins),
                "win_rate_pct": round(len(d_wins) / len(d_closed) * 100, 1) if d_closed else None,
                "avg_pnl_pct": round(sum(t.get("pnl_pct", 0) for t in d_closed) / len(d_closed), 2) if d_closed else None,
            }

        long_stats = _direction_stats("long")
        short_stats = _direction_stats("short")

        # آمار تفکیکی به‌تفکیک ارز
        by_symbol = defaultdict(lambda: {"count": 0, "closed": 0, "wins": 0, "losses": 0, "scores": [], "pnls": []})
        for t in trades:
            s = by_symbol[t["symbol"]]
            s["count"] += 1
            s["scores"].append(t["score"])
            if t.get("status") == "closed":
                s["closed"] += 1
                if _is_win(t.get("outcome")):
                    s["wins"] += 1
                else:
                    s["losses"] += 1
                s["pnls"].append(t.get("pnl_pct", 0))

        per_symbol_stats = []
        for symbol, s in by_symbol.items():
            per_symbol_stats.append({
                "symbol": symbol,
                "total_signals": s["count"],
                "closed": s["closed"],
                "wins": s["wins"],
                "losses": s["losses"],
                "win_rate_pct": round(s["wins"] / s["closed"] * 100, 1) if s["closed"] else None,
                "avg_score": round(sum(s["scores"]) / len(s["scores"]), 1),
                "avg_pnl_pct": round(sum(s["pnls"]) / len(s["pnls"]), 2) if s["pnls"] else None,
            })
        per_symbol_stats.sort(key=lambda x: (x["win_rate_pct"] is None, -(x["win_rate_pct"] or -999)))

        best_symbol = per_symbol_stats[0]["symbol"] if per_symbol_stats and per_symbol_stats[0]["win_rate_pct"] is not None else None
        closed_ranked = [s for s in per_symbol_stats if s["win_rate_pct"] is not None]
        worst_symbol = closed_ranked[-1]["symbol"] if closed_ranked else None

        today_pnl, week_pnl, drawdown = storage.get_daily_weekly_pnl_pct()

        return jsonify({
            "total_signals_sent": total,
            "still_open": total - len(closed),
            "closed_trades": len(closed),
            "real_win_rate_pct": win_rate,
            "wins": len(wins),
            "losses": len(losses),
            "average_pnl_pct_per_closed_trade": avg_pnl,
            "long_stats": long_stats,
            "short_stats": short_stats,
            "today_pnl_pct": today_pnl,
            "week_pnl_pct": week_pnl,
            "current_drawdown_pct": drawdown,
            "average_score": round(sum(t["score"] for t in trades) / total, 1),
            "average_ai_probability": round(sum(t["ai_probability"] for t in trades) / total, 1),
            "best_symbol_by_avg_score": best_symbol,
            "worst_symbol_by_avg_score": worst_symbol,
            "per_symbol_stats": per_symbol_stats,
            "all_signals": list(reversed(trades)),  # همه‌ی معاملات، جدیدترین اول
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/news", methods=["GET", "POST"])
def news():
    if not _authorized():
        return jsonify({"error": "unauthorized"}), 401
    try:
        from bot import notifier as notifier_module
        report = full_sentiment_report()
        text = format_news_digest(report)
        sent = broadcast_news_text(text)
        return jsonify({"sent": bool(sent), "text": text, "error_detail": notifier_module.LAST_TELEGRAM_ERROR})
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
