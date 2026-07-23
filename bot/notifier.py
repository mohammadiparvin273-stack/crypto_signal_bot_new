"""
ارسال هشدار/سیگنال به تلگرام (اصلی)، دیسکورد و ایمیل (اختیاری).
پیام تلگرام شامل تمام فیلدهای درخواستی: ارز، جهت، ورود، SL، TP1-3، RR، احتمال موفقیت، دلیل ورود،
تایم‌فریم، میزان ریسک.
"""
import logging
import smtplib
from email.mime.text import MIMEText
import requests
from bot.config import (
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID, TELEGRAM_NEWS_CHANNEL_ID,
    DISCORD_WEBHOOK_URL, SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, ALERT_EMAIL_TO,
)

logger = logging.getLogger("notifier")


def format_signal_message(signal: dict) -> str:
    grade_emoji = {"golden": "🥇", "good": "✅", "acceptable": "🔹"}.get(signal["grade"], "")
    direction_fa = "لانگ 📈" if signal["direction"] == "long" else "شورت 📉"
    prob_source = signal["ai_probability"].get("source", "rule_based_fallback")
    prob_label = "احتمال موفقیت (مدل AI آموزش‌دیده)" if prob_source == "ml_model" else "تخمین اولیه (⚠️ مدل AI هنوز آموزش ندیده - این عدد فقط بازتاب امتیاز قانون‌محوره)"
    lines = [
        f"{grade_emoji} سیگنال {signal['grade'].upper()} — {signal['symbol']}",
        "",
        f"جهت: {direction_fa}",
        f"تایم‌فریم: {signal['timeframe']}",
        f"قیمت ورود: {signal['trade_plan']['entry']}",
        f"حد ضرر (SL): {signal['trade_plan']['stop_loss']}",
        f"هدف ۱ (TP1): {signal['trade_plan']['take_profit_1']}",
        f"هدف ۲ (TP2): {signal['trade_plan']['take_profit_2']}",
        f"هدف ۳ (TP3): {signal['trade_plan']['take_profit_3']}",
        f"نسبت ریسک به بازده: {signal['trade_plan']['risk_reward_ratios']}",
        f"{prob_label}: {signal['ai_probability']['probability_pct']}%",
        f"امتیاز کل: {signal['score']['total_score']}/100",
        f"ریسک این معامله: {signal['trade_plan']['risk_pct_of_balance']}% از سرمایه "
        f"(≈ {signal['trade_plan']['risk_amount_usdt']} USDT)",
        "",
        "دلیل ورود:",
        signal["reason"],
    ]
    return "\n".join(lines)


def send_telegram_message(text: str, channel_id: str | None = None) -> bool:
    channel_id = channel_id or TELEGRAM_CHANNEL_ID
    if not TELEGRAM_BOT_TOKEN or not channel_id:
        logger.warning("تلگرام تنظیم نشده - پیام ارسال نشد.")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        resp = requests.post(url, json={"chat_id": channel_id, "text": text, "parse_mode": "HTML"}, timeout=10)
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error("ارسال تلگرام شکست خورد: %s", e)
        return False


def send_discord_message(text: str) -> bool:
    if not DISCORD_WEBHOOK_URL:
        return False
    try:
        resp = requests.post(DISCORD_WEBHOOK_URL, json={"content": text}, timeout=10)
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error("ارسال دیسکورد شکست خورد: %s", e)
        return False


def send_email(subject: str, body: str) -> bool:
    if not (SMTP_HOST and SMTP_USER and SMTP_PASS and ALERT_EMAIL_TO):
        return False
    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = SMTP_USER
        msg["To"] = ALERT_EMAIL_TO
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(SMTP_USER, [ALERT_EMAIL_TO], msg.as_string())
        return True
    except Exception as e:
        logger.error("ارسال ایمیل شکست خورد: %s", e)
        return False


def broadcast_signal(signal: dict):
    text = format_signal_message(signal)
    send_telegram_message(text)
    send_discord_message(text)
    send_email(f"سیگنال {signal['grade']} - {signal['symbol']}", text)


def broadcast_news_text(text: str) -> bool:
    """برای بخش ۱۱: ارسال متن تحلیل/خبر به کانال (بدون فرمت سیگنال)"""
    return send_telegram_message(text, channel_id=TELEGRAM_NEWS_CHANNEL_ID)


def format_news_digest(sentiment_report: dict) -> str:
    """
    ساخت یک متن خلاصه‌ی تحلیل بازار (احساسات + اخبار) برای پست دوره‌ای در کانال.
    این چیزیه که بخش ۱۱ درخواستی ("اخبار و تحلیل‌ها به‌صورت متن داخل کانال") رو پیاده می‌کنه.
    """
    fg = sentiment_report.get("fear_greed")
    news_sent = sentiment_report.get("news_sentiment", {})
    news_sample = sentiment_report.get("news_sample", [])

    lines = ["📰 خلاصه‌ی تحلیل بازار", ""]
    if fg:
        lines.append(f"شاخص ترس و طمع (Fear & Greed): {fg['value']}/100 ({fg['classification']})")
    lines.append(f"احساس کلی اخبار: {news_sent.get('label', 'نامشخص')} "
                 f"(از {news_sent.get('sample_size', 0)} خبر بررسی‌شده)")
    lines.append("")
    if news_sample:
        lines.append("چند خبر اخیر:")
        for item in news_sample[:5]:
            lines.append(f"• {item['title']}")
    lines.append("")
    lines.append("⚠️ این خلاصه صرفاً جهت اطلاع‌رسانیه، نه توصیه‌ی مالی.")
    return "\n".join(lines)
