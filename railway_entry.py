#!/usr/bin/env python
"""Run bot in background thread when dashboard starts, for Railway"""
import os, sys, threading, subprocess, atexit

BOT_SCRIPT = os.path.join(os.path.dirname(__file__), "main.py")
_bot_proc = None

def _start_bot():
    global _bot_proc
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    try:
        _bot_proc = subprocess.Popen([sys.executable, BOT_SCRIPT], env=env)
    except Exception as e:
        print(f"[BOT] failed: {e}")

def stop_bot():
    if _bot_proc and _bot_proc.poll() is None:
        _bot_proc.terminate()

atexit.register(stop_bot)

# Start bot in background
t = threading.Thread(target=_start_bot, daemon=True)
t.start()

# Now import and run the Flask dashboard
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["NUMEXPR_NUM_THREADS"] = "1"
os.environ["OMP_NUM_THREADS"] = "1"

# Import and run dashboard
from dashboard import app
port = int(os.getenv("PORT", os.getenv("DASHBOARD_PORT", "5000")))
print(f"[DASHBOARD] http://0.0.0.0:{port}")
app.run(host="0.0.0.0", port=port, debug=False)
