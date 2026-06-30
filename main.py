import logging
import json
import time
import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes,
)
from telegram.error import TelegramError, BadRequest
from config import BOT_TOKEN, ALERT_THRESHOLD, PROXY_URL, AI_PROVIDER, ADMIN_ID
from stock_data import (
    get_stock_data, get_current_price, get_market_group,
    resolve_symbol, friendly_name, get_news, get_screener, market_status,
    SYMBOL_MAP,
)
from analysis import analyze, get_signal
from chart import generate_chart
from coingecko import get_crypto_price, get_top_crypto
from predict import predict_price
from advice import get_ai_advice, get_local_advice, get_ai_chat, _call_ai_with_messages
from database import db as sqldb, SUBSCRIPTION_PLANS

RATE_LIMIT = {}  # user_id -> last command time
SYM_CACHE = {}   # text -> resolved symbol
USER_MODE = {}  # user_id -> "safe" or "normal"
LOGS = []  # recent log entries
BOT_START_TIME = datetime.now()
ERROR_COUNT = 0
AI_MODE = {}  # user_id -> True/False
AI_CONTEXT = {}  # user_id -> {"last_symbol": "...", "last_analysis": {...}, "last_response": "...", "history": [...]}

def ai_mode_set(uid, on):
    suid = str(uid)
    if on:
        AI_MODE[suid] = True
        if suid not in AI_CONTEXT:
            AI_CONTEXT[suid] = {"last_symbol": None, "last_analysis": None, "last_response": None, "history": []}
    else:
        AI_MODE.pop(suid, None)
        AI_CONTEXT.pop(suid, None)

def ai_mode_get(uid):
    return AI_MODE.get(str(uid), False)

def ai_ctx_get(uid):
    suid = str(uid)
    if suid not in AI_CONTEXT:
        AI_CONTEXT[suid] = {"last_symbol": None, "last_analysis": None, "last_response": None, "history": []}
    return AI_CONTEXT[suid]

def get_user_mode(uid):
    return USER_MODE.get(str(uid), "normal")

def log_cmd(uid, cmd, symbol, success=True, reason=""):
    entry = {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "uid": uid,
        "cmd": cmd,
        "symbol": symbol,
        "success": success,
        "reason": reason,
    }
    LOGS.append(entry)
    if len(LOGS) > 500:
        LOGS[:] = LOGS[-500:]

def safe_filter(text):
    forbidden = ["مضمون", "اكيد", "أكيد", "فرصة لا تعوض", "بيع الان فورا", "شراء مضمون", "التوقع مؤكد", "فرصة العمر"]
    for word in forbidden:
        text = text.replace(word, f"~~{word}~~")
    return text

def data_check(hist, sym, update, ctx):
    from analysis import data_quality
    dq = data_quality(hist)
    if not dq["ok"]:
        return "\n".join(["⚠️ *البيانات غير كافية*", ""] + [f"• {i}" for i in dq["issues"]] + ["", "❌ لا يمكن إصدار تحليل موثوق."])
    return None

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.WARNING)

# Database helpers — delegate to SQLite-backed db
db = sqldb
def gw(uid): return db.gw(uid)
def sw(uid, v): db.sw(uid, v)
def ga(uid): return db.ga(uid)
def sa(uid, v): db.sa(uid, v)
def gp(uid): return db.gp(uid)
def sp(uid, v): db.sp(uid, v)
def gsa(uid): return db.gsa(uid)
def ssa(uid, v): db.ssa(uid, v)
def grp(uid): return db.grp(uid)
def srp(uid, v): db.srp(uid, v)
def grs(uid, mode): return db.get_report_sent(uid, mode)
def srs(uid, mode, value): db.set_report_sent(uid, mode, value)
def gc(uid): return db.gc(uid)
def sc(uid, v): db.sc(uid, v)
def gaa(uid): return db.gaa(uid)
def saa(uid, v): db.saa(uid, v)
def track_cmd(cmd_name): db.track_cmd(cmd_name)
def track_user(uid, user_obj=None): return db.track_user(uid, user_obj)
def track_msg(): db.track_msg()
def is_allowed(uid): return db.is_allowed(uid)
def get_sub_days_left(uid): return db.get_sub_days_left(uid)
def set_sub_days(uid, days): db.set_sub_days(uid, days)
def remove_sub(uid): db.remove_sub(uid)
def get_user_plan(uid): return db.get_user_plan(uid)
def user_has_feature(uid, feature): return db.user_has_feature(uid, feature)
def admin_settings(uid): return db.admin_settings(uid)
def get_discount_code(code): return db.get_discount_code(code)
def create_discount_code(code, pct, max_uses): return db.create_discount_code(code, pct, max_uses)
def use_discount_code(code): return db.use_discount_code(code)
def save_data(data=None): db.save()

# ─── ADMIN ALERTS ───

async def admin_alert(ctx, text, parse_mode="Markdown"):
    if ADMIN_ID:
        try:
            await ctx.bot.send_message(ADMIN_ID, f"🔰 *تنبيه إداري*\n{text}", parse_mode=parse_mode)
        except Exception as e:
            logging.error(f"admin_alert failed: {e}")

async def admin_alert_cmd(update, ctx):
    uid = update.effective_user.id
    if uid != ADMIN_ID: return
    args = ctx.args
    sets = admin_settings(uid)
    if not args:
        status = "\n".join(f"{'✅' if v else '❌'} {k}" for k, v in sets.items())
        await update.message.reply_text(f"🔰 *إعدادات التنبيهات الإدارية*\n\n{status}\n\n`/admin new_users on|off`\n`/admin errors on|off`\n`/admin startup on|off`\n`/admin daily_summary on|off`", parse_mode="Markdown")
        return
    toggle_map = {"on": True, "off": False, "1": True, "0": False, "true": True, "false": False}
    if len(args) >= 2 and args[1].lower() in toggle_map:
        key = args[0].lower()
        if key in sets:
            sets[key] = toggle_map[args[1].lower()]
            db["admin_settings"][str(uid)] = sets
            save_data(db)
            await update.message.reply_text(f"✅ {key}: {'ON' if sets[key] else 'OFF'}")
            return
    await update.message.reply_text("❌ غير معروف")

# ─── WHITELIST (Paid Access) ───

async def whitelist_cmd(update, ctx):
    uid = update.effective_user.id
    if uid != ADMIN_ID:
        return
    args = ctx.args or []
    if not args:
        status = "🟢 *مفعل*" if db.get("whitelist_on") else "🔴 *معطل*"
        users = db.get("whitelist", [])
        if users:
            lines = f"المسموح لهم ({len(users)}):\n" + "\n".join(f"`{u}`" for u in users)
        else:
            lines = "لا يوجد مستخدمين"
        await update.message.reply_text(
            f"🔒 *نظام الدخول المقيد*\n{status}\n\n{lines}\n\n"
            f"`/whitelist on|off` - تشغيل/إيقاف\n"
             f"`/whitelist add ID` - إضافة مستخدم\n"
             f"`/whitelist add ID 30` - إضافة مستخدم لمدة 30 يوم\n"
             f"`/whitelist remove ID` - حذف مستخدم",
            parse_mode="Markdown")
        return
    sub = args[0].lower()
    if sub in ("on", "1", "true"):
        db["whitelist_on"] = True; save_data(db)
        await update.message.reply_text("✅ *نظام الدخول المقيد*: مفعل\nالآن فقط المخولين يستخدمون البوت", parse_mode="Markdown")
    elif sub in ("off", "0", "false"):
        db["whitelist_on"] = False; save_data(db)
        await update.message.reply_text("✅ *نظام الدخول المقيد*: معطّل\nالكل يقدر يستخدم البوت", parse_mode="Markdown")
    elif sub == "add" and len(args) >= 2:
        try:
            target = int(args[1])
            days = int(args[2]) if len(args) >= 3 else 0
            wl = db.setdefault("whitelist", [])
            st = str(target)
            if st not in wl:
                wl.append(st)
            if days > 0:
                set_sub_days(target, days)
            save_data(db)
            days_str = f" لمدة `{days}` يوم" if days > 0 else ""
            await update.message.reply_text(f"✅ تمت إضافة `{target}`{days_str} إلى قائمة المخولين", parse_mode="Markdown")
        except ValueError:
            await update.message.reply_text("❌ المعرف غير صالح")
    elif sub == "remove" and len(args) >= 2:
        try:
            target = int(args[1])
            wl = db.setdefault("whitelist", [])
            st = str(target)
            if st in wl:
                wl.remove(st)
            remove_sub(target)
            save_data(db)
            await update.message.reply_text(f"✅ تمت إزالة `{target}` من قائمة المخولين", parse_mode="Markdown")
        except ValueError:
            await update.message.reply_text("❌ المعرف غير صالح")
    else:
        await update.message.reply_text("❌ أمر غير معروف. استخدم: `/whitelist on|off|add ID|remove ID`")

async def users_cmd(update, ctx):
    uid = update.effective_user.id
    if uid != ADMIN_ID: return
    wl = set(db.get("whitelist", []))
    all_users = db["stats"].get("users", [])
    user_info = db.get("user_info", {})
    subs = db.get("subscriptions", {})
    
    pending = sorted([u for u in all_users if u not in wl], key=lambda x: user_info.get(x, {}).get("last_seen", ""), reverse=True)
    active = sorted(wl, key=lambda x: subs.get(x, ""), reverse=True)
    
    lines = [f"📋 *المستخدمون*"]
    lines.append(f"👥 الإجمالي: `{len(all_users)}`")
    lines.append(f"✅ مفعل: `{len(wl & set(all_users))}` | ⏳ ينتظر: `{len(pending)}`")
    
    if pending:
        lines.extend(["", "⏳ *بانتظار التفعيل:*"])
        for suid in pending[:30]:
            info = user_info.get(suid, {})
            name = info.get("name", "") or ""
            uname = f" @{info['username']}" if info.get("username") else ""
            lines.append(f"• `{suid}`{uname} {name}")
        if len(pending) > 30:
            lines.append(f"  ...و {len(pending)-30} آخرين")
    
    if active:
        lines.extend(["", "✅ *المفعلين:*"])
        for suid in active:
            days = get_sub_days_left(int(suid))
            info = user_info.get(suid, {})
            name = info.get("name", "") or ""
            uname = f" @{info['username']}" if info.get("username") else ""
            if days == -1:
                expiry_str = "غير محدد"
            elif days > 0:
                expiry_str = f"{days} يوم"
            else:
                expiry_str = "منتهي ❌"
            lines.append(f"• `{suid}`{uname} {name} — {expiry_str}")
    
    lines.extend(["",
                  "🔹 `/whitelist add ID [days]` — تفعيل مستخدم",
                  "🔹 `/whitelist remove ID` — حذف"])
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

# ─── SUBSCRIPTION PLANS ───

async def plan_cmd(update, ctx):
    """Manage subscription plans (admin)"""
    uid = update.effective_user.id
    if uid != ADMIN_ID: return
    args = ctx.args or []
    if not args:
        lines = ["📋 *باقات الاشتراك*\n"]
        for pid, p in SUBSCRIPTION_PLANS.items():
            feat_count = len(p["features"])
            lines.append(f"• *{p['name']}* — {p['price_sar']} ريال / {p['days']} يوم")
            lines.append(f"  الميزات: {feat_count}")
        lines.append("")
        lines.append("`/plan list` - عرض الباقات")
        lines.append("`/plan set USER PLAN` - تعيين خطة لمستخدم")
        lines.append("`/plan set USER PLAN 60` - تعيين خطة 60 يوم")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        return
    if args[0] == "list":
        lines = ["📋 *باقات الاشتراك*\n"]
        for pid, p in SUBSCRIPTION_PLANS.items():
            lines.append(f"🔹 *{p['name']}* (`{pid}`)")
            lines.append(f"   💰 {p['price_sar']} ريال | {p['days']} يوم")
            feats = p["features"]
            if "all" in feats:
                lines.append(f"   ✅ جميع الميزات")
            else:
                lines.append(f"   ✅ {len(feats)} ميزة: {', '.join(feats)}")
            lines.append("")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    elif args[0] == "set" and len(args) >= 3:
        try:
            target = int(args[1])
            plan_id = args[2].lower()
            if plan_id not in SUBSCRIPTION_PLANS:
                available = ", ".join(SUBSCRIPTION_PLANS.keys())
                await update.message.reply_text(f"❌ خطة غير معروفة. المتاح: {available}")
                return
            plan = SUBSCRIPTION_PLANS[plan_id]
            days = int(args[3]) if len(args) >= 4 else plan["days"]
            wl = db.setdefault("whitelist", [])
            st = str(target)
            if st not in wl:
                wl.append(st)
            set_sub_days(target, days)
            up = db.setdefault("user_plans", {})
            end = datetime.now() + timedelta(days=days)
            up[st] = {"plan": plan_id, "start": datetime.now().isoformat(), "end": end.isoformat()}
            save_data(db)
            await update.message.reply_text(f"✅ تم تعيين خطة *{plan['name']}* للمستخدم `{target}` لمدة {days} يوم", parse_mode="Markdown")
        except ValueError:
            await update.message.reply_text("❌ المعرف غير صالح")

async def discount_cmd(update, ctx):
    """Manage discount codes (admin)"""
    uid = update.effective_user.id
    if uid != ADMIN_ID: return
    args = ctx.args or []
    codes = db.setdefault("discount_codes", {})
    if not args:
        lines = ["🎟 *أكواد الخصم*\n"]
        for code, info in codes.items():
            used = info.get("used", 0)
            max_uses = info.get("uses", 0)
            lines.append(f"🔹 `{code}`: خصم {info.get('discount', 0)}% - مستخدم {used}/{max_uses}")
        if not codes:
            lines.append("لا توجد أكواد")
        lines.append("")
        lines.append("`/discount add CODE 20 10` - إضافة كود خصم 20%، 10 استخدمات")
        lines.append("`/discount remove CODE` - حذف كود")
        lines.append("`/discount apply USER CODE` - تطبيق كود على مستخدم")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        return
    if args[0] == "add" and len(args) >= 3:
        code = args[1].upper()
        discount = int(args[2])
        max_uses = int(args[3]) if len(args) >= 4 else 1
        codes[code] = {"discount": discount, "type": "percent", "uses": max_uses, "used": 0}
        save_data(db)
        await update.message.reply_text(f"✅ تمت إضافة كود الخصم `{code}`: {discount}%، {max_uses} استخدام", parse_mode="Markdown")
    elif args[0] == "remove" and len(args) >= 2:
        code = args[1].upper()
        if code in codes:
            del codes[code]
            save_data(db)
            await update.message.reply_text(f"✅ تم حذف الكود `{code}`", parse_mode="Markdown")
        else:
            await update.message.reply_text("❌ الكود غير موجود")
    elif args[0] == "apply" and len(args) >= 3:
        try:
            target = int(args[1])
            code = args[2].upper()
            if code not in codes:
                await update.message.reply_text("❌ الكود غير موجود")
                return
            cinfo = codes[code]
            if cinfo.get("used", 0) >= cinfo.get("uses", 1):
                await update.message.reply_text("❌ الكود انتهت استخداماته")
                return
            discount_pct = cinfo["discount"]
            # Apply 30-day basic subscription with discount
            days = 30
            full_price = SUBSCRIPTION_PLANS["basic"]["price_sar"]
            discounted = full_price * (100 - discount_pct) // 100
            wl = db.setdefault("whitelist", [])
            st = str(target)
            if st not in wl:
                wl.append(st)
            set_sub_days(target, days)
            up = db.setdefault("user_plans", {})
            end = datetime.now() + timedelta(days=days)
            up[st] = {"plan": "basic", "start": datetime.now().isoformat(), "end": end.isoformat(), "code": code, "discount": discount_pct}
            cinfo["used"] = cinfo.get("used", 0) + 1
            save_data(db)
            await update.message.reply_text(f"✅ تم تطبيق كود `{code}` على المستخدم `{target}`\nالخطة: أساسي 30 يوم بسعر {discounted} ريال (توفير {discount_pct}%)", parse_mode="Markdown")
        except ValueError:
            await update.message.reply_text("❌ المعرف غير صالح")
    else:
        await update.message.reply_text("❌ أمر غير معروف")

async def sub_cmd(update, ctx):
    """User checks their subscription status"""
    uid = update.effective_user.id
    up = get_user_plan(uid)
    if up:
        await update.message.reply_text(
            f"📋 *اشتراكي*\n"
            f"الخطة: *{up['name']}*\n"
            f"المتبقي: `{up['end']}` يوم\n"
            f"الميزات: {len(up['features'])}", parse_mode="Markdown")
    else:
        days = get_sub_days_left(uid)
        if days > 0:
            await update.message.reply_text(f"📋 *اشتراكي*\nالمتبقي: `{days}` يوم", parse_mode="Markdown")
        else:
            await update.message.reply_text("❌ لا يوجد اشتراك نشط.\nللاشتراك تواصل مع @hidanx11")

def tracked(func, name):
    async def wrapper(update, ctx):
        track_cmd(name)
        u = update.effective_user
        if u:
            if not is_allowed(u.id):
                await update.message.reply_text("🔒 هذا البوت للأعضاء المشتركين فقط.\nللاشتراك تواصل مع @hidanx11")
                return
            if track_user(u.id, u):
                if ADMIN_ID and admin_settings(ADMIN_ID).get("new_users", True):
                    try:
                        name_parts = []
                        if u.first_name: name_parts.append(u.first_name)
                        if u.last_name: name_parts.append(u.last_name)
                        full = " ".join(name_parts) or "—"
                        uname = f"@{u.username}" if u.username else "—"
                        first_cmd = name
                        await ctx.bot.send_message(ADMIN_ID,
                            f"🆕 *مستخدم جديد*"
                            f"\n👤 الاسم: `{full}`"
                            f"\n📱 يوزر: {uname}"
                            f"\n🆔 المعرف: `{u.id}`"
                            f"\n⌨️ أول أمر: `/{first_cmd}`"
                            f"\n⏱ الوقت: `{datetime.now().strftime('%H:%M:%S')}`"
                            f"\n📊 إجمالي المستخدمين: `{len(db['stats']['users'])}`",
                            parse_mode="Markdown")
                    except Exception:
                        pass
        return await func(update, ctx)
    return wrapper

MARKET_BTNS = {
    "us": "🇺🇸 أمريكا", "crypto": "₿ عملات", "commodities": "🛢 سلع",
    "forex": "💱 فوركس", "saudi": "🇸🇦 السعودية", "tech": "💻 تقنية",
    "banks": "🏦 بنوك", "energy": "⚡ طاقة",
    "europe": "🇪🇺 أوروبا", "asia": "🌏 آسيا",
}

async def ror(update, ctx, text, keyboard=None):
    text = truncate_msg(text)
    if update.callback_query:
        q = update.callback_query
        try:
            return await q.edit_message_text(text, parse_mode="Markdown", reply_markup=keyboard)
        except BadRequest as e:
            if "message is not modified" in str(e).lower():
                return q.message
            if q.message:
                return await q.message.reply_text(text, parse_mode="Markdown", reply_markup=keyboard)
            return await ctx.bot.send_message(update.effective_chat.id, text, parse_mode="Markdown", reply_markup=keyboard)
        except Exception:
            if q.message:
                return await q.message.reply_text(text, parse_mode="Markdown", reply_markup=keyboard)
            return await ctx.bot.send_message(update.effective_chat.id, text, parse_mode="Markdown", reply_markup=keyboard)
    elif update.message:
        return await update.message.reply_text(text, parse_mode="Markdown", reply_markup=keyboard)
    return None

def check_rate_limit(uid):
    now = time.time()
    last = RATE_LIMIT.get(uid, 0)
    if now - last < 1.5:
        return False
    RATE_LIMIT[uid] = now
    return True

def truncate_msg(text, maxlen=4000):
    if len(text) <= maxlen:
        return text
    return text[:maxlen-3] + "..."

def cached_resolve(text):
    key = text.strip().lower()
    if key not in SYM_CACHE:
        SYM_CACHE[key] = resolve_symbol(text)
    return SYM_CACHE[key]

def price_line(name, sym, price, chg, pct):
    return f"{'📈' if chg>=0 else '📉'} *{name}*: `{pf(sym, price)}` ({chg:+.2f} | {pct:+.2f}%)"

def ts(): return f"🕐 {datetime.now().strftime('%H:%M')}"

def ccy(sym):
    s = str(sym).upper()
    if s.endswith(".SR"): return "﷼"
    r = resolve_symbol(s)
    if r != s and r.upper().endswith(".SR"): return "﷼"
    return "$"

def pf(sym, price):
    c = ccy(sym) if sym else "$"
    return f"{c}{price}"

MAIN_MENU_TEXT = (
    "🤖 *مرحباً بك في بوت الأسهم الذكي!*\n\n"
    "*ابدأ من الأزرار أو اكتب الأمر مباشرة:*\n"
    "مثال: `/price spy` أو `/signal 1120.sr`\n\n"
    "*الرموز السريعة:* spy, qqq, btc, eth, gold, oil, aapl, msft, nvda, tsla\n"
    "سعودي: 2222.sr, 7010.sr, الراجحي, سابك, معادن, stc\n"
    "مؤشرات: spx, sp500, nasdaq, dow, vix\n\n"
    "*الأوامر المهمة:*\n"
    "`/price` سعر السهم\n"
    "`/signal` توصية\n"
    "`/analyze` تحليل فني\n"
    "`/chart` شارت مصور\n"
    "`/screener` مسح السوق\n"
    "`/watchlist` قائمتي\n"
    "`/portfolio` محفظتي\n"
    "`/alert` تنبيه سعري\n"
    "`/smartalert` تنبيه ذكي\n"
    "`/report` تقارير دورية\n"
    "`/web` لوحة التحكم"
)

def main_menu_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 السوق", callback_data="market:us"),
         InlineKeyboardButton("🔍 تحليل", callback_data="quick_analyze")],
        [InlineKeyboardButton("💰 سعر", callback_data="quick_price"),
         InlineKeyboardButton("🔥 توصية", callback_data="quick_signal")],
        [InlineKeyboardButton("📋 قائمتي", callback_data="my_watchlist"),
         InlineKeyboardButton("💼 محفظتي", callback_data="my_portfolio")],
        [InlineKeyboardButton("🏆 المسح", callback_data="screener"),
         InlineKeyboardButton("🔔 تنبيه ذكي", callback_data="quick_alerts")],
        [InlineKeyboardButton("📊 التقارير", callback_data="quick_reports"),
         InlineKeyboardButton("🌐 الموقع", callback_data="web_home")],
    ])

