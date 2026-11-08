# -*- coding: utf-8 -*-
"""
piton_ideas.py — копилка идей Питона.
Питон запоминает идеи/темы из разговоров и возвращается к ним позже.
Хранилище: %PITON_MEMORY%\\ideas.json (по умолчанию D:\\Piton_private\\PitonMemory)
"""
import datetime
import json
import os
import random

MEMORY = os.environ.get("PITON_MEMORY") or r"D:\Piton_private\PitonMemory"
IDEAS_FILE = os.path.join(MEMORY, "ideas.json")


def _load():
    if not os.path.exists(IDEAS_FILE):
        return []
    try:
        with open(IDEAS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save(ideas):
    os.makedirs(MEMORY, exist_ok=True)
    with open(IDEAS_FILE, "w", encoding="utf-8") as f:
        json.dump(ideas, f, ensure_ascii=False, indent=2)


def add_idea(text, source="хозяин"):
    """Сохранить идею. Возвращает новое количество идей."""
    text = (text or "").strip()
    if not text:
        return -1
    ideas = _load()
    # не дублируем одинаковые
    for idea in ideas:
        if idea.get("text", "").strip().lower() == text.lower():
            return len(ideas)
    ideas.append({
        "text": text,
        "source": source,
        "date": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
    })
    _save(ideas)
    return len(ideas)


def get_ideas(limit=50):
    """Список идей (последние limit)."""
    return _load()[-limit:]


def count():
    return len(_load())


def take_random_idea():
    """Взять случайную идею и УДАЛИТЬ её (использована). None если пусто."""
    ideas = _load()
    if not ideas:
        return None
    idea = random.choice(ideas)
    ideas.remove(idea)
    _save(ideas)
    return idea


def clear():
    _save([])
