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
from sklearn.calibration import CalibratedClassifierCV
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, roc_auc_score

from bot.ai_scorer import build_feature_vector, MODEL_PATH

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("train_model")

# حداقل AUC قابل‌قبول برای ذخیره‌ی مدل. اگه مدل جدید از این کمتر بود، ذخیره نمیشه
# (مدل قبلی دست‌نخورده می‌مونه) - تا یه مدل ضعیف/گمراه‌کننده جایگزین نسخه‌ی بهتر نشه.
MIN_ACCEPTABLE_AUC = 0.55


def train_and_save(records: list, sample_weight: list | None = None):
    X = np.array([build_feature_vector(r["features"], r["features"]) for r in records])
    y = np.array([r["label"] for r in records])
    w = np.array(sample_weight) if sample_weight is not None else None

    if len(set(y)) < 2:
        logger.warning("دیتاست فقط یک کلاس داره - آموزش مدل معنی نداره. بک‌تست بیشتر/تایم‌فریم دیگه امتحان کن.")
        return

    # رگولاریزیشن محافظه‌کارانه (subsample<1, min_samples_leaf بالاتر) تا با داده‌ی کم،
    # مدل بیش‌ازحد به چند نمونه اعتماد نکنه.
    base_model_kwargs = dict(n_estimators=100, max_depth=2, learning_rate=0.03,
                              subsample=0.8, min_samples_leaf=5, random_state=42)

    if len(records) < 20:
        logger.warning(
            "تعداد نمونه (%d) خیلی کمه برای جدا کردن داده‌ی تست یا کالیبراسیون. مدل ذخیره نمیشه "
            "تا وقتی حداقل ۲۰ نمونه جمع بشه (فعلاً از fallback قانون‌محور استفاده میشه).", len(records)
        )
        return

    X_train, X_test, y_train, y_test = (
        train_test_split(X, y, w, test_size=0.2, random_state=42, stratify=y)[:4]
        if w is not None else
        train_test_split(X, y, test_size=0.2, random_state=42, stratify=y)
    )

    # کالیبراسیون احتمالات (Platt scaling): بدون این، مدل‌های Gradient Boosting روی داده‌ی کم
    # اعداد افراطی (۹۸٪, ۱۰۰٪) می‌دن که گمراه‌کننده و اشتباهه؛ این باعث میشه خروجی واقعاً
    # به معنای احتمال آماری باشه، نه یه عدد دلبخواهی.
    base_model = GradientBoostingClassifier(**base_model_kwargs)
    model = CalibratedClassifierCV(base_model, method="sigmoid", cv=3)
    model.fit(X_train, y_train)

    preds = model.predict(X_test)
    proba = model.predict_proba(X_test)[:, 1]
    acc = accuracy_score(y_test, preds)
    try:
        auc = roc_auc_score(y_test, proba)
    except ValueError:
        auc = float("nan")

    logger.info("دقت مدل روی داده‌ی تست: %.2f%% | AUC: %.3f", acc * 100, auc)
    logger.info("محدوده‌ی احتمال پیش‌بینی‌شده روی داده‌ی تست: %.1f%% تا %.1f%% (بعد از کالیبراسیون، دیگه نباید افراطی باشه)",
                proba.min() * 100, proba.max() * 100)

    if np.isnan(auc) or auc < MIN_ACCEPTABLE_AUC:
        logger.warning(
            "AUC (%.3f) کمتر از حداقل قابل‌قبول (%.2f) هست. این مدل ذخیره نمیشه تا مدل قبلی "
            "(اگه بوده) خراب نشه. با داده‌ی بیشتر/متفاوت دوباره امتحان کن.",
            auc, MIN_ACCEPTABLE_AUC,
        )
        return

    # مدل نهایی روی کل داده (train+test) دوباره آموزش داده میشه تا از همه‌ی داده استفاده بشه
    final_base = GradientBoostingClassifier(**base_model_kwargs)
    final_model = CalibratedClassifierCV(final_base, method="sigmoid", cv=3)
    final_model.fit(X, y)

    joblib.dump(final_model, MODEL_PATH)
    logger.info("مدل کالیبره‌شده ذخیره شد در: %s", os.path.abspath(MODEL_PATH))


if __name__ == "__main__":
    logger.info("این اسکریپت باید بعد از تولید دیتاست (از طریق backtester.py) صدا زده بشه.")
    logger.info("مثال: python -m bot.backtester --symbol BTC/USDT --timeframe 4h --years 5 --train-model")
