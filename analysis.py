import pandas as pd
import numpy as np

def rsi(series: pd.Series, period: int = 14):
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta.where(delta < 0, 0.0))
    avg_gain = gain.rolling(window=period, min_periods=1).mean()
    avg_loss = loss.rolling(window=period, min_periods=1).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def sma(series: pd.Series, period: int = 20):
    return series.rolling(window=period).mean()

def ema(series: pd.Series, period: int = 20):
    return series.ewm(span=period, adjust=False).mean()

def macd(series: pd.Series):
    ema12 = ema(series, 12)
    ema26 = ema(series, 26)
    macd_line = ema12 - ema26
    signal = ema(macd_line, 9)
    hist = macd_line - signal
    return macd_line, signal, hist

def bollinger(series: pd.Series, period: int = 20, std: int = 2):
    middle = sma(series, period)
    rolling_std = series.rolling(window=period).std()
    upper = middle + (rolling_std * std)
    lower = middle - (rolling_std * std)
    return upper, middle, lower

def adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14):
    up_move = high.diff()
    down_move = low.diff() * -1
    pos_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=high.index)
    neg_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=high.index)
    tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(window=period).mean()
    plus_di = 100 * (pos_dm.ewm(alpha=1 / period).mean() / atr)
    minus_di = 100 * (neg_dm.ewm(alpha=1 / period).mean() / atr)
    dx = (abs(plus_di - minus_di) / (plus_di + minus_di).replace(0, np.nan)) * 100
    adx_line = dx.rolling(window=period).mean()
    return adx_line, plus_di, minus_di

def support_resistance(high: pd.Series, low: pd.Series, close: pd.Series, lookback: int = 60):
    levels = []
    pivot_highs = []
    pivot_lows = []
    for i in range(2, len(high) - 2):
        if high.iloc[i] > high.iloc[i - 1] and high.iloc[i] > high.iloc[i - 2] and high.iloc[i] > high.iloc[i + 1] and high.iloc[i] > high.iloc[i + 2]:
            pivot_highs.append((i, high.iloc[i]))
        if low.iloc[i] < low.iloc[i - 1] and low.iloc[i] < low.iloc[i - 2] and low.iloc[i] < low.iloc[i + 1] and low.iloc[i] < low.iloc[i + 2]:
            pivot_lows.append((i, low.iloc[i]))

    if pivot_highs:
        pivot_highs.sort(key=lambda x: x[1], reverse=True)
        resistance = round(float(pivot_highs[0][1]), 2)
        resistance2 = round(float(pivot_highs[1][1]), 2) if len(pivot_highs) > 1 else None
    else:
        resistance = round(float(high.iloc[-lookback:].max()), 2)
        resistance2 = None

    if pivot_lows:
        pivot_lows.sort(key=lambda x: x[1])
        support = round(float(pivot_lows[0][1]), 2)
        support2 = round(float(pivot_lows[1][1]), 2) if len(pivot_lows) > 1 else None
    else:
        support = round(float(low.iloc[-lookback:].min()), 2)
        support2 = None

    return support, resistance, support2, resistance2

def analyze_trend(close: pd.Series):
    trends = {}
    for period, name in [(20, "قصير المدى"), (50, "متوسط المدى"), (200, "طويل المدى")]:
        if len(close) > period:
            ma = sma(close, period)
            current_ma = ma.iloc[-1]
            trends[name] = "صاعد" if close.iloc[-1] > current_ma else "هابط" if close.iloc[-1] < current_ma else "محايد"
    return trends

def data_quality(hist: pd.DataFrame) -> dict:
    close = hist["Close"]
    issues = []
    ok = True
    if len(close) < 30:
        issues.append(f"البيانات غير كافية ({len(close)} يوم)")
        ok = False
    if close.isna().sum() > len(close) * 0.05:
        issues.append("توجد قيم مفقودة")
        ok = False
    if "Volume" in hist.columns and hist["Volume"].sum() == 0:
        issues.append("حجم تداول صفر")
    if close.iloc[-1] <= 0:
        issues.append("سعر إغلاق غير صالح")
        ok = False
    if len(close) >= 200 and all(close.iloc[-50:] == close.iloc[-1]):
        issues.append("سعر ثابت (بيانات قد تكون مجمدة)")
        ok = False
    return {"ok": ok, "issues": issues}