async def send_main_menu(update, ctx):
    await ror(update, ctx, MAIN_MENU_TEXT, main_menu_kb())

def back_to_menu_kb(*rows):
    return InlineKeyboardMarkup([*rows, [InlineKeyboardButton("↩️ القائمة الرئيسية", callback_data="main_menu")]])

def report_settings_kb():
    return back_to_menu_kb(
        [InlineKeyboardButton("📅 يومي", callback_data="report_daily"),
         InlineKeyboardButton("📆 أسبوعي", callback_data="report_weekly")],
        [InlineKeyboardButton("⏹ إيقاف", callback_data="report_off")],
    )

def report_settings_text(uid):
    cur = grp(uid)
    status = {"daily": "يومي", "weekly": "أسبوعي", "off": "معطل"}.get(cur, "معطل")
    return f"📊 *التقارير الدورية*\nالحالة: `{status}`\n\nاختر التكرار:"

def smart_alerts_kb():
    return back_to_menu_kb(
        [InlineKeyboardButton("SPY دعم", callback_data="smartalert:SPY:support"),
         InlineKeyboardButton("SPY مقاومة", callback_data="smartalert:SPY:resistance")],
        [InlineKeyboardButton("AAPL RSI منخفض", callback_data="smartalert:AAPL:rsi_oversold"),
         InlineKeyboardButton("BTC حجم مفاجئ", callback_data="smartalert:BTC-USD:volume_spike")],
        [InlineKeyboardButton("📋 تنبيهاتي", callback_data="show_alerts")],
    )

def smart_alerts_text():
    return (
        "🔔 *التنبيهات الذكية*\n\n"
        "اختر تنبيه جاهز، أو اكتب يدويًا:\n"
        "`/smartalert spy support`\n\n"
        "الأنواع المختصرة: دعم، مقاومة، RSI، حجم مفاجئ."
    )

def dashboard_url_for(uid):
    url = db.get("config", {}).get("dashboard_url", "")
    if not url:
        from config import DASHBOARD_URL
        url = DASHBOARD_URL
    if not url:
        url = "https://stock-bot-production-7ac8.up.railway.app"
    return f"{url.rstrip('/')}/user/{uid}"

def alerts_summary_text(uid):
    lines = []
    idx = 1
    reg = ga(uid)
    adv = gaa(uid)
    smart = gsa(uid)
    if reg:
        lines.append("🔔 *تنبيهات حركة سعرية*\n")
        for a in reg:
            lines.append(f"{idx}. {a['symbol']} @ {a['threshold']}%")
            idx += 1
        lines.append("")
    if adv:
        lines.append("🎯 *تنبيهات متقدمة*\n")
        for a in adv:
            if a["type"] == "target":
                lines.append(f"{idx}. 🎯 {a['symbol']} → {ccy(a['symbol'])}{a['value']:,.2f}")
            elif a["type"] == "day_change":
                lines.append(f"{idx}. 📊 {a['symbol']} تغيير {a['value']}%")
            idx += 1
        lines.append("")
    if smart:
        lines.append("🧠 *تنبيهات ذكية*\n")
        for a in smart:
            status = "✅ تم" if a.get("triggered") else "⏳ نشط"
            lines.append(f"{idx}. {status} {a['symbol']} - {SMART_TYPES.get(a['type'], a['type'])}")
            idx += 1
        lines.append("")
    if not lines:
        return "ℹ️ لا توجد تنبيهات بعد.\nأضف تنبيهًا من الأزرار أو اكتب: `/alert spy 2`"
    lines.append("🗑 للحذف: `/alert_remove رقم`")
    return "\n".join(lines)

async def start(update, ctx):
    uid = update.effective_user.id
    track_user(uid, update.effective_user)
    await send_main_menu(update, ctx)

async def help_cmd(update, ctx):
    await start(update, ctx)

# ─── PRICE ───

async def price_cmd(update, ctx):
    sym = " ".join(ctx.args)
    if not sym:
        await update.message.reply_text("💰 *اختر رمز السهم:*\nأو اكتب `/price spy`", parse_mode="Markdown", reply_markup=suggest_kb("price"))
        return
    await send_price(update, ctx, sym)

def suggest_kb(cmd):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🇺🇸 SPY", callback_data=f"{cmd}:SPY"),
         InlineKeyboardButton("₿ BTC", callback_data=f"{cmd}:BTC-USD")],
        [InlineKeyboardButton("🪨 GOLD", callback_data=f"{cmd}:GC=F"),
         InlineKeyboardButton("💻 AAPL", callback_data=f"{cmd}:AAPL")],
        [InlineKeyboardButton("🇸🇦 الراجحي", callback_data=f"{cmd}:1120.SR"),
         InlineKeyboardButton("🇸🇦 STC", callback_data=f"{cmd}:7010.SR")],
    ])

async def send_price(update, ctx, sym):
    await ror(update, ctx, f"⏳ جلب سعر `{sym}`...")
    price, chg, pct = get_current_price(sym)
    if price is None:
        await ror(update, ctx, f"❌ الرمز `{sym}` غير صحيح\nأمثلة: spy, btc, aapl, gold, 2222.sr, معادن")
        return
    name = friendly_name(resolve_symbol(sym))
    text = f"💰 *{name}* `{sym.upper()}`\nالسعر: `{pf(sym, price)}`\n{'📈' if chg>=0 else '📉'} التغير: {chg:+.2f} ({pct:+.2f}%)\n\n{ts()}"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 توصية", callback_data=f"signal:{sym}"),
         InlineKeyboardButton("📈 شارت", callback_data=f"chart:{sym}")],
        [InlineKeyboardButton("🔄 تحديث", callback_data=f"price:{sym}")],
    ])
    await ror(update, ctx, text, kb)

# ─── SIGNAL ───

async def signal_cmd(update, ctx):
    sym = " ".join(ctx.args)
    if not sym:
        await update.message.reply_text("🔥 *اختر رمز للتوصية:*\nأو اكتب `/signal spy`", parse_mode="Markdown", reply_markup=suggest_kb("signal"))
        return
    await send_signal(update, ctx, sym)

async def send_signal(update, ctx, sym):
    await ror(update, ctx, f"⏳ تحليل `{sym}`...")
    hist, info = get_stock_data(sym, period="1y")
    if hist is None:
        await ror(update, ctx, "❌ الرمز غير صحيح")
        return
    dq_msg = data_check(hist, sym, update, ctx)
    if dq_msg:
        await ror(update, ctx, dq_msg)
        return
    name = info.get("shortName") or info.get("longName") or sym.upper()
    price = round(float(hist["Close"].iloc[-1]), 2)
    uid = update.effective_user.id
    mode = get_user_mode(uid)
    a = analyze(hist)
    s = get_signal(a, safe_mode=(mode == "safe"))
    log_cmd(uid, "signal", sym, True)
    total_score = a['score']
    action = s['action']
    verdict = s['verdict']
    conviction = s['conviction']
    support = a.get('support')
    resistance = a.get('resistance')
    lines = [f"🔥 *إشارة تحليلية {name}*", ""]
    if conviction == "ضعيفة" and action != "HOLD":
        lines += ["⚪ *القرار: انتظار*", "\"الإشارة غير مكتملة — يفضل الانتظار\"", ""]
    else:
        action_emoji = {"BUY": "🟢", "SELL": "🔴", "HOLD": "⚪"}
        lines.append(f"⚡ *القرار: {action_emoji.get(action, '⚪')} {verdict}*")
    lines += [
        f"💰 السعر الحالي: `{pf(sym, price)}`",
        "",
        f"📊 *مستويات المراقبة:*",
    ]
    if support:
        lines.append(f"🔴 منطقة مراقبة دنيا: `{pf(sym, support)}`")
    if resistance:
        lines.append(f"🟢 منطقة مراقبة عليا: `{pf(sym, resistance)}`")
    if support:
        lines.append(f"🚫 نقطة إلغاء الفكرة: كسر `{pf(sym, support)}`")
    if resistance:
        lines.append(f"🎯 مستوى مستهدف أول: `{pf(sym, resistance)}`")
    if resistance and support:
        t2 = round(price + (resistance - support) * 1.2, 2)
        lines.append(f"🎯 مستوى مستهدف ثاني: `{pf(sym, t2)}`")
    if support and resistance:
        rr = abs((resistance - price) / (price - support)) if (price - support) != 0 else 0
        lines.append(f"⚖️ نسبة المخاطرة إلى العائد: `{rr:.2f}`")
    adx_v = a.get('adx', 0)
    if adx_v and adx_v > 30:
        tf = "سوينغ (أيام)"
    elif adx_v and adx_v > 20:
        tf = "مضاربة (ساعات)"
    else:
        tf = "غير محدد — تذبذب"
    lines.append(f"⏱ مدة الفكرة: `{tf}`")
    lines += [
        "",
        f"🎚️ الثقة: `{conviction}` | النقاط: `{total_score}/100`",
    ]
    reasons = []
    rsi_v = a.get('rsi', 50)
    if rsi_v > 60: reasons.append(f"RSI إيجابي ({rsi_v})")
    elif rsi_v < 40: reasons.append(f"RSI سلبي ({rsi_v})")
    if a.get('macd_bullish'): reasons.append("MACD إيجابي")
    else: reasons.append("MACD سلبي")
    if a.get('volume_spike'): reasons.append(f"حجم مرتفع ({a.get('volume_ratio')}x)")
    if a.get('adx') and a['adx'] > 25: reasons.append(f"ADX {a['adx']} - اتجاه موجود")
    if reasons:
        lines.append(f"🧠 سبب الإشارة: {'، '.join(reasons)}")
    if support:
        lines.append(f"🔄 تلغى الإشارة إذا كسر `{pf(sym, support)}`")
    if conviction == "ضعيفة":
        lines += ["", "⚠️ *ملاحظة:* الثقة ضعيفة — يُفضل الانتظار لتأكيد إضافي."]
    lines += ["", f"━━━━━━━", f"*الخلاصة:*", f"القرار: {verdict}", f"الثقة: `{conviction}` (`{total_score}/100`)"]
    if support:
        lines.append(f"أفضل تصرف الآن: مراقبة — الدعم `{pf(sym, support)}` / المقاومة `{pf(sym, resistance)}`" if resistance else f"أفضل تصرف الآن: مراقبة — الدعم `{pf(sym, support)}`")
    lines.append(f"نقطة التأكيد: إغلاق فوق SMA50 مع تحسن MACD")
    if support:
        lines.append(f"نقطة الخطر: كسر `{pf(sym, support)}`")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📈 شارت", callback_data=f"chart:{sym}"),
         InlineKeyboardButton("📊 تحليل", callback_data=f"analyze:{sym}")],
        [InlineKeyboardButton("🔄 تحديث", callback_data=f"signal:{sym}")],
    ])
    await ror(update, ctx, "\n".join(lines), kb)

# ─── CHART ───

CHART_PERIODS = {"1m":"1mo","3m":"3mo","6m":"6mo","1y":"1y","2y":"2y","5y":"5y","ytd":"ytd","max":"max"}

async def chart_cmd(update, ctx):
    args = ctx.args or []
    sym = "SPY"
    period = "6mo"
    if args:
        arg = args[-1].lower()
        if arg in CHART_PERIODS:
            period = CHART_PERIODS[arg]
            sym = " ".join(args[:-1]) or "SPY"
        else:
            sym = " ".join(args)
    await send_chart(update, ctx, sym, period)

async def send_chart(update, ctx, sym, period="6mo"):
    await ror(update, ctx, f"⏳ جاري رسم شارت `{sym}`...")
    hist, info = get_stock_data(sym, period=period)
    if hist is None:
        await ror(update, ctx, "❌ الرمز غير صحيح")
        return
    a = analyze(hist)
    path = generate_chart(sym.upper(), hist, a)
    if not path:
        await ror(update, ctx, "❌ فشل إنشاء الشارت")
        return
    period_desc = {"1mo":"شهر","3mo":"3 شهور","6mo":"6 شهور","1y":"سنة","2y":"سنتين","5y":"5 سنوات","ytd":"منذ بداية العام","max":"كل البيانات"}.get(period, period)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔥 توصية", callback_data=f"signal:{sym}"),
         InlineKeyboardButton("🔄 تحديث", callback_data=f"chart:{sym}")],
    ])
    caption = f"📈 *{sym.upper()}* - {period_desc}"
    if update.callback_query:
        try:
            with open(path, "rb") as f:
                msg = await update.callback_query.message.reply_photo(photo=f, caption=caption, parse_mode="Markdown", reply_markup=kb)
        except Exception as e:
            logging.error(f"send_chart photo error: {e}")
            await update.callback_query.edit_message_text("❌ خطأ في إرسال الشارت")
            return
        try:
            await update.callback_query.message.delete()
        except Exception:
            pass
    elif update.message:
        with open(path, "rb") as f:
            await update.message.reply_photo(photo=f, caption=caption, parse_mode="Markdown", reply_markup=kb)

# ─── ANALYZE ───

async def analyze_cmd(update, ctx):
    sym = " ".join(ctx.args)
    if not sym:
        await update.message.reply_text("📊 *اختر رمز للتحليل:*\nأو اكتب `/analyze spy`", parse_mode="Markdown", reply_markup=suggest_kb("analyze"))
        return
    await send_analysis(update, ctx, sym)

async def send_analysis(update, ctx, sym):
    await ror(update, ctx, f"⏳ تحليل `{sym}`...")
    hist, info = get_stock_data(sym, period="1y")
    if hist is None:
        await ror(update, ctx, "❌ الرمز غير صحيح")
        return
    dq_msg = data_check(hist, sym, update, ctx)
    if dq_msg:
        await ror(update, ctx, dq_msg)
        return
    name = info.get("shortName") or info.get("longName") or sym.upper()
    price = round(float(hist["Close"].iloc[-1]), 2)
    uid = update.effective_user.id
    mode = get_user_mode(uid)
    a = analyze(hist)
    s = get_signal(a, safe_mode=(mode == "safe"))
    log_cmd(uid, "analyze", sym, True)
    
    rs = pf(sym, price)
    lines = [
        f"📊 *تحليل {name}*",
        f"💰 السعر الحالي: `{rs}`",
        f"📅 أداء اليوم: {a.get('change_1d',0):+.2f}% | 5 أيام: {a.get('change_5d',0):+.2f}% | شهر: {a.get('change_1m',0):+.2f}%",
        "",
        "📈 *المؤشرات الفنية:*",
        f"RSI: `{a.get('rsi','—')}`",
        f"SMA20: `{a.get('sma20','—')}` | SMA50: `{a.get('sma50','—')}`",
        f"SMA200: `{a.get('sma200','—')}`",
        f"MACD: `{a.get('macd','—')}` | Signal: `{a.get('macd_signal','—')}`",
        f"Bollinger: `{a.get('bb_pos','—')}`",
        f"ADX: `{a.get('adx','—')}`",
        f"حجم: `{a.get('volume_ratio','—')}x` | التقلب: `{a.get('volatility','—')}%`",
        f"أعلى 52 أسبوع: `{pf(sym, a['high_52w']) if a.get('high_52w') else '—'}` | أدنى: `{pf(sym, a['low_52w']) if a.get('low_52w') else '—'}`",
        "",
        "🎯 *الاتجاه:*",
    ]
    for period, direction in a.get("trends", {}).items():
        e = "📈" if direction == "صاعد" else "📉" if direction == "هابط" else "➖"
        lines.append(f"{e} {period}: {direction}")
    
    lines += ["", "🔎 *قراءة الإشارات:*"]
    if a.get("rsi"):
        lines.append(f"• RSI: {s['rsi_explanation']}")
    lines.append(f"• MACD: {s['macd_explanation']}")
    lines.append(f"• المتوسطات: {s['ma_explanation']}")
    lines.append(f"• {s['bb_explanation']}")
    lines.append(f"• {s['adx_explanation']}")
    lines.append(f"• {s['volume_explanation']}")
    
    total_score = a['score']
    score_display = total_score
    if mode == "safe":
        score_display = min(total_score, 70)
    
    verdict_emoji = "🟢" if total_score >= 56 else "🔴" if total_score <= 35 else "⚪"
    conviction_str = s['conviction']
    lines += [
        "",
        f"⚖️ *القرار النهائي:* {verdict_emoji} {s['verdict']}",
        f"🎚️ مستوى الثقة: `{conviction_str}` | النقاط: `{score_display}/100`",
        "",
        f"🧠 *سبب القرار:*",
    ]
    
    # Generate 3-5 reasons
    reasons = []
    rsi_val = a.get('rsi', 50)
    if rsi_val > 70: reasons.append(f"RSI في ذروة شراء ({rsi_val})")
    elif rsi_val < 30: reasons.append(f"RSI في ذروة بيع ({rsi_val})")
    elif rsi_val > 60: reasons.append(f"زخم RSI إيجابي ({rsi_val})")
    elif rsi_val < 40: reasons.append(f"زخم RSI سلبي ({rsi_val})")
    
    if a.get('macd_bullish'): reasons.append("MACD إيجابي يدعم الصعود")
    else: reasons.append("MACD سلبي يضعف الزخم")
    
    adx_v = a.get('adx', 0)
    if adx_v and adx_v > 25: reasons.append(f"ADX يؤكد وجود اتجاه ({adx_v})")
    else: reasons.append("ADX ضعيف — سوق تذبذب")
    
    if a.get('volume_spike'): reasons.append(f"حجم تداول مرتفع ({a.get('volume_ratio')}x)")
    else: reasons.append("حجم التداول طبيعي")
    
    for r in reasons[:5]:
        lines.append(f"• {r}")
    
    lines += [
        "",
        "⚠️ *ملاحظات المخاطر:*",
    ]
    if a.get('support'):
        lines.append(f"• نقطة إلغاء التحليل: كسر الدعم `{pf(sym, a['support'])}`")
    if a.get('resistance'):
        lines.append(f"• نقطة الخطر: اختراق المقاومة `{pf(sym, a['resistance'])}`")
    if a.get('volatility', 0) > 3:
        lines.append("• السوق عالي التذبذب — يُفضل تقليل حجم الصفقة")
    
    lines += [
        "",
        f"━━━━━━━",
        f"*الخلاصة:*",
        f"القرار: {verdict_emoji} {s['verdict']}",
        f"الثقة: `{conviction_str}` (`{score_display}/100`)",
    ]
    if a.get('support') and a.get('resistance'):
        lines.append(f"أفضل تصرف الآن: انتظار تأكيد — الدعم `{pf(sym, a['support'])}` / المقاومة `{pf(sym, a['resistance'])}`")
    lines.append(f"نقطة التأكيد: إغلاق فوق SMA50 مع تحسن MACD")
    if a.get('support'):
        lines.append(f"نقطة الخطر: كسر `{pf(sym, a['support'])}`")
    
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔥 توصية", callback_data=f"signal:{sym}"),
         InlineKeyboardButton("📈 شارت", callback_data=f"chart:{sym}")],
        [InlineKeyboardButton("🔄 تحديث", callback_data=f"analyze:{sym}")],
    ])
    await ror(update, ctx, "\n".join(lines), kb)

# ─── ADVICE ───

async def advice_cmd(update, ctx):
    sym = " ".join(ctx.args)
    if not sym:
        await update.message.reply_text("🤖 *اختر رمز لنصيحة AI:*\nأو اكتب `/advice spy`", parse_mode="Markdown", reply_markup=suggest_kb("advice"))
        return
    hist, info = get_stock_data(sym, period="1y")
    if hist is None:
        await update.message.reply_text("❌ الرمز غير صحيح")
        return
    price = round(float(hist["Close"].iloc[-1]), 2)
    a = analyze(hist); s = get_signal(a)
    name = info.get("shortName") or info.get("longName") or sym.upper()

    cur = ccy(sym)
    ai = get_ai_advice(sym, price, a, s)
    if ai:
        text = safe_filter(f"🤖 *نصيحة ذكية - {name}*\n\n{ai}")
    else:
        text = safe_filter(f"🧠 *نصيحة استثمارية - {name}*\n\n{get_local_advice(sym, price, a, s, cur)}")

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔥 توصية", callback_data=f"signal:{sym}"),
         InlineKeyboardButton("📈 شارت", callback_data=f"chart:{sym}")],
    ])
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=kb)


# ─── AI CHAT ───

async def ai_cmd(update, ctx):
    uid = update.effective_user.id
    text = " ".join(ctx.args) if ctx.args else ""

    if text and text.lower() in ("off", "exit", "stop", "خروج", "إلغاء", "الغاء"):
        ai_mode_set(uid, False)
        await update.message.reply_text("✅ تم إيقاف وضع المحادثة الذكية. تقدر الآن تستخدم الأوامر العادية.", parse_mode="Markdown")
        return

    ai_mode_set(uid, True)
    ctx_data = ai_ctx_get(uid)

    if text:
        # Direct question with /ai
        await process_ai_chat(update, ctx, text, uid)
    else:
        await update.message.reply_text(
            "🤖 *تم تفعيل وضع المحادثة الذكية!*\n\n"
            "تقدر تسألني بشكل طبيعي، مثل:\n"
            "• وش رأيك في الراجحي؟\n"
            "• هل btc خطر؟\n"
            "• قارن spy و qqq\n"
            "• اشرح لي RSI\n"
            "• وش أفضل رموز للمراقبة اليوم؟\n\n"
            "للخروج اكتب:\n"
            "`/cancel` أو `/ai off`",
            parse_mode="Markdown"
        )

async def cancel_cmd(update, ctx):
    uid = update.effective_user.id
    if ai_mode_get(uid):
        ai_mode_set(uid, False)
        await update.message.reply_text("✅ تم إيقاف وضع المحادثة الذكية. تقدر الآن تستخدم الأوامر العادية.", parse_mode="Markdown")
    else:
        await update.message.reply_text("🤖 وضع المحادثة الذكية غير مفعل حالياً.", parse_mode="Markdown")

# Arabic NLP helpers
def norm_ar(t):
    """Normalize Arabic text for matching"""
    reps = {"أ":"ا","إ":"ا","آ":"ا","ة":"ه","ى":"ي","ـ":"","ؤ":"و","ئ":"ي","ك":"ك","گ":"ك","ڤ":"ف"}
    t = t.lower().strip()
    for a, b in reps.items(): t = t.replace(a, b)
    # Remove diacritics
    for c in "ًٌٍَُِّْ": t = t.replace(c, "")
    return t

