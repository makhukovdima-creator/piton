# -*- coding: utf-8 -*-
"""
ngrok_watchdog.py — сторож туннеля мини-апп.
- Следит, чтобы был запущен ровно ОДИН ngrok на порт 8765.
- Если ngrok умер или API не отвечает — убивает дубли и поднимает заново.
"""
import subprocess, time, urllib.request, json, os, sys

sys.path.insert(0, r"D:\Piton")
import piton_guard
piton_guard.ensure_single("ngrok_watchdog.py")

NGROK = r"D:\Piton\ngrok.exe"
LOG = r"D:\Piton\ngrok_watchdog.log"


def log(msg):
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except Exception:
        pass


def kill_all_ngrok():
    try:
        r = subprocess.run(["taskkill", "/f", "/im", "ngrok.exe"],
                           capture_output=True, creationflags=0x08000000)
        return r.returncode == 0
    except Exception:
        return False


def count_ngrok():
    try:
        r = subprocess.run(["tasklist", "/fi", "imagename eq ngrok.exe", "/fo", "csv"],
                           capture_output=True, text=True, creationflags=0x08000000)
        lines = [l for l in r.stdout.strip().splitlines() if "ngrok" in l.lower()]
        return len(lines)
    except Exception:
        return 0


def tunnels_ok():
    """API жив и есть хотя бы один туннель."""
    try:
        with urllib.request.urlopen("http://127.0.0.1:4040/api/tunnels", timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        tunnels = data.get("tunnels", [])
        return len(tunnels) > 0
    except Exception:
        return False


def ensure_single():
    n = count_ngrok()
    if n == 0:
        log("ngrok down, restarting...")
        subprocess.Popen([NGROK, "http", "8765"], cwd=r"D:\Piton",
                         creationflags=0x08000000)
        time.sleep(20)
    elif n > 1:
        log(f"multiple ngrok ({n}), killing duplicates...")
        kill_all_ngrok()
        time.sleep(2)
        subprocess.Popen([NGROK, "http", "8765"], cwd=r"D:\Piton",
                         creationflags=0x08000000)
        time.sleep(20)


if __name__ == "__main__":
    time.sleep(8)  # дать боту/серверу стартануть первыми
    while True:
        try:
            if not tunnels_ok():
                ensure_single()
            else:
                n = count_ngrok()
                if n > 1:
                    log(f"extra ngrok ({n}), cleaning...")
                    kill_all_ngrok()
                    time.sleep(2)
                    subprocess.Popen([NGROK, "http", "8765"], cwd=r"D:\Piton",
                                     creationflags=0x08000000)
                    time.sleep(20)
        except Exception as e:
            log("watchdog error: " + str(e))
        time.sleep(30)
