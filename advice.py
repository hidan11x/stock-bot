import json
import logging
import requests
import os
from config import AI_PROVIDER

PROVIDERS = {
    "openai": {
        "url": "https://api.openai.com/v1/chat/completions",
        "model": os.getenv("OPENAI_MODEL", "gpt-4o"),
        "key_env": "OPENAI_API_KEY",
    },
    "deepseek": {
        "url": "https://api.deepseek.com/v1/chat/completions",
        "model": "deepseek-chat",
        "key_env": "DEEPSEEK_API_KEY",
    },
    "gemini": {
        "url": "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent",
        "key_env": "GEMINI_API_KEY",
        "model": None,
    },
}


def _call_ai_with_messages(messages: list, max_tokens: int = 500) -> str | None:
    provider = PROVIDERS.get(AI_PROVIDER)
    if not provider:
        return None

    api_key = os.getenv(provider["key_env"], "")
    if not api_key:
        return None

    try:
        if AI_PROVIDER == "gemini":
            gemini_parts = [{"text": m["content"]} for m in messages if m["role"] in ("user", "assistant")]
            url = f"{provider['url']}?key={api_key}"
            resp = requests.post(url, json={"contents": [{"parts": gemini_parts}],
                                            "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0.3}},
                                 timeout=20)
            if resp.ok:
                data = resp.json()
                return data["candidates"][0]["content"]["parts"][0]["text"]
        else:
            payload = {"model": provider["model"], "messages": messages,
                       "temperature": 0.3, "max_tokens": max_tokens}
            resp = requests.post(provider["url"],
                                 headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                                 json=payload, timeout=20)
            if resp.ok:
                return resp.json()["choices"][0]["message"]["content"]
    except Exception as e:
        logging.error(f"_call_ai_with_messages error: {e}")
    return None


def _call_ai(prompt: str, max_tokens: int = 500) -> str | None:
    provider = PROVIDERS.get(AI_PROVIDER)
    if not provider:
        return None

    import os
    api_key = os.getenv(provider["key_env"], "")
    if not api_key:
        return None

    try:
        if AI_PROVIDER == "gemini":
            url = f"{provider['url']}?key={api_key}"
            resp = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}],
                                            "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0.3}},
                                 timeout=20)
            if resp.ok:
                data = resp.json()
                return data["candidates"][0]["content"]["parts"][0]["text"]
        else:
            resp = requests.post(provider["url"],
                                 headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                                 json={"model": provider["model"], "messages": [{"role": "user", "content": prompt}],
                                       "temperature": 0.3, "max_tokens": max_tokens}, timeout=20)
            if resp.ok:
                return resp.json()["choices"][0]["message"]["content"]
    except Exception as e:
        logging.error(f"_call_ai error: {e}")
    return None


def get_ai_advice(symbol: str, price: float, analysis: dict, signal: dict) -> str | None:
    trends = analysis.get("trends", {})
    trend_str = ", ".join(f"{k}: {v}" for k, v in trends.items())
    signals_str = "\n".join(f"- {txt}" for _, txt in signal["signals"])

    prompt = f"""You are a professional Arabic financial advisor. Analyze this stock and give clear, concise advice.

Symbol: {symbol}
Price: {analysis.get('currency', '$')}{price}
Technical Analysis:
- RSI (14): {analysis.get('rsi', 'N/A')}
- SMA50: {analysis.get('sma50', 'N/A')} | SMA200: {analysis.get('sma200', 'N/A')}
- MACD: {'Bullish' if analysis.get('macd_bullish') else 'Bearish'}
- ADX: {analysis.get('adx', 'N/A')} (trend strength)
- Support: {analysis.get('currency', '$')}{analysis.get('support', 'N/A')} | Resistance: {analysis.get('currency', '$')}{analysis.get('resistance', 'N/A')}
- Volatility: {analysis.get('volatility', 'N/A')}%
- 52W High: {analysis.get('currency', '$')}{analysis.get('high_52w', 'N/A')} | 52W Low: {analysis.get('currency', '$')}{analysis.get('low_52w', 'N/A')}
- Trends: {trend_str}
- Volume: {analysis.get('volume_ratio', 'N/A')}x average

Trading Signal: {signal['action']} - {signal['verdict']} (confidence: {signal['conviction']}, score: {signal['score']})
Signals:
{signals_str}

Give advice in Arabic (3-4 sentences):
1. Current situation
2. Recommendation (buy/sell/hold) with reasoning
3. Risk level and stop-loss suggestion
4. Price target if applicable

Be direct and practical. Don't say "استشر مستشاراً مالياً". Give real advice."""

    ai_text = _call_ai(prompt)
    if ai_text:
        return ai_text
    return None