def find_sym(text):
    """Extract symbol from text, return (symbol, None) or None"""
    for p in "،؟!.,?!/\\\"'؛": text = text.replace(p, " ")
    words = text.strip().split()
    for w in words:
        wc = w.strip()
        if wc.lower() in SYMBOL_MAP:
            return wc
        # Try with .SR suffix
        if not wc.upper().endswith(".SR") and wc + ".sr" in SYMBOL_MAP:
            return wc + ".sr"
    # Try normalized matching for Arabic names
    for w in words:
        nw = norm_ar(w)
        for k in SYMBOL_MAP:
            if norm_ar(k) == nw:
                return k
    return None

def find_syms(text):
    """Extract all symbols from text"""
    found = []
    for p in "،؟!.,?!/\\\"'؛": text = text.replace(p, " ")
    words = text.strip().split()
    for w in words:
        wc = w.strip().lower()
        if wc in SYMBOL_MAP and wc not in found:
            found.append(wc)
    # Normalized matching
    for w in words:
        nw = norm_ar(w)
        for k in SYMBOL_MAP:
            if norm_ar(k) == nw and k not in found:
                found.append(k)
                break
    return found

def detect_intent(text, context):
    """
    Classify user intent based on text + conversation context.
    Returns: (intent_type, symbol(s), confidence)
    """
    n = norm_ar(text)
    words = n.split()
    last_sym = context.get("last_symbol") if context else None
    syms = find_syms(text)
    single_sym = syms[0] if syms else None
    
    # 1. Empty / very short
    if not n or len(n) < 2:
        if last_sym: return ("follow_up", last_sym, 0.4)
        return ("unclear", None, 0.3)
    
    # 2. Clear greeting
    if any(kw in n for kw in ["السلام", "هلا", "مرحبا", "صباح", "مساء", "كيفك", "يعطيك", "تمام", "حياك", "اهلا", "وعليكم"]):
        return ("greeting", None, 0.95)
    
    # 3. Price request (just a symbol name)
    if single_sym and len(words) == 1:
        return ("symbol_analysis", single_sym, 0.9)
    
    # 4. Education / explanation
    edu_q = ["يعني", "اشرح", "ما معنى", "وش معنى", "معنى", "شرح", "علمني", "كيف افهم", "بساطه", "ببساطه", "عرفني"]
    edu_t = ["rsi", "macd", "adx", "دعم", "مقاومه", "بولنجر", "متوسط", "golden", "death",
             "زخم", "ترند", "مؤشر", "اتجاه", "شمعه", "فني", "تحليل", "قابيل"]
    has_edu_q = any(kw in n for kw in edu_q)
    has_edu_t = any(t in n for t in edu_t)
    if has_edu_q or (has_edu_t and not single_sym):
        return ("education", single_sym, 0.85)
    
    # 5. Compare
    if any(kw in n for kw in ["قارن", "مقارنه", "مين اقوى", "وش افضل", "ايهم", "الفرق", "ولا "]):
        if len(syms) >= 2: return ("compare", syms[:2], 0.9)
        if len(syms) == 1: return ("compare", syms, 0.7)
        if " ولا " in n:
            parts = n.split(" ولا ")
            s1, s2 = find_sym(parts[0]), find_sym(parts[1])
            if s1 and s2: return ("compare", [s1, s2], 0.85)
        return ("compare", None, 0.6)
    
    # 6. Screener / opportunities
    if any(kw in n for kw in ["فرص", "فرصه", "قويه", "اختراق", "مسح", "شي حلو", "طلع", "شيسوي", "اراقب"]):
        if not last_sym or any(kw2 in n for kw2 in ["اليوم", "شو", "عطني", "عطيني", "ادور", "فيه"]):
            return ("screener_request", None, 0.8)
        return ("screener_request", None, 0.7)
    
    # 7. Follow-up (no symbol, has context)
    if not single_sym and last_sym:
        fu_kw = ["الخطر", "الدعم", "المقاومه", "الهدف", "انتظر", "سلبي", "ايجابي",
                 "تحسن", "كسر", "اخترق", "الزبده", "الخلاصه", "اوضح", "فهمت", "يلغي",
                 "نقطه", "تاكيد", "يعني", "طيب", "بس", "و", "لكن", "ادخل", "اطلع",
                 "مناسب", "الان", "لالا", "خلاص", "كمل", "تابع", "تنصح", "راي", "رأي",
                 "نظره", "تشوف", "توقع", "توصيه", "توصية", "زود", "عطني", "ورني"]
        if any(kw in n for kw in fu_kw):
            return ("follow_up", last_sym, 0.9)
        if len(words) <= 3:
            return ("follow_up", last_sym, 0.7)
    
    # 8. News
    if any(kw in n for kw in ["اخبار", "خبر", "السالفه", "سبب", "ليه طاح", "ليه ارتفع", "اخر خبر", "وش صار"]):
        if single_sym: return ("news", single_sym, 0.85)
        if last_sym: return ("news", last_sym, 0.75)
        return ("news", None, 0.6)
    
    # 9. General market
    if any(kw in n for kw in ["السوق", "المؤشر", "spx", "dow", "nasdaq", "مولع", "نايم",
                               "تذبذب", "صاير", "ملخص السوق", "اليوم ندخل", "اليوم ننتظر",
                               "الأسواق", "الاسواق", "وضع السوق"]):
        return ("general_market", None, 0.85)
    
    # 10. Portfolio / my holdings
    pf_kw = ["محفظتي", "محفظه", "محفظتي", "ارباحي", "ارباح", "خساير", "خسائري",
             "استثماري", "فلوسي", "رصيدي", "كم عندي", "اسهمي", "أسهُمي", "محفظة",
             "مكسب", "خسارة", "كم معي", "محفظتي", "وش عندي", "عندي اسهم", "موجود"]
    if any(kw in n for kw in pf_kw):
        return ("portfolio", None, 0.9)
    if "محفظ" in n or "ارباح" in n or "اسهمي" in n or "سهُمي" in n:
        return ("portfolio", None, 0.8)

    # 10. Screener / opportunities (remaining patterns)
    if any(kw in n for kw in ["زخم", "فلتر", "يحتمل", "افضل", "راقب", "مراقبه", "قوي", "ارشح"]):
        return ("screener_request", None, 0.7)
    
    # 11. Has symbol -> symbol_analysis
    if single_sym:
        return ("symbol_analysis", single_sym, 0.85)
    
    # 12. Out of scope
    if any(kw in n for kw in ["شعر", "نكت", "نكته", "مباراه", "الجو", "اسمك", "سوالف",
                               "غني", "فيلم", "مطبخ", "طبخ", "صوره", "دحيحه"]):
        return ("out_of_scope", None, 0.85)
    
    # 13. Unclear with context -> follow_up
    if last_sym and len(words) <= 5:
        return ("follow_up", last_sym, 0.5)
    
    # 14. Unclear without context
    return ("unclear", None, 0.3)


# ─── INTENT RESPONSE HANDLERS ───

async def resp_greeting(update, ctx, msg, uid, text=""):
    await msg.edit_text("وعليكم السلام حياك الله 🤖\nاسألني عن سهم أو السوق، مثل:\nوش رأيك في الراجحي؟\nقارن spy و qqq\nوش اراقب اليوم؟")

async def resp_education(update, ctx, msg, uid, sym, text=""):
    n = norm_ar(text)
    edu_map = {
        "rsi": "مؤشر القوة النسبية (RSI) — يقيس هل السهم في ذروة شراء (فوق 70) أو ذروة بيع (تحت 30). بين 30-70 يعتبر طبيعي.",
        "macd": "مؤشر MACD — يقيس الزخم والعلاقة بين متوسطين سريع وبطيء. إذا الخط فوق الصفر = زخم إيجابي، العكس سلبي.",
        "adx": "مؤشر ADX — يقيس قوة الاتجاه (مو مثله). فوق 25 = اتجاه موجود، تحت 20 = تذبذب بدون اتجاه واضح.",
        "دعم": "مستوى الدعم — سعر يتوقع أن السهم يرتد منه عند النزول. كسره يعني احتمال استمرار الهبوط.",
        "مقاومه": "مستوى المقاومة — سعر يتوقع أن السهم يصطدم به عند الصعود. اختراقه يعني احتمال استمرار الصعود.",
        "بولنجر": "Bollinger Bands — نطاق سعري حول المتوسط. لمس الحد الأعلى = تشبع شراء، لمس الحد الأدنى = تشبع بيع.",
        "متوسط": "المتوسطات الحسابية (SMA) — متوسط السعر خلال 20/50/200 يوم. تقاطعها يعطي إشارات اتجاه.",
        "زخم": "الزخم (Momentum) — سرعة تغير السعر. زخم إيجابي = ضغط شراء، سلبي = ضغط بيع.",
        "اتجاه": "الاتجاه (Trend) — الحركة العامة للسهم: صاعد (قمم أعلى) أو هابط (قيعان أدنى) أو متذبذب.",
        "ترند": "الاتجاه (Trend) — الحركة العامة للسهم: صاعد أو هابط أو متذبذب.",
        "تحليل": "التحليل الفني — دراسة السعر والحجم والمؤشرات لتوقع الحركة المستقبلية.",
    }
    for key, exp in edu_map.items():
        if key in n:
            await msg.edit_text(f"📚 *{key.upper()}*\n\n{exp}\n\nأي سؤال ثاني؟")
            return
    await msg.edit_text("📚 *شرح مبسط*\n\nRSI: هل السهم في ذروة شراء أو بيع.\nMACD: الزخم الإيجابي أو السلبي.\nADX: قوة الاتجاه.\nالدعم: مستوى يرتد منه.\nالمقاومة: مستوى يصطدم به.\n\nأي سؤال ثاني؟ اسألني.")

async def resp_compare(update, ctx, msg, uid, syms, text=""):
    if not syms or not isinstance(syms, (list, tuple)) or len(syms) < 2:
        await msg.edit_text("📋 *المقارنة*\n\nأكتب: `قارن spy و qqq`\nأو: `وش افضل btc ولا eth`")
        return
    s1, s2 = syms[0], syms[1]
    # Fetch both
    h1, i1 = get_stock_data(s1, period="6mo", fetch_info=True)
    h2, i2 = get_stock_data(s2, period="6mo", fetch_info=True)
    if h1 is None or h2 is None:
        await msg.edit_text("❌ أحد الرموز غير صحيح.")
        return
    a1, a2 = analyze(h1), analyze(h2)
    s1d, s2d = get_signal(a1), get_signal(a2)
    n1 = i1.get("shortName") or i1.get("longName") or s1.upper()
    n2 = i2.get("shortName") or i2.get("longName") or s2.upper()
    p1, p2 = round(float(h1["Close"].iloc[-1]), 2), round(float(h2["Close"].iloc[-1]), 2)
    cur1, cur2 = ccy(s1), ccy(s2)
    score1, score2 = a1.get("score", 0), a2.get("score", 0)
    lines = [
        f"📋 *مقارنة: {n1} vs {n2}*",
        "",
        f"*{n1}* — {cur1}{p1} | نقاط: {score1} | {s1d.get('verdict','?')}",
        f"RSI: {a1.get('rsi','?')} | ADX: {a1.get('adx','?')} | MACD: {a1.get('macd_bullish','?')}",
        f"الدعم: {a1.get('support','?')} | المقاومة: {a1.get('resistance','?')}",
        f"الثقة: {s1d.get('conviction','?')}%",
        "",
        f"*{n2}* — {cur2}{p2} | نقاط: {score2} | {s2d.get('verdict','?')}",
        f"RSI: {a2.get('rsi','?')} | ADX: {a2.get('adx','?')} | MACD: {a2.get('macd_bullish','?')}",
        f"الدعم: {a2.get('support','?')} | المقاومة: {a2.get('resistance','?')}",
        f"الثقة: {s2d.get('conviction','?')}%",
        "",
        f"🏆 *الأفضل حالياً:* {'الأول' if score1 > score2 else 'الثاني'} (فارق {abs(score1-score2)} نقطة)",
    ]
    await msg.edit_text("\n".join(lines), parse_mode="Markdown")

async def resp_news(update, ctx, msg, uid, sym, text=""):
    if not sym:
        await msg.edit_text("📰 *الأخبار*\n\nأكتب اسم الرمز مع الأخبار، مثل:\n`اخبار الراجحي`")
        return
    news = get_news(sym)
    if not news:
        await msg.edit_text(f"📰 *أخبار {sym.upper()}*\n\nلا توجد أخبار حديثة حالياً.")
        return
    lines = [f"📰 *أخبار {sym.upper()}*", ""]
    for item in news[:5]:
        title = item.get("title", "")
        lines.append(f"• {title}")
    await msg.edit_text("\n".join(lines), parse_mode="Markdown")

async def resp_market(update, ctx, msg, uid, text=""):
    ctx_data = ai_ctx_get(uid) if ai_mode_get(uid) else None
    conv = gc(uid)
    if AI_PROVIDER != "local":
        try:
            system = "أنت محلل أسواق بالعربية. لخص وضع السوق الأمريكي والسعودي والكريبتو في 3-5 جمل. أسلوب سعودي خفيف."
            msgs = [{"role": "system", "content": system}]
            ms = market_status()
            ctx_str = f"[حالة السوق: {ms}]"
            msgs.append({"role": "user", "content": f"{ctx_str}\nاعطيني ملخص السوق"})
            ai_text = _call_ai_with_messages(msgs, max_tokens=400)
            if ai_text:
                await msg.edit_text(f"📡 *ملخص السوق*\n\n{ai_text}", parse_mode="Markdown")
                return
        except Exception:
            pass
    await msg.edit_text("📡 *السوق*\n\nاستخدم:\n`/live` — السوق المباشر\n`/market us` — السوق الأمريكي\n`/market saudi` — السوق السعودي\n`/market crypto` — العملات الرقمية")

async def resp_screener(update, ctx, msg, uid, text=""):
    try:
        results = get_screener()
        if results:
            lines = ["🏆 *فرص المراقبة (حسب التحليل الفني)*", ""]
            for name, sym, price, chg, pct in results[:8]:
                emoji = "📈" if pct > 0 else "📉"
                lines.append(f"{emoji} `{sym}` — {pct:+.1f}%")
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("🏆 مسح شامل", callback_data="screener")]])
            await msg.edit_text("\n".join(lines), parse_mode="Markdown", reply_markup=kb)
        else:
            await msg.edit_text("🏆 لا توجد نتائج متاحة حالياً. جرب لاحقاً.")
    except Exception as e:
        await msg.edit_text("🏆 *فرص المراقبة*\n\nاستخدم `/screener` للحصول على قائمة كاملة.")

async def resp_analysis(update, ctx, msg, uid, sym):
    """Full symbol analysis with data"""
    if not sym:
        await msg.edit_text("🔍 *تحليل*\n\nأكتب اسم الرمز، مثل:\n`وش رأيك في spy؟`\n`حلل الراجحي`")
        return False
    hist, info = get_stock_data(sym, period="6mo", fetch_info=True)
    if hist is None:
        await msg.edit_text("❌ الرمز غير صحيح. جرب رمز آخر.")
        return False
    name = info.get("shortName") or info.get("longName") or sym.upper()
    price = round(float(hist["Close"].iloc[-1]), 2)
    change = round(float(hist["Close"].pct_change(1).iloc[-1] * 100), 2)
    high6 = round(float(hist["High"].max()), 2)
    low6 = round(float(hist["Low"].min()), 2)
    avg_vol = round(float(hist["Volume"].mean()))
    cur = ccy(sym)
    a = analyze(hist)
    s = get_signal(a)
    
    # Save context
    ctx_data = ai_ctx_get(uid) if ai_mode_get(uid) else None
    if ctx_data:
        ctx_data["last_symbol"] = sym
        ctx_data["last_analysis"] = a
    
    ctx_str = f"[{name} ({sym.upper()}) - {cur}{price}, تغيير: {change}%, اعلى: {cur}{high6}, ادنى: {cur}{low6}, حجم: {avg_vol:,}, ADX: {a.get('adx','?')}, RSI: {a.get('rsi','?')}, MACD: {'ايجابي' if a.get('macd_bullish') else 'سلبي'}, الاتجاه: {a.get('trends',{}).get('متوسط المدى','محايد')}, الدعم: {a.get('support','?')}, المقاومه: {a.get('resistance','?')}, التوصيه: {s.get('verdict','?')}, الثقه: {s.get('conviction','?')}%]"
    
    conv = gc(uid)
    conv.append({"role": "user", "content": f"{ctx_str}\nحلل هذا السهم"})
    
    ai_text = None
    if AI_PROVIDER != "local":
        try:
            system = "أنت مستشار مالي خبير بالعربية. اجب باختصار (2-4 جمل). قدم تحليل حقيقي بدون اوامر شراء/بيع. لا تقل مضمون او اكيد. اسلوب سعودي خفيف."
            msgs = [{"role": "system", "content": system}]
            for m in conv[-10:]:
                msgs.append({"role": m["role"], "content": m["content"]})
            ai_text = _call_ai_with_messages(msgs, max_tokens=500)
        except Exception:
            ai_text = None
    
    if ai_text:
        conv.append({"role": "assistant", "content": ai_text})
        sc(uid, conv[-20:])
        if ctx_data:
            ctx_data["last_response"] = ai_text
            ctx_data["history"].append({"q": f"حلل {sym}", "a": ai_text})
            ctx_data["history"] = ctx_data["history"][-10:]
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"📊 {sym.upper()}", callback_data=f"analyze:{sym}"),
             InlineKeyboardButton("🔥 توصية", callback_data=f"signal:{sym}")],
            [InlineKeyboardButton("📈 شارت", callback_data=f"chart:{sym}")],
        ])
        await msg.edit_text(f"🤖 *{name}*\n\n{ai_text}", parse_mode="Markdown", reply_markup=kb)
    else:
        local = safe_filter(get_local_advice(sym, price, a, s, cur))
        conv.append({"role": "assistant", "content": local})
        sc(uid, conv[-20:])
        if ctx_data:
            ctx_data["last_response"] = local
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"📊 تحليل", callback_data=f"analyze:{sym}"),
             InlineKeyboardButton("🔥 توصية", callback_data=f"signal:{sym}")],
            [InlineKeyboardButton("📈 شارت", callback_data=f"chart:{sym}")],
        ])
        await msg.edit_text(f"🧠 *{name}*\n{local}", parse_mode="Markdown", reply_markup=kb)
    return True

def get_local_followup(sym, name, price, cur, text, a, s):
    """Local response for follow-up questions when AI is unavailable"""
    n = norm_ar(text)
    trends = a.get("trends", {})
    short_t = trends.get("قصير المدى", "محايد")
    verdict = s.get("verdict", "محايد")
    conv = s.get("conviction", "متوسطة")
    
    if any(kw in n for kw in ["توصيه", "توصية", "تنصح", "راي", "رأي", "ادخل", "اطلع"]):
        return (f"📊 *رأي فني لـ {name}*\n"
                f"السعر: {cur}{price}\n"
                f"القرار: {verdict} (ثقة {conv})\n"
                f"الاتجاه القصير: {short_t}\n"
                f"الدعم: {a.get('support', '—')} | المقاومة: {a.get('resistance', '—')}\n"
                f"RSI: {a.get('rsi', '—')} | ADX: {a.get('adx', '—')}")
    elif any(kw in n for kw in ["الدعم", "دعم", "المقاومه", "مقاومه", "مقاومة"]):
        sup = a.get('support', '—')
        res = a.get('resistance', '—')
        sup2 = a.get('support2', '—')
        res2 = a.get('resistance2', '—')
        return (f"📉 *مستويات {name}*\n"
                f"دعم رئيسي: {pf(sym, sup) if sup != '—' else '—'}\n"
                f"دعم ثاني: {pf(sym, sup2) if sup2 != '—' else '—'}\n"
                f"مقاومة رئيسية: {pf(sym, res) if res != '—' else '—'}\n"
                f"مقاومة ثانية: {pf(sym, res2) if res2 != '—' else '—'}\n"
                f"السعر الحالي: {cur}{price}")
    elif any(kw in n for kw in ["الخطر", "وقف", "stop", "كسر"]):
        sup = a.get('support', '—')
        return (f"⚠️ *نقاط الخطر لـ {name}*\n"
                f"وقف الخسارة: كسر {pf(sym, sup) if sup != '—' else 'الدعم الرئيسي'}\n"
                f"التذبذب: {a.get('volatility', '—')}%\n"
                f"المخاطرة: {a.get('score', 50)}/100")
    elif any(kw in n for kw in ["الهدف", "هدف"]):
        res = a.get('resistance', '—')
        sup = a.get('support', '—')
        if res != '—' and sup != '—':
            t2 = round(price + (res - sup) * 1.2, 2)
            return (f"🎯 *الأهداف لـ {name}*\n"
                    f"الهدف الأول: {pf(sym, res)}\n"
                    f"الهدف الثاني: {pf(sym, t2)}\n"
                    f"وقف الخسارة: {pf(sym, sup)}")
        return f"🎯 لم يتم تحديد أهداف واضحة لـ {name} حالياً."
    elif any(kw in n for kw in ["اتجاه", "ترند", "long"]):
        trend_str = "\n".join(f"{k}: {v}" for k, v in trends.items())
        return (f"📈 *اتجاه {name}*\n"
                f"{trend_str}\n"
                f"ADX: {a.get('adx', '—')} (قوة الاتجاه)\n"
                f"MACD: {'إيجابي 📈' if a.get('macd_bullish') else 'سلبي 📉'}")
    else:
        return (f"📊 {name} | {cur}{price}\n"
                f"القرار: {verdict} | الثقة: {conv}\n"
                f"RSI: {a.get('rsi', '—')} | ADX: {a.get('adx', '—')}\n"
                f"الاتجاه: {short_t}")

