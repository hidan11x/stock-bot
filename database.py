import json
import sqlite3
import logging
from datetime import datetime, timedelta
from pathlib import Path
from config import ADMIN_ID

SUBSCRIPTION_PLANS = {
    "basic": {"name": "أساسي", "price_sar": 29, "days": 30, "features": ["price", "signal", "watchlist", "levels"]},
    "pro": {"name": "احترافي", "price_sar": 79, "days": 30, "features": ["price", "signal", "analyze", "watchlist", "levels", "alerts", "portfolio", "screener", "news", "trend"]},
    "vip": {"name": "VIP", "price_sar": 199, "days": 30, "features": ["all"]},
    "yearly_basic": {"name": "أساسي سنوي", "price_sar": 249, "days": 365, "features": ["price", "signal", "watchlist", "levels"]},
    "yearly_pro": {"name": "احترافي سنوي", "price_sar": 699, "days": 365, "features": ["price", "signal", "analyze", "watchlist", "levels", "alerts", "portfolio", "screener", "news", "trend"]},
    "yearly_vip": {"name": "VIP سنوي", "price_sar": 1799, "days": 365, "features": ["all"]},
}


class Database(dict):
    def __init__(self, path="data.db"):
        super().__init__()
        db_dir = Path(__file__).parent
        self.db_path = db_dir / path
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._create_tables()
        self._migrate_from_json()
        self._load_from_db()

    def _create_tables(self):
        c = self.conn.cursor()
        c.executescript("""
            CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY, value TEXT
            );
            CREATE TABLE IF NOT EXISTS whitelist (
                user_id TEXT PRIMARY KEY
            );
            CREATE TABLE IF NOT EXISTS subscriptions (
                user_id TEXT PRIMARY KEY, expiry TEXT
            );
            CREATE TABLE IF NOT EXISTS user_info (
                user_id TEXT PRIMARY KEY, name TEXT, username TEXT,
                first_seen TEXT, last_seen TEXT
            );
            CREATE TABLE IF NOT EXISTS user_plans (
                user_id TEXT PRIMARY KEY, plan_name TEXT, features TEXT, end_date TEXT
            );
            CREATE TABLE IF NOT EXISTS admin_settings (
                user_id TEXT, key TEXT, value TEXT,
                PRIMARY KEY(user_id, key)
            );
            CREATE TABLE IF NOT EXISTS discount_codes (
                code TEXT PRIMARY KEY, percentage REAL, max_uses INTEGER,
                used INTEGER, created TEXT
            );
            CREATE TABLE IF NOT EXISTS stats (
                key TEXT PRIMARY KEY, value TEXT
            );
            CREATE TABLE IF NOT EXISTS watchlists (
                user_id TEXT, data TEXT
            );
            CREATE TABLE IF NOT EXISTS alerts (
                user_id TEXT, data TEXT
            );
            CREATE TABLE IF NOT EXISTS portfolios (
                user_id TEXT, data TEXT
            );
            CREATE TABLE IF NOT EXISTS smart_alerts (
                user_id TEXT, data TEXT
            );
            CREATE TABLE IF NOT EXISTS reports (
                user_id TEXT, value TEXT
            );
            CREATE TABLE IF NOT EXISTS conversations (
                user_id TEXT, data TEXT
            );
            CREATE TABLE IF NOT EXISTS advanced_alerts (
                user_id TEXT, data TEXT
            );
            CREATE TABLE IF NOT EXISTS daily_commands (
                date TEXT, cmd TEXT, count INTEGER,
                PRIMARY KEY(date, cmd)
            );
            CREATE TABLE IF NOT EXISTS daily_users (
                date TEXT, user_id TEXT,
                PRIMARY KEY(date, user_id)
            );
        """)
        self.conn.commit()

    def _set_config(self, key, value):
        c = self.conn.cursor()
        c.execute("INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)", (key, str(value)))
        self.conn.commit()

    def _get_config(self, key, default=None):
        c = self.conn.cursor()
        c.execute("SELECT value FROM config WHERE key=?", (key,))
        row = c.fetchone()
        return row["value"] if row else default

    def _migrate_from_json(self):
        json_path = Path(__file__).parent / "data.json"
        if not json_path.exists():
            return
        c = self.conn.cursor()
        c.execute("SELECT COUNT(*) FROM config")
        if c.fetchone()[0] > 0:
            return
        try:
            data = json.loads(json_path.read_text("utf-8"))
        except Exception as e:
            logging.error(f"_migrate_from_json: {e}")
            return

        self._set_config("whitelist_on", str(data.get("whitelist_on", False)))
        for uid in data.get("whitelist", []):
            c.execute("INSERT OR IGNORE INTO whitelist (user_id) VALUES (?)", (uid,))
        for uid, expiry in data.get("subscriptions", {}).items():
            c.execute("INSERT OR REPLACE INTO subscriptions (user_id, expiry) VALUES (?, ?)", (uid, expiry))
        for uid, info in data.get("user_info", {}).items():
            c.execute("INSERT OR REPLACE INTO user_info (user_id, name, username, first_seen, last_seen) VALUES (?,?,?,?,?)",
                      (uid, info.get("name", ""), info.get("username", ""),
                       info.get("first_seen", ""), info.get("last_seen", "")))
        for uid, plan in data.get("user_plans", {}).items():
            c.execute("INSERT OR REPLACE INTO user_plans (user_id, plan_name, features, end_date) VALUES (?,?,?,?)",
                      (uid, plan.get("plan", ""),
                       json.dumps(plan.get("features", []), ensure_ascii=False),
                       plan.get("end", "")))
        for uid, sets in data.get("admin_settings", {}).items():
            for key, val in sets.items():
                c.execute("INSERT OR REPLACE INTO admin_settings (user_id, key, value) VALUES (?,?,?)",
                          (uid, key, str(val).lower()))
        for code, info in data.get("discount_codes", {}).items():
            c.execute("INSERT OR REPLACE INTO discount_codes (code, percentage, max_uses, used, created) VALUES (?,?,?,?,?)",
                      (code, info.get("discount", 0), info.get("uses", 1),
                       info.get("used", 0), datetime.now().isoformat()))
        for table in ["watchlists", "alerts", "portfolios", "smart_alerts", "conversations", "advanced_alerts"]:
            for uid, val in data.get(table, {}).items():
                c.execute(f"INSERT OR REPLACE INTO {table} (user_id, data) VALUES (?,?)",
                          (uid, json.dumps(val, ensure_ascii=False)))
        for uid, val in data.get("reports", {}).items():
            c.execute("INSERT OR REPLACE INTO reports (user_id, value) VALUES (?,?)", (uid, val))
        stats = data.get("stats", {})
        for sk in ["commands", "users", "total_messages"]:
            sv = stats.get(sk, {} if sk != "total_messages" else 0)
            c.execute("INSERT OR REPLACE INTO stats (key, value) VALUES (?,?)",
                      (sk, json.dumps(sv, ensure_ascii=False) if not isinstance(sv, (int, str)) else str(sv)))
        for date, cmds in stats.get("daily_commands", {}).items():
            for cmd, count in cmds.items():
                c.execute("INSERT OR REPLACE INTO daily_commands (date, cmd, count) VALUES (?,?,?)", (date, cmd, count))
        for date, users in stats.get("daily_users", {}).items():
            for uid in users:
                c.execute("INSERT OR REPLACE INTO daily_users (date, user_id) VALUES (?,?)", (date, uid))
        sp = data.get("subscription_plans", {})
        if sp:
            self._set_config("subscription_plans", json.dumps(sp, ensure_ascii=False))
        self.conn.commit()

    def _load_from_db(self):
        c = self.conn.cursor()
        wl_on = self._get_config("whitelist_on", "False")
        self["whitelist_on"] = wl_on == "True"
        sp = self._get_config("subscription_plans", "{}")
        self["subscription_plans"] = json.loads(sp)
        wl = []
        for row in c.execute("SELECT user_id FROM whitelist"):
            wl.append(row["user_id"])
        self["whitelist"] = wl
        subs = {}
        for row in c.execute("SELECT user_id, expiry FROM subscriptions"):
            subs[row["user_id"]] = row["expiry"]
        self["subscriptions"] = subs
        ui = {}
        for row in c.execute("SELECT user_id, name, username, first_seen, last_seen FROM user_info"):
            ui[row["user_id"]] = {"name": row["name"], "username": row["username"],
                                   "first_seen": row["first_seen"], "last_seen": row["last_seen"]}
        self["user_info"] = ui
        up = {}
        for row in c.execute("SELECT user_id, plan_name, features, end_date FROM user_plans"):
            up[row["user_id"]] = {"plan": row["plan_name"],
                                   "features": json.loads(row["features"]) if row["features"] else [],
                                   "end": row["end_date"]}
        self["user_plans"] = up
        ads = {}
        for row in c.execute("SELECT user_id, key, value FROM admin_settings"):
            uid = row["user_id"]
            if uid not in ads:
                ads[uid] = {}
            val = row["value"]
            if val.lower() == "true":
                val = True
            elif val.lower() == "false":
                val = False
            ads[uid][row["key"]] = val
        self["admin_settings"] = ads
        dc = {}
        for row in c.execute("SELECT code, percentage, max_uses, used, created FROM discount_codes"):
            dc[row["code"]] = {"discount": row["percentage"], "type": "percent",
                                "uses": row["max_uses"], "used": row["used"],
                                "created": row["created"]}
        self["discount_codes"] = dc
        for table in ["watchlists", "alerts", "portfolios", "smart_alerts", "conversations", "advanced_alerts"]:
            coll = {}
            for row in c.execute(f"SELECT user_id, data FROM {table}"):
                try:
                    coll[row["user_id"]] = json.loads(row["data"])
                except (json.JSONDecodeError, TypeError):
                    coll[row["user_id"]] = []
            self[table] = coll
        reports = {}
        for row in c.execute("SELECT user_id, value FROM reports"):
            reports[row["user_id"]] = row["value"]
        self["reports"] = reports
        stats = {"commands": {}, "users": [], "total_messages": 0, "daily_commands": {}, "daily_users": {}}
        for row in c.execute("SELECT key, value FROM stats WHERE key='commands'"):
            try:
                stats["commands"] = json.loads(row["value"])
            except (json.JSONDecodeError, TypeError):
                stats["commands"] = {}
        for row in c.execute("SELECT key, value FROM stats WHERE key='users'"):
            try:
                stats["users"] = json.loads(row["value"])
            except (json.JSONDecodeError, TypeError):
                stats["users"] = []
        for row in c.execute("SELECT key, value FROM stats WHERE key='total_messages'"):
            try:
                stats["total_messages"] = int(row["value"])
            except (ValueError, TypeError):
                stats["total_messages"] = 0
        daily_cmds = {}
        for row in c.execute("SELECT date, cmd, count FROM daily_commands"):
            date = row["date"]
            if date not in daily_cmds:
                daily_cmds[date] = {}
            daily_cmds[date][row["cmd"]] = row["count"]
        stats["daily_commands"] = daily_cmds
        daily_users = {}
        for row in c.execute("SELECT date, user_id FROM daily_users"):
            date = row["date"]
            if date not in daily_users:
                daily_users[date] = []
            daily_users[date].append(row["user_id"])
        stats["daily_users"] = daily_users
        self["stats"] = stats

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        if key == "whitelist_on":
            self._set_config("whitelist_on", str(value))
        elif key == "subscription_plans":
            self._set_config("subscription_plans", json.dumps(value, ensure_ascii=False))

    def setdefault(self, key, default=None):
        if key not in self:
            self[key] = default
        return self[key]

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def __contains__(self, key):
        return dict.__contains__(self, key)

    def gw(self, uid):
        return self["watchlists"].get(str(uid), [])

    def sw(self, uid, v):
        suid = str(uid)
        self["watchlists"][suid] = v
        c = self.conn.cursor()
        c.execute("INSERT OR REPLACE INTO watchlists (user_id, data) VALUES (?,?)",
                  (suid, json.dumps(v, ensure_ascii=False)))
        self.conn.commit()

    def ga(self, uid):
        return self["alerts"].get(str(uid), [])

    def sa(self, uid, v):
        suid = str(uid)
        self["alerts"][suid] = v
        c = self.conn.cursor()
        c.execute("INSERT OR REPLACE INTO alerts (user_id, data) VALUES (?,?)",
                  (suid, json.dumps(v, ensure_ascii=False)))
        self.conn.commit()

    def gp(self, uid):
        return self["portfolios"].get(str(uid), [])

    def sp(self, uid, v):
        suid = str(uid)
        self["portfolios"][suid] = v
        c = self.conn.cursor()
        c.execute("INSERT OR REPLACE INTO portfolios (user_id, data) VALUES (?,?)",
                  (suid, json.dumps(v, ensure_ascii=False)))
        self.conn.commit()

    def gsa(self, uid):
        return self["smart_alerts"].get(str(uid), [])

    def ssa(self, uid, v):
        suid = str(uid)
        self["smart_alerts"][suid] = v
        c = self.conn.cursor()
        c.execute("INSERT OR REPLACE INTO smart_alerts (user_id, data) VALUES (?,?)",
                  (suid, json.dumps(v, ensure_ascii=False)))
        self.conn.commit()

    def grp(self, uid):
        return self["reports"].get(str(uid), "off")

    def srp(self, uid, v):
        suid = str(uid)
        self["reports"][suid] = v
        c = self.conn.cursor()
        c.execute("INSERT OR REPLACE INTO reports (user_id, value) VALUES (?,?)", (suid, v))
        self.conn.commit()

    def gc(self, uid):
        return self["conversations"].get(str(uid), [])

    def sc(self, uid, v):
        suid = str(uid)
        self["conversations"][suid] = v
        c = self.conn.cursor()
        c.execute("INSERT OR REPLACE INTO conversations (user_id, data) VALUES (?,?)",
                  (suid, json.dumps(v, ensure_ascii=False)))
        self.conn.commit()

    def gaa(self, uid):
        return self["advanced_alerts"].get(str(uid), [])

    def saa(self, uid, v):
        suid = str(uid)
        self["advanced_alerts"][suid] = v
        c = self.conn.cursor()
        c.execute("INSERT OR REPLACE INTO advanced_alerts (user_id, data) VALUES (?,?)",
                  (suid, json.dumps(v, ensure_ascii=False)))
        self.conn.commit()

    def track_cmd(self, cmd_name):
        stats = self["stats"]
        stats["commands"][cmd_name] = stats["commands"].get(cmd_name, 0) + 1
        today = datetime.now().strftime("%Y-%m-%d")
        daily = stats.setdefault("daily_commands", {}).setdefault(today, {})
        daily[cmd_name] = daily.get(cmd_name, 0) + 1
        c = self.conn.cursor()
        c.execute("INSERT OR REPLACE INTO stats (key, value) VALUES (?,?)",
                  ("commands", json.dumps(stats["commands"], ensure_ascii=False)))
        c.execute("INSERT OR REPLACE INTO daily_commands (date, cmd, count) VALUES (?,?,?)",
                  (today, cmd_name, daily[cmd_name]))
        self.conn.commit()

    def track_user(self, uid, user_obj=None):
        suid = str(uid)
        stats = self["stats"]
        users = stats["users"]
        if user_obj:
            ui = self.setdefault("user_info", {})
            prev = ui.get(suid, {})
            ui[suid] = {
                "name": user_obj.full_name,
                "username": user_obj.username or "",
                "first_seen": prev.get("first_seen", datetime.now().isoformat()),
                "last_seen": datetime.now().isoformat(),
            }
            c = self.conn.cursor()
            c.execute("INSERT OR REPLACE INTO user_info (user_id, name, username, first_seen, last_seen) VALUES (?,?,?,?,?)",
                      (suid, ui[suid]["name"], ui[suid]["username"],
                       ui[suid]["first_seen"], ui[suid]["last_seen"]))
            self.conn.commit()
        today = datetime.now().strftime("%Y-%m-%d")
        daily_users = stats.setdefault("daily_users", {})
        today_list = daily_users.setdefault(today, [])
        is_new = False
        if suid not in users:
            users.append(suid)
            is_new = True
            c = self.conn.cursor()
            c.execute("INSERT OR REPLACE INTO stats (key, value) VALUES (?,?)",
                      ("users", json.dumps(users, ensure_ascii=False)))
            self.conn.commit()
        if suid not in today_list:
            today_list.append(suid)
            c = self.conn.cursor()
            c.execute("INSERT OR REPLACE INTO daily_users (date, user_id) VALUES (?,?)", (today, suid))
            self.conn.commit()
        return is_new

    def track_msg(self):
        stats = self["stats"]
        stats["total_messages"] = stats["total_messages"] + 1
        c = self.conn.cursor()
        c.execute("INSERT OR REPLACE INTO stats (key, value) VALUES (?,?)",
                  ("total_messages", str(stats["total_messages"])))
        self.conn.commit()

    def is_allowed(self, uid):
        if not self.get("whitelist_on"):
            return True
        suid = str(uid)
        if uid == ADMIN_ID:
            return True
        if suid not in self.get("whitelist", []):
            return False
        days = self.get_sub_days_left(uid)
        if days == -1:
            return True
        return days > 0

    def get_sub_days_left(self, uid):
        subs = self.get("subscriptions", {})
        expiry = subs.get(str(uid))
        if not expiry:
            return -1
        try:
            end = datetime.fromisoformat(expiry)
            remaining = (end - datetime.now()).days
            return max(remaining, 0)
        except Exception:
            return -1

    def set_sub_days(self, uid, days):
        suid = str(uid)
        subs = self.setdefault("subscriptions", {})
        end = datetime.now() + timedelta(days=days)
        subs[suid] = end.isoformat()
        c = self.conn.cursor()
        c.execute("INSERT OR REPLACE INTO subscriptions (user_id, expiry) VALUES (?,?)", (suid, subs[suid]))
        self.conn.commit()

    def remove_sub(self, uid):
        suid = str(uid)
        self.get("subscriptions", {}).pop(suid, None)
        c = self.conn.cursor()
        c.execute("DELETE FROM subscriptions WHERE user_id=?", (suid,))
        self.conn.commit()

    def get_user_plan(self, uid):
        up = self.get("user_plans", {}).get(str(uid))
        if not up:
            days = self.get_sub_days_left(uid)
            if days > 0:
                return {"plan": "basic", "name": "أساسي",
                        "features": SUBSCRIPTION_PLANS["basic"]["features"], "end": days}
            return None
        plan_id = up.get("plan", "basic")
        plan = SUBSCRIPTION_PLANS.get(plan_id)
        if not plan:
            return None
        try:
            end = datetime.fromisoformat(up.get("end", ""))
            remaining = max((end - datetime.now()).days, 0)
            if remaining <= 0:
                return None
        except Exception:
            return None
        return {"plan": plan_id, "name": plan["name"], "features": plan["features"], "end": remaining}

    def user_has_feature(self, uid, feature):
        up = self.get_user_plan(uid)
        if not up:
            return False
        if "all" in up["features"]:
            return True
        return feature in up["features"]

    def get_user_mode(self, uid):
        return "normal"

    def admin_settings(self, uid):
        suid = str(uid)
        ads = self.setdefault("admin_settings", {})
        if suid not in ads:
            ads[suid] = {"new_users": True, "errors": True, "startup": True, "daily_summary": False}
            self._sync_admin_settings(suid)
        return ads[suid]

    def _sync_admin_settings(self, uid):
        c = self.conn.cursor()
        c.execute("DELETE FROM admin_settings WHERE user_id=?", (uid,))
        ads = self.get("admin_settings", {}).get(uid, {})
        for key, val in ads.items():
            c.execute("INSERT INTO admin_settings (user_id, key, value) VALUES (?,?,?)",
                      (uid, key, str(val).lower()))
        self.conn.commit()

    def save_admin_settings(self, uid, settings):
        suid = str(uid)
        self["admin_settings"][suid] = settings
        self._sync_admin_settings(suid)

    def get_discount_code(self, code):
        return self.get("discount_codes", {}).get(code.upper())

    def create_discount_code(self, code, pct, max_uses):
        code = code.upper()
        dc = self.setdefault("discount_codes", {})
        info = {"discount": pct, "type": "percent", "uses": max_uses, "used": 0,
                "created": datetime.now().isoformat()}
        dc[code] = info
        c = self.conn.cursor()
        c.execute("INSERT OR REPLACE INTO discount_codes (code, percentage, max_uses, used, created) VALUES (?,?,?,?,?)",
                  (code, pct, max_uses, 0, info["created"]))
        self.conn.commit()
        return info

    def use_discount_code(self, code):
        code = code.upper()
        dc = self.get("discount_codes", {})
        info = dc.get(code)
        if not info:
            return None
        if info.get("used", 0) >= info.get("uses", 1):
            return None
        info["used"] = info.get("used", 0) + 1
        c = self.conn.cursor()
        c.execute("UPDATE discount_codes SET used=? WHERE code=?", (info["used"], code))
        self.conn.commit()
        return info

    def get_stats(self):
        return self.get("stats", {})

    def get_total_users(self):
        return len(self.get("stats", {}).get("users", []))

    def get_daily_users(self, today):
        return self.get("stats", {}).get("daily_users", {}).get(today, [])

    def save(self):
        c = self.conn.cursor()
        self._set_config("whitelist_on", str(self.get("whitelist_on", False)))
        sp = self.get("subscription_plans", {})
        if sp:
            self._set_config("subscription_plans", json.dumps(sp, ensure_ascii=False))
        else:
            c.execute("DELETE FROM config WHERE key='subscription_plans'")
        c.execute("DELETE FROM whitelist")
        for uid in self.get("whitelist", []):
            c.execute("INSERT INTO whitelist (user_id) VALUES (?)", (uid,))
        c.execute("DELETE FROM subscriptions")
        for uid, expiry in self.get("subscriptions", {}).items():
            c.execute("INSERT INTO subscriptions (user_id, expiry) VALUES (?,?)", (uid, expiry))
        c.execute("DELETE FROM user_info")
        for uid, info in self.get("user_info", {}).items():
            c.execute("INSERT INTO user_info (user_id, name, username, first_seen, last_seen) VALUES (?,?,?,?,?)",
                      (uid, info.get("name", ""), info.get("username", ""),
                       info.get("first_seen", ""), info.get("last_seen", "")))
        c.execute("DELETE FROM user_plans")
        for uid, plan in self.get("user_plans", {}).items():
            c.execute("INSERT INTO user_plans (user_id, plan_name, features, end_date) VALUES (?,?,?,?)",
                      (uid, plan.get("plan", ""),
                       json.dumps(plan.get("features", []), ensure_ascii=False),
                       plan.get("end", "")))
        c.execute("DELETE FROM admin_settings")
        for uid, sets in self.get("admin_settings", {}).items():
            for key, val in sets.items():
                c.execute("INSERT INTO admin_settings (user_id, key, value) VALUES (?,?,?)",
                          (uid, key, str(val).lower()))
        c.execute("DELETE FROM discount_codes")
        for code, info in self.get("discount_codes", {}).items():
            c.execute("INSERT INTO discount_codes (code, percentage, max_uses, used, created) VALUES (?,?,?,?,?)",
                      (code, info.get("discount", 0), info.get("uses", 1),
                       info.get("used", 0), info.get("created", datetime.now().isoformat())))
        for table in ["watchlists", "alerts", "portfolios", "smart_alerts", "conversations", "advanced_alerts"]:
            c.execute(f"DELETE FROM {table}")
            for uid, val in self.get(table, {}).items():
                c.execute(f"INSERT INTO {table} (user_id, data) VALUES (?,?)",
                          (uid, json.dumps(val, ensure_ascii=False)))
        c.execute("DELETE FROM reports")
        for uid, val in self.get("reports", {}).items():
            c.execute("INSERT INTO reports (user_id, value) VALUES (?,?)", (uid, val))
        c.execute("DELETE FROM stats")
        stats = self.get("stats", {})
        c.execute("INSERT INTO stats (key, value) VALUES (?,?)",
                  ("commands", json.dumps(stats.get("commands", {}), ensure_ascii=False)))
        c.execute("INSERT INTO stats (key, value) VALUES (?,?)",
                  ("users", json.dumps(stats.get("users", []), ensure_ascii=False)))
        c.execute("INSERT INTO stats (key, value) VALUES (?,?)",
                  ("total_messages", str(stats.get("total_messages", 0))))
        c.execute("DELETE FROM daily_commands")
        for date, cmds in stats.get("daily_commands", {}).items():
            for cmd, count in cmds.items():
                c.execute("INSERT INTO daily_commands (date, cmd, count) VALUES (?,?,?)", (date, cmd, count))
        c.execute("DELETE FROM daily_users")
        for date, users in stats.get("daily_users", {}).items():
            for uid in users:
                c.execute("INSERT INTO daily_users (date, user_id) VALUES (?,?)", (date, uid))
        self.conn.commit()


db = Database()
