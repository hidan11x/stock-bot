"""Stock bot web dashboard — live prices, signals, portfolio, screener, sentiment"""
import json
import io
import logging
import os
from datetime import datetime
from flask import Flask, render_template, jsonify, request, abort
from database import db
from config import ADMIN_ID
from stock_data import get_stock_data, get_current_price, get_screener, get_news, market_status, resolve_symbol
from analysis import analyze, get_signal
from chart import generate_chart
from advice import get_local_advice

def _ccy(sym):
    s = str(sym).upper()
    if s.endswith(".SR"): return "﷼"
    r = resolve_symbol(s)
    if r != s and r.upper().endswith(".SR"): return "﷼"
    return "$"

app = Flask(__name__)

logging.basicConfig(
    filename=os.path.join(os.path.dirname(__file__), "dashboard_error.log"),
    level=logging.ERROR,
    format="%(asctime)s %(levelname)s: %(message)s",
)

# ─── HELPERS ───

def _convert(obj):
    """Convert numpy types to native Python for JSON serialization"""
    import numpy as np
    if isinstance(obj, dict):
        return {k: _convert(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_convert(v) for v in obj]
    elif isinstance(obj, np.integer):
        return int(obj)
    elif isinstance(obj, np.floating):
        return float(obj)
    elif isinstance(obj, np.bool_):
        return bool(obj)
    elif isinstance(obj, np.ndarray):
        return obj.tolist()
    return obj

def _user_data(uid):
    """Get user portfolio, watchlist, virtual portfolio"""
    return {
        "portfolio": db.gp(uid),
        "watchlist": db.gw(uid),
        "virtual": db.get("virtual_portfolios", {}).get(str(uid), {}),
        "info": db.get("user_info", {}).get(str(uid), {}),
    }

def _user_allowed(uid):
    return uid == ADMIN_ID or db.is_allowed(uid)

def _get_chart_b64(sym):
    """Generate chart and return as base64 data URI"""
    try:
        import base64
        from analysis import analyze
        hist, info = get_stock_data(sym, period="6mo", fetch_info=False)
        if hist is None:
            return None
        a = analyze(hist)
        chart = generate_chart(sym, hist, a)
        if not chart:
            return None
        if hasattr(chart, "savefig"):
            buf = io.BytesIO()
            chart.savefig(buf, format="png", dpi=100, bbox_inches="tight")
            buf.seek(0)
            return "data:image/png;base64," + base64.b64encode(buf.read()).decode()
        with open(chart, "rb") as f:
            return "data:image/png;base64," + base64.b64encode(f.read()).decode()
    except Exception as e:
        logging.error(f"chart error for {sym}: {e}")
    return None

def _analysis_data(sym):
    """Get full analysis for a symbol"""
    hist, info = get_stock_data(sym, period="6mo", fetch_info=True)
    if hist is None:
        return None
    a = analyze(hist)
    s = get_signal(a)
    price = round(float(hist["Close"].iloc[-1]), 2)
    cur = _ccy(sym)
    name = (info or {}).get("shortName") or (info or {}).get("longName") or sym.upper()
    return {
        "symbol": sym, "name": name, "price": price, "currency": cur,
        "score": a.get("score", 0), "verdict": s.get("verdict", "محايد"),
        "conviction": s.get("conviction", "متوسطة"),
        "rsi": a.get("rsi"), "adx": a.get("adx"),
        "macd_bullish": a.get("macd_bullish"),
        "support": a.get("support"), "resistance": a.get("resistance"),
        "support2": a.get("support2"), "resistance2": a.get("resistance2"),
        "volatility": a.get("volatility"), "volume_ratio": a.get("volume_ratio"),
        "volume_spike": a.get("volume_spike"),
        "trends": a.get("trends", {}),
        "golden_cross": a.get("golden_cross"),
        "sma20": a.get("sma20"), "sma50": a.get("sma50"), "sma200": a.get("sma200"),
        "bb_upper": a.get("bb_upper"), "bb_lower": a.get("bb_lower"),
    }

# ─── PAGES ───

@app.route("/")
def index():
    stats = db.get("stats", {})
    users = stats.get("users", [])
    wl = set(db.get("whitelist", []))
    user_info = db.get("user_info", {})
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    daily = stats.get("daily_users", {})
    active = sum(1 for u in users if str(u) in wl)
    pending = sum(1 for u in users if str(u) not in wl)
    sub_expiring = sum(1 for u in users if db.get("subscriptions", {}).get(str(u), 0) == 0)
    s = {
        "total_users": len(users),
        "active_users": active,
        "pending_users": pending,
        "sub_expiring": sub_expiring,
        "total_commands": sum(stats.get("commands", {}).values()),
        "total_messages": stats.get("total_messages", 0),
    }
    return render_template("dashboard.html", stats=s, users=[{
        "id": u,
        "name": user_info.get(str(u), {}).get("name", ""),
        "username": user_info.get(str(u), {}).get("username", ""),
        "first_seen": user_info.get(str(u), {}).get("first_seen", ""),
        "last_seen": user_info.get(str(u), {}).get("last_seen", ""),
        "days": db.get("subscriptions", {}).get(str(u), -1),
    } for u in users], now=now.strftime("%Y-%m-%d %H:%M"))

@app.route("/user/<int:uid>")
def user_dashboard(uid):
    if not _user_allowed(uid):
        return "🔒 غير مصرح", 403
    data = _user_data(uid)
    return render_template("user_dashboard.html", uid=uid, data=data, NOW=datetime.now().strftime("%Y-%m-%d %H:%M"))

@app.route("/user/<int:uid>/price")
def user_price(uid):
    try:
        if not _user_allowed(uid):
            return "🔒 غير مصرح", 403
        sym = request.args.get("symbol", "SPY").upper()
        resolved = resolve_symbol(sym)
        analysis = _analysis_data(resolved)
        if not analysis:
            return render_template("error.html", msg=f"❌ الرمز {sym} غير صحيح", uid=uid)
        chart_b64 = _get_chart_b64(resolved)
        return render_template("price.html", uid=uid, a=_convert(analysis), chart=chart_b64, NOW=datetime.now().strftime("%Y-%m-%d %H:%M"))
    except Exception as e:
        logging.exception("Price page error")
        return str(e), 500

@app.route("/user/<int:uid>/signal")
def user_signal(uid):
    if not _user_allowed(uid):
        return "🔒 غير مصرح", 403
    sym = request.args.get("symbol", "SPY").upper()
    resolved = resolve_symbol(sym)
    analysis = _analysis_data(resolved)
    if not analysis:
        return render_template("error.html", msg=f"❌ الرمز {sym} غير صحيح", uid=uid)
    hist, info = get_stock_data(resolved, period="6mo", fetch_info=True)
    cur = _ccy(resolved)
    local_advice = ""
    if hist is not None:
        a = analysis  # already has all analysis
        s = get_signal(analyze(hist))  # re-use
        try:
            local_advice = get_local_advice(resolved, analysis["price"], a, s, cur)
        except Exception:
            local_advice = ""
    chart_b64 = _get_chart_b64(resolved)
    return render_template("signal.html", uid=uid, a=analysis, chart=chart_b64, advice=local_advice, NOW=datetime.now().strftime("%Y-%m-%d %H:%M"))

@app.route("/user/<int:uid>/portfolio")
def user_portfolio(uid):
    if not _user_allowed(uid):
        return "🔒 غير مصرح", 403
    data = _user_data(uid)
    return render_template("portfolio.html", uid=uid, data=data, NOW=datetime.now().strftime("%Y-%m-%d %H:%M"))

@app.route("/user/<int:uid>/watchlist")
def user_watchlist(uid):
    if not _user_allowed(uid):
        return "🔒 غير مصرح", 403
    data = _user_data(uid)
    return render_template("watchlist.html", uid=uid, data=data, NOW=datetime.now().strftime("%Y-%m-%d %H:%M"))

@app.route("/user/<int:uid>/screener")
def user_screener(uid):
    if not _user_allowed(uid):
        return "🔒 غير مصرح", 403
    return render_template("screener.html", uid=uid, NOW=datetime.now().strftime("%Y-%m-%d %H:%M"))

@app.route("/user/<int:uid>/sentiment")
def user_sentiment(uid):
    if not _user_allowed(uid):
        return "🔒 غير مصرح", 403
    sym = request.args.get("symbol", "").upper()
    from sentiment import analyze_news_sentiment, get_market_sentiment
    if sym:
        resolved = resolve_symbol(sym)
        news = analyze_news_sentiment(resolved, max_news=10)
        return render_template("sentiment.html", uid=uid, symbol=resolved, news=news, NOW=datetime.now().strftime("%Y-%m-%d %H:%M"))
    market = get_market_sentiment()
    return render_template("sentiment.html", uid=uid, symbol="", news=[], market=market, NOW=datetime.now().strftime("%Y-%m-%d %H:%M"))

@app.route("/user/<int:uid>/heatmap")
def user_heatmap(uid):
    if not _user_allowed(uid):
        return "🔒 غير مصرح", 403
    return render_template("heatmap.html", uid=uid, NOW=datetime.now().strftime("%Y-%m-%d %H:%M"))

@app.route("/user/<int:uid>/virtual")
def user_virtual(uid):
    if not _user_allowed(uid):
        return "🔒 غير مصرح", 403
    vp = db.get("virtual_portfolios", {}).get(str(uid), {"cash": 100000, "holdings": [], "history": []})
    return render_template("virtual.html", uid=uid, vp=vp, NOW=datetime.now().strftime("%Y-%m-%d %H:%M"))

# ─── API ───

@app.route("/api/price")
def api_price():
    try:
        sym = request.args.get("symbol", "SPY").upper()
        resolved = resolve_symbol(sym)
        analysis = _analysis_data(resolved)
        if not analysis:
            return jsonify({"error": "symbol not found"}), 404
        price, chg, pct = get_current_price(resolved)
        analysis["change"] = chg
        analysis["change_pct"] = pct
        return jsonify(_convert(analysis))
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route("/api/chart")
def api_chart():
    sym = request.args.get("symbol", "SPY").upper()
    resolved = resolve_symbol(sym)
    b64 = _get_chart_b64(resolved)
    return jsonify({"chart": b64})

@app.route("/api/chart_data")
def api_chart_data():
    """Stats data for dashboard charts"""
    stats = db.get("stats", {})
    commands = stats.get("commands", {})
    cmd_labels = list(commands.keys())[:15]
    cmd_values = [commands.get(k, 0) for k in cmd_labels]
    daily = stats.get("daily_users", {})
    daily_labels = sorted(daily.keys())[-30:]
    daily_values = [daily.get(k, 0) for k in daily_labels]
    return jsonify({"cmd_labels": cmd_labels, "cmd_values": cmd_values,
                    "daily_labels": daily_labels, "daily_values": daily_values})

@app.route("/api/screener")
def api_screener():
    import time as _t
    cache_age = getattr(api_screener, '_cache_ts', 0)
    if _t.time() - cache_age < 60 and getattr(api_screener, '_scored', None):
        scored = api_screener._scored
    else:
        results = get_screener() or []
        filter_type = request.args.get("filter", "")
        scored = []
        for name, sym, price, chg, pct in (results or [])[:50]:
            if filter_type:
                hist, _ = get_stock_data(sym, period="6mo", fetch_info=False)
                if hist is None:
                    continue
                a = analyze(hist)
                score = a.get("score", 0)
                trends = a.get("trends", {})
                short_t = trends.get("قصير المدى", "محايد")
                match = False
                if filter_type == "golden" and a.get("golden_cross"): match = True
                elif filter_type == "death" and a.get("golden_cross") is False: match = True
                elif filter_type == "uptrend" and short_t == "صاعد": match = True
                elif filter_type == "downtrend" and short_t == "هابط": match = True
                elif filter_type == "rsi_low" and a.get("rsi", 50) < 35: match = True
                elif filter_type == "rsi_high" and a.get("rsi", 50) > 65: match = True
                elif filter_type == "volume" and a.get("volume_ratio", 1) > 1.5: match = True
                elif filter_type == "adx" and a.get("adx", 0) > 25: match = True
                if not match:
                    continue
                scored.append({"name": name, "symbol": sym, "price": price, "change": chg,
                              "change_pct": pct, "score": score,
                              "rsi": a.get("rsi"), "adx": a.get("adx")})
            else:
                scored.append({"name": name, "symbol": sym, "price": price, "change": chg,
                              "change_pct": pct, "score": 0})
        if not filter_type:
            api_screener._scored = scored
            api_screener._cache_ts = _t.time()
    return jsonify(_convert({"results": scored[:30]}))

@app.route("/api/sentiment")
def api_sentiment():
    sym = request.args.get("symbol", "").upper()
    if not sym:
        from sentiment import get_market_sentiment
        return jsonify(get_market_sentiment())
    from sentiment import analyze_news_sentiment
    news = analyze_news_sentiment(resolve_symbol(sym), max_news=10)
    return jsonify(_convert({"symbol": sym, "news": news}))

@app.route("/api/virtual/trade", methods=["POST"])
def api_virtual_trade():
    data = request.json
    uid = data.get("uid")
    action = data.get("action")
    symbol = data.get("symbol", "").upper()
    qty = float(data.get("qty", 0))
    if not uid or not action or not symbol or qty <= 0:
        return jsonify({"error": "invalid"}), 400
    vp = db.setdefault("virtual_portfolios", {}).setdefault(str(uid),
        {"cash": 100000.0, "holdings": [], "history": []})
    price, _, _ = get_current_price(symbol)
    if not price:
        return jsonify({"error": "رمز غير صحيح"}), 400
    if action == "buy":
        cost = price * qty
        if cost > vp["cash"]:
            return jsonify({"error": "رصيد غير كافٍ"}), 400
        vp["cash"] -= cost
        for h in vp["holdings"]:
            if h["symbol"] == symbol:
                total_qty = h["qty"] + qty
                h["avg_price"] = (h["avg_price"] * h["qty"] + price * qty) / total_qty
                h["qty"] = total_qty
                break
        else:
            vp["holdings"].append({"symbol": symbol, "qty": qty, "avg_price": price})
        vp["history"].append({"action": "buy", "symbol": symbol, "qty": qty, "price": price, "time": datetime.now().isoformat()})
        db["virtual_portfolios"][str(uid)] = vp
        db.save()
        return jsonify({"success": True, "cash": vp["cash"], "holdings": vp["holdings"]})
    elif action == "sell":
        for h in vp["holdings"]:
            if h["symbol"] == symbol and h["qty"] >= qty:
                h["qty"] -= qty
                vp["cash"] += price * qty
                if h["qty"] == 0:
                    vp["holdings"] = [x for x in vp["holdings"] if x["qty"] > 0]
                vp["history"].append({"action": "sell", "symbol": symbol, "qty": qty, "price": price, "time": datetime.now().isoformat()})
                db["virtual_portfolios"][str(uid)] = vp
                db.save()
                return jsonify({"success": True, "cash": vp["cash"], "holdings": vp["holdings"]})
        return jsonify({"error": "الكمية غير متوفرة"}), 400
    return jsonify({"error": "unknown action"}), 400

# ─── ADMIN ───

@app.route("/admin")
def admin_panel():
    users = db.get("stats", {}).get("users", [])
    wl = set(db.get("whitelist", []))
    user_info = db.get("user_info", {})
    commands = db.get("stats", {}).get("commands", {})
    total_msgs = db.get("stats", {}).get("total_messages", 0)
    return render_template("admin.html", users=users, wl=wl, user_info=user_info,
                          commands=commands, total_msgs=total_msgs, NOW=datetime.now().strftime("%Y-%m-%d %H:%M"))

if __name__ == "__main__":
    port = int(os.getenv("PORT", os.getenv("DASHBOARD_PORT", "5000")))
    print(f"Dashboard: http://0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)
