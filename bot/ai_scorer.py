"""
مدل AI برای پیش‌بینی احتمال موفقیت سیگنال، بر مبنای الگوهای معاملات گذشته.
صادقانه: آموزش یک مدل یادگیری عمیق واقعی (که "از هزاران معامله یاد بگیرد") نیاز به
زیرساخت GPU/آموزش جدا داره که در یک تابع سرورلس رایگان (Vercel Hobby) نمی‌گنجه.

راه‌حل عملی و واقعی: یک مدل Gradient Boosting سبک (scikit-learn) که:
1) با اسکریپت `train_model.py` روی داده‌ی بک‌تست تاریخی (چند سال) آموزش داده میشه (اجرا لوکال روی کامپیوتر خودت،
   یک‌بار یا هر چند وقت یک‌بار - نه در هر اجرای cron)
2) فایل مدل (`model.joblib`) کنار پروژه ذخیره و به گیت‌هاب/ورسل push میشه
3) در هر اجرای ربات (api/scan.py) این مدل فقط لود میشه و روی فیچرهای لحظه‌ای پیش‌بینی می‌کنه (سریع و سبک)

اگه فایل مدل هنوز آموزش داده نشده باشه (اولین اجرا)، سیستم به‌جای AI، از خودِ اسکور قانون-محور
(scoring.py) به عنوان تخمین احتمال استفاده می‌کنه تا کرش نکنه.

نکته‌ی مهم درباره‌ی فیچرها: به‌جای دادن «خلاصه‌ی دستی» (امتیازهای از‌قبل‌محاسبه‌شده‌ی scoring.py)،
اینجا داده‌ی خام‌تر (RSI واقعی، فاصله‌ی EMA، تعداد Order Block هم‌جهت، ...) به مدل داده میشه تا
خودش الگوها و ترکیب‌های مفید رو کشف کنه - نه اینکه محدود به فرضیات دستی من باشه.
"""
import logging
import os
import numpy as np

logger = logging.getLogger("ai_scorer")

MODEL_PATH = os.path.join(os.path.dirname(__file__), "..", "model.joblib")
_MODEL_CACHE = None

# ترتیب ثابت فیچرهای خام - این ترتیب بین آموزش (train_model.py) و پیش‌بینی زنده باید دقیقاً یکی باشه
RAW_FEATURE_KEYS = [
    "rsi_value",                      # مقدار خام RSI (نه امتیاز)
    "ema_diff_pct",                   # فاصله‌ی درصدی EMA20 از EMA50 (قدرت روند کوتاه‌مدت، علامت‌دار)
    "adx_value",                      # قدرت روند خام
    "atr_pct",                        # نوسان (ATR به نسبت قیمت)
    "vwap_aligned",                   # 1 اگه هم‌جهت VWAP، 0 اگه نه، 0.5 اگه نامشخص
    "liquidity_grab_match_count",     # چندتا Liquidity Grab هم‌جهت شناسایی شده
    "order_block_match_count",        # چندتا Order Block هم‌جهت
    "fvg_count",                      # تعداد Fair Value Gap شناسایی‌شده
    "breakout_retest_match",          # 1 اگه الگوی Breakout+Retest هم‌جهت بود
    "premium_discount_position_pct",  # موقعیت قیمت در رنج اخیر (۰-۱۰۰٪)
    "volume_z",                       # z-score اسپایک حجم
    "rr_target_2",                    # نسبت ریسک به ریوارد هدف دوم
    "funding_rate_x1000",             # نرخ فاندینگ (ضرب‌شده در ۱۰۰۰ برای مقیاس خواناتر)
    "fear_greed_value",               # شاخص ترس و طمع (۰-۱۰۰، پیش‌فرض ۵۰)
    "news_sentiment_score",           # امتیاز احساس اخبار (-۱ تا +۱)
    "hour_utc",                       # ساعت روز (۰-۲۳) - برای یادگیری اثر Kill Zone/جلسه‌ی معاملاتی
    "direction_encoded",              # ۱=لانگ، ۰=شورت - تا مدل بتونه الگوی مخصوص هر جهت رو جدا یاد بگیره
    "order_book_imbalance",           # عدم‌تعادل دفتر سفارش (-۱ فشار فروش تا +۱ فشار خرید)
]


def _load_model():
    global _MODEL_CACHE
    if _MODEL_CACHE is not None:
        return _MODEL_CACHE
    try:
        import joblib
        if os.path.exists(MODEL_PATH):
            _MODEL_CACHE = joblib.load(MODEL_PATH)
            logger.info("مدل AI بارگذاری شد.")
        else:
            _MODEL_CACHE = False
            logger.warning("فایل model.joblib پیدا نشد - از fallback قانون‌محور استفاده میشه. train_model.py را اجرا کن.")
    except Exception as e:
        _MODEL_CACHE = False
        logger.warning("لود مدل شکست خورد: %s", e)
    return _MODEL_CACHE


def build_feature_vector(raw_features: dict) -> list:
    """
    فیچرهای ورودی مدل، از روی دیکشنری فیچر خام (نه امتیاز خلاصه‌شده).
    اگه RAW_FEATURE_KEYS رو تغییر بدی، باید مدل رو دوباره آموزش بدی (ابعاد فرق می‌کنه).
    """
    return [raw_features.get(k, 0) or 0 for k in RAW_FEATURE_KEYS]


def predict_success_probability(raw_features: dict, rule_based_total_score: float) -> dict:
    model = _load_model()
    if not model:
        # Fallback: از خودِ امتیاز قانون‌محور به عنوان تخمین احتمال استفاده می‌کنیم (مقیاس 0-100 -> 0-100%)
        return {
            "probability_pct": round(min(max(rule_based_total_score, 0), 100), 1),
            "source": "rule_based_fallback",
        }
    try:
        features = np.array([build_feature_vector(raw_features)])
        raw_proba = float(model.predict_proba(features)[0][1]) * 100  # احتمال خام مدل (۰-۱۰۰)

        # محافظ مهم: تا وقتی مدل با داده‌ی واقعی کافی (صدها نمونه) اعتبارسنجی نشده، به آن اجازه‌ی
        # اعداد افراطی (مثلاً ۹۸٪ یا ۲٪) نمی‌دیم - چون با داده‌ی کم این اعداد گمراه‌کننده و خطرناکن.
        # عدد نهایی: ترکیب ۶۰٪ مدل + ۴۰٪ امتیاز قانون‌محور، و بعد به بازه‌ی ۲۰-۸۵٪ محدود میشه.
        blended = 0.6 * raw_proba + 0.4 * rule_based_total_score
        clamped = min(max(blended, 20.0), 85.0)
        return {"probability_pct": round(clamped, 1), "source": "ml_model", "raw_model_output_pct": round(raw_proba, 1)}
    except Exception as e:
        logger.warning("پیش‌بینی مدل شکست خورد (احتمالاً مدل قدیمیه و باید دوباره train بشه): %s", e)
        return {"probability_pct": round(min(max(rule_based_total_score, 0), 100), 1), "source": "rule_based_fallback"}
