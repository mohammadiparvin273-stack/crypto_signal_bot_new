"""
تحلیل احساسات بازار با منابع کاملاً رایگان:
- Fear & Greed Index: API عمومی alternative.me (رایگان، بدون کلید)
- اخبار: RSS رایگان کوین‌دسک/کوین‌تلگراف (بدون کلید) + امتیازدهی ساده‌ی کلمه‌کلیدی (lexicon-based)
- Google Trends: از کتابخانه‌ی pytrends (غیررسمی ولی رایگان) - اختیاری، چون بعضی وقت‌ها ریت‌لیمیت میشه

توجه صادقانه: Twitter/X, Reddit, Discord API رسمی برای دیتای زنده پولی/محدود هستند؛
اینجا جایگزین رایگان (اخبار + Fear&Greed) استفاده شده که نمای کلی احساسات بازار رو می‌ده.
"""
import logging
import requests

logger = logging.getLogger("sentiment")

POSITIVE_WORDS = [
    "surge", "rally", "bullish", "breakout", "adoption", "approval", "partnership",
    "record high", "ath", "inflow", "upgrade", "growth", "buy", "accumulate", "positive",
]
NEGATIVE_WORDS = [
    "crash", "bearish", "hack", "exploit", "ban", "lawsuit", "sec charges", "sell-off",
    "outflow", "liquidation", "collapse", "fraud", "delist", "downgrade", "negative", "dump",
]


def fetch_fear_greed_index():
    try:
        resp = requests.get("https://api.alternative.me/fng/", timeout=10)
        resp.raise_for_status()
        data = resp.json()["data"][0]
        return {"value": int(data["value"]), "classification": data["value_classification"]}
    except Exception as e:
        logger.warning("fear&greed unavailable: %s", e)
        return None


def fetch_crypto_news(limit=15):
    """اخبار از RSS رایگان (بدون نیاز به کلید API)"""
    import feedparser
    feeds = [
        "https://www.coindesk.com/arc/outboundfeeds/rss/",
        "https://cointelegraph.com/rss",
    ]
    items = []
    for url in feeds:
        try:
            parsed = feedparser.parse(url)
            for entry in parsed.entries[:limit]:
                items.append({"title": entry.get("title", ""), "link": entry.get("link", ""),
                              "published": entry.get("published", "")})
        except Exception as e:
            logger.warning("rss fetch failed for %s: %s", url, e)
    return items[:limit]


def score_news_sentiment(news_items: list, symbol_keyword: str | None = None):
    """
    امتیازدهی lexicon-based ساده (نه یک مدل NLP سنگین، به‌خاطر محدودیت زمان اجرا در سرورلس).
    اگه symbol_keyword داده بشه (مثلا 'bitcoin')، فقط اخبار مرتبط رو در نظر می‌گیره.
    خروجی نمره بین -1 (خیلی منفی) تا +1 (خیلی مثبت).
    """
    relevant = news_items
    if symbol_keyword:
        relevant = [n for n in news_items if symbol_keyword.lower() in n["title"].lower()] or news_items

    if not relevant:
        return {"score": 0.0, "label": "neutral", "sample_size": 0}

    pos, neg = 0, 0
    for item in relevant:
        title_lower = item["title"].lower()
        pos += sum(1 for w in POSITIVE_WORDS if w in title_lower)
        neg += sum(1 for w in NEGATIVE_WORDS if w in title_lower)

    total = pos + neg
    score = 0.0 if total == 0 else (pos - neg) / total
    label = "positive" if score > 0.2 else ("negative" if score < -0.2 else "neutral")
    return {"score": round(score, 2), "label": label, "sample_size": len(relevant), "positive_hits": pos, "negative_hits": neg}


def full_sentiment_report(symbol_keyword: str | None = None) -> dict:
    fg = fetch_fear_greed_index()
    news = fetch_crypto_news()
    news_sent = score_news_sentiment(news, symbol_keyword)
    return {
        "fear_greed": fg,
        "news_sentiment": news_sent,
        "news_sample": news[:5],
    }
