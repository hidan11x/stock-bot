#!/usr/bin/env python
"""Run both bot and dashboard together for Railway"""
import subprocess, sys, os, time, signal

BOT_SCRIPT = os.path.join(os.path.dirname(__file__), "main.py")
DASH_SCRIPT = os.path.join(os.path.dirname(__file__), "dashboard.py")

process_map = {}

def start(name, script):
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    p = subprocess.Popen([sys.executable, script], env=env)
    process_map[name] = p
    print(f"[{name}] PID {p.pid}")
    return p

os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["NUMEXPR_NUM_THREADS"] = "1"
os.environ["OMP_NUM_THREADS"] = "1"

if __name__ == "__main__":
    print("Starting bot and dashboard...")
    start("BOT", BOT_SCRIPT)
    time.sleep(3)
    start("DASHBOARD", DASH_SCRIPT)

    def cleanup(sig, frame):
        for p in process_map.values():
            p.terminate()
        sys.exit(0)
    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    while True:
        for name, p in list(process_map.items()):
            if p.poll() is not None:
                print(f"[{name}] died, restarting in 10s...")
                del process_map[name]
                time.sleep(10)  # let old connections close
                start(name, BOT_SCRIPT if name == "BOT" else DASH_SCRIPT)
        time.sleep(3)
