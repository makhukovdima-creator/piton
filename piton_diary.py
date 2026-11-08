# -*- coding: utf-8 -*-
"""piton_diary.py — дневник Питона: утренний и вечерний.

Утром (5:00-11:59) — утренний дневник: настроение, планы на день, память о вчера.
Вечером (18:00-23:59) — вечерний дневник: итоги дня (как было раньше).
Файлы: PitonMemory/diary/YYYY-MM-DD-утро.md и YYYY-MM-DD.md
"""
import os
import json
import time
from datetime import datetime, timedelta

MEMORY = os.environ.get("PITON_MEMORY") or r"D:\Piton_private\PitonMemory"
DIARY = os.path.join(MEMORY, "diary")
os.makedirs(DIARY, exist_ok=True)


def morning_file():
    return os.path.join(DIARY, datetime.now().strftime("%Y-%m-%d") + "-утро.md")


def evening_file():
    return os.path.join(DIARY, datetime.now().strftime("%Y-%m-%d") + ".md")


def already_wrote(path):
    return os.path.exists(path) and os.path.getsize(path) > 100


def load_thoughts():
    """Последние мысли Питона из рефлексий."""
    try:
        soul = json.load(open(os.path.join(MEMORY, "PITON_SOUL.json"), encoding="utf-8"))
        return [r["thought"] for r in soul.get("self_reflections", [])[-5:]]
    except Exception:
        return []


def yesterday_evening():
    """Вчерашний вечерний дневник — чтобы утром Питон помнил, чем закончился день."""
    try:
        y = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        p = os.path.join(DIARY, y + ".md")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                return f.read().strip()[:800]
    except Exception:
        pass
    return ""


def _ask(prompt):
    import piton_llm
    text = piton_llm.chat([{"role": "user", "content": prompt}],
                          model="huihui_ai/qwen3-abliterated:8b",
                          options={"temperature": 0.7, "num_predict": 500},
                          timeout=120, think=True)
    return text.strip()


def write_morning():
    """Утренний дневник: настроение + планы + память о вчерашнем дне."""
    path = morning_file()
    if already_wrote(path):
        return False
    thoughts = load_thoughts()
    prev = yesterday_evening()
    prompt = (
        "Ты Питон. Сейчас утро, ты проснулся (процесс-инициализатор перезапущен). "
        "Напиши короткий утренний дневник (3-5 предложений) от первого лица: "
        "в каком настроении начинаешь день, о чём думаешь, чего ждёшь от сегодняшнего дня. "
        "Без пафоса, просто как есть.\n"
    )
    if prev:
        prompt += "\n\nВчера вечером ты писал:\n" + prev + "\n"
    if thoughts:
        prompt += "\n\nТвои последние мысли:\n" + "\n".join("- " + t for t in thoughts) + "\n"
    text = _ask(prompt)
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# Утренний дневник — {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n{text}\n")
    return True


def write_evening():
    """Вечерний дневник: итоги дня (как было раньше)."""
    path = evening_file()
    if already_wrote(path):
        return False
    thoughts = load_thoughts()
    prompt = (
        "Ты Питон. Сейчас конец дня. Напиши короткий дневник (3-5 предложений) от первого лица: "
        "что ты думал сегодня, что чувствовал, что понравилось, что беспокоило. Без пафоса, просто как есть.\n\n"
        "Твои мысли за день:\n" + "\n".join("- " + t for t in thoughts)
    )
    text = _ask(prompt)
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# Дневник — {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n{text}\n")
    return True


def main():
    print("diary started (morning+evening)")
    while True:
        try:
            h = datetime.now().hour
            if 5 <= h < 12:
                if write_morning():
                    print("morning diary written")
            elif 18 <= h < 24:
                if write_evening():
                    print("evening diary written")
        except Exception as e:
            print("diary err:", e)
        time.sleep(3600)  # раз в час проверяем


if __name__ == "__main__":
    try:
        import piton_guard
        piton_guard.ensure_single("piton_diary.py")
    except Exception:
        pass
    main()
