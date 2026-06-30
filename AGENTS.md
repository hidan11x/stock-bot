# Stock Bot — دليل المساعد الذكي

## هيكل المشروع
```
C:\Users\hidan\Documents\stock-bot\
├── main.py              # بوت التليجرام (~3300 سطر)
├── dashboard.py         # Flask web dashboard (كل الصفحات + API)
├── database.py          # SQLite بقاعدة بيانات
├── config.py            # إعدادات البيئة (BOT_TOKEN, ADMIN_ID, ...)
├── stock_data.py        # جلب بيانات YFinance
├── analysis.py          # المؤشرات الفنية (RSI, MACD, ADX, ...)
├── chart.py             # رسم الرسوم البيانية (matplotlib)
├── advice.py            # توصيات محلية
├── sentiment.py         # تحليل المشاعر (أخبار)
├── predict.py           # توقع الأسعار
├── railway_entry.py     # نقطة الدخول لـ Railway (Flask + bot)
├── runner.py            # مشغل احتياطي (per-process restart)
├── requirements.txt     # بايثون Dependencies
├── Dockerfile           # إعدادات Railway
├── data.db              # SQLite database
├── data.json            # بيانات قديمة (تم التحويل)
├── templates/           # 12 قالب HTML
└── .env                 # الأسرار المحلية (لا تدفع)
```

## الأكواد المهمة

### config.py
```python
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
PROXY_URL = os.getenv("PROXY_URL", "")
AI_PROVIDER = os.getenv("AI_PROVIDER", "local")
DASHBOARD_URL = os.getenv("DASHBOARD_URL", "https://stock-bot-production-7ac8.up.railway.app")
```

### database.py - API سريع
```python
db.gp(uid)        # محفظة المستخدم
db.gw(uid)        # قائمة متابعة المستخدم
db.is_allowed(uid) # هل المستخدم مصرح؟
db["key"]         # الوصول للقاموس (gets saved automatically)
db["stats"]       # إحصائيات
db["whitelist"]   # قائمة المستخدمين المصرح لهم
db["user_info"]   # معلومات المستخدمين
db["advanced_alerts"] # التنبيهات الذكية
db["subscriptions"]   # الاشتراكات
db["virtual_portfolios"] # المحافظ الافتراضية
db.save()         # حفظ يدوي
```

## الأوامر المتاحة في البوت
`/start`, `/help`, `/price <symbol>`, `/signal <symbol>`, `/analyze <symbol>`,
`/chart <symbol>`, `/news <symbol>`, `/screener`, `/watch <symbol>`, 
`/unwatch <symbol>`, `/watchlist`, `/portfolio`, `/virtual buy/sell`,
`/alert <symbol> <price>`, `/web`, `/dashboard`, `/whitelist <uid>`,
`/broadcast <msg>`, `/pchart`, `/sectors`, `/accuracy`, `/sizing`

## قواعد مهمة للمساعد الذكي

### البوت (main.py)
- جميع الهاندلرز مسجلة في `main()` في `for` loop
- `chat_handler` يلتقط الرسائل العادية ويوجهها لـ AI
- `tracked()` ديكوريتور يتتبع استخدام الأوامر
- `check_advanced_alerts()` تعمل كل 5 دقائق

### الموقع (dashboard.py)
- جميع الصفحات تتطلب `_user_allowed(uid)` وهو يتحقق من `ADMIN_ID` أو `db.is_allowed(uid)`
- القوالب الجديدة تستخدم `base.html` المركزي
- الصفحات الموجودة: price, signal, portfolio, watchlist, screener, sentiment, heatmap, virtual
- API: `/api/price`, `/api/screener`, `/api/sentiment`, `/api/chart`, `/api/chart_data`, `/api/virtual/trade`
- تنسيق العملة: `_ccy(sym)` تعيد `﷼` للأسهم السعودية و `$` للباقي

### النشر (Railway)
- **الرابط**: `https://stock-bot-production-7ac8.up.railway.app`
- **GitHub**: `hidan11x/stock-bot` → master → auto-deploy
- **Admin ID**: `8601339909`
- **Bot Token**: متغير بيئة في Railway
- **النشر**: `git push` → Railway يبني وينشر تلقائياً
- **Dockerfile**: يستخدم `railway_entry.py` كـ entrypoint

### تصميم الموقع
- RTL اتجاه (يمين → يسار)
- واجهة داكنة (ألوان: #0a0e17, #131722, #1e2a3a, #2196F3)
- TradingView widgets في صفحات price و signal
- شريط أسعار متحرك في أعلى كل صفحة
- Chart.js للرسوم البيانية الأخرى
- Font Awesome 6 للأيقونات

### ملاحظات للحفاظ على الاستقرار
- `get_screener()` يستخدم cache (5 دقائق) لتجنب الوقت الطويل
- لا تستدعي `get_screener()` في route handlers مباشرة
- الملفات تستخدم LF line endings (git يحولها)
- Dockerfile لا يحتوي على `ENV BOT_TOKEN` أو `ENV ADMIN_ID` (يأخذها من Railway Environment)

## المستخدمون الحاليون
- **ادمن**: 8601339909 (@hidanx11)
- **مشترك**: 806383038 (A @abdulto)
- **مشترك**: 792449933 (Saud @isaudx77)
