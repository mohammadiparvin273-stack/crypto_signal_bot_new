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


LAST_TELEGRAM_ERROR = None


def send_telegram_message(text: str, channel_id: str | None = None, reply_to_message_id: int | None = None):
    """
    ارسال پیام به تلگرام. در صورت موفقیت، آیدی پیام (message_id) رو برمی‌گردونه (برای Reply کردن بعدی).
    در صورت شکست، None برمی‌گردونه و جزئیات خطا رو در LAST_TELEGRAM_ERROR ثبت می‌کنه.
    """
    global LAST_TELEGRAM_ERROR
    channel_id = channel_id or TELEGRAM_CHANNEL_ID
    if not TELEGRAM_BOT_TOKEN or not channel_id:
        LAST_TELEGRAM_ERROR = "توکن یا شناسه‌ی کانال تلگرام تنظیم نشده"
        logger.warning(LAST_TELEGRAM_ERROR)
        return None
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": channel_id, "text": text}
    if reply_to_message_id:
        payload["reply_to_message_id"] = reply_to_message_id
    try:
        resp = requests.post(url, json=payload, timeout=10)
        data = resp.json()
        if data.get("ok"):
            LAST_TELEGRAM_ERROR = None
            return data["result"]["message_id"]
        LAST_TELEGRAM_ERROR = f"تلگرام خطا داد: {data.get('description', data)}"
        logger.error(LAST_TELEGRAM_ERROR)
        return None
    except Exception as e:
        LAST_TELEGRAM_ERROR = f"استثنا در ارسال: {e}"
        logger.error(LAST_TELEGRAM_ERROR)
        return None


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
    """
    ارسال سیگنال به تلگرام/دیسکورد/ایمیل. خروجی: آیدی پیام تلگرام (برای Reply کردن پیام نتیجه بعداً)
    یا None اگه ارسال تلگرام شکست خورده باشه.
    """
    text = format_signal_message(signal)
    message_id = send_telegram_message(text)
    send_discord_message(text)
    send_email(f"سیگنال {signal['grade']} - {signal['symbol']}", text)
    return message_id


def broadcast_news_text(text: str) -> bool:
    """برای بخش ۱۱: ارسال متن تحلیل/خبر به همون کانال اصلی سیگنال‌ها (ساده و مطمئن)"""
    return send_telegram_message(text, channel_id=TELEGRAM_CHANNEL_ID)


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