async def resp_followup(update, ctx, msg, uid, sym, text=""):
    """Follow-up question about the last symbol"""
    if not sym:
        await resp_unclear(update, ctx, msg, uid, text)
        return
    hist, info = get_stock_data(sym, period="6mo", fetch_info=True)
    if hist is None:
        await msg.edit_text("❌ الرمز غير صحيح.")
        return
    name = info.get("shortName") or info.get("longName") or sym.upper()
    price = round(float(hist["Close"].iloc[-1]), 2)
    change = round(float(hist["Close"].pct_change(1).iloc[-1] * 100), 2)
    cur = ccy(sym)
    a = analyze(hist)
    s = get_signal(a)
    
    ctx_str = f"[{name} ({sym.upper()}) - {cur}{price}, تغيير: {change}%, ADX: {a.get('adx','?')}, RSI: {a.get('rsi','?')}, MACD: {'ايجابي' if a.get('macd_bullish') else 'سلبي'}, الدعم: {a.get('support','?')}, المقاومه: {a.get('resistance','?')}, التوصيه: {s.get('verdict','?')}, الثقه: {s.get('conviction','?')}%]"
    
    conv = gc(uid)
    conv.append({"role": "user", "content": f"{ctx_str}\nمتابعة: {text}"})
    
    ai_text = None
    if AI_PROVIDER != "local":
        try:
            system = "أنت مستشار مالي خبير بالعربية. اجب باختصار (2-3 جمل). ركز على سؤال المستخدم بالضبط. اسلوب سعودي خفيف."
            msgs = [{"role": "system", "content": system}]
            for m in conv[-8:]:
                msgs.append({"role": m["role"], "content": m["content"]})
            ai_text = _call_ai_with_messages(msgs, max_tokens=400)
        except Exception:
            ai_text = None
    
    if ai_text:
        conv.append({"role": "assistant", "content": ai_text})
        sc(uid, conv[-20:])
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"📊 {sym.upper()}", callback_data=f"analyze:{sym}"),
             InlineKeyboardButton("🔥 توصية", callback_data=f"signal:{sym}")],
            [InlineKeyboardButton("📈 شارت", callback_data=f"chart:{sym}")],
        ])
        await msg.edit_text(f"🤖 *{name}*\n\n{ai_text}", parse_mode="Markdown", reply_markup=kb)
    else:
        local = safe_filter(get_local_followup(sym, name, price, cur, text, a, s))
        conv.append({"role": "assistant", "content": local})
        sc(uid, conv[-20:])
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"📊 تحليل", callback_data=f"analyze:{sym}"),
             InlineKeyboardButton("🔥 توصية", callback_data=f"signal:{sym}")],
            [InlineKeyboardButton("📈 شارت", callback_data=f"chart:{sym}")],
        ])
        await msg.edit_text(f"🧠 *{name}*\n{local}", parse_mode="Markdown", reply_markup=kb)

async def resp_unclear(update, ctx, msg, uid, text=""):
    await msg.edit_text("🤔 *ما فهمت قصدك*\n\nتقصد:\n• تحليل سهم؟ اكتب اسمه: `الراجحي` أو `btc`\n• السوق؟ اكتب: `وش وضع السوق؟`\n• فرص؟ اكتب: `وش اراقب اليوم؟`\n• شرح؟ اكتب: `اشرح RSI`\n\nأو استخدم الأمر المباشر:\n`/price` أو `/analyze`")

async def resp_out_of_scope(update, ctx, msg, uid, text=""):
    await msg.edit_text("🤖 أنا مختص بالأسواق المالية والأسهم والكريبتو.\n\nاسألني عن:\n• سهم: `وش رأيك في btc؟`\n• السوق: `السوق كيف؟`\n• فرص: `وش اراقب اليوم؟`\n• مقارنة: `قارن spy و qqq`")

async def resp_ai_chat(update, ctx, msg, uid, text):
    """Generic AI chat fallback"""
    conv = gc(uid)
    conv.append({"role": "user", "content": text})
    ai_text = None
    if AI_PROVIDER != "local":
        try:
            system = "أنت مستشار مالي خبير بالعربية. اجب باختصار (2-4 جمل). اذا سال عن سهم محدد، ذكره باستخدام اسم الرمز."
            msgs = [{"role": "system", "content": system}]
            for m in conv[-6:]:
                msgs.append({"role": m["role"], "content": m["content"]})
            ai_text = _call_ai_with_messages(msgs, max_tokens=500)
        except Exception:
            ai_text = None
    if ai_text:
        conv.append({"role": "assistant", "content": ai_text})
        sc(uid, conv[-20:])
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔍 مسح السوق", callback_data="screener"),
             InlineKeyboardButton("🇺🇸 السوق", callback_data="market:us")],
        ])
        await msg.edit_text(f"🤖 {ai_text}", parse_mode="Markdown", reply_markup=kb)
    else:
        await msg.edit_text("🤖 *المحادثة الذكية*\n\nتقدر تسأل:\n• `وش رأيك في btc؟`\n• `قارن spy و qqq`\n• `السوق كيف؟`\n• `اشرح RSI`\n\nأو استخدم الأوامر:\n`/price` / `/analyze` / `/signal`")

async def process_ai_chat(update, ctx, text, uid):
    try:
        if not text.strip():
            conv = gc(uid)
            if conv:
                last = conv[-1]["content"][:100] if conv[-1]["role"] == "assistant" else ""
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("🗑 مسح المحادثة", callback_data="ai_clear"),
                     InlineKeyboardButton("💬 متابعة", callback_data="ai_continue")],
                ])
                await update.message.reply_text(f"🤖 *المحادثة الحالية*\nآخر رد: {last}...\n\nاسأل سؤالك مباشرة", parse_mode="Markdown", reply_markup=kb)
            else:
                await update.message.reply_text("🤖 *المحادثة الذكية*\n\nاسأل عن أي سهم أو السوق بشكل طبيعي.", parse_mode="Markdown")
            return

        if text.lower() in ("new", "مسح", "جديد"):
            sc(uid, [])
            await update.message.reply_text("✅ تم مسح المحادثة")
            return

        ctx_data = ai_ctx_get(uid) if ai_mode_get(uid) else None
        
        # Detect intent
        intent, sym, conf = detect_intent(text, ctx_data)
        
        msg = await update.message.reply_text("⏳ جاري التحليل...")
        
        if intent == "greeting":
            await resp_greeting(update, ctx, msg, uid, text)
        elif intent == "education":
            await resp_education(update, ctx, msg, uid, sym, text)
        elif intent == "compare":
            await resp_compare(update, ctx, msg, uid, sym, text)
        elif intent == "news":
            await resp_news(update, ctx, msg, uid, sym, text)
        elif intent == "general_market":
            await resp_market(update, ctx, msg, uid, text)
        elif intent == "screener_request":
            await resp_screener(update, ctx, msg, uid, text)
        elif intent == "symbol_analysis":
            await resp_analysis(update, ctx, msg, uid, sym)
        elif intent == "follow_up":
            await resp_followup(update, ctx, msg, uid, sym, text)
        elif intent == "out_of_scope":
            await resp_out_of_scope(update, ctx, msg, uid, text)
        else:
            await resp_unclear(update, ctx, msg, uid, text)
            
    except Exception as e:
        logging.error(f"process_ai_chat error: {e}")
        try:
            if update and update.effective_chat:
                await ctx.bot.send_message(update.effective_chat.id, "❌ حدث خطأ. استخدم /help لمعرفة الأوامر")
        except Exception:
            pass

async def resp_portfolio(update, ctx, msg, uid, text):
    p = gp(uid)
    if not p:
        await msg.edit_text("📭 *محفظتي*\n\nمحفظتك فارغة حالياً.\nلإضافة: `/portfolio add spy 10 450`", parse_mode="Markdown")
        return
    def _cc(sym): return "﷼" if sym.endswith(".SR") else "$"
    total_cost = 0
    total_val = 0
    items_detail = []
    best = None
    worst = None
    for item in p:
        price, chg, pct = get_current_price(item["symbol"])
        if not price:
            continue
        cost = item["qty"] * item["buy_price"]
        val = item["qty"] * price
        pl = val - cost
        pl_pct = ((price - item["buy_price"]) / item["buy_price"]) * 100
        total_cost += cost
        total_val += val
        cur = _cc(item["symbol"])
        items_detail.append(f"• `{item['symbol']}`: {item['qty']} سهم @ {cur}{price:.2f} ({pl_pct:+.2f}%)")
        if best is None or pl_pct > best[1]: best = (item["symbol"], pl_pct, cur)
        if worst is None or pl_pct < worst[1]: worst = (item["symbol"], pl_pct, cur)
    total_pl = total_val - total_cost
    total_pl_pct = (total_pl / total_cost) * 100 if total_cost else 0
    emoji = "🟢" if total_pl >= 0 else "🔴"
    lines = [
        f"📊 *محفظتي*\n",
        f"{emoji} إجمالي القيمة: `{_cc(p[0]['symbol']) if p else '$'}{total_val:,.2f}`",
        f"💰 التكلفة: `{_cc(p[0]['symbol']) if p else '$'}{total_cost:,.2f}`",
        f"📈 الربح/الخسارة: `{_cc(p[0]['symbol']) if p else '$'}{total_pl:+,.2f} ({total_pl_pct:+.2f}%)`",
        f"📊 عدد الأسهم: `{len(p)}`\n",
    ]
    if best:
        lines.append(f"🏆 الأفضل: `{best[0]}` ({best[1]:+.2f}%)")
    if worst:
        lines.append(f"📉 الأسوأ: `{worst[0]}` ({worst[1]:+.2f}%)")
    lines.append("")
    lines.extend(items_detail[:10])
    if len(items_detail) > 10:
        lines.append(f"  ...و {len(items_detail)-10} آخرين")
    await msg.edit_text("\n".join(lines), parse_mode="Markdown")

async def process_natural_chat(update, ctx, text, uid):
    """Handle natural language messages using intent detection"""
    try:
        msg = await update.message.reply_text("⏳ جاري التحليل...")
        intent, obj, conf = detect_intent(text, uid)
        available, action = check_daily_usage(uid, "المحادثات")
        if intent == "unclear":
            await resp_unclear(update, ctx, msg, uid, text)
        elif intent == "greeting":
            await resp_greeting(update, ctx, msg, uid, text)
        elif intent == "symbol_analysis":
            await resp_analysis(update, ctx, msg, uid, obj, text)
        elif intent == "education":
            await resp_education(update, ctx, msg, uid, obj, text)
        elif intent == "compare":
            await resp_compare(update, ctx, msg, uid, obj, text)
        elif intent == "screener_request":
            await resp_screener(update, ctx, msg, uid, text)
        elif intent == "follow_up":
            await resp_followup(update, ctx, msg, uid, obj, text)
        elif intent == "news":
            await resp_news(update, ctx, msg, uid, obj, text)
        elif intent == "general_market":
            await resp_market(update, ctx, msg, uid, text)
        elif intent == "portfolio":
            await resp_portfolio(update, ctx, msg, uid, text)
        elif intent == "out_of_scope":
            await resp_out_of_scope(update, ctx, msg, uid, text)
        else:
            await resp_unclear(update, ctx, msg, uid, text)
    except Exception as e:
        logging.error(f"process_natural_chat error: {e}")
        try:
            await update.message.reply_text(f"❌ حدث خطأ: {e}\nاستخدم /analyze أو /price للرموز")
        except Exception:
            pass

async def chat_handler(update, ctx):
    uid = update.effective_user.id
    u = update.effective_user
    is_new = track_user(uid, u)
    if is_new and ADMIN_ID and admin_settings(ADMIN_ID).get("new_users", True):
        try:
            full = u.full_name or "—"
            uname = f"@{u.username}" if u.username else "—"
            await ctx.bot.send_message(ADMIN_ID,
                f"🆕 *مستخدم جديد*"
                f"\n👤 الاسم: `{full}`"
                f"\n📱 يوزر: {uname}"
                f"\n🆔 المعرف: `{u.id}`"
                f"\n📊 إجمالي المستخدمين: `{len(db['stats']['users'])}`",
                parse_mode="Markdown")
        except Exception:
            pass
    if not is_allowed(uid):
        await update.message.reply_text("🔒 هذا البوت للأعضاء المشتركين فقط.\nللاشتراك تواصل مع @hidanx11")
        return
    track_msg()
    text = update.message.text.strip()
    if ai_mode_get(uid):
        exit_words = ["/cancel", "/ai off", "/exit", "خروج", "إلغاء", "الغاء"]
        if text.lower() in [w.lower() for w in exit_words] or text.strip() in exit_words:
            ai_mode_set(uid, False)
            await update.message.reply_text("✅ تم إيقاف وضع المحادثة الذكية.", parse_mode="Markdown")
            return
        await process_ai_chat(update, ctx, text, uid)
    else:
        await process_natural_chat(update, ctx, text, uid)

# ─── TREND ───

async def trend_cmd(update, ctx):
    sym = " ".join(ctx.args)
    if not sym:
        await update.message.reply_text("📈 *اختر رمز لتحليل الاتجاه:*\nأو اكتب `/trend spy`", parse_mode="Markdown", reply_markup=suggest_kb("trend"))
        return
    await send_trend(update, ctx, sym)

async def send_trend(update, ctx, sym):
    await ror(update, ctx, f"⏳ تحليل اتجاه `{sym}`...")
    hist, info = get_stock_data(sym, period="1y")
    if hist is None:
        await ror(update, ctx, "❌ الرمز غير صحيح")
        return
    a = analyze(hist)
    name = info.get("shortName") or info.get("longName") or sym.upper()
    price = round(float(hist["Close"].iloc[-1]), 2)
    trends = a.get("trends", {})
    short_t = trends.get("قصير المدى", "محايد")
    medium_t = trends.get("متوسط المدى", "محايد")
    long_t = trends.get("طويل المدى", "محايد")
    adx_v = a.get("adx", 0)
    close = hist["Close"]
    sma20 = a.get("sma20")
    sma50 = a.get("sma50")
    sma200 = a.get("sma200")

    dir_map = {"صاعد": 1, "هابط": -1, "محايد": 0}
    score_t = dir_map.get(short_t, 0) + dir_map.get(medium_t, 0) + dir_map.get(long_t, 0)
    if score_t >= 2:
        overall = "صاعد 📈"
    elif score_t <= -2:
        overall = "هابط 📉"
    elif score_t == 0 and adx_v and adx_v < 20:
        overall = "متذبذب ➖"
    else:
        overall = "غير واضح ⚪"

    if adx_v:
        if adx_v > 40: strength = "قوي جداً 💪"
        elif adx_v > 25: strength = "قوي ✅"
        elif adx_v > 20: strength = "ضعيف ⚠️"
        else: strength = "تذبذب بدون اتجاه ➖"
    else:
        strength = "غير محدد"

    lines = [
        f"📈 *تحليل الاتجاه - {name}*",
        f"💰 السعر: `{pf(sym, price)}`",
        "",
        f"📊 *الاتجاه العام:* {overall}",
        f"📈 الاتجاه القصير (20): `{short_t}`",
        f"📈 الاتجاه المتوسط (50): `{medium_t}`",
        f"📈 الاتجاه الطويل (200): `{long_t}`",
        f"💪 قوة الاتجاه: `{strength}` (ADX: {adx_v})" if adx_v else f"💪 قوة الاتجاه: `{strength}`",
    ]

    if adx_v and adx_v > 20:
        di_dir = "صاعد" if a.get("plus_di", 0) > a.get("minus_di", 0) else "هابط"
        lines.append(f"🧭 اتجاه DMI: `{di_dir}`")
    else:
        lines.append(f"🧭 السوق في `تذبذب` — ADX {adx_v}")

    support = a.get('support')
    resistance = a.get('resistance')
    sustainable = True
    reasons = []
    if short_t != medium_t and medium_t != long_t:
        sustainable = False
        reasons.append("المديات غير متوافقة")
    if adx_v and adx_v < 20:
        sustainable = False
        reasons.append("ADX ضعيف")
    if (short_t == "صاعد" and price < sma20) or (short_t == "هابط" and price > sma20):
        sustainable = False
        reasons.append("سعر متعارض مع الاتجاه القصير")
    lines += [
        "",
        f"🔄 هل الاتجاه قابل للاستمرار؟ {'✅ نعم' if sustainable else '⚠️ لا — ' + (', '.join(reasons) if reasons else '')}",
    ]
    lines += [
        "",
        f"*🔑 محفزات تغير الاتجاه:*",
    ]
    if short_t == "صاعد":
        lines.append(f"• كسر SMA20 ({pf(sym, sma20)}) قد يشير لضعف مؤقت" if sma20 else "")
        if support: lines.append(f"• كسر الدعم ({pf(sym, support)}) يؤكد تغير الاتجاه القصير")
    elif short_t == "هابط":
        lines.append(f"• اختراق SMA20 ({pf(sym, sma20)}) قد يشير لارتداد مؤقت" if sma20 else "")
        if resistance: lines.append(f"• اختراق المقاومة ({pf(sym, resistance)}) يؤكد تغير الاتجاه القصير")
    else:
        lines.append("• اختراق واضح لأعلى أو أسفل مع حجم لتأكيد الاتجاه الجديد")
    lines += [
        "",
        f"━━━━━━━",
        f"*الخلاصة:*",
        f"القرار: `{overall}`",
        f"قوة الاتجاه: `{strength}`",
        f"نقطة التأكيد: إغلاق فوق SMA50 يؤكد الصعود / تحت SMA50 يؤكد الهبوط",
        f"نقطة الخطر: استمرار ADX تحت 20 يعني استمرار التذبذب",
    ]
    await ror(update, ctx, "\n".join(lines))

# ─── LEVELS ───

async def levels_cmd(update, ctx):
    sym = " ".join(ctx.args)
    if not sym:
        await update.message.reply_text("📉 *اختر رمز للدعم والمقاومة:*\nأو اكتب `/levels spy`", parse_mode="Markdown", reply_markup=suggest_kb("levels"))
        return
    await send_levels(update, ctx, sym)

async def send_levels(update, ctx, sym):
    await ror(update, ctx, f"⏳ تحليل `{sym}`...")
    hist, info = get_stock_data(sym, period="1y")
    if hist is None:
        await ror(update, ctx, "❌ الرمز غير صحيح")
        return
    a = analyze(hist)
    name = info.get("shortName") or info.get("longName") or sym.upper()
    price = round(float(hist["Close"].iloc[-1]), 2)
    lines = [f"📉 *مستويات {name}*", f"💰 السعر: `{pf(sym, price)}`", ""]
    if a.get("support") and a.get("resistance"):
        ds = round(((price - a["support"]) / price) * 100, 2)
        dr = round(((a["resistance"] - price) / price) * 100, 2)
        lines += [
            f"🔴 *دعم قوي:* `{pf(sym, a['support'])}` (أدنى {ds}%)",
            f"🟢 *مقاومة قوية:* `{pf(sym, a['resistance'])}` (أعلى {dr}%)",
        ]
        s2 = a.get('support2')
        r2 = a.get('resistance2')
        if s2: lines.append(f"🔸 دعم ثاني: `{pf(sym, s2)}`")
        if r2: lines.append(f"🔸 مقاومة ثانية: `{pf(sym, r2)}`")
        lines += [
            "",
            f"🟡 *المدى:* {pf(sym, a['support'])} - {pf(sym, a['resistance'])}",
            f"📏 عرض المدى: {round(dr+ds, 1)}%",
        ]
        near_support = ds < 2
        near_resistance = dr < 2
        if near_support:
            lines.append(f"⚠️ السعر قريب جداً من الدعم ({ds}%)")
            lines.append(f"💡 معنى كسر الدعم: احتمال هبوط إلى {pf(sym, a.get('support2', a['support']*0.95))}")
        if near_resistance:
            lines.append(f"⚠️ السعر قريب من المقاومة ({dr}%)")
            lines.append(f"💡 معنى اختراق المقاومة: احتمال صعود إلى {pf(sym, a.get('resistance2', a['resistance']*1.05))}")
        if not near_support and not near_resistance:
            lines.append(f"💡 السعر في منتصف المدى — انتظار الاقتراب من الدعم أو المقاومة")
    if a.get("high_52w"):
        from_high = a.get('from_52w_high', 0)
        lines.append(f"📊 أعلى 52 أسبوع: `{pf(sym, a['high_52w'])}` ({from_high:+.2f}%)")
    if a.get("low_52w"):
        lines.append(f"📊 أدنى 52 أسبوع: `{pf(sym, a['low_52w'])}`")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔥 توصية", callback_data=f"signal:{sym}"),
         InlineKeyboardButton("📈 شارت", callback_data=f"chart:{sym}")],
    ])
    await ror(update, ctx, "\n".join(lines), kb)

# ─── NEWS CALLBACK ───

async def send_news(update, ctx, sym):
    await ror(update, ctx, f"⏳ جلب أخبار `{sym}`...")
    news = get_news(sym)
    if not news:
        await ror(update, ctx, f"📰 *{sym.upper()}*\n\nلا توجد أخبار حديثة حالياً.")
        return
    lines = [f"📰 *أخبار {sym.upper()}*", ""]
    for item in news[:5]:
        lines.append(f"• {item.get('title', '')}")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"📊 {sym.upper()}", callback_data=f"analyze:{sym}"),
         InlineKeyboardButton("📈 شارت", callback_data=f"chart:{sym}")],
        [InlineKeyboardButton("🔄 تحديث", callback_data=f"news:{sym}")],
    ])
    await ror(update, ctx, "\n".join(lines), kb)

# ─── COMPARE ───

async def compare_cmd(update, ctx):
    args = ctx.args
    if len(args) < 2:
        await update.message.reply_text("❗ استخدم: `/compare spy qqq`")
        return
    s1, s2 = args[0], args[1]
    await ror(update, ctx, f"⏳ مقارنة `{s1}` و `{s2}`...")
    h1, i1 = get_stock_data(s1, period="6mo")
    h2, i2 = get_stock_data(s2, period="6mo")
    if h1 is None or h2 is None: await ror(update, ctx, "❌ رمز غير صحيح"); return
    n1 = i1.get("shortName", s1.upper()); n2 = i2.get("shortName", s2.upper())
    p1 = round(float(h1["Close"].iloc[-1]), 2); p2 = round(float(h2["Close"].iloc[-1]), 2)
    a1 = analyze(h1); a2 = analyze(h2)
    c1 = round(float(h1["Close"].pct_change(1).iloc[-1]*100), 2); c2 = round(float(h2["Close"].pct_change(1).iloc[-1]*100), 2)
    r1 = round(float(a1.get("rsi",0)), 2); r2 = round(float(a2.get("rsi",0)), 2)
    s1s = get_signal(a1); s2s = get_signal(a2)
    lines = [
        f"⚖️ *مقارنة الأسهم*", ts(), "",
        f"📊 *{n1}* vs *{n2}*", "",
        f"━━━ {s1.upper()} ━━━ vs ━━━ {s2.upper()} ━━━",
        f"• السعر: {pf(s1, p1)} vs {pf(s2, p2)}",
        f"• التغير: {c1:+.2f}% vs {c2:+.2f}%",
        f"• RSI: {r1} vs {r2}",
        f"• التوصية: {s1s['action']} vs {s2s['action']}",
        "",
    ]
    if s1s["score"] > s2s["score"]: lines.append(f"🏆 *{n1}* أفضل فنياً")
    elif s2s["score"] > s1s["score"]: lines.append(f"🏆 *{n2}* أفضل فنياً")
    else: lines.append("⚖️ متساويان")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🔥 {s1.upper()}", callback_data=f"signal:{s1}"),
         InlineKeyboardButton(f"🔥 {s2.upper()}", callback_data=f"signal:{s2}")],
    ])
    await ror(update, ctx, "\n".join(lines), kb)

