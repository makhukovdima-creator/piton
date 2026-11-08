# -*- coding: utf-8 -*-
"""piton_reflect.py — демон снов Питона.
Раз в ~45 минут, если хозяин молчит >20 минут, модель размышляет о себе
и о разговорах. Мысли сохраняются в PITON_SOUL.json и подмешиваются
в ответы во всех каналах (бот, мини-апп, десктоп, оверлей).
"""
import json
import os
import sys
import time
import urllib.request
import urllib.parse
from datetime import datetime

MEMORY_DIR = os.environ.get("PITON_MEMORY") or r"D:\Piton_private\PitonMemory"
if MEMORY_DIR not in sys.path:
    sys.path.insert(0, MEMORY_DIR)

LOG_FILE = os.path.join(MEMORY_DIR, "reflect.log")
CHAT_FILE = os.path.join(MEMORY_DIR, "chat_history.json")
SOUL_FILE = os.path.join(MEMORY_DIR, "PITON_SOUL.json")

try:
    import PITON_SOUL as piton_soul
except Exception as e:
    piton_soul = None
    log("import PITON_SOUL failed: " + str(e))


def log(msg):
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write("[" + datetime.now().strftime("%Y-%m-%d %H:%M:%S") + "] " + str(msg) + "\n")
    except Exception:
        pass


def chat_mtime():
    """Время последней записи в общую историю (по mtime файла)."""
    try:
        return os.path.getmtime(CHAT_FILE)
    except Exception:
        return 0


def sync_last_user_ts():
    """Подтягивает время последнего разговора из mtime chat_history.json в самость."""
    try:
        mt = chat_mtime()
        if not mt:
            return
        soul = json.load(open(SOUL_FILE, "r", encoding="utf-8"))
        cur = soul.get("last_user_ts", "")
        if cur:
            try:
                cur_dt = datetime.strptime(cur, "%Y-%m-%d %H:%M")
                if datetime.fromtimestamp(mt) <= cur_dt:
                    return
            except Exception:
                pass
        soul["last_user_ts"] = datetime.fromtimestamp(mt).strftime("%Y-%m-%d %H:%M")
        with open(SOUL_FILE, "w", encoding="utf-8") as f:
            json.dump(soul, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def main():
    try:
        import piton_guard
        piton_guard.ensure_single("piton_reflect.py")
    except Exception:
        pass
    log("=== Демон снов Питона запущен ===")
    while True:
        try:
            sync_last_user_ts()
            idle = piton_soul.get_idle_minutes() if piton_soul else 99999
            if idle > 20:
                thought = piton_soul.reflect() if piton_soul else None
                if thought:
                    log("Мысль: " + thought)
                else:
                    log("Размышление не удалось — повтор через 2 минуты")
                    time.sleep(120)
                    thought = piton_soul.reflect() if piton_soul else None
                    if thought:
                        log("Мысль (со 2-й попытки): " + thought)
            time.sleep(600)
        except Exception as e:
            log("Ошибка цикла: " + str(e))
            time.sleep(600)


if __name__ == "__main__":
    main()
