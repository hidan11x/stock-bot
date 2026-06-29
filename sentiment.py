"""Sentiment analysis for news and social media using transformers"""
import logging
import re
from stock_data import get_news, get_stock_data

_SENTIMENT_PIPELINE = None

def _get_pipeline():
    global _SENTIMENT_PIPELINE
    if _SENTIMENT_PIPELINE is None:
        try:
            from transformers import pipeline
            _SENTIMENT_PIPELINE = pipeline(
                "sentiment-analysis",
                model="cardiffnlp/twitter-roberta-base-sentiment-latest",
                max_length=128, truncation=True
            )
        except Exception as e:
            logging.warning(f"Sentiment model load failed: {e}")
            _SENTIMENT_PIPELINE = False
    return _SENTIMENT_PIPELINE if _SENTIMENT_PIPELINE else None

_AR_KEYWORDS = {
    "positive": ["ارتفاع", "صعود", "أرباح", "قوي", "إيجابي", "نمو", "اختراق", "شراء", "تفاؤل",
                 "توزيعات", "استثمار", "توسع", "انتعاش", "جيد", "ممتاز", "قفزة", "تحسن", "ربح"],
    "negative": ["هبوط", "خسارة", "ديون", "سلبي", "ضعيف", "انهيار", "بيع", "تشاؤم",
                 "تضخم", "ركود", "حظر", "غرامة", "فشل", "تراجع", "انخفاض", "عجز", "مخاطرة", "ضرر"],
}

def _keyword_sentiment(text: str) -> dict:
    """Fast Arabic keyword-based sentiment fallback"""
    text_lower = text.lower()
    pos_count = sum(1 for kw in _AR_KEYWORDS["positive"] if kw in text_lower)
    neg_count = sum(1 for kw in _AR_KEYWORDS["negative"] if kw in text_lower)
    if pos_count > neg_count:
        label, score = "POSITIVE", min(0.5 + 0.1 * (pos_count - neg_count), 0.99)
    elif neg_count > pos_count:
        label, score = "NEGATIVE", min(0.5 + 0.1 * (neg_count - pos_count), 0.99)
    else:
        label, score = "NEUTRAL", 0.5
    return {"label": label, "score": score}

def analyze_sentiment(text: str) -> dict:
    """Analyze sentiment of a text, returns {label, score}"""
    pipe = _get_pipeline()
    if pipe:
        try:
            result = pipe(text[:512])[0]
            return {"label": result["label"], "score": result["score"]}
        except Exception:
            pass
    return _keyword_sentiment(text)

def analyze_news_sentiment(symbol: str, max_news: int = 10) -> list:
    """Fetch news and analyze sentiment for a symbol"""
    raw = get_news(symbol, max_items=max_news)
    results = []
    for item in raw:
        if isinstance(item, dict):
            title = item.get("title", "")
            link = item.get("link", "")
        else:
            title = item[0] if len(item) > 0 else ""
            link = item[2] if len(item) > 2 else ""
        if not title:
            continue
        sentiment = analyze_sentiment(title)
        results.append({
            "title": title,
            "link": link,
            "sentiment": sentiment["label"],
            "score": sentiment["score"],
        })
    return results

def get_market_sentiment(symbols: list = None) -> dict:
    """Overall market sentiment from top symbols"""
    if not symbols:
        symbols = ["SPY", "QQQ", "^GSPC", "BTC-USD", "GLD"]
    total_score = 0
    count = 0
    results = []
    for sym in symbols[:10]:
        news = analyze_news_sentiment(sym, max_news=3)
        if not news:
            continue
        avg_score = sum(
            1 if n["sentiment"] == "POSITIVE" else -1 if n["sentiment"] == "NEGATIVE" else 0
            for n in news
        ) / len(news)
        total_score += avg_score
        count += 1
        label = "إيجابي 🟢" if avg_score > 0 else "سلبي 🔴" if avg_score < 0 else "محايد ⚪"
        results.append({"symbol": sym, "sentiment": label, "score": avg_score, "count": len(news)})
    overall = total_score / count if count > 0 else 0
    return {
        "overall": "إيجابي 🟢" if overall > 0.2 else "سلبي 🔴" if overall < -0.2 else "محايد ⚪",
        "score": overall,
        "symbols": results,
    }
