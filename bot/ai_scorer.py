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
"""
import logging
import os
import numpy as np

logger = logging.getLogger("ai_scorer")

MODEL_PATH = os.path.join(os.path.dirname(__file__), "..", "model.joblib")
_MODEL_CACHE = None


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


def build_feature_vector(score_breakdown: dict, extra: dict) -> list:
    """
    فیچرهای ورودی مدل. اگه train_model.py رو تغییر بدی، این تابع هم باید هماهنگ آپدیت بشه
    (چون ترتیب فیچرها باید بین آموزش و پیش‌بینی یکسان باشه).
    """
    return [
        score_breakdown.get("trend", 0),
        score_breakdown.get("volume", 0),
        score_breakdown.get("news", 0),
        score_breakdown.get("liquidity", 0),
        score_breakdown.get("order_block", 0),
        score_breakdown.get("rsi", 0),
        score_breakdown.get("open_interest", 0),
        score_breakdown.get("funding", 0),
        extra.get("atr_pct", 0),          # ATR به نسبت قیمت (نوسان)
        extra.get("adx", 0),               # قدرت روند
        extra.get("rr_target_2", 2.5),     # نسبت ریسک به ریوارد هدف دوم
    ]


def predict_success_probability(score_breakdown: dict, rule_based_total_score: float, extra: dict) -> dict:
    model = _load_model()
    if not model:
        # Fallback: از خودِ امتیاز قانون‌محور به عنوان تخمین احتمال استفاده می‌کنیم (مقیاس 0-100 -> 0-100%)
        return {
            "probability_pct": round(min(max(rule_based_total_score, 0), 100), 1),
            "source": "rule_based_fallback",
        }
    try:
        features = np.array([build_feature_vector(score_breakdown, extra)])
        proba = model.predict_proba(features)[0][1]  # احتمال کلاس "موفق"
        return {"probability_pct": round(float(proba) * 100, 1), "source": "ml_model"}
    except Exception as e:
        logger.warning("پیش‌بینی مدل شکست خورد: %s", e)
        return {"probability_pct": round(min(max(rule_based_total_score, 0), 100), 1), "source": "rule_based_fallback"}
