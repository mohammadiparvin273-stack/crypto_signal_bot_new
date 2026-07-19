# ربات تحلیلگر و سیگنال‌دهی حرفه‌ای ارز دیجیتال

نسخه‌ی پایتون استفاده‌شده: **Python 3.12** (هم برای اجرای لوکال/بک‌تست و هم برای تابع سرورلس Vercel، طبق تنظیم `runtime: vercel-python@3.12` در `vercel.json`)

ربات فقط **تحلیل می‌کند و سیگنال می‌فرستد** — هیچ اتصال مستقیمی به صرافی برای معامله‌ی خودکار ندارد. تمام معامله‌ها را خودتان دستی انجام می‌دهید.

---

## ۱. نقشه‌ی کامل ۱۲ بخش درخواستی -> فایل مربوطه

| # | بخش | فایل | وضعیت |
|---|---|---|---|
| 1 | پرایس اکشن + SMC + ICT + اندیکاتورها | `bot/price_action.py`, `bot/smc.py`, `bot/ict.py`, `bot/indicators.py` | ✅ کامل |
| 2 | چندتایم‌فریمی (ماهانه تا ۱ دقیقه) | `bot/config.py` (`TIMEFRAMES`), `bot/signal_engine.py` | ✅ کامل |
| 3 | حجم معاملات (Spike/Delta/VPVR/POC/HVN/LVN) | `bot/volume_analysis.py` | ✅ کامل (Delta با تخمین OHLCV، نه تیک واقعی) |
| 4 | Order Flow (Footprint/Bid-Ask/Tape) | `bot/data_fetcher.py::fetch_order_book` | ⚠️ نسخه‌ی ساده (عدم‌تعادل Order Book)، فوت‌پرینت واقعی نیاز به دیتای پولی |
| 5 | مشتقه (OI/Funding/Long-Short/Liquidation) | `bot/derivatives.py` | ✅ OI/Funding/LS واقعی و رایگان، Liquidation Heatmap تقریبی |
| 6 | آنچین | `bot/onchain.py` | ⚠️ فقط whale-tx نمونه برای BTC رایگان؛ بقیه نیاز به API پولی (توضیح در فایل) |
| 7 | احساسات بازار | `bot/sentiment.py` | ✅ Fear&Greed + اخبار رایگان (به‌جای توییتر/ردیت/دیسکورد پولی) |
| 8 | هوش مصنوعی (احتمال موفقیت) | `bot/ai_scorer.py`, `bot/train_model.py`, `bot/backtester.py` | ✅ مدل Gradient Boosting آموزش‌دیده روی بک‌تست |
| 9 | مدیریت سرمایه | `bot/risk_management.py` | ✅ کامل |
| 10 | سیستم امتیازدهی | `bot/scoring.py` | ✅ دقیقاً طبق وزن‌های درخواستی (Trend20/Volume15/News10/Liquidity20/OB15/RSI5/OI10/Funding5) |
| 11 | سیستم هشدار (تلگرام/ایمیل/دیسکورد) | `bot/notifier.py` | ✅ کامل، همه‌ی فیلدهای درخواستی در پیام |
| 12 | داشبورد | `api/dashboard.py` + `bot/storage.py` | ✅ کامل (نیاز به Upstash Redis رایگان) |
| + | اسکن کل بازار / تشخیص پامپ‌دامپ / دستکاری بازار / همبستگی / بک‌تست | `bot/signal_engine.py`, `bot/manipulation.py`, `bot/correlation.py`, `bot/backtester.py` | ✅ |

جزئیات محدودیت‌های داده‌ی رایگان (صادقانه و شفاف) در `bot/config.py -> FREE_TIER_LIMITATIONS` مستند شده.

---

## ۲. معماری اجرا (چون Vercel Cron رایگان فقط روزی یک‌بار مجازه)

```
GitHub Actions (رایگان، هر ۳۰ دقیقه) --HTTP--> /api/scan روی Vercel --> تحلیل --> تلگرام/دیسکورد/ایمیل
                                                      |
                                                      v
                                         Upstash Redis (ثبت تاریخچه برای داشبورد)
```

`vercel.json` هم یک کرون روزانه‌ی داخلی به عنوان فال‌بک دارد، ولی **موتور اصلی زمان‌بندی، GitHub Actions است.**

---

## ۳. راه‌اندازی گام‌به‌گام

### گام ۱ - ساخت ربات تلگرام
۱. در تلگرام با `@BotFather` صحبت کن، `/newbot` بزن، توکن رو کپی کن.
۲. یک کانال بساز، ربات رو به عنوان ادمین کانال اضافه کن.
۳. `chat_id` کانال رو پیدا کن (اگه کانال عمومیه، `@channel_username` کافیه).