def get_rsi_score(rsi_val):
    if rsi_val > 80: return 5, "ذروة شراء قوية - احتمال تصحيح"
    if rsi_val > 70: return 10, "ذروة شراء - ترقب"
    if rsi_val > 60: return 15, "إيجابي - زخم صاعد"
    if rsi_val > 40: return 12, "محايد"
    if rsi_val > 30: return 8, "ضعيف - زخم هابط"
    if rsi_val > 20: return 4, "ذروة بيع - احتمال ارتداد"
    return 2, "ذروة بيع قوية"

def get_trend_score(trends, close, a):
    score = 0
    explanations = []
    short = trends.get("قصير المدى", "محايد")
    medium = trends.get("متوسط المدى", "محايد")
    long_t = trends.get("طويل المدى", "محايد")
    dir_map = {"صاعد": 1, "هابط": -1, "محايد": 0}
    score += dir_map.get(short, 0) * 5
    score += dir_map.get(medium, 0) * 5
    score += dir_map.get(long_t, 0) * 5
    total_dir = dir_map.get(short, 0) + dir_map.get(medium, 0) + dir_map.get(long_t, 0)
    if total_dir >= 2:
        score += 5
        explanations.append("الاتجاه متوافق عبر المديات")
    elif total_dir <= -2:
        score -= 5
        explanations.append("الاتجاه هابط بجميع المديات")
    elif total_dir == 0:
        explanations.append("اتجاهات متضاربة - يفضل انتظار وضوح")
    else:
        explanations.append("اتجاه غير متكامل")
    sma50 = a.get("sma50")
    sma200 = a.get("sma200")
    if sma50 and sma200:
        if a.get("golden_cross") and short == "صاعد":
            score += 5
            explanations.append("Golden Cross يعزز الاتجاه الصاعد")
        elif a.get("golden_cross") and short == "هابط":
            explanations.append("رغم Golden Cross طويل المدى، الزخم القصير هابط")
        elif not a.get("golden_cross") and short == "هابط":
            score -= 5
        elif not a.get("golden_cross") and short == "صاعد":
            explanations.append("اتجاه قصير صاعد رغم Death Cross طويل المدى")
    return max(-20, min(20, score)), explanations

def get_macd_score(a):
    macd_val = a.get("macd", 0)
    signal_val = a.get("macd_signal", 0)
    hist_val = a.get("macd_hist", 0)
    score = 10
    explanations = []
    bullish = a.get("macd_bullish", False)
    if bullish and hist_val > 0:
        score += 5
        explanations.append("MACD إيجابي - الزخم الصاعد يتعزز")
    elif bullish:
        score += 2
        explanations.append("MACD إيجابي")
    elif not bullish and hist_val < 0:
        score -= 5
        explanations.append("MACD سلبي - الزخم الهابط يتعزز")
    else:
        score -= 2
        explanations.append("MACD سلبي")
    if abs(macd_val - signal_val) < 0.5:
        explanations.append("MACD يقترب من التقاطع - ترقب")
    return max(0, min(15, score)), explanations

def get_volume_score(a):
    ratio = a.get("volume_ratio", 1.0)
    spike = a.get("volume_spike", False)
    score = 7
    explanations = []
    if spike and ratio > 2:
        score += 8
        explanations.append(f"حجم قوي جداً ({ratio}x)")
    elif spike:
        score += 5
        explanations.append(f"حجم أعلى من المتوسط ({ratio}x)")
    elif ratio < 0.5:
        score -= 3
        explanations.append("حجم ضعيف")
    else:
        explanations.append("حجم طبيعي")
    return max(0, min(15, score)), explanations