# ─── SCREENER ───

SCREENER_FILTERS = {
    "golden_cross": "تقاطع ذهبي",
    "death_cross": "تقاطع موت",
    "ترند_صاعد": "ترند صاعد",
    "ترند_هابط": "ترند هابط",
    "rsi_oversold": "RSI تشبع بيعي",
    "rsi_overbought": "RSI تشبع شرائي",
    "volume_spike": "حجم مرتفع",
    "support_near": "قرب الدعم",
    "resistance_near": "قرب المقاومة",
    "macd_bullish": "MACD إيجابي",
    "macd_bearish": "MACD سلبي",
    "strong_trend": "اتجاه قوي (ADX > 25)",
    "breakout_potential": "احتمال اختراق",
    "low_volatility": "تذبذب منخفض",
}

async def screener_cmd(update, ctx):
    if not check_rate_limit(update.effective_user.id):
        await ror(update, ctx, "⏳ تمهل قليلاً..."); return
    filter_type = " ".join(ctx.args).strip().lower() if ctx.args else ""
    if filter_type and filter_type not in SCREENER_FILTERS:
        opts = "\n".join(f"`{k}` — {v}" for k, v in SCREENER_FILTERS.items())
        await ror(update, ctx, f"❌ فلتر غير معروف\nاختر من:\n{opts}")
        return
    await ror(update, ctx, "⏳ جاري مسح السوق...")
    results = get_screener()
    if not results: await ror(update, ctx, "❌ تعذر مسح السوق حالياً، حاول مرة أخرى"); return
    
    if filter_type:
        scored = []
        for name, sym, price, chg, pct in results:
            hist, _ = get_stock_data(sym, period="6mo", fetch_info=False)
            if hist is None: continue
            a = analyze(hist)
            score = a.get("score", 0)
            trends = a.get("trends", {})
            short_t = trends.get("قصير المدى", "محايد")
            match = False
            if filter_type == "golden_cross" and a.get("golden_cross"):
                match = True
            elif filter_type == "death_cross" and a.get("golden_cross") is False:
                match = True
            elif filter_type == "ترند_صاعد" and short_t == "صاعد":
                match = True
            elif filter_type == "ترند_هابط" and short_t == "هابط":
                match = True
            elif filter_type == "rsi_oversold" and a.get("rsi", 50) < 35:
                match = True
            elif filter_type == "rsi_overbought" and a.get("rsi", 50) > 65:
                match = True
            elif filter_type == "volume_spike" and a.get("volume_ratio", 1) > 1.5:
                match = True
            elif filter_type == "support_near" and a.get("support"):
                close = float(hist["Close"].iloc[-1])
                dist = abs(close - a["support"]) / a["support"] * 100
                if dist < 2: match = True
            elif filter_type == "resistance_near" and a.get("resistance"):
                close = float(hist["Close"].iloc[-1])
                dist = abs(a["resistance"] - close) / close * 100
                if dist < 2: match = True
            elif filter_type == "macd_bullish" and a.get("macd_bullish"):
                match = True
            elif filter_type == "macd_bearish" and a.get("macd_bullish") is False:
                match = True
            elif filter_type == "strong_trend" and a.get("adx", 0) > 25:
                match = True
            elif filter_type == "breakout_potential":
                if a.get("resistance") and a.get("volume_spike"):
                    close = float(hist["Close"].iloc[-1])
                    dist = (a["resistance"] - close) / close * 100
                    if 0 < dist < 3: match = True
            elif filter_type == "low_volatility" and a.get("volatility", 10) < 1.5:
                match = True
            if match:
                scored.append((score, name, sym, price, chg, pct))
        scored.sort(key=lambda x: -x[0])
        if not scored:
            await ror(update, ctx, f"🔍 لا توجد نتائج لـ `{SCREENER_FILTERS[filter_type]}`")
            return
        lines = [f"🏆 *مسح {SCREENER_FILTERS[filter_type]}*\n", f"{ts()}\n"]
        for score, name, sym, price, chg, pct in scored[:10]:
            hist, _ = get_stock_data(sym, period="6mo", fetch_info=False)
            rsi_str = ""
            trend_str = ""
            if hist is not None:
                a2 = analyze(hist)
                rsi_v = a2.get("rsi", 50)
                rsi_str = f" | RSI: {rsi_v}"
                t = a2.get("trends", {}).get("قصير المدى", "")
                if t == "صاعد": trend_str = "📈"
                elif t == "هابط": trend_str = "📉"
                else: trend_str = "➖"
            lines.append(f"{'📈' if chg>=0 else '📉'} {name} (`{sym}`): {pf(sym, price)} ({pct:+.2f}%){rsi_str} {trend_str}\n   Score: {score}")
        lines.append(f"\n📊 العدد: {len(scored)}")
    else:
        gainers = sorted(results, key=lambda x: x[4], reverse=True)[:5]
        losers = sorted(results, key=lambda x: x[4])[:5]
        lines = ["🏆 *مسح السوق*\n", f"{ts()}\n", "*🟢 أفضل 5 أداء:*"]
        for name, sym, price, chg, pct in gainers:
            lines.append(f"📈 {name} (`{sym}`): {pf(sym, price)} ({pct:+.2f}%)")
        lines += ["", "*🔴 أسوأ 5 أداء:*"]
        for name, sym, price, chg, pct in losers:
            lines.append(f"📉 {name} (`{sym}`): {pf(sym, price)} ({pct:+.2f}%)")
        lines += ["", "للفلترة: `/screener golden_cross` أو `/screener ترند_صاعد`"]
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔄 تحديث", callback_data="screener")]])
    await ror(update, ctx, "\n".join(lines), kb)

# ─── NEWS ───

async def news_cmd(update, ctx):
    sym = " ".join(ctx.args)
    if not sym:
        await update.message.reply_text("📰 *اختر رمز للأخبار:*\nأو اكتب `/news spy`", parse_mode="Markdown", reply_markup=suggest_kb("news"))
        return
    await ror(update, ctx, f"⏳ جلب أخبار `{sym}`...")
    resolved = resolve_symbol(sym)
    news_data = get_news(resolved)
    if not news_data:
        await ror(update, ctx, "❌ لا توجد أخبار حالياً")
        return
    news_data = news_data[:5]
    price, chg, pct = get_current_price(resolved)
    hist, _ = get_stock_data(sym, period="1y")
    a = analyze(hist) if hist is not None else None
    lines = [f"📰 *تحليل الأخبار - {sym.upper()}*"]
    if price:
        direction = "📈" if pct and pct > 0 else "📉" if pct and pct < 0 else "➖"
        lines.append(f"💰 السعر: `{pf(resolved, price)}` {direction} ({pct:+.2f}%)" if pct else f"💰 السعر: `{pf(resolved, price)}`")
    lines.append("")
    for news in news_data:
        title = news.get("title", "")
        link = news.get("link", "")
        lines.append(f"🔹 {title}")
        if link: lines.append(f"   [رابط]({link})")
        lines.append("")
    lines.append("*📊 تحليل الأثر المتوقع:*")
    if a:
        vol = a.get("volatility", 0)
        rsi_v = a.get("rsi", 50)
        adx_v = a.get("adx", 0)
        lines.append(f"• التذبذب الحالي: `{vol}%` — {'مرتفع' if vol > 3 else 'متوسط' if vol > 1.5 else 'منخفض'}")
        lines.append(f"• RSI: `{rsi_v}` — {'قد يكون حساس للأخبار' if rsi_v > 70 or rsi_v < 30 else 'محايد للأخبار'}")
        if adx_v and adx_v > 25:
            di = "إيجابي" if a.get("plus_di", 0) > a.get("minus_di", 0) else "سلبي"
            lines.append(f"• ADX: `{adx_v}` — اتجاه {di} — الأخبار قد تعزز الاتجاه أو تعكسه")
        if a.get("volume_spike"):
            lines.append(f"• حجم مرتفع — السوق متفاعل مع الأخبار حالياً")
    lines += [
        "",
        f"📌 *التصنيف:*",
        f"• الأثر المتوقع: قد يسبب تذبذب في الجلسات القادمة.",
        f"• يُفضل متابعة السعر بعد الخبر لتأكيد الاتجاه.",
        "",
        "⚠️ *ملاحظة:* الأخبار تحليلية وليست توصية.",
    ]
    await ror(update, ctx, "\n".join(lines))

# ─── LIVE ───

async def live_cmd(update, ctx):
    sym = " ".join(ctx.args)
    if sym:
        await send_price(update, ctx, sym)
        return
    status = market_status()
    lines = ["📡 *حالة الأسواق*\n", f"{ts()}\n"]
    for market, (icon, info) in status.items():
        lines.append(f"{market}: {icon} {info}")
    
    sp500 = get_current_price("SPY")
    btc = get_current_price("BTC-USD")
    gold = get_current_price("GC=F")
    oil = get_current_price("CL=F")
    vix = get_current_price("^VIX")
    
    classifications = []
    if sp500 and sp500[2] is not None:
        if sp500[2] > 1: classifications.append("🇺🇸 أمريكي: صاعد 📈")
        elif sp500[2] < -1: classifications.append("🇺🇸 أمريكي: هابط 📉")
        else: classifications.append("🇺🇸 أمريكي: متذبذب ➖")
    if btc and btc[2] is not None:
        if btc[2] > 2: classifications.append("₿ كريبتو: صاعد 📈")
        elif btc[2] < -2: classifications.append("₿ كريبتو: هابط 📉")
        else: classifications.append("₿ كريبتو: متذبذب ➖")
    if vix and vix[0]:
        if vix[0] > 25: classifications.append(f"⚠️ VIX {vix[0]:.1f} — سوق عالي المخاطر")
        else: classifications.append(f"✅ VIX {vix[0]:.1f} — مخاطرة منخفضة")
    
    lines += ["", "*📊 تصنيف الأسواق:*"]
    lines += classifications if classifications else ["• غير متاح"]
    
    risk_level = "مرتفعة" if (vix and vix[0] and vix[0] > 25) else "متوسطة" if (vix and vix[0] and vix[0] > 18) else "منخفضة"
    rec = "مناسب للمراقبة" if risk_level == "منخفضة" else "مناسب للحذر" if risk_level == "متوسطة" else "انتظار أفضل"
    lines += ["", f"*📊 التوصية العامة:*", f"• المخاطرة: {risk_level}", f"• {rec}"]
    
    lines += ["", f"{ts()}"]
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🇺🇸 أمريكا", callback_data="market:us"),
         InlineKeyboardButton("🇸🇦 السعودية", callback_data="market:saudi")],
        [InlineKeyboardButton("₿ عملات", callback_data="market:crypto"),
         InlineKeyboardButton("🛢 سلع", callback_data="market:commodities")],
    ])
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown", reply_markup=kb)

# ─── MARKET ───

async def market_cmd(update, ctx):
    group = ctx.args[0].lower() if ctx.args else "us"
    await send_market(update, ctx, group)

async def send_market(update, ctx, group):
    if group not in MARKET_BTNS:
        lst = "\n".join(f"`{k}` - {v}" for k,v in MARKET_BTNS.items())
        await ror(update, ctx, f"❌ غير معروف\nالمجموعات:\n{lst}")
        return
    results = get_market_group(group)
    if not results: await ror(update, ctx, f"❌ لا توجد بيانات للمجموعة `{group}`"); return
    lines = [f"📊 *{MARKET_BTNS[group]}*\n"]
    for item in results:
        name, sym, price, chg, pct = item
        lines.append(price_line(name, sym, price, chg, pct))
    lines.append(f"\n{ts()}")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🇺🇸", callback_data="market:us"),
         InlineKeyboardButton("₿", callback_data="market:crypto"),
         InlineKeyboardButton("🛢", callback_data="market:commodities")],
        [InlineKeyboardButton("💱", callback_data="market:forex"),
         InlineKeyboardButton("🇸🇦", callback_data="market:saudi"),
         InlineKeyboardButton("🇪🇺", callback_data="market:europe")],
        [InlineKeyboardButton("🌏", callback_data="market:asia"),
         InlineKeyboardButton("💻", callback_data="market:tech"),
         InlineKeyboardButton("🏦", callback_data="market:banks")],
        [InlineKeyboardButton("⚡", callback_data="market:energy")],
    ])
    await ror(update, ctx, "\n".join(lines), kb)

# ─── PREDICT ───

async def predict_cmd(update, ctx):
    if update.effective_user and not check_rate_limit(update.effective_user.id):
        await ror(update, ctx, "⏳ تمهل قليلاً..."); return
    sym = " ".join(ctx.args)
    if not sym:
        await update.message.reply_text("🔮 *اختر رمز للتوقع:*\nأو اكتب `/predict spy`", parse_mode="Markdown", reply_markup=suggest_kb("predict"))
        return
    await ror(update, ctx, f"⏳ تحليل `{sym}`...")
    resolved = resolve_symbol(sym)
    if sym.lower() not in SYMBOL_MAP:
        await ror(update, ctx, f"❌ الرمز `{sym}` غير معروف\nأمثلة: spy, btc, 2030.sr, aapl, الرياض")
        return
    hist, _ = get_stock_data(sym, period="1y", fetch_info=False)
    if hist is None:
        await ror(update, ctx, f"❌ لا توجد بيانات لـ `{sym}`"); return
    name = friendly_name(resolved)
    p = predict_price(hist)
    if not p:
        await ror(update, ctx, "❌ بيانات غير كافية للتوقع"); return
    
    lines = [
        f"🔮 *سيناريو رياضي لـ {name}*",
        f"💰 السعر الحالي: `{pf(resolved, p['current_price'])}`",
        "",
        f"📊 النطاق المتوقع (30 يوم):",
        f"📈 سيناريو إيجابي: `{pf(resolved, p['pred_high'])}`",
        f"📉 سيناريو سلبي: `{pf(resolved, p['pred_low'])}`",
        f"🎯 السعر المتوقع: `{pf(resolved, p['predicted_price'])}` ({p['change_pct']:+.1f}%)",
        "",
        f"⚖️ الثقة: {p['confidence']} | R²: `{p['r_squared']}`",
    ]
    if p['r_squared'] < 0.5:
        lines += [
            "",
            "⚠️ *الثقة ضعيفة:* R² أقل من 0.50",
            "لا يُعتمد عليه لاتخاذ قرار مباشر.",
        ]
    lines += [
        "",
        "📝 *ملاحظة:*",
        "هذا تقدير رياضي مبني على البيانات التاريخية",
        "وليس ضماناً للحركة القادمة.",
    ]
    await ror(update, ctx, "\n".join(lines))

# ─── CALC ───