### گام ۲ - ساخت دیتابیس رایگان Upstash (برای داشبورد و تاریخچه)
۱. به [upstash.com](https://upstash.com) برو، ثبت‌نام کن (رایگان).
۲. یک Redis Database بساز.
۳. از تب "REST API"، مقادیر `UPSTASH_REDIS_REST_URL` و `UPSTASH_REDIS_REST_TOKEN` رو کپی کن.

### گام ۳ - دیپلوی روی Vercel
۱. این پروژه رو به یک ریپازیتوری گیت‌هاب push کن.
۲. در [vercel.com](https://vercel.com) پروژه رو از روی ریپازیتوری Import کن.
۳. در بخش Environment Variables، همه‌ی مقادیر فایل `.env.example` رو ست کن (مخصوصاً `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHANNEL_ID`, `CRON_SECRET`, `UPSTASH_REDIS_REST_URL`, `UPSTASH_REDIS_REST_TOKEN`).
۴. Deploy بزن. بعد از دیپلوی، آدرس‌هایی مثل `https://your-app.vercel.app/api/scan` در دسترس میشن.

### گام ۴ - تنظیم GitHub Actions (زمان‌بند رایگان و اصلی)
در ریپازیتوری گیت‌هاب -> Settings -> Secrets and variables -> Actions، این‌ها رو اضافه کن:
- `VERCEL_SCAN_URL` = `https://your-app.vercel.app/api/scan`
- `VERCEL_RESET_URL` = `https://your-app.vercel.app/api/reset-period`
- `CRON_SECRET` = همون مقداری که در Vercel ست کردی

بعد از push، تب Actions ریپازیتوری رو چک کن؛ هر ۳۰ دقیقه به‌طور خودکار اجرا میشه. برای تست فوری، از تب Actions دکمه‌ی "Run workflow" رو بزن.

> نکته: GitHub Actions روی ریپازیتوری‌های Public کاملاً رایگان و نامحدوده. روی Private، ماهی ۲۰۰۰ دقیقه‌ی رایگان داری که برای این کار کاملاً کافیه.

### گام ۵ - (اختیاری ولی پیشنهادشده) آموزش مدل AI
```bash
pip install -r requirements.txt
python -m bot.backtester --symbol BTC/USDT --timeframe 4h --years 3 --train-model
```
این کار فایل `model.joblib` رو می‌سازه. اون رو به ریشه‌ی پروژه commit و push کن تا در دیپلوی بعدی Vercel، ربات به‌جای fallback قانون‌محور، از مدل واقعی AI برای تخمین احتمال موفقیت استفاده کنه. توصیه میشه این کار رو برای چند ارز مهم (BTC, ETH, ...) و چند تایم‌فریم تکرار کنی و نتایج بک‌تست (Win Rate/Profit Factor/Sharpe/Max Drawdown) رو قبل از اعتماد به سیگنال‌ها بررسی کنی.

### گام ۶ - گزارش دستی نتیجه‌ی معاملات (برای مدیریت ریسک دقیق)
بعد از هر معامله‌ای که دستی می‌بندی:
```bash
curl -X POST https://your-app.vercel.app/api/report-trade \
  -H "Authorization: Bearer $CRON_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"pnl_pct": -1.2}'
```
این عدد در محاسبه‌ی Max Daily/Weekly Loss و Drawdown و داشبورد لحاظ میشه.

---

## ۴. تست لوکال قبل از دیپلوی
```bash
python -m venv venv && source venv/bin/activate   # ویندوز: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env   # و مقادیرش رو پر کن
python -c "from bot.signal_engine import analyze_symbol; import json; print(json.dumps(analyze_symbol('BTC/USDT'), default=str, ensure_ascii=False, indent=2))"
```

## ۵. دیدن داشبورد
```
GET https://your-app.vercel.app/api/dashboard
```

---

## ۶. محدودیت‌های صادقانه (لطفاً قبل از استفاده‌ی واقعی بخون)
- این ربات **توصیه‌ی مالی نیست**؛ فقط ابزار تحلیل و اطلاع‌رسانیه. تصمیم نهایی و ریسک معامله با خودته.
- بخش‌های آنچین عمیق (Whale wallet دقیق، Exchange net-flow کامل، Token unlock، Miner activity) و Order Flow واقعی (Footprint) نیاز به سرویس‌های پولی (Glassnode, Nansen, CoinGlass Pro) دارند و در این نسخه با بهترین جایگزین رایگان یا با مقدار `None` (به همراه توضیح در کد) برگردانده می‌شوند.
- Sentiment از توییتر/ردیت/دیسکورد رسمی نیست (چون API آن‌ها پولی/محدوده)؛ به‌جاش از اخبار رایگان + Fear&Greed Index استفاده شده.
- مدل AI فقط به‌اندازه‌ی کیفیت و حجم بک‌تستی که خودت اجرا می‌کنی خوبه؛ حتماً قبل از اتکا به سیگنال‌ها، `train_model.py` رو با داده‌ی چند سال روی چند ارز مختلف اجرا و نتایج backtest رو بررسی کن.
- روی Vercel Hobby (رایگان)، هر اجرای `/api/scan` حداکثر ۶۰ ثانیه وقت داره. اگه با ۱۵-۲۰ ارز به مشکل timeout خوردی، از پارامتر `?symbols=BTC/USDT,ETH/USDT` برای batch کردن اسکن به چند گروه کوچک‌تر (و چند GitHub Action جدا یا چند مرحله در یک workflow) استفاده کن.