def get_sr_score(a, close_val):
    support = a.get("support")
    resistance = a.get("resistance")
    if not support or not resistance:
        return 10, []
    score = 10
    explanations = []
    range_size = (resistance - support) / support * 100
    dist_to_support = abs(close_val - support) / support * 100
    dist_to_resistance = abs(resistance - close_val) / close_val * 100
    if dist_to_support < 1.5:
        score += 5
        explanations.append(f"السعر قريب جداً من الدعم ({dist_to_support:.1f}%) - فرصة ارتداد")
    elif dist_to_resistance < 1.5:
        score -= 3
        explanations.append(f"السعر قريب من المقاومة ({dist_to_resistance:.1f}%) - حذر")
    if range_size < 3:
        explanations.append("المدى ضيق - اختراق وشيك")
    elif range_size > 15:
        explanations.append("المدى واسع")
    return max(0, min(15, score)), explanations

def get_volatility_score(a):
    vol = a.get("volatility", 0)
    score = 7
    explanations = []
    if vol > 4:
        score -= 4
        explanations.append(f"تذبذب عالي ({vol}%) - مخاطرة مرتفعة")
    elif vol > 2:
        score -= 1
        explanations.append(f"تذبذب متوسط ({vol}%)")
    else:
        score += 3
        explanations.append(f"تذبذب منخفض ({vol}%)")
    return max(0, min(10, score)), explanations

def get_adx_score(a):
    adx_val = a.get("adx")
    if adx_val is None:
        return 5, []
    score = 5
    explanations = []
    if adx_val > 40:
        score += 5
        explanations.append(f"اتجاه قوي جداً (ADX {adx_val})")
    elif adx_val > 25:
        score += 3
        explanations.append(f"اتجاه موجود (ADX {adx_val})")
    elif adx_val > 20:
        explanations.append(f"اتجاه ضعيف (ADX {adx_val}) - تذبذب")
    else:
        score -= 3
        explanations.append(f"بدون اتجاه واضح (ADX {adx_val}) - تذبذب")
    return max(0, min(10, score)), explanations

def get_bb_score(a, close_val):
    bb_upper = a.get("bb_upper")
    bb_lower = a.get("bb_lower")
    if not bb_upper or not bb_lower:
        return 5, []
    score = 5
    explanations = []
    pos = a.get("bb_pos", "middle")
    if pos == "above":
        if close_val > bb_upper * 1.02:
            score -= 2
            explanations.append("Bollinger: السعر أعلى من الحد العلوي - مبالغة في الشراء")
        else:
            score -= 1
            explanations.append("Bollinger: السعر عند الحد العلوي")
    elif pos == "below":
        if close_val < bb_lower * 0.98:
            score += 2
            explanations.append("Bollinger: السعر أدنى من الحد السفلي - مبالغة في البيع")
        else:
            score += 1
            explanations.append("Bollinger: السعر عند الحد السفلي")
    else:
        explanations.append("Bollinger: السعر داخل النطاق")
        score += 1
    return max(0, min(10, score)), explanations

def compute_total_score(scores):
    total = sum(s["score"] for s in scores.values())
    details = {}
    for k, v in scores.items():
        details[k] = v["score"]
    return min(100, max(0, total)), details

def get_verdict_from_score(total_score, a, safe_mode=False):
    if total_score >= 86:
        if safe_mode and total_score < 90:
            return "شراء بحذر 🟢", "شراء بحذر", "متوسطة"
        return "شراء قوي 🟢", "شراء قوي", "قوية"
    elif total_score >= 71:
        return "شراء بحذر 🟢", "شراء بحذر", "متوسطة"
    elif total_score >= 56:
        return "مراقبة إيجابية 🟡", "مراقبة إيجابية", "متوسطة"
    elif total_score >= 36:
        return "انتظار ⚪", "انتظار", "ضعيفة"
    elif total_score >= 0:
        return "تجنب 🔴", "تجنب", "متوسطة"
    return "بيع قوي 🔴", "بيع قوي", "قوية"