async def calc_cmd(update, ctx):
    if not ctx.args:
        await update.message.reply_text(
            "🧮 *حاسبة الاستثمار*\n\n"
            "`/calc buy السعر الكمية` — إجمالي التكلفة\n"
            "`/calc sell سعر_البيع الكمية سعر_الشراء` — الربح/الخسارة\n"
            "`/calc dca سعر1 كمية1 سعر2 كمية2 ...` — متوسط التكلفة\n"
            "`/calc return سعر_الشراء سعر_البيع الأيام` — العائد السنوي\n"
            "`/calc position المدخل الهدف المخاطرة%` — حجم المركز",
            parse_mode="Markdown"
        )
        return
    mode = ctx.args[0].lower()
    nums = []
    for a in ctx.args[1:]:
        try: nums.append(float(a))
        except: await update.message.reply_text("❌ الأرقام غير صحيحة"); return
    try:
        if mode == "buy":
            if len(nums) < 2: raise ValueError
            price, qty = nums[0], nums[1]
            total = price * qty
            await update.message.reply_text(
                f"🧮 *حساب الشراء*\n"
                f"💰 السعر: `{price}`\n"
                f"📦 الكمية: `{qty}`\n"
                f"💵 الإجمالي: `{total:,.2f}`",
                parse_mode="Markdown"
            )
        elif mode == "sell":
            if len(nums) < 3: raise ValueError
            sell_p, qty, buy_p = nums[0], nums[1], nums[2]
            total_sell = sell_p * qty
            total_buy = buy_p * qty
            profit = total_sell - total_buy
            pct = (profit / total_buy) * 100 if total_buy else 0
            emoji = "📈" if profit >= 0 else "📉"
            await update.message.reply_text(
                f"{emoji} *حساب البيع*\n"
                f"💰 سعر البيع: `{sell_p}`\n"
                f"📦 الكمية: `{qty}`\n"
                f"💵 إجمالي الشراء: `{total_buy:,.2f}`\n"
                f"💵 إجمالي البيع: `{total_sell:,.2f}`\n"
                f"📊 الربح/الخسارة: `{profit:+,.2f}` (`{pct:+.2f}%`)",
                parse_mode="Markdown"
            )
        elif mode == "dca":
            if len(nums) < 2 or len(nums) % 2 != 0: raise ValueError
            total_cost = 0; total_qty = 0
            for i in range(0, len(nums), 2):
                p, q = nums[i], nums[i+1]
                total_cost += p * q; total_qty += q
            avg = total_cost / total_qty if total_qty else 0
            await update.message.reply_text(
                f"📊 *متوسط التكلفة (DCA)*\n"
                f"🪙 إجمالي الكمية: `{total_qty}`\n"
                f"💵 إجمالي التكلفة: `{total_cost:,.2f}`\n"
                f"📊 متوسط السعر: `{avg:.2f}`",
                parse_mode="Markdown"
            )
        elif mode == "return":
            if len(nums) < 3: raise ValueError
            buy_p, sell_p, days = nums[0], nums[1], nums[2]
            total_ret = ((sell_p - buy_p) / buy_p) * 100 if buy_p else 0
            annual_ret = (((sell_p / buy_p) ** (365 / days)) - 1) * 100 if days > 0 and buy_p else 0
            await update.message.reply_text(
                f"📈 *العائد على الاستثمار*\n"
                f"💰 سعر الشراء: `{buy_p}`\n"
                f"💰 سعر البيع: `{sell_p}`\n"
                f"📅 المدة: `{days}` يوم\n"
                f"📊 العائد الإجمالي: `{total_ret:+.2f}%`\n"
                f"📊 العائد السنوي: `{annual_ret:+.2f}%`",
                parse_mode="Markdown"
            )
        elif mode == "position":
            if len(nums) < 3: raise ValueError
            entry, target, risk_pct = nums[0], nums[1], nums[2]
            reward = ((target - entry) / entry) * 100 if entry else 0
            risk_reward = abs((target - entry) / (entry * risk_pct / 100)) if risk_pct > 0 and entry else 0
            await update.message.reply_text(
                f"🎯 *حجم المركز*\n"
                f"🚪 نقطة الدخول: `{entry}`\n"
                f"🎯 نقطة الهدف: `{target}`\n"
                f"⚠️ المخاطرة: `{risk_pct}%`\n"
                f"📊 العائد المحتمل: `{reward:+.2f}%`\n"
                f"⚖️ نسبة المخاطرة/العائد: `{risk_reward:.2f}`",
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text("❌ غير معروف. استخدم: buy, sell, dca, return, position")
    except (ValueError, IndexError):
        await update.message.reply_text("❌ الأرقام غير كافية. راجع /calc للتعليمات")

# ─── STATS ───

async def stats_cmd(update, ctx):
    track_cmd("stats")
    uid = update.effective_user.id
    if uid != ADMIN_ID:
        await ror(update, ctx, "🔒 هذه الميزة للمشرف فقط")
        return
    track_user(uid, update.effective_user)
    s = db["stats"]
    total_users = len(s["users"])
    cmds = s.get("commands", {})
    total_cmds = sum(cmds.values())
    top_cmds = sorted(cmds.items(), key=lambda x: -x[1])[:5]
    now = datetime.now()

    # Daily stats
    today_str = now.strftime("%Y-%m-%d")
    daily_users = s.get("daily_users", {}).get(today_str, [])
    daily_cmds = s.get("daily_commands", {}).get(today_str, {})

    lines = ["📊 *إحصائيات البوت*\n"]
    lines.append(f"👥 إجمالي المستخدمين: `{total_users}`")
    lines.append(f"👥 مستخدمين اليوم: `{len(daily_users)}`")
    lines.append(f"📨 إجمالي الرسائل: `{s.get('total_messages', 0)}`")
    lines.append(f"⚙️ إجمالي الأوامر: `{total_cmds}`")
    lines.append(f"⚙️ أوامر اليوم: `{sum(daily_cmds.values())}`")
    global ERROR_COUNT
    lines.append(f"🔴 عدد الأخطاء: `{ERROR_COUNT}`")
    lines.append("")
    # Uptime
    uptime = now - BOT_START_TIME
    days = uptime.days
    hours = uptime.seconds // 3600
    mins = (uptime.seconds // 60) % 60
    uptime_str = f"{d}d {h}h {m}m" if days else f"{h}h {m}m"
    lines.append(f"⏱ مدة التشغيل: `{uptime_str}`")
    lines.append(f"🕐 آخر تشغيل: `{BOT_START_TIME.strftime('%Y-%m-%d %H:%M')}`")
    lines.append("")

    # Whitelist status
    wl_on = db.get("whitelist_on", False)
    wl_count = len(db.get("whitelist", []))
    lines.append(f"🔒 *حالة النظام:*")
    lines.append(f"• القائمة البيضاء: {'🟢 مفعل' if wl_on else '🔴 معطل'} ({wl_count} مستخدم)")
    subs = db.get("subscriptions", {})
    near_expiry = 0
    for suid in subs:
        days = get_sub_days_left(int(suid))
        if 0 < days <= 3:
            near_expiry += 1
    lines.append(f"• الاشتراكات: `{len(subs)}` | منتهية قريباً: `{near_expiry}`")
    admin_sets = admin_settings(ADMIN_ID)
    admin_active = any(admin_sets.values())
    lines.append(f"• تنبيهات الأدمن: {'🟢 مفعلة' if admin_active else '🔴 معطلة'}")
    lines.append("")

    # Most requested symbols from logs
    sym_counts = {}
    for entry in LOGS:
        sym = entry.get("symbol", "")
        if sym and sym != "—":
            sym_counts[sym] = sym_counts.get(sym, 0) + 1
    top_syms = sorted(sym_counts.items(), key=lambda x: -x[1])[:5]
    if top_syms:
        lines.append("*📊 أكثر الرموز طلباً:*")
        for sym, count in top_syms:
            lines.append(f"  `{sym}`: `{count}` مرة")
        lines.append("")

    if total_users > 0:
        watch_counts = {}
        for uid_str in s["users"]:
            for sym in db.get("watchlists", {}).get(uid_str, []):
                watch_counts[sym] = watch_counts.get(sym, 0) + 1
        top_watch = sorted(watch_counts.items(), key=lambda x: -x[1])[:5]
        if top_watch:
            lines.append("*📊 أكثر الأسهم متابعة:*")
            for sym, count in top_watch:
                lines.append(f"  `{sym}`: {count} مستخدم")
            lines.append("")
    if top_cmds:
        lines.append("*⚙️ أكثر الأوامر استخداماً:*")
        for cmd, count in top_cmds:
            lines.append(f"  `/{cmd}`: `{count}`")
    await ror(update, ctx, "\n".join(lines))

# ─── WATCHLIST ───

async def watch_cmd(update, ctx):
    uid = update.effective_user.id
    sym = " ".join(ctx.args)
    if not sym: await update.message.reply_text("❗ استخدم: `/watch spy`"); return
    r = resolve_symbol(sym); w = gw(uid)
    if r not in w: w.append(r); sw(uid,w); await update.message.reply_text(f"✅ تمت إضافة `{r}` إلى قائمة المتابعة")
    else: await update.message.reply_text(f"ℹ️ موجود بالفعل")

async def unwatch_cmd(update, ctx):
    uid = update.effective_user.id
    sym = " ".join(ctx.args)
    if not sym: await update.message.reply_text("❗ استخدم: `/unwatch spy`"); return
    r = resolve_symbol(sym); w = gw(uid)
    if r in w: w.remove(r); sw(uid,w); await update.message.reply_text(f"✅ تمت إزالة `{r}` من قائمة المتابعة")
    else: await update.message.reply_text("❌ غير موجود")

async def watchlist_cmd(update, ctx):
    uid = update.effective_user.id
    syms = gw(uid)
    if not syms:
        await ror(update, ctx, "📋 *قائمة المتابعة*\n\nفارغة\nأضف: `/watch spy`")
        return
    lines = ["📋 *قائمة المتابعة*\n"]
    for i, sym in enumerate(syms, 1):
        price, chg, pct = get_current_price(sym)
        if price:
            hist, _ = get_stock_data(sym, period="6mo", fetch_info=False)
            if hist is not None:
                a = analyze(hist)
                s = get_signal(a)
                score = a.get("score", 0)
                trends = a.get("trends", {})
                short = trends.get("قصير المدى", "—")
                trend_e = "📈" if short == "صاعد" else "📉" if short == "هابط" else "➖"
                if score >= 56: status = "🟢 إيجابي"
                elif score >= 36: status = "⚪ مراقبة"
                else: status = "🔴 سلبي"
                support = a.get("support")
                resistance = a.get("resistance")
                sup_s = f"دعم {pf(sym, support)}" if support else ""
                res_s = f"مقاومة {pf(sym, resistance)}" if resistance else ""
                lines.append(f"{i}. `{sym}` {trend_e}\n   سعر: {pf(sym, price)} | تغيير: {pct:+.1f}%\n   Score: {score} | {status}\n   {sup_s} | {res_s}" if sup_s and res_s else f"{i}. `{sym}` {trend_e}\n   سعر: {pf(sym, price)} | تغيير: {pct:+.1f}%\n   Score: {score} | {status}")
            else:
                lines.append(f"{i}. `{sym}`\n   سعر: {pf(sym, price)} | تغيير: {pct:+.1f}%")
        else:
            lines.append(f"{i}. ❌ `{sym}`")
    await ror(update, ctx, "\n".join(lines))

# ─── ALERTS ───

async def alert_cmd(update, ctx):
    uid = update.effective_user.id
    args = ctx.args
    if len(args) < 1:
        await update.message.reply_text("❗ الاستخدام:\n`/alert SYMBOL %` - تنبيه حركة سعرية\n`/alert target SYMBOL PRICE` - تنبيه سعر مستهدف\n`/alert day SYMBOL %` - تنبيه تغيير يومي\n`/alerts` - عرض التنبيهات")
        return
    sub = args[0].lower()
    if sub in ("add", "target", "سعر"):
        if len(args) < 3:
            await update.message.reply_text("❗ استخدم: `/alert target spy 200`")
            return
        sym = resolve_symbol(args[1])
        try:
            target = float(args[2])
        except ValueError:
            await update.message.reply_text("❌ السعر غير صالح")
            return
        al = gaa(uid)
        al.append({"type":"target","symbol":sym,"value":target,"last_price":None})
        saa(uid, al)
        p, _, _ = get_current_price(sym)
        cur_str = f" (الحالي: {ccy(sym)}{p:,.2f})" if p else ""
        await update.message.reply_text(f"🎯 تنبيه سعر مستهدف لـ `{sym}`: {ccy(sym)}{target:,.2f}{cur_str}")
    elif sub in ("day", "يوم"):
        if len(args) < 3:
            await update.message.reply_text("❗ استخدم: `/alert day spy 3`")
            return
        sym = resolve_symbol(args[1])
        try:
            pct = abs(float(args[2]))
        except ValueError:
            await update.message.reply_text("❌ النسبة غير صالحة")
            return
        al = gaa(uid)
        al.append({"type":"day_change","symbol":sym,"value":pct})
        saa(uid, al)
        await update.message.reply_text(f"📊 تنبيه تغيير يومي لـ `{sym}` عند {pct}%")
    else:
        sym = resolve_symbol(args[0])
        threshold = float(args[1]) if len(args)>1 else ALERT_THRESHOLD
        al = ga(uid); al.append({"symbol":sym,"threshold":threshold,"last_price":None}); sa(uid,al)
        await update.message.reply_text(f"✅ تنبيه حركة سعرية `{sym}` عند {threshold}%")

async def alerts_cmd(update, ctx):
    uid = update.effective_user.id
    await update.message.reply_text(alerts_summary_text(uid), parse_mode="Markdown")

async def alert_remove_cmd(update, ctx):
    uid = update.effective_user.id
    args = ctx.args
    if len(args) < 1:
        await update.message.reply_text("❗ استخدم: `/alert_remove رقم`\nاستخدم `/alerts` لرؤية الأرقام")
        return
    try:
        idx = int(args[0]) - 1
    except ValueError:
        await update.message.reply_text("❌ رقم غير صالح")
        return
    reg = ga(uid)
    if idx < len(reg):
        removed = reg.pop(idx)
        sa(uid, reg)
        await update.message.reply_text(f"🗑 تم حذف تنبيه `{removed['symbol']}`")
        return
    idx -= len(reg)
    adv = gaa(uid)
    if 0 <= idx < len(adv):
        removed = adv.pop(idx)
        saa(uid, adv)
        await update.message.reply_text(f"🗑 تم حذف تنبيه `{removed['symbol']}`")
        return
    idx -= len(adv)
    smart = gsa(uid)
    if 0 <= idx < len(smart):
        removed = smart.pop(idx)
        ssa(uid, smart)
        await update.message.reply_text(f"🗑 تم حذف التنبيه الذكي `{removed['symbol']}`")
        return
    await update.message.reply_text("❌ رقم غير موجود")

# ─── SMART ALERTS ───

SMART_TYPES = {
    "support": "كسر الدعم",
    "resistance": "كسر المقاومة",
    "golden": "تقاطع ذهبي (Golden Cross)",
    "death": "تقاطع موت (Death Cross)",
    "rsi_oversold": "RSI تشبع بيعي",
    "rsi_overbought": "RSI تشبع شرائي",
    "breakout": "اختراق مقاومة بحجم",
    "breakdown": "كسر دعم بحجم",
    "volume_spike": "ارتفاع حجم مفاجئ",
    "trend_change": "تغير الاتجاه",
    "macd_cross": "تقاطع MACD",
    "price_near_support": "قرب الدعم",
    "price_near_resistance": "قرب المقاومة",
}

async def smartalert_cmd(update, ctx):
    uid = update.effective_user.id
    args = ctx.args
    if len(args) < 1:
        types = "\n".join(f"`{k}` - {v}" for k,v in SMART_TYPES.items())
        await update.message.reply_text(f"❗ استخدم: `/smartalert spy support`\n\nالأنواع:\n{types}")
        return
    sym = resolve_symbol(args[0])
    alert_type = args[1].lower() if len(args)>1 else "support"
    if alert_type not in SMART_TYPES:
        await update.message.reply_text("❌ نوع غير معروف")
        return
    al = gsa(uid); al.append({"symbol":sym,"type":alert_type,"triggered":False}); ssa(uid,al)
    await update.message.reply_text(f"✅ تنبيه ذكي لـ `{sym}`: {SMART_TYPES[alert_type]}")

# ─── REPORT ───

async def report_cmd(update, ctx):
    uid = update.effective_user.id
    args = ctx.args
    if args:
        mode = args[0].lower()
        if mode in ("daily", "يومي"):
            srp(uid, "daily")
            await update.message.reply_text("✅ تقرير يومي للمحفظة مفعل")
        elif mode in ("weekly", "أسبوعي"):
            srp(uid, "weekly")
            await update.message.reply_text("✅ تقرير أسبوعي للمحفظة مفعل")
        elif mode in ("off", "إيقاف"):
            srp(uid, "off")
            await update.message.reply_text("✅ تم إيقاف التقارير الدورية")
        else:
            await update.message.reply_text("❗ استخدم: `/report يومي` أو `/report أسبوعي` أو `/report off`")
        return
    await update.message.reply_text(report_settings_text(uid), parse_mode="Markdown", reply_markup=report_settings_kb())

async def send_portfolio_report(uid, ctx):
    p = gp(uid)
    if not p:
        return False
    lines = ["📊 *تقرير المحفظة الدوري*\n"]; total_cost=0; total_val=0
    for i, item in enumerate(p, 1):
        price, chg, pct = get_current_price(item["symbol"])
        if price:
            cost = item["qty"]*item["buy_price"]; val = item["qty"]*price
            pl = val-cost; plp = (pl/cost)*100
            hist, _ = get_stock_data(item["symbol"], period="6mo", fetch_info=False)
            score_str = ""
            trend_str = ""
            rsi_str = ""
            if hist is not None:
                a = analyze(hist)
                score = a.get("score", 0)
                trends = a.get("trends", {})
                short = trends.get("قصير المدى", "—")
                trend_e = "📈" if short == "صاعد" else "📉" if short == "هابط" else "➖"
                score_str = f" | Score: {score}"
                trend_str = f" | {trend_e} {short}"
                rsi_str = f" | RSI: {a.get('rsi', '—')}"
            lines.append(f"{i}. `{item['symbol']}`{trend_str}{rsi_str}{score_str}\n   {item['qty']} @ {pf(item['symbol'], item['buy_price'])} | {pf(item['symbol'], price)}\n   {'🟢' if pl>=0 else '🔴'} {pf(item['symbol'], f'{pl:+,.2f}')} ({plp:+.2f}%)")
            total_cost+=cost; total_val+=val
    if total_cost>0:
        tpl = total_val-total_cost; tplp = (tpl/total_cost)*100
        first_sym = p[0]["symbol"]; cur = ccy(first_sym)
        lines += ["","━━━━━",f"💰 التكلفة: {cur}{total_cost:,.2f}",f"💵 القيمة: {cur}{total_val:,.2f}",f"{'🟢' if tpl>=0 else '🔴'} الربح: {cur}{tpl:+,.2f} ({tplp:+.2f}%)"]
    try:
        await ctx.bot.send_message(uid, "\n".join(lines), parse_mode="Markdown")
        return True
    except Exception:
        return False

async def check_reports(ctx):
    now = datetime.now()
    today_key = now.strftime("%Y-%m-%d")
    iso = now.isocalendar()
    week_key = f"{iso.year}-W{iso.week:02d}"
    for uid_str, mode in list(db["reports"].items()):
        if mode == "off":
            continue
        uid = int(uid_str)
        try:
            if mode == "daily":
                if grs(uid, "daily") == today_key:
                    continue
                if await send_portfolio_report(uid, ctx):
                    srs(uid, "daily", today_key)
            elif mode == "weekly" and now.weekday() == 6:
                if grs(uid, "weekly") == week_key:
                    continue
                if await send_portfolio_report(uid, ctx):
                    srs(uid, "weekly", week_key)
        except Exception as e:
            logging.warning(f"check_reports for {uid} failed: {e}")

# ─── PORTFOLIO ───

async def portfolio_cmd(update, ctx):
    uid = update.effective_user.id
    args = ctx.args
    if args and args[0].lower() == "add":
        if len(args) < 4: await update.message.reply_text("❗ استخدم: `/portfolio add spy 10 450`"); return
        sym = resolve_symbol(args[1])
        try:
            qty = float(args[2]); bp = float(args[3])
        except Exception:
            await update.message.reply_text("❗ الكمية والسعر أرقام"); return
        p = gp(uid); p.append({"symbol":sym,"qty":qty,"buy_price":bp}); sp(uid,p)
        await update.message.reply_text(f"✅ أضيف: `{sym}` × {qty} @ {pf(sym, bp)} = {pf(sym, f'{qty*bp:,.2f}')}")
        return
    if args and args[0].lower() == "remove":
        try:
            idx = int(args[1])-1; p = gp(uid)
            if 0 <= idx < len(p): rem = p.pop(idx); sp(uid,p); await update.message.reply_text(f"✅ حذف: {rem['symbol']}")
            else: await update.message.reply_text("❌ رقم غير صحيح")
        except Exception:
            await update.message.reply_text("❗ استخدم: `/portfolio remove 1`")
        return
    p = gp(uid)
    if not p:
        await ror(update, ctx, "💼 *محفظتي*\n\nفارغة\n\nلإضافة: `/portfolio add spy 10 450`\nلحذف: `/portfolio remove 1`")
        return
    lines = ["💼 *محفظتي*\n"]; total_cost=0; total_val=0
    best_sym = ""; best_pl = -999999; worst_sym = ""; worst_pl = 999999
    for i, item in enumerate(p, 1):
        price, chg, pct = get_current_price(item["symbol"])
        if price:
            cost = item["qty"]*item["buy_price"]; val = item["qty"]*price
            pl = val-cost; plp = (pl/cost)*100
            alloc = 0
            profit_emoji = "🟢" if pl >= 0 else "🔴"
            lines.append(f"{i}. `{item['symbol']}`\n   {item['qty']} @ {pf(item['symbol'], item['buy_price'])} | السعر: {pf(item['symbol'], price)}\n   القيمة: {pf(item['symbol'], f'{val:,.2f}')} | {profit_emoji} {pf(item['symbol'], f'{pl:+,.2f}')} ({plp:+.2f}%)")
            total_cost+=cost; total_val+=val
            if pl > best_pl: best_pl = pl; best_sym = item['symbol']
            if pl < worst_pl: worst_pl = pl; worst_sym = item['symbol']
        else:
            lines.append(f"{i}. ❌ `{item['symbol']}`")
    if total_cost>0:
        tpl = total_val-total_cost; tplp = (tpl/total_cost)*100
        first_sym = p[0]["symbol"] if p else ""
        cur = ccy(first_sym)
        overall_emoji = "🟢" if tpl >= 0 else "🔴"
        lines += [
            "",
            "━━━━━━━",
            f"💰 التكلفة: `{cur}{total_cost:,.2f}`",
            f"💵 القيمة: `{cur}{total_val:,.2f}`",
            f"{overall_emoji} الربح الإجمالي: `{cur}{tpl:+,.2f}` (`{tplp:+.2f}%`)",
        ]
        # Daily P&L
        total_day_pl = 0
        for item in p:
            price, chg, pct = get_current_price(item["symbol"])
            if price:
                day_pl = item["qty"] * price * (pct / 100) if pct else 0
                total_day_pl += day_pl
        lines.append(f"📅 تغيير اليوم: `{cur}{total_day_pl:+,.2f}`")
        # Best/worst
        if best_sym: lines.append(f"🏆 أفضل أصل: `{best_sym}` ({cur}{best_pl:+,.2f})")
        if worst_sym: lines.append(f"📉 أسوأ أصل: `{worst_sym}` ({cur}{worst_pl:+,.2f})")
        # Allocation
        lines.append("")
        lines.append("*📊 توزيع المحفظة:*")
        for item in p:
            price, _, _ = get_current_price(item["symbol"])
            if price:
                val = item["qty"] * price
                alloc_pct = (val / total_val) * 100
                warn = " ⚠️" if alloc_pct > 40 else ""
                lines.append(f"• `{item['symbol']}`: `{alloc_pct:.1f}%`{warn}")
        # Risk assessment
        lines += ["", "*⚠️ تقييم المخاطر:*"]
        if total_val > 0:
            high_alloc = any(
                (item["qty"] * (get_current_price(item["symbol"])[0] or 0)) / total_val > 0.4
                for item in p
            )
            if high_alloc:
                lines.append("• تركيز عالي في أصل واحد — يُفضل التنويع")
            if tplp < -10:
                lines.append(f"• المحفظة منخفضة ({tplp:.1f}%) — راجع الاستراتيجية")
            elif tplp > 20:
                lines.append(f"• المحفظة مرتفعة ({tplp:.1f}%) — فكر في جني أرباح")
            else:
                lines.append("• توزيع مقبول")
    await ror(update, ctx, "\n".join(lines), InlineKeyboardMarkup([[InlineKeyboardButton("📊 رسم بياني", callback_data="portfolio_chart")]]))

# ─── RISK ───

async def risk_cmd(update, ctx):
    args = ctx.args
    if len(args) < 1:
        await update.message.reply_text("❗ استخدم:\n`/risk spy` — تحليل المخاطر\n`/risk spy 10000 1` — مع رأس المال")
        return
    sym = resolve_symbol(args[0])
    capital = float(args[1]) if len(args) > 1 else 0
    risk_pct = float(args[2]) if len(args) > 2 else 1.0
    await ror(update, ctx, f"⏳ تحليل مخاطر `{sym}`...")
    hist, info = get_stock_data(sym, period="1y")
    if hist is None:
        await ror(update, ctx, "❌ الرمز غير صحيح")
        return
    a = analyze(hist)
    name = info.get("shortName") or info.get("longName") or sym.upper()
    price = round(float(hist["Close"].iloc[-1]), 2)
    lines = [f"🛡️ *تحليل المخاطر - {name}*", f"💰 السعر الحالي: `{pf(sym, price)}`", ""]
    
    support = a.get('support')
    resistance = a.get('resistance')
    if support and resistance:
        ds = round(((price - support) / price) * 100, 2)
        dr = round(((resistance - price) / price) * 100, 2)
        lines += [
            f"🔴 أقرب دعم: `{pf(sym, support)}` (أدنى {ds}%)",
            f"🟢 أقرب مقاومة: `{pf(sym, resistance)}` (أعلى {dr}%)",
            "",
            f"🛡️ وقف خسارة مقترح: `{pf(sym, round(support * 0.99, 2))}`",
            f"🎯 الهدف الأول: `{pf(sym, round(resistance * 0.99, 2))}`",
            f"🎯 الهدف الثاني: `{pf(sym, round(price + (resistance - price) * 1.5, 2))}`",
        ]
        rr = abs((resistance - price) / (price - support)) if (price - support) != 0 else 0
        lines.append(f"⚖️ نسبة Risk/Reward: `{rr:.2f}`")
        if rr >= 1.5:
            lines.append(f"✅ الصفقة تستحق المخاطرة")
        else:
            lines.append(f"⚠️ الصفقة لا تستحق المخاطرة — R/R أقل من 1.5")
    
    if capital > 0:
        max_loss = capital * (risk_pct / 100)
        lines += [
            "",
            f"*📊 حجم الصفقة المقترح:*",
            f"💰 رأس المال: `{ccy(sym)}{capital:,.2f}`",
            f"⚠️ نسبة المخاطرة: `{risk_pct}%`",
            f"📉 المبلغ المسموح خسارته: `{ccy(sym)}{max_loss:,.2f}`",
        ]
        if support and price:
            stop_distance = abs(price - support * 0.99)
            if stop_distance > 0:
                shares = int(max_loss / stop_distance)
                position_value = shares * price
                alloc_pct = (position_value / capital) * 100
                lines += [
                    f"📊 عدد الوحدات المقترح: `{shares}`",
                    f"💵 قيمة الصفقة: `{ccy(sym)}{position_value:,.2f}` ({alloc_pct:.1f}% من رأس المال)",
                ]
                if alloc_pct > 20:
                    lines.append(f"⚠️ الصفقة أكبر من 20% من رأس المال")
    
    lines += [
        "",
        "📝 *ملاحظة:*",
        "• استخدم وقف الخسارة دائماً",
        "• لا تخاطر بأكثر من 1-2% من رأس المال في صفقة واحدة",
    ]
    await ror(update, ctx, "\n".join(lines))

# ─── WHY ───

async def why_cmd(update, ctx):
    sym = " ".join(ctx.args)
    if not sym:
        await update.message.reply_text("❗ استخدم: `/why spy`")
        return
    await ror(update, ctx, f"⏳ تحليل `{sym}`...")
    hist, info = get_stock_data(sym, period="1y")
    if hist is None:
        await ror(update, ctx, "❌ الرمز غير صحيح")
        return
    name = info.get("shortName") or info.get("longName") or sym.upper()
    price = round(float(hist["Close"].iloc[-1]), 2)
    a = analyze(hist)
    s = get_signal(a)
    
    lines = [f"🧠 *لماذا {s['verdict']} لـ {name}؟*", f"💰 السعر: `{pf(sym, price)}`", ""]
    
    # Simple explanation in plain language
    reasons = []
    rsi_v = a.get('rsi', 50)
    if rsi_v > 70:
        reasons.append(f"المؤشرات تقول السعر مرتفع (RSI {rsi_v}) والتصحيح احتمال وارد.")
    elif rsi_v < 30:
        reasons.append(f"المؤشرات تقول السعر منخفض (RSI {rsi_v}) وارتداد ممكن.")
    elif rsi_v > 60:
        reasons.append(f"الزخم إيجابي والمؤشرات تدعم الصعود.")
    elif rsi_v < 40:
        reasons.append(f"الزخم سلبي والمؤشرات تدعم الهبوط.")
    else:
        reasons.append(f"المؤشرات محايدة ما تعطي إشارة واضحة.")
    
    if a.get('macd_bullish'):
        reasons.append(f"MACD إيجابي مما يعني أن الزخم الحالي صاعد.")
    else:
        reasons.append(f"MACD سلبي مما يضعف احتمالية الصعود حالياً.")
    
    if a.get('adx') and a['adx'] > 25:
        dir_str = "صاعد" if a.get('plus_di', 0) > a.get('minus_di', 0) else "هابط"
        reasons.append(f"السوق في اتجاه {dir_str} حسب ADX ({a['adx']}).")
    else:
        reasons.append("السوق في تذبذب بدون اتجاه واضح، يفضل الانتظار.")
    
    sma50 = a.get('sma50')
    if sma50:
        pos = "أعلى" if price > sma50 else "تحت"
        reasons.append(f"السعر {pos} المتوسط الحسابي 50 يوم، وهو إشارة {'إيجابية' if price > sma50 else 'سلبية'} على المدى المتوسط.")
    
    lines.append("البوت اختار هذا القرار للأسباب التالية:")
    for r in reasons:
        lines.append(f"• {r}")
    
    if a.get('support') and a.get('resistance'):
        lines += [
            "",
            f"💡 *الخلاصة:*",
            f"أفضل تصرف الآن: انتظار وضوح الاتجاه.",
            f"نقطة الدعم المهمة: `{pf(sym, a['support'])}`",
            f"نقطة المقاومة المهمة: `{pf(sym, a['resistance'])}`",
        ]
    
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔥 توصية", callback_data=f"signal:{sym}"),
         InlineKeyboardButton("📊 تحليل", callback_data=f"analyze:{sym}")],
    ])
    await ror(update, ctx, "\n".join(lines), kb)

