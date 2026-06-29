#!/usr/bin/env python
"""Run both bot and dashboard together for Railway"""
import subprocess, sys, os, time, signal

BOT_SCRIPT = os.path.join(os.path.dirname(__file__), "main.py")
DASH_SCRIPT = os.path.join(os.path.dirname(__file__), "dashboard.py")

processes = []

def start(name, script):
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    p = subprocess.Popen([sys.executable, script], env=env)
    processes.append(p)
    print(f"[{name}] PID {p.pid}")
    return p

if __name__ == "__main__":
    print("Starting bot and dashboard...")
    p1 = start("BOT", BOT_SCRIPT)
    time.sleep(2)
    p2 = start("DASHBOARD", DASH_SCRIPT)

    def cleanup(sig, frame):
        for p in processes:
            p.terminate()
        sys.exit(0)
    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    while True:
        for p in list(processes):
            if p.poll() is not None:
                print(f"Process {p.pid} died, restarting...")
                processes.remove(p)
                # determine which script based on index
                # just restart both
                start("BOT", BOT_SCRIPT)
                start("DASHBOARD", DASH_SCRIPT)
        time.sleep(5)