def explain_rsi(rsi_val):
    if rsi_val > 70:
        return f"مؤشر القوة النسبية عند {rsi_val}، وهو أعلى من 70 مما يعني تشبع شرائي. تاريخياً، عندما يصل RSI لهذه المستويات قد يشهد السعر تصحيحاً قريباً."
    elif rsi_val < 30:
        return f"مؤشر القوة النسبية عند {rsi_val}، وهو أقل من 30 مما يشير إلى تشبع بيعي. غالباً ما يتبع ذلك ارتداد صاعد."
    elif rsi_val > 60:
        return f"مؤشر القوة النسبية عند {rsi_val}، في منطقة القوة. الزخم الحالي إيجابي."
    elif rsi_val < 40:
        return f"مؤشر القوة النسبية عند {rsi_val}، في منطقة الضعف. الزخم الحالي سلبي."
    return f"مؤشر القوة النسبية عند {rsi_val}، في منطقة محايدة. لا يوجد تشبع."

def explain_macd(a):
    bullish = a.get("macd_bullish", False)
    hist = a.get("macd_hist", 0)
    if bullish and hist > 0:
        return "MACD أعلى من خط الإشارة مع هيستوجرام إيجابي، مما يعزز الزخم الصاعد."
    elif bullish:
        return "MACD أعلى من خط الإشارة، لكن الهيستوجرام يضعف. الزخم الصاعد يحتاج تأكيد."
    elif not bullish and hist < 0:
        return "MACD تحت خط الإشارة مع هيستوجرام سلبي، مما يعزز الزخم الهابط."
    else:
        return "MACD تحت خط الإشارة، مما يشير إلى زخم هابط حالياً."

def explain_ma(a, close_val):
    sma20 = a.get("sma20")
    sma50 = a.get("sma50")
    sma200 = a.get("sma200")
    parts = []
    if sma20:
        pos = "أعلى" if close_val > sma20 else "تحت"
        parts.append(f"السعر {pos} SMA20 ({sma20})")
    if sma50:
        pos = "أعلى" if close_val > sma50 else "تحت"
        parts.append(f"السعر {pos} SMA50 ({sma50})")
    if sma200:
        pos = "أعلى" if close_val > sma200 else "تحت"
        parts.append(f"السعر {pos} SMA200 ({sma200})")
    return "، ".join(parts)

def explain_bb(a):
    pos = a.get("bb_pos", "middle")
    if pos == "above":
        return "السعر عند الحد العلوي لبولينجر وهو مستوى مبالغة في الشراء."
    elif pos == "below":
        return "السعر عند الحد السفلي لبولينجر وهو مستوى مبالغة في البيع."
    return "السعر ضمن النطاق الطبيعي لبولينجر."

def explain_adx(a):
    adx_val = a.get("adx")
    if adx_val is None: return "لا توجد بيانات كافية لـ ADX."
    if adx_val > 40: return f"ADX عند {adx_val} - اتجاه قوي جداً، مناسب للصفقات الاتجاهية."
    if adx_val > 25: return f"ADX عند {adx_val} - يوجد اتجاه."
    if adx_val > 20: return f"ADX عند {adx_val} - اتجاه ضعيف، السوق في تذبذب."
    return f"ADX عند {adx_val} - سوق تذبذب بدون اتجاه واضح."

def explain_volume(a):
    ratio = a.get("volume_ratio", 1.0)
    if ratio > 2: return f"حجم التداول أعلى من المعدل بـ {ratio}x، تأكيد قوي للحركة."
    if ratio > 1.5: return f"حجم التداول أعلى من المعدل بـ {ratio}x."
    if ratio < 0.5: return "حجم التداول ضعيف جداً مقارنة بالمعدل."
    return "حجم التداول طبيعي."

