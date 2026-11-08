# -*- coding: utf-8 -*-
"""
piton_agent.py — инструменты агентного цикла Питона.

Модель решает (REMEMBER:/REMIND:/SEARCH:/WEATHER:), а этот модуль выполняет:
  remember(text)        — записать в заметки (PitonMemory/notes.md)
  add_reminder(t, text) — поставить напоминание (PitonMemory/reminders.json)
  due_reminders()       — какие напоминания наступили (и удалить их)
Никогда не падает.
"""
import json
import os
import re
import time

MEMORY_DIR = os.environ.get("PITON_MEMORY") or r"D:\Piton_private\PitonMemory"
NOTES_FILE = os.path.join(MEMORY_DIR, "notes.md")
REMINDERS_FILE = os.path.join(MEMORY_DIR, "reminders.json")

_HM_RE = re.compile(r"([01]?\d|2[0-3])[:.\-]([0-5]\d)")


def _now_hm():
    return time.strftime("%H:%M")


def remember(text: str):
    """Записать заметку. Возвращает True при успехе."""
    text = (text or "").strip()
    if not text:
        return False
    try:
        os.makedirs(MEMORY_DIR, exist_ok=True)
        line = "### " + time.strftime("%d.%m %H:%M") + "\n" + text + "\n\n"
        with open(NOTES_FILE, "a", encoding="utf-8") as f:
            f.write(line)
        return True
    except Exception:
        return False


def add_reminder(hm: str, text: str):
    """Поставить напоминание. hm вида '18:00'. Возвращает True при успехе."""
    text = (text or "").strip()
    m = _HM_RE.search(hm or "")
    if not m or not text:
        return False
    hm_norm = "%s:%s" % (m.group(1).zfill(2), m.group(2))
    try:
        os.makedirs(MEMORY_DIR, exist_ok=True)
        rems = []
        if os.path.exists(REMINDERS_FILE):
            with open(REMINDERS_FILE, "r", encoding="utf-8") as f:
                rems = json.load(f)
        if not isinstance(rems, list):
            rems = []
        rems.append({"t": hm_norm, "x": text, "ts": int(time.time())})
        tmp = REMINDERS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(rems, f, ensure_ascii=False, indent=2)
        os.replace(tmp, REMINDERS_FILE)
        return True
    except Exception:
        return False


def due_reminders():
    """Наступившие напоминания (удаляются из очереди). Возвращает [text, ...]."""
    try:
        if not os.path.exists(REMINDERS_FILE):
            return []
        with open(REMINDERS_FILE, "r", encoding="utf-8") as f:
            rems = json.load(f)
        if not isinstance(rems, list):
            return []
        now = _now_hm()
        due = [r.get("x", "") for r in rems
               if r.get("t") == now and (r.get("x") or "").strip()]
        keep = [r for r in rems if r.get("t") != now]
        tmp = REMINDERS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(keep, f, ensure_ascii=False, indent=2)
        os.replace(tmp, REMINDERS_FILE)
        return due
    except Exception:
        return []


if __name__ == "__main__":
    # самопроверка
    print("remember:", remember("тестовая заметка"))
    print("add_reminder:", add_reminder(_now_hm(), "тестовое напоминание"))
    print("due:", due_reminders())
