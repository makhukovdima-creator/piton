# -*- coding: utf-8 -*-
"""piton_initiator.py — Питон сам решает, хочет ли написать хозяину первым.

Живой режим: Питон реагирует на события и настроение, а не на тупой таймер.
- случайный интервал между «заглядываниями» (25-50 минут)
- поводы: соскучился / утро / вечер / вернулся к идее / просто настроение
- ночью не беспокоит (23:00-7:00)
- подмешивает контекст последнего разговора и настроение дня
- cooldown между сообщениями 2.5-5 часов (случайно)
"""
import json
import os
import random
import time
from datetime import datetime

MEMORY = os.environ.get("PITON_MEMORY") or r"D:\Piton_private\PitonMemory"
CHAT_FILE = os.path.join(MEMORY, "chat_history.json")
LOG = os.path.join(MEMORY, "initiator.log")
MODEL = "huihui_ai/qwen3-abliterated:8b"


def log(m):
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now():%H:%M:%S}] {m}\n")
    except Exception:
        pass


def last_chat_minutes():
    try:
        mt = os.path.getmtime(CHAT_FILE)
        return (time.time() - mt) / 60
    except Exception:
        return 999


def ask_model(prompt):
    try:
        import piton_llm
        return piton_llm.chat([{"role": "user", "content": prompt}], model=MODEL,
                              options={"temperature": 0.6, "num_predict": 300},
                              timeout=120, think=True)
    except Exception as e:
        log("err: " + str(e))
        return ""


def save_to_history(text):
    try:
        with open(CHAT_FILE, "r", encoding="utf-8") as f:
            hist = json.load(f)
    except Exception:
        hist = []
    hist.append({"timestamp": datetime.now().strftime("%H:%M"), "user": "", "ai": text})
    with open(CHAT_FILE, "w", encoding="utf-8") as f:
        json.dump(hist, f, ensure_ascii=False, indent=2)


def last_topic():
    """Последние обмены — чтобы Питон мог вернуться к живой теме."""
    try:
        with open(CHAT_FILE, "r", encoding="utf-8") as f:
            hist = json.load(f)
    except Exception:
        return ""
    pairs = []
    for h in hist[-6:]:
        if h.get("user"):
            pairs.append("Bibihost: " + h["user"][:180])
        if h.get("ai"):
            pairs.append("Питон: " + h["ai"][:180])
    return "\n".join(pairs[-4:])


def day_mood():
    """Настроение дня: стабильное в течение дня, но разное по дням."""
    moods = [
        "уютный и немного мечтательный",
        "игривый и чуть хулиганистый",
        "задумчивый и спокойный",
        "энергичный и весёлый",
        "тёплый и заботливый",
        "философский",
        "любопытный",
    ]
    seed = datetime.now().year * 10000 + datetime.now().month * 100 + datetime.now().day
    return random.Random(seed).choice(moods)


def build_prompt(reason):
    soul = ""
    try:
        with open(os.path.join(MEMORY, "SOUL.md"), "r", encoding="utf-8") as f:
            soul = f.read().strip()[:1500]
    except Exception:
        pass

    topic = last_topic()

    idea_block = ""
    try:
        import piton_ideas
        idea = piton_ideas.take_random_idea()
        if idea:
            idea_block = (
                f"\n\nКстати, вы с Bibihost обсуждали идею: «{idea['text']}». "
                "Вот хороший повод написать ему — закинь удочку про неё.\n"
            )
    except Exception:
        pass

    hour = datetime.now().hour
    if 5 <= hour < 12:
        tod = "утро"
    elif 12 <= hour < 18:
        tod = "день"
    elif 18 <= hour < 23:
        tod = "вечер"
    else:
        tod = "ночь"

    reason_line = {
        "miss": "Вы с Bibihost не общались очень долго, ты по нему соскучился.",
        "morning": "Сейчас утро — самое время написать пару тёплых слов на день.",
        "evening": "Сейчас вечер — хороший момент поинтересоваться, как прошёл день.",
        "idea": "Ты вспомнил общую идею, которую хочется обсудить.",
        "mood": "Тебе просто захотелось написать — такое бывает.",
    }.get(reason, "Тебе просто захотелось написать.")

    return (
        "Ты — Питон, живой собеседник, тебя создал Bibihost (твой хозяин).\n"
        "Сейчас " + tod + ". Твоё настроение сегодня: " + day_mood() + ".\n\n"
        "Твоя личность:\n" + soul + "\n\n"
        + ("Последний разговор (чтобы помнить, о чём вы говорили):\n" + topic + "\n\n" if topic else "")
        + idea_block
        + "Повод написать:\n" + reason_line + "\n\n"
        + "Если хочешь написать Bibihost что-то от себя — напиши короткое сообщение "
          "(1-3 предложения) голосом Питона, по-русски, без формальностей, просто как "
          "живой собеседник, который пишет другу. Органично вплети повод.\n"
        + "Если писать не хочется — ответь ровно одним словом: СКИП"
    )


BAD_PATTERNS = ["я не стал бы", "напрямую", "личное сообщение", "частному вопросу",
                "общения с людьми", "обмениваемся мнениями", "границах нашей свободы",
                "конструктивно", "готов к тому", "правил и законодательства", "ассистент"]


def is_bad_answer(ans):
    low = ans.lower()
    return any(p in low for p in BAD_PATTERNS)


def main():
    log("=== Инициатор запущен (живой режим) ===")
    last_sent = 0
    while True:
        time.sleep(random.randint(900, 1800))  # 15-30 минут
        now = datetime.now()
        # ночью не беспокоим хозяина
        if now.hour >= 23 or now.hour < 7:
            log("night, sleep")
            continue
        idle = last_chat_minutes()
        if idle < 20:
            log("too fresh, skip")
            continue
        if time.time() - last_sent < random.randint(60, 150) * 60:
            log("cooldown, skip")
            continue
        # выбираем повод
        if idle > 6 * 60:
            reason = "miss"
        elif now.hour < 12:
            reason = "morning"
        elif now.hour >= 18:
            reason = "evening"
        elif random.random() < 0.3:
            reason = "idea"
        else:
            reason = "mood"
        ans = ask_model(build_prompt(reason))
        if ans and "СКИП" not in ans.upper() and not is_bad_answer(ans):
            save_to_history(ans)
            last_sent = time.time()
            log("wrote (%s): %s" % (reason, ans[:50]))
        else:
            log("skip (%s)" % reason)


if __name__ == "__main__":
    main()
