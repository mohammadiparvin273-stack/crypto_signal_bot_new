"""
آموزش مدل Gradient Boosting روی دیتاست خروجی backtester.py و ذخیره‌ی آن در model.joblib
این اسکریپت **لوکال** اجرا میشه (نه در تابع سرورلس Vercel) چون آموزش مدل زمان‌بر است.
بعد از اجرا، فایل model.joblib تولیدشده را به ریشه‌ی پروژه (کنار vercel.json) اضافه و commit/push کن
تا در دیپلوی بعدی Vercel در دسترس ربات باشه.

اجرا:
    python -m bot.backtester --symbol BTC/USDT --timeframe 4h --years 5 --train-model
یا مستقیم با دیتاست آماده:
    python -m bot.train_model
"""
import os
import logging
import joblib
import numpy as np
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, roc_auc_score

from bot.ai_scorer import build_feature_vector, MODEL_PATH

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("train_model")


def train_and_save(records: list):
    X = np.array([build_feature_vector(r["features"], r["features"]) for r in records])
    y = np.array([r["label"] for r in records])

    if len(set(y)) < 2:
        logger.warning("دیتاست فقط یک کلاس داره - آموزش مدل معنی نداره. بک‌تست بیشتر/تایم‌فریم دیگه امتحان کن.")
        return

    # اگه داده خیلی کم باشه (کمتر از ۲۰ نمونه)، train_test_split با stratify کرش می‌کنه؛
    # در این حالت روی کل داده آموزش می‌دیم بدون داده‌ی جدا برای تست (فقط برای شروع سریع، دقتش قابل اتکا نیست)
    if len(records) < 20:
        logger.warning(
            "تعداد نمونه (%d) خیلی کمه برای جدا کردن داده‌ی تست. مدل روی کل داده آموزش می‌بینه "
            "ولی دقتش قابل‌اتکا نیست - فقط برای شروع سریع/تست اولیه استفاده کن.", len(records)
        )
        model = GradientBoostingClassifier(n_estimators=100, max_depth=2, learning_rate=0.05, random_state=42)
        model.fit(X, y)
        joblib.dump(model, MODEL_PATH)
        logger.info("مدل (با داده‌ی محدود) ذخیره شد در: %s", os.path.abspath(MODEL_PATH))
        return

    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42, stratify=y)
    model = GradientBoostingClassifier(n_estimators=150, max_depth=3, learning_rate=0.05, random_state=42)
    model.fit(X_train, y_train)

    preds = model.predict(X_test)
    proba = model.predict_proba(X_test)[:, 1]
    acc = accuracy_score(y_test, preds)
    try:
        auc = roc_auc_score(y_test, proba)
    except ValueError:
        auc = float("nan")

    logger.info("دقت مدل روی داده‌ی تست: %.2f%% | AUC: %.3f", acc * 100, auc)

    joblib.dump(model, MODEL_PATH)
    logger.info("مدل ذخیره شد در: %s", os.path.abspath(MODEL_PATH))


if __name__ == "__main__":
    logger.info("این اسکریپت باید بعد از تولید دیتاست (از طریق backtester.py) صدا زده بشه.")
    logger.info("مثال: python -m bot.backtester --symbol BTC/USDT --timeframe 4h --years 5 --train-model")