def get_ai_chat(symbol: str, question: str, hist, info) -> str | None:
    price = round(float(hist["Close"].iloc[-1]), 2)
    name = info.get("shortName") or info.get("longName") or symbol.upper()
    change = round(float(hist["Close"].pct_change(1).iloc[-1] * 100), 2)
    high = round(float(hist["High"].max()), 2)
    low = round(float(hist["Low"].min()), 2)
    avg_vol = round(float(hist["Volume"].mean()))

    prompt = f"""You are a professional Arabic financial assistant. Answer the user's question about {name} ({symbol}).

Current price: ${price} (daily change: {change}%)
Period high: ${high} | Low: ${low}
Average volume: {avg_vol:,}

User question: {question}

Answer in Arabic (2-3 sentences). Be direct and practical."""

    return _call_ai(prompt, max_tokens=300)


def get_local_advice(symbol: str, price: float, analysis: dict, signal: dict, currency: str = "$") -> str:
    trends = analysis.get("trends", {})
    trend_list = [f"{k}: {v}" for k, v in trends.items()]
    trend_str = "، ".join(trend_list)

    action = signal["action"]
    verdict = signal["verdict"]
    conv = signal["conviction"]

    c = currency
    advice = f"📊 *تحليل {symbol}*\n"
    advice += f"💰 السعر: {c}{price}\n\n"

    advice += "*المؤشرات الرئيسية:*\n"
    advice += f"• RSI: {analysis.get('rsi', '—')} "
    rsi = analysis.get("rsi", 50)
    if rsi > 70: advice += "(تشبع شرائي 🔴)"
    elif rsi < 30: advice += "(تشبع بيعي 🟢)"
    else: advice += "(محايد ⚪)"
    advice += f"\n• MACD: {'إيجابي 📈' if analysis.get('macd_bullish') else 'سلبي 📉'}"

    if analysis.get("adx"):
        adx = analysis["adx"]
        advice += f"\n• قوة الاتجاه (ADX): {adx} ({'قوي 💪' if adx > 25 else 'ضعيف'})"

    advice += f"\n• الاتجاه: {trend_str}\n"

    if analysis.get("support"):
        advice += f"\n*المستويات:*\n"
        advice += f"• الدعم: {c}{analysis['support']}\n"
        advice += f"• المقاومة: {c}{analysis['resistance']}\n"

    advice += f"\n*التوصية: {signal['emoji']} {action} - {verdict}*\n"
    advice += f"القناعة: {conv}\n\n"

    if action == "BUY":
        advice += "💡 *خطة العمل:*\n"
        advice += "1️⃣ الدخول عند التصحيح قرب الدعم\n"
        advice += "2️⃣ وقف الخسارة تحت الدعم ب 2-3%\n"
        advice += f"3️⃣ الهدف الأول: {c}{analysis.get('resistance', 'المقاومة')}\n"
        advice += "4️⃣ لا تدخل بأكثر من 5% من المحفظة"
    elif action == "SELL":
        advice += "💡 *خطة العمل:*\n"
        advice += "1️⃣ خفف المراكز تدريجياً\n"
        advice += "2️⃣ انتظر كسر الدعم لتأكيد الاتجاه\n"
        advice += "3️⃣ لا تشتري الآن"
    else:
        advice += "💡 *خطة العمل:*\n"
        advice += "1️⃣ انتظر وضوح الاتجاه\n"
        advice += "2️⃣ راقب كسر الدعم أو المقاومة\n"
        advice += "3️⃣ لا تتسرع في اتخاذ قرار"

    return advice