def analyze(hist: pd.DataFrame) -> dict:
    a = {}
    dq = data_quality(hist)
    a["data_quality"] = dq

    close = hist["Close"]
    high = hist["High"]
    low = hist["Low"]
    last_close = float(close.iloc[-1])

    rsi_values = rsi(close)
    a["rsi"] = round(float(rsi_values.iloc[-1]), 2)

    sma20 = sma(close, 20)
    sma50 = sma(close, 50)
    sma200 = sma(close, 200)
    if not sma20.empty: a["sma20"] = round(float(sma20.iloc[-1]), 2)
    if not sma50.empty: a["sma50"] = round(float(sma50.iloc[-1]), 2)
    if not sma200.empty: a["sma200"] = round(float(sma200.iloc[-1]), 2)
    if not sma50.empty and not sma200.empty:
        a["golden_cross"] = sma50.iloc[-1] > sma200.iloc[-1]

    macd_line, signal_line, hist_line = macd(close)
    a["macd"] = round(float(macd_line.iloc[-1]), 2)
    a["macd_signal"] = round(float(signal_line.iloc[-1]), 2)
    a["macd_hist"] = round(float(hist_line.iloc[-1]), 2)
    a["macd_bullish"] = bool(macd_line.iloc[-1] > signal_line.iloc[-1])

    bb_upper, bb_mid, bb_lower = bollinger(close)
    a["bb_upper"] = round(float(bb_upper.iloc[-1]), 2)
    a["bb_mid"] = round(float(bb_mid.iloc[-1]), 2)
    a["bb_lower"] = round(float(bb_lower.iloc[-1]), 2)
    a["bb_pos"] = "above" if last_close > bb_upper.iloc[-1] else "below" if last_close < bb_lower.iloc[-1] else "middle"

    vol_avg = hist["Volume"].rolling(20).mean()
    a["volume_spike"] = bool(hist["Volume"].iloc[-1] > vol_avg.iloc[-1] * 1.5) if not vol_avg.empty else False
    a["volume_ratio"] = round(float(hist["Volume"].iloc[-1] / vol_avg.iloc[-1]), 2) if not vol_avg.empty else 1.0

    daily_returns = close.pct_change()
    a["volatility"] = round(float(daily_returns.std() * 100), 2)

    high_52w = close.rolling(252).max()
    low_52w = close.rolling(252).min()
    a["high_52w"] = round(float(high_52w.iloc[-1]), 2) if not high_52w.empty else None
    a["low_52w"] = round(float(low_52w.iloc[-1]), 2) if not low_52w.empty else None
    if a.get("high_52w"):
        a["from_52w_high"] = round(((last_close / a["high_52w"]) - 1) * 100, 2)

    a["change_1d"] = round(float(close.pct_change(1).iloc[-1] * 100), 2) if len(close) > 1 else 0
    a["change_5d"] = round(float(close.pct_change(5).iloc[-1] * 100), 2) if len(close) > 5 else 0
    a["change_1m"] = round(float(close.pct_change(21).iloc[-1] * 100), 2) if len(close) > 21 else 0

    if len(high) > 60:
        support, resistance, support2, resistance2 = support_resistance(high, low, close)
        a["support"] = support
        a["resistance"] = resistance
        a["support2"] = support2
        a["resistance2"] = resistance2
    else:
        a["support"] = round(float(low.min()), 2)
        a["resistance"] = round(float(high.max()), 2)
        a["support2"] = None
        a["resistance2"] = None

    if len(close) > 20:
        adx_line, plus_di, minus_di = adx(high, low, close)
        a["adx"] = round(float(adx_line.iloc[-1]), 2) if not adx_line.empty else None
        a["plus_di"] = round(float(plus_di.iloc[-1]), 2) if not plus_di.empty else None
        a["minus_di"] = round(float(minus_di.iloc[-1]), 2) if not minus_di.empty else None

    a["trends"] = analyze_trend(close)
    a["close"] = last_close

    # Compute score
    scores = {}
    rsi_score, rsi_exp = get_rsi_score(a["rsi"])
    scores["RSI"] = {"score": rsi_score, "exp": rsi_exp}
    trend_score, trend_exp = get_trend_score(a["trends"], close, a)
    scores["اتجاه"] = {"score": trend_score, "exp": trend_exp}
    macd_score, macd_exp = get_macd_score(a)
    scores["MACD"] = {"score": macd_score, "exp": macd_exp}
    vol_score, vol_exp = get_volume_score(a)
    scores["حجم"] = {"score": vol_score, "exp": vol_exp}
    sr_score, sr_exp = get_sr_score(a, last_close)
    scores["دعم/مقاومة"] = {"score": sr_score, "exp": sr_exp}
    vola_score, vola_exp = get_volatility_score(a)
    scores["تقلب"] = {"score": vola_score, "exp": vola_exp}
    adx_score, adx_exp = get_adx_score(a)
    scores["ADX"] = {"score": adx_score, "exp": adx_exp}
    bb_score, bb_exp = get_bb_score(a, last_close)
    scores["Bollinger"] = {"score": bb_score, "exp": bb_exp}

    total_score, score_details = compute_total_score(scores)
    a["score"] = total_score
    a["score_details"] = score_details
    a["score_explanations"] = {}
    for k, v in scores.items():
        a["score_explanations"][k] = v["exp"]
    a["signal_strength"] = total_score

    return a

