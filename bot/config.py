"""
تنظیمات مرکزی ربات
همه‌ی مقادیر حساس از Environment Variables خونده میشن (روی Vercel در Project Settings -> Environment Variables ست میشن)
برای اجرای لوکال (بک‌تست/آموزش مدل)، می‌تونی یه فایل .env کنار پروژه بسازی؛ اینجا خودکار لود میشه.
"""
import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # روی Vercel نیازی به این نیست، env varها مستقیم ست شدن

# ---------- صرافی‌ها ----------
# نکته‌ی مهم: توابع سرورلس Vercel از IP آمریکا اجرا میشن. Binance و Bybit به‌خاطر
# قوانین خودشون، دسترسی از IP آمریکا رو می‌بندن (نه ایران - این یه محدودیت خودِ صرافیه).
# پس KuCoin و OKX رو اول امتحان می‌کنیم (که این محدودیت رو ندارن)، بعد Binance/Bybit
# رو به‌عنوان فال‌بک نگه می‌داریم (برای کسانی که این پروژه رو جای دیگه دیپلوی می‌کنن).
EXCHANGES = ["kucoin", "okx", "binance", "bybit"]
PRIMARY_EXCHANGE = "kucoin"

# ---------- ارزهایی که اسکن میشن ----------
# می‌تونی این لیست رو با متغیر محیطی SYMBOLS override کنی: "BTC/USDT,ETH/USDT,..."
DEFAULT_SYMBOLS = [
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "ADA/USDT",
    "DOGE/USDT", "AVAX/USDT", "LINK/USDT", "DOT/USDT", "LTC/USDT",
    "NEAR/USDT", "ARB/USDT", "BNB/USDT", "ATOM/USDT", "UNI/USDT",
]
SYMBOLS = [s.strip() for s in os.environ.get("SYMBOLS", ",".join(DEFAULT_SYMBOLS)).split(",") if s.strip()]

# ---------- تایم‌فریم‌ها (از بزرگ به کوچک) ----------
TIMEFRAMES = ["1M", "1w", "1d", "4h", "1h", "15m", "5m", "1m"]
# تایم‌فریم‌هایی که برای تشخیص "روند بزرگ" استفاده میشن (باید سیگنال هم‌جهت این‌ها باشه)
TREND_TIMEFRAMES = ["1w", "1d", "4h"]
# تایم‌فریم‌هایی که برای نقطه‌ی ورود دقیق استفاده میشن
ENTRY_TIMEFRAMES = ["1h", "15m"]  # 5m حذف شد - با تاخیر چند دقیقه‌ای فعلی (اسکن سنگین شده)، قیمت "لحظه‌ای" ۵ دقیقه‌ای دیگه معتبر نیست

CANDLES_LIMIT = 300  # تعداد کندل برای هر فچ (برای اکثر تحلیل‌ها کافیه)

# حداکثر زمانی که کل عملیات اسکن مجاز است طول بکشد (ثانیه). باید کمی کمتر از
# maxDuration در vercel.json باشد تا قبل از این‌که خودِ Vercel تابع را قطع کند
# (که باعث خطای 504 می‌شود)، ربات به‌آرامی متوقف شود و نتایج جزئی را برگرداند/بفرستد.
MAX_SCAN_SECONDS = int(os.environ.get("MAX_SCAN_SECONDS", "250"))

# حداقل فاصله‌ی زمانی (ساعت) بین دو سیگنال برای یک ارز - تا کاربر با چند سیگنال پشت‌سرهم
# از یک ارز گیج نشه. اگه سیگنال جدید ظرف این بازه بیاد، نادیده گرفته میشه.
SIGNAL_COOLDOWN_HOURS = float(os.environ.get("SIGNAL_COOLDOWN_HOURS", "1"))

# ---------- تلگرام ----------
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHANNEL_ID = os.environ.get("TELEGRAM_CHANNEL_ID", "")  # مثلا @mychannel یا -100123456789
TELEGRAM_NEWS_CHANNEL_ID = os.environ.get("TELEGRAM_NEWS_CHANNEL_ID", TELEGRAM_CHANNEL_ID)

# ---------- ایمیل (اختیاری) ----------
SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
ALERT_EMAIL_TO = os.environ.get("ALERT_EMAIL_TO", "")

# ---------- دیسکورد (اختیاری) ----------
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")

# ---------- ذخیره‌سازی (Upstash Redis - رایگان تا حجم مشخص) ----------
UPSTASH_REDIS_REST_URL = os.environ.get("UPSTASH_REDIS_REST_URL", "")
UPSTASH_REDIS_REST_TOKEN = os.environ.get("UPSTASH_REDIS_REST_TOKEN", "")

# ---------- امنیت Cron ----------
CRON_SECRET = os.environ.get("CRON_SECRET", "")  # هدر Authorization برای جلوگیری از صدا زدن غیرمجاز endpoint

# ---------- سیستم امتیازدهی (طبق درخواست کاربر) ----------
SCORE_WEIGHTS = {
    "trend": 27,  # افزایش از ۲۰ - قابل‌اتکاترین فاکتور (ساختار بازار چندتایم‌فریمی)
    "volume": 15,
    "news": 10,
    "liquidity": 20,
    "order_block": 8,  # کاهش از ۱۵ - توی ۳ بک‌تست پشت‌سرهم همبستگی نزدیک صفر یا منفی داشت (-0.099, -0.166)
    "rsi": 5,
    "open_interest": 10,
    "funding": 5,
}
TOTAL_SCORE = sum(SCORE_WEIGHTS.values())  # = 100

