import numpy as np
import pandas as pd


def _exp_smoothing(close, alpha=0.3):
    result = [close[0]]
    for i in range(1, len(close)):
        result.append(alpha * close[i] + (1 - alpha) * result[-1])
    return np.array(result)


def predict_price(hist: pd.DataFrame, days_ahead: int = 30) -> dict | None:
    if hist.empty or len(hist) < 30:
        return None

    close = hist["Close"].values
    last_price = float(close[-1])

    x = np.arange(len(close))
    last_x = len(close) - 1
    future_x = np.arange(len(close), len(close) + days_ahead)
    rel_future = future_x - last_x

    # Model 1: Quadratic polynomial regression
    try:
        coeffs = np.polyfit(x, close, deg=2)
        poly = np.poly1d(coeffs)
        poly_future = poly(future_x)
        poly_pred = float(poly_future[-1])
    except Exception:
        poly_future = np.full(days_ahead, last_price)
        poly_pred = last_price

    # Model 2: Exponential smoothing with trend
    try:
        smoothed = _exp_smoothing(close, alpha=0.3)
        short_smooth = _exp_smoothing(close, alpha=0.6)
        recent_trend = short_smooth[-1] - smoothed[-1]
        es_future = np.array([smoothed[-1] + recent_trend * (i + 1) * 0.5 for i in range(days_ahead)])
        es_pred = float(es_future[-1])
    except Exception:
        es_future = np.full(days_ahead, last_price)
        es_pred = last_price

    # Ensemble: weighted average (polynomial 60%, exponential smoothing 40%)
    w_poly, w_es = 0.6, 0.4
    ensemble = poly_future * w_poly + es_future * w_es
    predicted = float(ensemble[-1])

    change_pct = round(((predicted - last_price) / last_price) * 100, 2)

    # R-squared for polynomial fit
    residuals = close - np.polyval(np.polyfit(x, close, 2), x)
    ss_res = np.sum(residuals ** 2)
    ss_tot = np.sum((close - np.mean(close)) ** 2)
    r_squared = 1 - (ss_res / ss_tot) if ss_tot != 0 else 0

    if r_squared > 0.8:
        confidence = "عالية 📊"
    elif r_squared > 0.5:
        confidence = "متوسطة 📉"
    else:
        confidence = "ضعيفة ⚠️"

    # Momentum-based direction confirmation (rate of change over 14 days)
    roc_14 = ((close[-1] - close[-14]) / close[-14] * 100) if len(close) >= 14 else 0
    momentum = "إيجابي" if roc_14 > 2 else "سلبي" if roc_14 < -2 else "محايد"

    if change_pct > 5:
        direction = "صعود قوي 📈"
    elif change_pct > 1:
        direction = "صعود 📈"
    elif change_pct > -1:
        direction = "ثابت ➖"
    elif change_pct > -5:
        direction = "هبوط 📉"
    else:
        direction = "هبوط قوي 📉"

    weekly = float(ensemble[6]) if days_ahead >= 7 else None
    monthly = float(ensemble[29]) if days_ahead >= 30 else None
    pred_high = float(np.max(ensemble))
    pred_low = float(np.min(ensemble))

    return {
        "current_price": round(last_price, 2),
        "predicted_price": round(predicted, 2),
        "change_pct": change_pct,
        "direction": direction,
        "confidence": confidence,
        "r_squared": round(r_squared, 3),
        "momentum": momentum,
        "weekly": round(weekly, 2) if weekly else None,
        "monthly": round(monthly, 2) if monthly else None,
        "pred_high": round(pred_high, 2),
        "pred_low": round(pred_low, 2),
        "days": days_ahead,
    }
