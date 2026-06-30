import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is not set. Create a .env file with BOT_TOKEN=your_token")

# AI settings: "openai", "deepseek", "gemini", or "local" (free, no key needed)
AI_PROVIDER = os.getenv("AI_PROVIDER", "local")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# Proxy for Telegram (if needed in countries where Telegram is blocked)
PROXY_URL = os.getenv("PROXY_URL", "")
# Example: socks5://user:pass@127.0.0.1:1080 or http://127.0.0.1:8080

# Dashboard URL (Railway or your domain)
DASHBOARD_URL = os.getenv("DASHBOARD_URL", "https://stock-bot-production-7ac8.up.railway.app").rstrip("/")

# Default watchlist
DEFAULT_SYMBOLS = ["SPY", "QQQ", "^GSPC", "^VIX"]

# Alert thresholds (% change)
ALERT_THRESHOLD = 1.0

# Admin (owner) Telegram user ID — gets admin-level notifications
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