SCORE_GOLDEN = 90   # سیگنال طلایی
SCORE_GOOD = 80     # سیگنال خوب
SCORE_MIN_SEND = 70  # کمتر از این اصلا ارسال نشود

# حداقل احتمال موفقیتی که مدل AI (وقتی واقعاً آموزش‌دیده باشه، نه fallback) باید تایید کنه.
# این گیت فارغ از جهت (لانگ/شورت) اعمال میشه - AI بر اساس نتایج واقعی تصمیم می‌گیره، نه یک قانون
# دستی جهت‌گرا. زیر ۵۰٪ یعنی از سکه انداختن هم بدتره، پس منطقی نیست ارسال بشه.
MIN_AI_PROBABILITY_PCT = float(os.environ.get("MIN_AI_PROBABILITY_PCT", "50"))

# حداقل قدرت روند (ADX) برای ورود. زیر این مقدار یعنی بازار رنج/بی‌رونده - سیگنال‌های
# پرایس‌اکشن/SMC توش نویزی‌تر و کم‌اعتمادترن.
ADX_MIN_TREND_STRENGTH = float(os.environ.get("ADX_MIN_TREND_STRENGTH", "20"))

# حداکثر مجموع ریسک معاملات باز هم‌زمان (به درصد سرمایه). مثلاً اگه هر معامله ۱٪ ریسک داره
# و این مقدار ۵ باشه، حداکثر ۵ معامله‌ی باز هم‌زمان مجاز است - جلوگیری از قرارگرفتن بیش‌ازحد
# در معرض ریسک وقتی چند معامله هم‌زمان باز می‌مونن.
MAX_PORTFOLIO_OPEN_RISK_PCT = float(os.environ.get("MAX_PORTFOLIO_OPEN_RISK_PCT", "5"))

# حداکثر تعداد معاملات هم‌جهت (همه لانگ یا همه شورت) باز هم‌زمان. چون اکثر آلت‌کوین‌ها با هم
# (و با بیت‌کوین) هم‌بستگی بالایی دارن، چند تا معامله‌ی لانگ هم‌زمان روی چند ارز مختلف عملاً
# همون یه ریسک رو تکرار می‌کنه (نه تنوع واقعی) - این محدودیت جلوی این تجمع ریسک رو می‌گیره.
MAX_SAME_DIRECTION_OPEN = int(os.environ.get("MAX_SAME_DIRECTION_OPEN", "3"))

# ---------- اتصال به LBank برای اجرای واقعی معامله (اختیاری، پیش‌فرض خاموش) ----------
LBANK_API_KEY = os.environ.get("LBANK_API_KEY", "")
LBANK_API_SECRET = os.environ.get("LBANK_API_SECRET", "")
# LBANK_DRY_RUN=true (پیش‌فرض) یعنی فقط شبیه‌سازی میشه و سفارش واقعی زده نمیشه - فقط محاسبات
# رو نشون می‌ده. وقتی کاملاً مطمئن شدی، این رو توی Vercel بذار false تا واقعی معامله باز بشه.
LBANK_DRY_RUN = os.environ.get("LBANK_DRY_RUN", "true").lower() != "false"
AMOUNT_PRESETS_USDT = [20, 50, 100, 200]
LEVERAGE_PRESETS = [5, 10, 20, 50]

# چت شخصی تلگرام (نه کانال) که دکمه‌های تعاملی «ورود به معامله» توش فرستاده میشه
TELEGRAM_PERSONAL_CHAT_ID = os.environ.get("TELEGRAM_PERSONAL_CHAT_ID", "")
TELEGRAM_WEBHOOK_SECRET = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")

# ---------- مدیریت ریسک ----------
ACCOUNT_BALANCE_USDT = float(os.environ.get("ACCOUNT_BALANCE_USDT", "1000"))
RISK_PER_TRADE_PCT = float(os.environ.get("RISK_PER_TRADE_PCT", "1.0"))  # درصد ریسک هر معامله از کل سرمایه
MAX_DAILY_LOSS_PCT = float(os.environ.get("MAX_DAILY_LOSS_PCT", "3.0"))
MAX_WEEKLY_LOSS_PCT = float(os.environ.get("MAX_WEEKLY_LOSS_PCT", "7.0"))
MAX_DRAWDOWN_PCT = float(os.environ.get("MAX_DRAWDOWN_PCT", "15.0"))
RR_TARGETS = [1.5, 2.5, 4.0]  # نسبت ریسک به ریوارد برای TP1 / TP2 / TP3

# ---------- محدودیت‌های داده‌ی رایگان (مستندسازی صادقانه) ----------
# این بخش‌ها با بهترین جایگزین رایگان پیاده‌سازی شدن ولی دقت آن‌ها با نسخه‌ی
# پولی (Glassnode/Nansen/CoinGlass Pro/Twitter API رسمی) قابل مقایسه نیست.
FREE_TIER_LIMITATIONS = {
    "onchain": "دیتای whale/exchange flow از بلاکچین‌اکسپلورر رایگان BTC/ETH با محدودیت نرخ درخواست",
    "order_flow": "فوت‌پرینت واقعی نیاز به دیتای Level2 پولی دارد؛ جایگزین: عدم‌تعادل Order Book از طریق ccxt",
    "sentiment": "به‌جای API رسمی توییتر (پولی)، از عناوین اخبار رمزارزی رایگان (RSS) و Fear&Greed Index استفاده می‌شود",
    "liquidation_heatmap": "نسخه‌ی تقریبی بر پایه‌ی OI و تغییرات قیمت (نه دیتای واقعی صرافی)",
}