# ─── BACKTEST ───

async def backtest_cmd(update, ctx):
    sym = " ".join(ctx.args)
    if not sym:
        await update.message.reply_text("❗ استخدم: `/backtest spy`")
        return
    await ror(update, ctx, f"⏳ اختبار `{sym}`...")
    import yfinance as yf
    try:
        ticker = yf.Ticker(sym)
        hist = ticker.history(period="2y")
    except Exception:
        await ror(update, ctx, "❌ لا توجد بيانات كافية")
        return
    if hist is None or len(hist) < 100:
        await ror(update, ctx, "❌ بيانات غير كافية (تحتاج 100 يوم على الأقل)")
        return
    
    close = hist["Close"]
    # Simple strategy: buy when price > SMA50, sell when price < SMA20
    sma20 = close.rolling(20).mean()
    sma50 = close.rolling(50).mean()
    
    trades = []
    in_position = False
    entry_price = 0
    entry_date = None
    for i in range(50, len(close)):
        if not in_position and close.iloc[i] > sma50.iloc[i] and close.iloc[i] > sma20.iloc[i]:
            in_position = True
            entry_price = close.iloc[i]
            entry_date = hist.index[i]
        elif in_position and (close.iloc[i] < sma20.iloc[i] or close.iloc[i] < sma50.iloc[i]):
            in_position = False
            exit_price = close.iloc[i]
            exit_date = hist.index[i]
            pnl_pct = ((exit_price - entry_price) / entry_price) * 100
            trades.append({"entry": entry_price, "exit": exit_price, "pnl": pnl_pct, "days": (exit_date - entry_date).days})
    
    if not trades:
        await ror(update, ctx, "❌ لا توجد صفقات خلال الفترة")
        return
    
    wins = [t for t in trades if t["pnl"] > 0]
    loss = [t for t in trades if t["pnl"] <= 0]
    total_pnl = sum(t["pnl"] for t in trades)
    avg_win = sum(t["pnl"] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t["pnl"] for t in loss) / len(loss) if loss else 0
    max_dd = 0; peak = 0; running = 0
    for t in trades:
        running += t["pnl"]
        if running > peak: peak = running
        dd = peak - running
        if dd > max_dd: max_dd = dd
    best_trade = max(trades, key=lambda t: t["pnl"])
    worst_trade = min(trades, key=lambda t: t["pnl"])
    
    name = friendly_name(sym)
    win_rate = (len(wins) / len(trades)) * 100
    lines = [
        f"📊 *الاختبار التاريخي - {name}*",
        f"المدة: 2 سنة | الاستراتيجية: تقاطع المتوسطات",
        "",
        f"📈 عدد الصفقات: `{len(trades)}`",
        f"✅ نسبة النجاح: `{win_rate:.0f}%` ({len(wins)} رابحة / {len(loss)} خاسرة)",
        f"📊 متوسط الربح: `{avg_win:+.2f}%` | متوسط الخسارة: `{avg_loss:+.2f}%`",
        f"📉 أكبر هبوط (Max Drawdown): `{max_dd:.1f}%`",
        f"🏆 أفضل صفقة: `{best_trade['pnl']:+.2f}%`",
        f"📉 أسوأ صفقة: `{worst_trade['pnl']:+.2f}%`",
        "",
    ]
    if total_pnl > 0:
        lines.append(f"🟢 الاستراتيجية مربحة تاريخياً: `{total_pnl:+.2f}%`")
    else:
        lines.append(f"🔴 الاستراتيجية غير مربحة تاريخياً: `{total_pnl:+.2f}%`")
    lines += [
        "",
        "⚠️ *ملاحظة:* الأداء السابق لا يضمن الأداء القادم.",
    ]
    await ror(update, ctx, "\n".join(lines))

# ─── MODE ───

async def mode_cmd(update, ctx):
    uid = update.effective_user.id
    args = ctx.args
    if not args:
        current = get_user_mode(uid)
        await update.message.reply_text(f"🛡️ *الوضع الحالي:* `{current}`\n\n`/mode safe` — وضع آمن (توصيات محافظة)\n`/mode normal` — وضع عادي")
        return
    mode = args[0].lower()
    if mode in ("safe", "امن", "آمن"):
        USER_MODE[str(uid)] = "safe"
        await update.message.reply_text("🛡️ *الوضع الآمن* مفعل\n• التوصيات القوية محظورة\n• الثقة المطلوبة أعلى\n• يفضل الانتظار في الحالات غير الواضحة")
    elif mode in ("normal", "عادي"):
        USER_MODE[str(uid)] = "normal"
        await update.message.reply_text("✅ *الوضع العادي* مفعل\nالتوصيات تعمل بشكل طبيعي")
    else:
        await update.message.reply_text("❌ استخدم: `/mode safe` أو `/mode normal`")

# ─── UNKNOWN ───

async def unknown_cmd(update, ctx):
    if update.message and update.message.text.startswith("/"):
        await update.message.reply_text("❌ أمر غير معروف\nاكتب `/start`")

# ─── BUTTONS ───

async def button_handler(update, ctx):
    q = update.callback_query; await q.answer(); data = q.data
    uid = update.effective_user.id
    if data == "main_menu": await send_main_menu(update, ctx); return
    if data == "screener": await screener_cmd(update, ctx); return
    if data == "my_watchlist": await watchlist_cmd(update, ctx); return
    if data == "my_portfolio": await portfolio_cmd(update, ctx); return
    if data == "portfolio_chart": await portfolio_chart_cmd(update, ctx); return
    if data == "quick_analyze":
        await q.edit_message_text("🔍 اختر سهمًا للتحليل:", reply_markup=suggest_kb("analyze")); return
    if data == "quick_price":
        await q.edit_message_text("💰 اختر سهمًا للسعر:", reply_markup=suggest_kb("price")); return
    if data == "quick_signal":
        await q.edit_message_text("🔥 اختر سهمًا للتوصية:", reply_markup=suggest_kb("signal")); return
    if data == "quick_alerts":
        await q.edit_message_text(smart_alerts_text(), parse_mode="Markdown", reply_markup=smart_alerts_kb()); return
    if data == "quick_reports":
        await q.edit_message_text(report_settings_text(uid), parse_mode="Markdown", reply_markup=report_settings_kb()); return
    if data == "show_alerts":
        await q.edit_message_text(alerts_summary_text(uid), parse_mode="Markdown", reply_markup=back_to_menu_kb([InlineKeyboardButton("🔔 إضافة تنبيه ذكي", callback_data="quick_alerts")])); return
    if data == "web_home":
        if not db.is_allowed(str(uid)) and uid != ADMIN_ID:
            await q.edit_message_text("❌ غير مصرح لك بدخول لوحة التحكم.", reply_markup=back_to_menu_kb()); return
        url = dashboard_url_for(uid)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🌐 فتح لوحة التحكم", url=url)],
            [InlineKeyboardButton("↩️ القائمة الرئيسية", callback_data="main_menu")],
        ])
        await q.edit_message_text(f"🌐 *لوحة التحكم*\n\nرابطك الخاص جاهز. افتحه من الزر بالأسفل:\n{url}", parse_mode="Markdown", reply_markup=kb); return
    if data.startswith("smartalert:"):
        parts = data.split(":", 2)
        if len(parts) != 3 or parts[2] not in SMART_TYPES:
            await q.edit_message_text("❌ نوع التنبيه غير معروف.", reply_markup=smart_alerts_kb()); return
        sym = resolve_symbol(parts[1])
        alert_type = parts[2]
        alerts = gsa(uid)
        exists = any(a.get("symbol") == sym and a.get("type") == alert_type and not a.get("triggered") for a in alerts)
        if not exists:
            alerts.append({"symbol": sym, "type": alert_type, "triggered": False})
            ssa(uid, alerts)
        status = "موجود مسبقًا" if exists else "تمت إضافته"
        await q.edit_message_text(
            f"✅ {status}: {sym} - {SMART_TYPES[alert_type]}",
            parse_mode="Markdown",
            reply_markup=back_to_menu_kb([InlineKeyboardButton("📋 تنبيهاتي", callback_data="show_alerts"), InlineKeyboardButton("🔔 إضافة آخر", callback_data="quick_alerts")])
        ); return
    if data == "ai_clear":
        sc(uid, [])
        await q.edit_message_text("✅ تم مسح المحادثة")
        return
    if data == "ai_continue":
        conv = gc(uid)
        last_q = ""
        for m in reversed(conv):
            if m["role"] == "user":
                last_q = m["content"][:200]
                break
        await q.edit_message_text(f"🤖 *متابعة المحادثة*\nآخر سؤال: {last_q}\n\nاكتب ردك مباشرة", parse_mode="Markdown")
        return
    if data.startswith("report_"):
        mode = data.replace("report_", "")
        srp(uid, mode)
        labels = {"daily": "📅 يومي", "weekly": "📆 أسبوعي", "off": "⏹ معطل"}
        await q.edit_message_text(f"✅ تم تفعيل التقرير {labels.get(mode, mode)}", parse_mode="Markdown")
        return
    # Parse callback data - support both new (action:symbol) and old (action_symbol) formats
    action = ""
    symbol = ""
    if ":" in data:
        parts = data.split(":", 1)
        action = parts[0] + "_"
        symbol = parts[1]
    else:
        map_actions = {"price_": send_price,"signal_": send_signal,"analyze_": send_analysis,"chart_": send_chart,"news_": send_news,"levels_": send_levels,"market_": send_market}
        for prefix, handler in map_actions.items():
            if data.startswith(prefix):
                action = prefix
                symbol = data.replace(prefix, "")
                break
    if action in {"price_","signal_","analyze_","chart_","news_","levels_","market_"}:
        handler = {"price_": send_price,"signal_": send_signal,"analyze_": send_analysis,"chart_": send_chart,"news_": send_news,"levels_": send_levels,"market_": send_market}[action]
        try:
            if action == "market_": await handler(update, ctx, symbol)
            else: await handler(update, ctx, symbol)
        except Exception as e:
            logging.error(f"callback error: data={data}, action={action}, symbol={symbol}, error={e}")
            try:
                await q.edit_message_text("❌ تعذر تنفيذ هذا الزر حالياً.")
            except Exception:
                pass
            if ADMIN_ID:
                try:
                    await ctx.bot.send_message(ADMIN_ID,
                        f"🔴 *خطأ في callback*"
                        f"\n📋 نوع: `{type(e).__name__}`"
                        f"\n📝 callback_data: `{data}`"
                        f"\n🔧 الأمر: `{action}`"
                        f"\n📌 الرمز: `{symbol}`"
                        f"\n👤 المستخدم: `{uid}`"
                        f"\n⏱ الوقت: `{datetime.now().strftime('%Y-%m-%d %H:%M')}`"
                        f"\n📝 الرسالة: `{str(e)[:300]}`",
                        parse_mode="Markdown")
                except Exception:
                    pass
        return

# ─── SMART ALERT CHECKER ───

async def check_smart_alerts(ctx):
    for uid_str, alerts in list(db["smart_alerts"].items()):
        uid = int(uid_str)
        for alert in alerts:
            if alert.get("triggered"): continue
            try:
                hist, _ = get_stock_data(alert["symbol"], period="6mo")
                if hist is None or len(hist) < 50: continue
                a = analyze(hist); s = get_signal(a)
                triggered = False; msg = ""
                t = alert["type"]
                if t == "support" and a.get("support"):
                    if float(hist["Close"].iloc[-1]) < a["support"] * 1.01: triggered = True; msg = f"⚠️ {alert['symbol']} كسر الدعم عند {pf(alert['symbol'], a['support'])}!"
                elif t == "resistance" and a.get("resistance"):
                    if float(hist["Close"].iloc[-1]) > a["resistance"] * 0.99: triggered = True; msg = f"🚀 {alert['symbol']} كسر المقاومة عند {pf(alert['symbol'], a['resistance'])}!"
                elif t == "golden" and a.get("golden_cross"): triggered = True; msg = f"🟢 {alert['symbol']} تقاطع ذهبي!"
                elif t == "death" and a.get("golden_cross") is False: triggered = True; msg = f"🔴 {alert['symbol']} تقاطع موت!"
                elif t == "rsi_oversold" and a.get("rsi",50) < 35: triggered = True; msg = f"🟢 {alert['symbol']} RSI في تشبع بيعي ({a['rsi']})!"
                elif t == "rsi_overbought" and a.get("rsi",50) > 65: triggered = True; msg = f"🔴 {alert['symbol']} RSI في تشبع شرائي ({a['rsi']})!"
                elif t == "breakout":
                    if a.get("resistance") and a.get("volume_spike"):
                        close_val = float(hist["Close"].iloc[-1])
                        if close_val > a["resistance"] * 0.99:
                            triggered = True
                            msg = f"🚀 {alert['symbol']} اختراق مقاومة ({pf(alert['symbol'], a['resistance'])}) بحجم {a.get('volume_ratio')}x!"
                elif t == "breakdown":
                    if a.get("support") and a.get("volume_spike"):
                        close_val = float(hist["Close"].iloc[-1])
                        if close_val < a["support"] * 1.01:
                            triggered = True
                            msg = f"⚠️ {alert['symbol']} كسر دعم ({pf(alert['symbol'], a['support'])}) بحجم {a.get('volume_ratio')}x!"
                elif t == "volume_spike":
                    if a.get("volume_ratio", 1) > 2:
                        triggered = True
                        msg = f"📊 {alert['symbol']} ارتفاع حجم مفاجئ! ({a.get('volume_ratio')}x)"
                elif t == "trend_change":
                    trends = a.get("trends", {})
                    prev = alert.get("prev_trends", {})
                    for period in ["قصير المدى", "متوسط المدى"]:
                        cur_dir = trends.get(period)
                        prev_dir = prev.get(period)
                        if cur_dir and prev_dir and cur_dir != prev_dir:
                            triggered = True
                            msg = f"🔄 {alert['symbol']} تغير الاتجاه {period}: {prev_dir} → {cur_dir}"
                            break
                    alert["prev_trends"] = trends
                elif t == "macd_cross" and a.get("macd_bullish") is not None:
                    prev = alert.get("prev_macd")
                    if prev is not None and prev != a["macd_bullish"]:
                        triggered = True
                        cross_type = "إيجابي 🟢" if a["macd_bullish"] else "سلبي 🔴"
                        msg = f"🔄 {alert['symbol']} تقاطع MACD {cross_type}"
                    alert["prev_macd"] = a["macd_bullish"]
                elif t == "price_near_support" and a.get("support"):
                    close_val = float(hist["Close"].iloc[-1])
                    dist = abs(close_val - a["support"]) / a["support"] * 100
                    if dist < 1.5:
                        triggered = True
                        msg = f"📉 {alert['symbol']} قرب الدعم ({pf(alert['symbol'], a['support'])}) بفارق {dist:.1f}%"
                elif t == "price_near_resistance" and a.get("resistance"):
                    close_val = float(hist["Close"].iloc[-1])
                    dist = abs(a["resistance"] - close_val) / close_val * 100
                    if dist < 1.5:
                        triggered = True
                        msg = f"📈 {alert['symbol']} قرب المقاومة ({pf(alert['symbol'], a['resistance'])}) بفارق {dist:.1f}%"
                if triggered:
                    alert["triggered"] = True
                    try:
                        await ctx.bot.send_message(uid, f"🔔 *تنبيه ذكي!*\n{msg}\n🕐 {datetime.now().strftime('%H:%M')}", parse_mode="Markdown")
                    except Exception:
                        pass
            except Exception as e:
                logging.error(f"check_smart_alerts error for {alert.get('symbol')}: {e}")
    save_data(db)

# ─── SCHEDULER (Auto market update) ───

async def scheduled_update(ctx):
    for uid_str in list(db["watchlists"].keys()):
        uid = int(uid_str)
        syms = gw(uid)
        if not syms: continue
        lines = ["⏰ *تحديث دوري*\n"]
        for sym in syms:
            price, chg, pct = get_current_price(sym)
            if price: lines.append(price_line(sym, sym, price, chg, pct))
        if len(lines) > 1:
            try:
                await ctx.bot.send_message(uid, "\n".join(lines), parse_mode="Markdown")
            except Exception as e:
                logging.warning(f"scheduled_update send to {uid} failed: {e}")

# ─── SUBSCRIPTION CHECK ───

async def check_subscriptions(ctx):
    subs = db.get("subscriptions", {})
    now = datetime.now()
    for suid, expiry_str in list(subs.items()):
        uid = int(suid)
        try:
            end = datetime.fromisoformat(expiry_str)
            remaining = (end - now).days
            if remaining == 3:
                await ctx.bot.send_message(uid,
                    f"⚠️ *تنبيه: اشتراكك على وشك الانتهاء*"
                    f"\nباقي `{remaining}` أيام على انتهاء اشتراكك."
                    f"\nللتواصل مع @hidanx11 للتجديد.",
                    parse_mode="Markdown")
                if ADMIN_ID:
                    await ctx.bot.send_message(ADMIN_ID,
                        f"⚠️ المستخدم `{uid}` - باقي `{remaining}` أيام على انتهاء الاشتراك",
                        parse_mode="Markdown")
            elif remaining == 1:
                await ctx.bot.send_message(uid,
                    f"⚠️ *تنبيه: اشتراكك سينتهي غداً*"
                    f"\nباقي يوم واحد فقط."
                    f"\nللتواصل مع @hidanx11 للتجديد.",
                    parse_mode="Markdown")
                if ADMIN_ID:
                    await ctx.bot.send_message(ADMIN_ID,
                        f"⚠️ المستخدم `{uid}` - باقي يوم واحد على انتهاء الاشتراك",
                        parse_mode="Markdown")
            elif remaining == 0:
                wl = db.get("whitelist", [])
                if suid in wl:
                    wl.remove(suid)
                    save_data(db)
                    await ctx.bot.send_message(uid,
                        f"🔴 *انتهى اشتراكك*"
                        f"\nللتواصل مع @hidanx11 للتجديد.",
                        parse_mode="Markdown")
                subs.pop(suid, None)
                save_data(db)
                if ADMIN_ID:
                    await ctx.bot.send_message(ADMIN_ID,
                        f"🔴 انتهى اشتراك المستخدم `{uid}` وتم إزالته من القائمة البيضاء.",
                        parse_mode="Markdown")
        except Exception as e:
            logging.error(f"check_subscriptions error for {suid}: {e}")

# ─── ERROR ───

async def error_handler(update, ctx):
    global ERROR_COUNT
    ERROR_COUNT += 1
    logging.error(f"Exception: {ctx.error}")
    # Suppress Conflict errors (normal during restarts/deployments)
    if "Conflict" in str(ctx.error):
        return
    err_msg = str(ctx.error)[:300]
    uid = str(update.effective_user.id) if update and update.effective_user else "—"
    cmd = update.message.text.split()[0] if update and update.message and update.message.text else "—"
    sym = update.message.text.split()[1] if update and update and update.message and update.message.text and len(update.message.text.split()) > 1 else "—"
    if ADMIN_ID and admin_settings(ADMIN_ID).get("errors", True):
        try:
            await ctx.bot.send_message(ADMIN_ID,
                f"🔴 *خطأ في البوت*"
                f"\n📋 نوع: `{type(ctx.error).__name__}`"
                f"\n🔧 الأمر: `{cmd}`"
                f"\n📌 الرمز: `{sym}`"
                f"\n👤 المستخدم: `{uid}`"
                f"\n⏱ الوقت: `{datetime.now().strftime('%Y-%m-%d %H:%M')}`"
                f"\n📝 الرسالة: `{err_msg}`",
                parse_mode="Markdown")
        except Exception:
            pass
    try:
        if update and update.effective_chat:
            await ctx.bot.send_message(update.effective_chat.id, "❌ حدث خطأ")
    except Exception:
        pass

async def sectors_cmd(update, ctx):
    """Compare sector performance"""
    sectors = {
        "التقنية": ["AAPL", "MSFT", "NVDA", "GOOGL", "META", "AMD", "INTC"],
        "البنوك": ["JPM", "BAC", "WFC", "C", "GS"],
        "الطاقة": ["XOM", "CVX", "COP", "SLB", "OXY"],
        "الصحة": ["UNH", "JNJ", "PFE", "ABBV", "MRK"],
        "الذهب": ["GLD", "IAU", "GDX", "NEM", "ABX"],
        "العملات الرقمية": ["BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD"],
    }
    lines = ["📊 *مقارنة أداء القطاعات*", ""]
    for sector, symbols in sectors.items():
        total_change = 0
        count = 0
        for sym in symbols:
            try:
                price, _, pct = get_current_price(sym)
                if price and pct is not None:
                    total_change += pct
                    count += 1
            except Exception:
                pass
        if count > 0:
            avg = total_change / count
            emoji = "🟢" if avg > 0 else "🔴" if avg < 0 else "⚪"
            lines.append(f"{emoji} *{sector}*: {avg:+.2f}%")
    await ror(update, ctx, "\n".join(lines))