def get_signal(analysis: dict, safe_mode=False) -> dict:
    verdict, verdict_raw, conviction = get_verdict_from_score(analysis.get("score", 50), analysis, safe_mode)
    score = analysis.get("score", 50)
    total_score = score

    # Generate context-aware signal list
    signals = []
    trends = analysis.get("trends", {})
    short_trend = trends.get("قصير المدى", "محايد")
    medium_trend = trends.get("متوسط المدى", "محايد")
    long_trend = trends.get("طويل المدى", "محايد")

    rsi_val = analysis.get("rsi", 50)
    if rsi_val > 70:
        signals.append((rsi_val, f"⚠️ RSI {rsi_val}: ذروة شراء"))
    elif rsi_val < 30:
        signals.append((rsi_val, f"💡 RSI {rsi_val}: ذروة بيع"))
    elif rsi_val > 60:
        signals.append((rsi_val, f"📈 RSI {rsi_val}: زخم إيجابي"))
    elif rsi_val < 40:
        signals.append((rsi_val, f"📉 RSI {rsi_val}: زخم سلبي"))

    if analysis.get("macd_bullish"):
        signals.append((None, "📈 MACD إيجابي"))
    else:
        signals.append((None, "📉 MACD سلبي"))

    sma50 = analysis.get("sma50")
    sma200 = analysis.get("sma200")
    last_close = analysis.get("close", 0)
    if sma50 and sma200:
        gc = analysis.get("golden_cross")
        if gc and short_trend == "صاعد":
            signals.append((None, "🟢 Golden Cross يعزز الصعود"))
        elif gc:
            signals.append((None, "🟢 Golden Cross طويل المدى، لكن الزخم القصير يحتاج تأكيد"))
        elif not gc and short_trend == "هابط":
            signals.append((None, "🔴 Death Cross يعزز الهبوط"))
        elif not gc:
            signals.append((None, "🔴 Death Cross طويل المدى"))
    if sma50:
        if last_close > sma50: signals.append((None, f"📈 السعر أعلى SMA50 ({sma50})"))
        else: signals.append((None, f"📉 السعر تحت SMA50 ({sma50})"))

    if analysis.get("volume_spike"):
        signals.append((None, f"📊 حجم مرتفع ({analysis.get('volume_ratio')}x)"))
    if analysis.get("adx") and analysis["adx"] > 25:
        di = "إيجابي" if analysis.get("plus_di", 0) > analysis.get("minus_di", 0) else "سلبي"
        signals.append((None, f"📊 ADX {analysis['adx']} اتجاه {di}"))

    action = "HOLD"
    if total_score >= 71: action = "BUY"
    elif total_score <= 35: action = "SELL"

    emoji_map = {"BUY": "🟢", "SELL": "🔴", "HOLD": "⚪"}

    return {
        "score": total_score,
        "signals": signals,
        "verdict": verdict,
        "conviction": conviction,
        "action": action,
        "emoji": emoji_map.get(action, "⚪"),
        "score_details": analysis.get("score_details", {}),
        "score_explanations": analysis.get("score_explanations", {}),
        "rsi_explanation": explain_rsi(rsi_val),
        "macd_explanation": explain_macd(analysis),
        "ma_explanation": explain_ma(analysis, last_close),
        "bb_explanation": explain_bb(analysis),
        "adx_explanation": explain_adx(analysis),
        "volume_explanation": explain_volume(analysis),
    }