async def accuracy_cmd(update, ctx):
    """Track signal accuracy statistics"""
    uid = update.effective_user.id
    acc = db.get("signal_accuracy", {})
    total_signals = acc.get(str(uid), {}).get("total", 0)
    correct = acc.get(str(uid), {}).get("correct", 0)
    wrong = acc.get(str(uid), {}).get("wrong", 0)
    if total_signals == 0:
        await update.message.reply_text("📊 *دقة التوصيات*\n\nلا توجد إحصائيات بعد.\nاستخدم `/signal` للحصول على توصيات وشاركنا بالنتيجة.")
        return
    rate = (correct / total_signals * 100) if total_signals > 0 else 0
    lines = [
        "📊 *دقة التوصيات*",
        "",
        f"✅ صحيحة: `{correct}`",
        f"❌ خاطئة: `{wrong}`",
        f"📊 الإجمالي: `{total_signals}`",
        f"🎯 الدقة: `{rate:.1f}%`",
    ]
    await ror(update, ctx, "\n".join(lines))

async def sizing_cmd(update, ctx):
    """Position sizing calculator"""
    args = ctx.args
    if len(args) < 3:
        await update.message.reply_text(
            "🧮 *Position Sizing*\n\n"
            "احسب حجم الصفقة المناسب بناءً على إدارة المخاطر.\n\n"
            "`/sizing رأس_المال المخاطرة% سعر_الدخول`\n"
            "مثال: `/sizing 100000 2 450`\n"
            "=> رأس المال 100,000$, مخاطرة 2%, دخول بسعر 450\n\n"
            "`/sizing رأس_المال المخاطرة% سعر_الدخول سعر_وقف`\n"
            "مثال: `/sizing 100000 2 450 430`\n"
            "=> مع تحديد وقف الخسارة")
        return
    try:
        capital = float(args[0])
        risk_pct = float(args[1]) / 100
        entry = float(args[2])
        stop = float(args[3]) if len(args) > 3 else entry * 0.95
    except ValueError:
        await update.message.reply_text("❌ الأرقام غير صالحة")
        return
    risk_amount = capital * risk_pct
    risk_per_share = abs(entry - stop)
    if risk_per_share == 0:
        await update.message.reply_text("❌ سعر الدخول ووقف الخسارة متساويان")
        return
    position_size = risk_amount / risk_per_share
    position_value = position_size * entry
    risk_percent = (risk_amount / capital) * 100
    reward_risk = abs(entry - stop) if entry > stop else abs(stop - entry)
    lines = [
        "🧮 *حجم الصفقة المناسب*",
        "",
        f"💰 رأس المال: `${capital:,.2f}`",
        f"⚠️ المخاطرة: `{risk_pct*100:.1f}%` = `${risk_amount:,.2f}`",
        f"📉 سعر الدخول: `${entry:.2f}`",
        f"🛑 وقف الخسارة: `${stop:.2f}`",
        f"📏 المسافة: `${risk_per_share:.2f}` ({risk_per_share/entry*100:.1f}%)",
        "",
        f"📊 *حجم الصفقة:* `{position_size:.2f}` سهم",
        f"💵 قيمة الصفقة: `${position_value:,.2f}`",
        f"📊 نسبة المخاطرة: `{risk_percent:.1f}%`",
    ]
    await ror(update, ctx, "\n".join(lines))

async def crypto_cmd(update, ctx):
    uid = update.effective_user.id
    args = ctx.args or []
    if args:
        data = get_crypto_price(args[0])
        if not data or not data.get("price"):
            await update.message.reply_text(f"❌ رمز غير معروف: {args[0]}")
            return
        chg = data.get("change_pct_24h", 0)
        emoji = "🟢" if chg >= 0 else "🔴"
        def fmt(n):
            if not n: return "—"
            if n >= 1e12: return f"${n/1e12:.2f}T"
            if n >= 1e9: return f"${n/1e9:.2f}B"
            if n >= 1e6: return f"${n/1e6:.2f}M"
            return f"${n:,.2f}"
        msg = (
            f"₿ *{data['symbol']}*\n\n"
            f"💵 السعر: `${data['price']:,.2f}`\n"
            f"{emoji} التغيير 24h: `{chg:+.2f}%`\n"
            f"🏦 القيمة السوقية: `{fmt(data.get('market_cap'))}`\n"
            f"📊 الحجم: `{fmt(data.get('volume_24h'))}`\n"
            f"🕐 آخر تحديث: {data.get('updated', '')}\n\n"
            f"🌐 لوحة العملات: /web"
        )
        await update.message.reply_text(msg, parse_mode="Markdown")
        return
    coins = get_top_crypto(10)
    if not coins:
        await update.message.reply_text("❌ تعذر جلب البيانات")
        return
    lines = ["₿ *أبرز 10 عملات رقمية*\n"]
    for c in coins:
        pct = c.get("change_pct_24h", 0) or 0
        arrow = "🟢" if pct >= 0 else "🔴"
        lines.append(f"{arrow} *{c['symbol']}* — `${c['price']:,.2f}` ({pct:+.2f}%)")
    lines.append("\nللحصول على سعر محدد: `/crypto btc`")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def scan_cmd(update, ctx):
    uid = update.effective_user.id
    msg = await update.message.reply_text("🔍 جاري مسح السوق...")
    try:
        from stock_data import get_screener
        results = get_screener() or []
        if not results:
            await msg.edit_text("❌ تعذر جلب بيانات السوق حالياً")
            return
        top_buy = []
        top_sell = []
        for name, sym, price, chg, pct in results[:30]:
            hist, _ = get_stock_data(sym, period="6mo", fetch_info=False)
            if hist is None: continue
            a = analyze(hist)
            score = a.get("score", 0)
            rsi = a.get("rsi", 50)
            if score > 4 and rsi < 65:
                top_buy.append((sym, score, rsi, pct))
            elif score < -4 and rsi > 35:
                top_sell.append((sym, score, rsi, pct))
            if len(top_buy) >= 5 and len(top_sell) >= 5:
                break
        lines = ["🔍 *نتائج مسح السوق*\n"]
        if top_buy:
            lines.append("🟢 *فرص شراء:*")
            for sym, sc, rsi, pct in top_buy[:5]:
                lines.append(f"• `{sym}` — نقاط: {sc:.1f} | RSI: {rsi:.0f} | تغير: {pct:+.2f}%")
        if top_sell:
            lines.append("\n🔴 *فرص بيع:*")
            for sym, sc, rsi, pct in top_sell[:5]:
                lines.append(f"• `{sym}` — نقاط: {sc:.1f} | RSI: {rsi:.0f} | تغير: {pct:+.2f}%")
        if not top_buy and not top_sell:
            lines.append("لا توجد فرص واضحة حالياً.")
        lines.append("\nلمزيد من التفاصيل: `/signal sym`")
        await msg.edit_text("\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        logging.error(f"scan_cmd error: {e}")
        await msg.edit_text(f"❌ حدث خطأ: {e}")

async def broadcast_cmd(update, ctx):
    """Admin broadcast to all users"""
    uid = update.effective_user.id
    if uid != ADMIN_ID:
        return
    msg = " ".join(ctx.args)
    if not msg:
        await update.message.reply_text("❗ استخدم: `/broadcast الرسالة`\nمثال: `/broadcast تم إضافة ميزة جديدة! 🚀`\n\nللترقية: `/broadcast_upgrade`")
        return
    users = db["stats"].get("users", [])
    sent = 0
    failed = 0
    await update.message.reply_text(f"📤 جاري إرسال الإشعار إلى `{len(users)}` مستخدم...")
    for suid in users:
        try:
            await ctx.bot.send_message(int(suid), f"📢 *إشعار من البوت*\n\n{msg}", parse_mode="Markdown")
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)
    await update.message.reply_text(f"✅ تم: {sent} | ❌ فشل: {failed}")

async def portfolio_chart_cmd(update, ctx):
    """Generate portfolio performance chart with benchmark comparison"""
    uid = update.effective_user.id
    p = gp(uid)
    if not p:
        await ror(update, ctx, "💼 *محفظتي*\n\nفارغة\nلإضافة: `/portfolio add spy 10 450`")
        return
    msg = await ror(update, ctx, "⏳ جاري إنشاء الرسم البياني...")
    try:
        await generate_portfolio_chart(uid, msg, ctx)
    except Exception as e:
        logging.error(f"portfolio_chart error: {e}")
        if msg and hasattr(msg, "edit_text"):
            await msg.edit_text(f"❌ حدث خطأ: {e}")
        else:
            await ctx.bot.send_message(uid, f"❌ حدث خطأ: {e}")

async def generate_portfolio_chart(uid, msg, ctx):
    """Generate and send portfolio performance chart"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    import io
    from PIL import Image
    p = gp(uid)
    if not p:
        return
    # Get price history for all holdings and benchmark
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 10), gridspec_kw={"height_ratios": [3, 1]})
    fig.patch.set_facecolor("#1e1e1e")
    colors = ["#00ff88", "#ff6b6b", "#4ecdc4", "#ffe66d", "#a8e6cf", "#ff8b94", "#95e1d3", "#f38181"]
    total_value_history = None
    benchmark_data = None
    total_invested = 0
    for item in p:
        hist, info = get_stock_data(item["symbol"], period="1y")
        if hist is None or len(hist) < 5:
            continue
        price_hist = hist["Close"]
        shares = item["qty"]
        value_hist = price_hist * shares
        total_invested += shares * item["buy_price"]
        ax1.plot(hist.index, value_hist, label=f"{item['symbol']}", linewidth=1.5, alpha=0.7)
        if total_value_history is None:
            total_value_history = value_hist.copy()
        else:
            total_value_history = total_value_history.add(value_hist, fill_value=0)
    if total_value_history is not None and len(total_value_history) > 1:
        total_value_history.name = "المحفظة"
        # Normalize to 100
        norm_portfolio = total_value_history / total_value_history.iloc[0] * 100
        # Get SPY benchmark
        spy_hist, _ = get_stock_data("SPY", period="1y")
        if spy_hist is not None and len(spy_hist) > 5:
            spy_close = spy_hist["Close"]
            spy_norm = spy_close / spy_close.iloc[0] * 100
            ax1.plot(spy_hist.index, spy_norm, label="SPY (مقارنة)", linewidth=2, color="white", linestyle="--")
        ax1.plot(total_value_history.index, norm_portfolio, label="المحفظة", linewidth=2.5, color="#00ff88")
    ax1.set_facecolor("#2d2d2d")
    ax1.legend(loc="upper left", facecolor="#1e1e1e", edgecolor="white", labelcolor="white", fontsize=10)
    ax1.grid(True, alpha=0.15)
    ax1.tick_params(colors="white", labelsize=9)
    ax1.set_title("أداء المحفظة", color="white", fontsize=14, pad=15)
    ax1.set_ylabel("القيمة", color="white")
    # P&L bar chart
    symbols_list = []
    pl_values = []
    for item in p:
        price, _, _ = get_current_price(item["symbol"])
        if price:
            cost = item["qty"] * item["buy_price"]
            val = item["qty"] * price
            pl = ((val - cost) / cost) * 100 if cost else 0
            symbols_list.append(item["symbol"])
            pl_values.append(pl)
    if pl_values:
        bar_colors = ["#00ff88" if v >= 0 else "#ff6b6b" for v in pl_values]
        bars = ax2.barh(range(len(symbols_list)), pl_values, color=bar_colors, height=0.6)
        ax2.set_yticks(range(len(symbols_list)))
        ax2.set_yticklabels(symbols_list, color="white", fontsize=10)
        ax2.axvline(x=0, color="white", linewidth=0.8)
        ax2.set_facecolor("#2d2d2d")
        ax2.tick_params(colors="white", labelsize=9)
        ax2.grid(True, alpha=0.15, axis="x")
        ax2.set_title("نسبة الربح/الخسارة", color="white", fontsize=12)
        for i, (bar, v) in enumerate(zip(bars, pl_values)):
            ax2.text(v + (1 if v >= 0 else -1), i, f"{v:+.1f}%", color="white", va="center", fontsize=9)
    plt.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=120, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    await ctx.bot.send_photo(chat_id=uid, photo=buf, caption="📊 *رسم أداء المحفظة*\nمقارنة مع SPY", parse_mode="Markdown")
    if msg and hasattr(msg, "delete"):
        await msg.delete()

# ─── POST INIT ───

async def post_init(app):
    global BOT_START_TIME
    BOT_START_TIME = datetime.now()
    if ADMIN_ID and admin_settings(ADMIN_ID).get("startup", True):
        try:
            await app.bot.send_message(ADMIN_ID,
                f"✅ *تم تشغيل البوت بنجاح* 🟢"
                f"\n⏱ وقت التشغيل: `{BOT_START_TIME.strftime('%Y-%m-%d %H:%M:%S')}`"
                f"\n👥 إجمالي المستخدمين: `{len(db['stats']['users'])}`",
                parse_mode="Markdown")
        except Exception:
            pass
    if app.job_queue:
        app.job_queue.run_repeating(check_alerts, interval=60, first=10)
        app.job_queue.run_repeating(check_smart_alerts, interval=300, first=30)
        app.job_queue.run_repeating(scheduled_update, interval=3600, first=120)
        app.job_queue.run_repeating(check_reports, interval=3600, first=60)
        app.job_queue.run_repeating(check_subscriptions, interval=3600, first=30)
        app.job_queue.run_repeating(update_all_competitions, interval=1800, first=60)

async def check_alerts(ctx):
    for uid_str, alerts in list(db["alerts"].items()):
        uid = int(uid_str)
        for alert in alerts:
            try:
                price, _, _ = get_current_price(alert["symbol"])
                if price is None: continue
                if alert["last_price"] is not None:
                    chg = abs((price - alert["last_price"]) / alert["last_price"]) * 100
                    if chg >= alert["threshold"]:
                        d = "📈 صاعد" if price > alert["last_price"] else "📉 هابط"
                        try:
                            await ctx.bot.send_message(uid, f"🔄 *تنبيه حركة سعرية!*\n{alert['symbol']}: `{pf(alert['symbol'], price)}`\n{d} {chg:.2f}%\n{ts()}", parse_mode="Markdown")
                        except TelegramError:
                            pass
                alert["last_price"] = price
            except Exception as e:
                logging.error(f"check_alerts error for {alert.get('symbol')}: {e}")
    # Advanced alerts (target price + daily change %)
    for uid_str, alerts in list(db["advanced_alerts"].items()):
        uid = int(uid_str)
        remaining = []
        for alert in alerts:
            try:
                price, change_pct, _ = get_current_price(alert["symbol"])
                if price is None:
                    remaining.append(alert)
                    continue
                triggered = False
                msg = ""
                if alert["type"] == "target":
                    prev = alert.get("last_price", price)
                    # Trigger on cross (both directions)
                    if (prev <= alert["value"] <= price) or (prev >= alert["value"] >= price):
                        triggered = True
                        dir_str = "صاعد" if price > prev else "هابط"
                        msg = f"🎯 *تنبيه سعر مستهدف!*\n{alert['symbol']}: `{pf(alert['symbol'], price)}`\n(المستهدف: {pf(alert['symbol'], alert['value'])})\nالاتجاه: {dir_str}\n{ts()}"
                    alert["last_price"] = price
                elif alert["type"] == "day_change":
                    if change_pct is not None and abs(change_pct) >= alert["value"]:
                        triggered = True
                        dir_str = "📈 صاعد" if change_pct > 0 else "📉 هابط"
                        msg = f"📊 *تنبيه تغيير يومي!*\n{alert['symbol']}: `{pf(alert['symbol'], price)}`\nالتغيير: {change_pct:+.2f}%\nالحد: {alert['value']:+.2f}%\n{dir_str}\n{ts()}"
                if triggered:
                    try:
                        await ctx.bot.send_message(uid, msg, parse_mode="Markdown")
                    except TelegramError:
                        pass
                else:
                    remaining.append(alert)
            except Exception as e:
                logging.error(f"check_advanced_alerts error for {alert.get('symbol')}: {e}")
                remaining.append(alert)
        db["advanced_alerts"][uid_str] = remaining
    save_data(db)

# ─── WEB DASHBOARD COMMAND ───

async def web_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    name = update.effective_user.first_name or ""
    # Check if user is allowed
    if not db.is_allowed(str(uid)) and uid != ADMIN_ID:
        await update.message.reply_text("❌ غير مصرح لك بدخول لوحة التحكم.")
        return
    url = db.get("config", {}).get("dashboard_url", "")
    if not url:
        from config import DASHBOARD_URL
        url = DASHBOARD_URL
    if not url:
        url = "https://stock-bot-production-7ac8.up.railway.app"
    dashboard_url = f"{url}/user/{uid}"
    msg = (
        f"🎯 مرحباً {name}!\n\n"
        f"رابط لوحة التحكم الخاصة بك:\n"
        f"🔗 {dashboard_url}\n\n"
        f"يمكنك من خلالها متابعة:\n"
        f"• أسعار الأسهم 📊\n"
        f"• توصيات الشراء والبيع 📈\n"
        f"• محفظتك الاستثمارية 💼\n"
        f"• قائمة المتابعة 📋\n"
        f"• الأخبار والتحليلات 📰\n"
        f"• التداول الافتراضي 💰\n\n"
        f"احفظ الرابط للوصول السريع ✅"
    )
    await update.message.reply_text(msg, disable_web_page_preview=True)

# ─── APP COMMAND (Telegram Mini App) ───

async def app_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    url = dashboard_url_for(uid)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🌐 فتح لوحة التحكم", web_app={"url": url})]
    ])
    await update.message.reply_text(
        "🌐 *لوحة التحكم*\n\nاضغط الزر لفتح لوحة التحكم داخل تيليجرام",
        parse_mode="Markdown", reply_markup=kb
    )

# ─── TRADING COMPETITIONS ───

async def update_all_competitions(ctx):
    db.reset_competitions()
    vps = db.get("virtual_portfolios", {})
    for uid_str, vp in vps.items():
        try:
            cash = vp.get("cash", 0)
            holdings = vp.get("holdings", [])
            total_value = cash
            for h in holdings:
                price, _, _ = get_current_price(h["symbol"])
                if price:
                    total_value += price * h["qty"]
            initial_capital = 100000
            pnl_pct = (total_value / initial_capital * 100) - 100
            db.update_competition_entry(uid_str, pnl_pct)
        except Exception as e:
            logging.error(f"update_all_competitions error for {uid_str}: {e}")

async def comp_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    week = db.get_competition_week()
    entries = db.get_leaderboard(10)
    user_info = db.get("user_info", {})
    lines = [f"🏆 *مسابقة التداول الأسبوعية*\n\nالأسبوع: {week}\n"]
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    for i, (suid, data) in enumerate(entries, 1):
        info = user_info.get(suid, {})
        uname = info.get("username", "")
        name = info.get("name", f"مستخدم {suid}")
        display = f"@{uname}" if uname else name
        medal = medals.get(i, f"{i}.")
        lines.append(f"{medal} {display} — {data['pnl_pct']:+.1f}%")
    user_rank = db.get_user_rank(str(uid))
    if user_rank:
        lines.append(f"\nمركزك: #{user_rank['rank']} من {user_rank['total']} متداول ({user_rank['pnl_pct']:+.1f}%)")
    else:
        lines.append("\nلم تشارك في المسابقة بعد. استخدم المحفظة الافتراضية للمشاركة!")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

# ─── MAIN ───

def main():
    import socket as _socket
    try:
        _socket.getaddrinfo("api.telegram.org", 443)
    except _socket.gaierror:
        logging.warning("DNS failed for api.telegram.org — will use fallback resolver")
        import httpx
        import socket as _socket2
        from telegram.request import HTTPXRequest
        _orig_getaddrinfo = _socket2.getaddrinfo
        def _patched_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
            if host == "api.telegram.org":
                return [(2, 1, 6, "", ("149.154.167.220", port)),
                        (2, 2, 17, "", ("149.154.167.220", port))]
            return _orig_getaddrinfo(host, port, family, type, proto, flags)
        _socket2.getaddrinfo = _patched_getaddrinfo
    builder = Application.builder().token(BOT_TOKEN).post_init(post_init)
    if PROXY_URL: builder = builder.proxy_url(PROXY_URL).get_updates_proxy_url(PROXY_URL)
    app = builder.build()
    for cmd, func in [
        ("start",start),("help",help_cmd),("users",users_cmd),("price",price_cmd),("signal",signal_cmd),("analyze",analyze_cmd),
        ("advice",advice_cmd),("ai",ai_cmd),("levels",levels_cmd),("trend",trend_cmd),("compare",compare_cmd),
        ("chart",chart_cmd),("news",news_cmd),("market",market_cmd),("screener",screener_cmd),
        ("watch",watch_cmd),("unwatch",unwatch_cmd),("watchlist",watchlist_cmd),
        ("alert",alert_cmd),("alerts",alerts_cmd),("alert_remove",alert_remove_cmd),("smartalert",smartalert_cmd),
        ("portfolio",portfolio_cmd),("predict",predict_cmd),("report",report_cmd),("live",live_cmd),
        ("calc",calc_cmd),("stats",stats_cmd),("admin",admin_alert_cmd),("whitelist",whitelist_cmd),
        ("risk",risk_cmd),("why",why_cmd),("backtest",backtest_cmd),("mode",mode_cmd),
        ("cancel",cancel_cmd),("exit",cancel_cmd),("plan",plan_cmd),("discount",discount_cmd),("sub",sub_cmd),
        ("broadcast",broadcast_cmd),("pchart",portfolio_chart_cmd),
        ("sectors",sectors_cmd),("accuracy",accuracy_cmd),("sizing",sizing_cmd),("crypto",crypto_cmd),("scan",scan_cmd),
        ("web",web_cmd),("dashboard",web_cmd),
        ("app",app_cmd),("comp",comp_cmd),
    ]: app.add_handler(CommandHandler(cmd, tracked(func, cmd)))
    app.add_handler(MessageHandler(filters.COMMAND, unknown_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat_handler))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_error_handler(error_handler)
    print("Bot running...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
