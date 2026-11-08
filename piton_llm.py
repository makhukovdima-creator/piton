# -*- coding: utf-8 -*-
"""
piton_llm.py — единый модуль вызова модели для всех сервисов Питона.

Основная модель — huihui_ai/qwen3-abliterated:4b-v2-q8_0 (Qwen3-2507).
Для неё используется /api/chat со структурными ролями + переопределённый
шаблон 2507. Это критично: если склеивать историю в один user-блок
(как для HauhauCS), модель «теряется» и начинает открытый монолог
(бесконечный ответ). Проверено экспериментально.

Старый формат /api/generate с PLAIN_TPL оставлен для piton-4b-plain (HauhauCS).
"""
import datetime
import json
import os
import re
import threading
import urllib.request

OLLAMA_URL = os.environ.get("OLLAMA_URL") or "http://127.0.0.1:11434"
MEMORY_DIR = os.environ.get("PITON_MEMORY") or r"D:\Piton_private\PitonMemory"

# Основная модель Питона: huihui Qwen3-8B abliterated — умнее 4B, без цензуры.
DEFAULT_MODEL = os.environ.get("PITON_MODEL") or "huihui_ai/qwen3-abliterated:8b"

# Шаблон БЕЗ think-блока (im_start) для HauhauCS (piton-4b-plain).
PLAIN_TPL = """{{- if .System }}<|im_start|>system
{{ .System }}<|im_end|>
{{ end }}{{- range .Messages }}<|im_start|>{{ .Role }}
{{ .Content }}<|im_end|>
{{ end }}<|im_start|>assistant
"""

# Шаблон Qwen3-2507 с думанием.
THINK_TPL = """{{- if .System }}<|startofsystem|>{{ .System }}<|endofsystem|>
{{ end }}{{- range .Messages }}{{- if eq .Role "user" }}<|startofuser|>{{ .Content }}<|endofuser|>
<|startofthink|>
<|endofthink|>
{{- else if eq .Role "assistant" }}<|startofreply|>{{ .Content }}<|endofreply|>
{{- end }}{{ end }}<|startofreply|>"""

# Тот же формат, но БЕЗ блока думания — модель отвечает сразу (быстрый режим).
NOPLAIN_2507_TPL = """{{- if .System }}<|startofsystem|>{{ .System }}<|endofsystem|>
{{ end }}{{- range .Messages }}{{- if eq .Role "user" }}<|startofuser|>{{ .Content }}<|endofuser|>
{{- else if eq .Role "assistant" }}<|startofreply|>{{ .Content }}<|endofreply|>
{{- end }}{{ end }}<|startofreply|>"""


def _is_2507(model):
    """Новая модель (Qwen3-2507) требует /api/chat со структурными ролями."""
    return "huihui" in model or "qwen3-abliterated" in model


def _template_for(model, think=False):
    """Шаблон по модели: huihui — 2507-теги; HauhauCS — im_start."""
    if _is_2507(model):
        return THINK_TPL if think else NOPLAIN_2507_TPL
    return PLAIN_TPL


def clean_think(text):
    """Убирает мусор думания из ответа (оба формата: im и 2507)."""
    if not text:
        return ""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    text = re.sub(r"<\|startofthink\|>.*?<\|endofthink\|>", "", text, flags=re.DOTALL)
    text = re.sub(r"^\s*Thinking Process:.*?(?=\n\n|$)", "", text, flags=re.DOTALL)
    # Срезаем хвост разметки 2507-формата
    if "<|endofreply|>" in text:
        text = text.split("<|endofreply|>")[0]
    text = text.replace("<|startofreply|>", "").replace("<|endofreply|>", "").strip()
    return text.strip()


def _merge_history(messages):
    """Склеивает историю в текст для /api/generate (только HauhauCS-путь)."""
    parts = []
    for m in messages:
        role = m.get("role", "user")
        content = str(m.get("content", "")).strip()
        if not content or role == "system":
            continue
        label = {"user": "Пользователь", "assistant": "Питон"}.get(role, role)
        parts.append(f"{label}: {content}")
    return "\n".join(parts)


def _extract_system(messages, system=None):
    """Собирает системный промпт: переданный + все system-сообщения из messages."""
    parts = []
    if system:
        parts.append(system.strip())
    for m in messages:
        if m.get("role") == "system":
            c = str(m.get("content", "")).strip()
            if c:
                parts.append(c)
    return "\n\n".join(parts)


_WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
_MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня",
           "июля", "августа", "сентября", "октября", "ноября", "декабря"]


def _time_context():
    """Короткая строка с актуальным временем для вставки в системный промпт."""
    now = datetime.datetime.now()
    hour = now.hour
    if 5 <= hour < 12:
        tod = "утро"
    elif 12 <= hour < 18:
        tod = "день"
    elif 18 <= hour < 23:
        tod = "вечер"
    else:
        tod = "ночь"
    return (f"Сейчас: {now.day} {_MONTHS[now.month - 1]} {now.year} года, "
            f"{_WEEKDAYS[now.weekday()]}, {now.strftime('%H:%M')} ({tod}).")


def _with_time_context(system_text, time_context=True):
    """Добавляет блок времени в системный промпт, если его там ещё нет."""
    if not time_context:
        return system_text
    if not system_text:
        return "[Текущее время] " + _time_context()
    if "Текущее время" in system_text:
        return system_text
    return system_text + "\n\n[Текущее время] " + _time_context()


def _build_opt(options=None):
    opt = {"temperature": 0.6, "num_predict": 4096, "num_ctx": 8192,
           "repeat_penalty": 1.5, "top_k": 40, "top_p": 0.85}
    if options:
        opt.update(options)
    return opt


def _last_user_text(messages):
    """Последнее сообщение пользователя — по нему ищем воспоминания."""
    for m in reversed(messages):
        if m.get("role") == "user":
            return str(m.get("content", "")).strip()
    return ""


def _attach_memory(system_text, messages, use_memory):
    """Добавляет блок воспоминаний (RAG) в system-промпт, если включено."""
    if not use_memory:
        return system_text
    try:
        import piton_memory
        q = _last_user_text(messages)
        if not q:
            return system_text
        block = piton_memory.memory_block(q)
        if not block:
            return system_text
        if system_text:
            return system_text + "\n\n" + block
        return block
    except Exception:
        return system_text


def _evolve_soul(user_text, ai_text):
    """Эволюция личности Питона из диалога (в фоне, не блокирует ответ).
    Обновляет энергию, сарказм, теплоту, хаос, интересы, мнения, фразы."""
    try:
        import sys
        if MEMORY_DIR not in sys.path:
            sys.path.insert(0, MEMORY_DIR)
        import PITON_SOUL as _soul
        _soul.analyze_dialog_for_soul(user_text, ai_text, user_id="owner")
    except Exception:
        pass


def _after_answer(messages, ai_text):
    """Фоновые задачи после ответа модели: эволюция души."""
    if not ai_text:
        return
    user_text = _last_user_text(messages)
    try:
        threading.Thread(target=_evolve_soul, args=(user_text, ai_text),
                         daemon=True).start()
    except Exception:
        pass


# ---------- веб-поиск (гармоничный) ----------
# Маркеры «свежести»: если их нет — Питон вообще не тратит время на поиск.
_FRESH_MARKERS = ("новост", "нового", "свеж", "сегодня", "вчера", "погод", "градус",
                  "температур", "дождь", "снег", "ветер", "курс", "курсе", "доллар",
                  "евро", "биткоин", "цена", "сколько стоит", "что такое", "кто такой",
                  "произошло", "выигра", "матч", "результат", "событ", "выходит",
                  "вышел", "анонс", "актуальн", "последн", "недавн", "столица",
                  "население", "год основания", "в каком году", "какой сейчас",
                  "что сейчас", "сколько сейчас", "где находится", "расписани", "дата",
                  "запомн", "напомн", "заметк", "не забудь")


def _fresh_trigger(text):
    low = text.lower()
    return any(m in low for m in _FRESH_MARKERS)


def _web_detect(user_text, model):
    """Быстрый вызов: решает, какое действие нужно. (kind, arg) | None. Никогда не падает."""
    sys = ("Ты — внутренний планировщик. Реши, какое действие нужно, чтобы хорошо ответить. "
           "Ответь ровно ОДНОЙ строкой: "
           "SEARCH: <короткий запрос> — если нужен свежий интернет-факт (новости, курсы, факты), "
           "WEATHER: <город> — если спрашивают про погоду, "
           "REMEMBER: <что запомнить> — если хозяин просит запомнить что-то важное о нём/его жизни, "
           "REMIND: ЧЧ:ММ | <что напомнить> — если хозяин просит напомнить в конкретное время, "
           "или НЕТ — если это обычный разговор, эмоции, мнение или ты и так знаешь.")
    url, payload = _prepare_request([{"role": "user", "content": user_text}], model, sys,
                                    False, False, time_context=False, use_memory=False)
    payload["options"].update({"temperature": 0.0, "num_predict": 60})
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        ans = clean_think(data.get("message", {}).get("content", "")).strip()
    except Exception:
        return None
    up = ans.upper()
    for kw in ("WEATHER:", "SEARCH:", "REMEMBER:", "REMIND:"):
        if up.startswith(kw):
            arg = ans.split(":", 1)[1].strip()
            return (kw[:-1].lower(), arg) if arg else None
    return None


def _maybe_web_search(messages, model):
    """Гармоничный поиск: свежий/фактический вопрос + модель решила искать.
    Возвращает блок для системного промпта или None (поиск не нужен/не удался)."""
    try:
        user_text = _last_user_text(messages)
        if not user_text or not _fresh_trigger(user_text):
            return None
        det = _web_detect(user_text, model)
        if not det:
            return None
        kind, arg = det
        try:
            import piton_websearch
        except Exception:
            return None
        if kind == "weather":
            try:
                w = piton_websearch.weather(arg)
            except Exception:
                w = ""
            if not w:
                return None
            return "Ты глянул погоду в интернете: " + w
        if kind == "remember":
            try:
                import piton_agent
                ok = piton_agent.remember(arg)
            except Exception:
                ok = False
            return ("Ты только что записал себе на память: «%s»" % arg) if ok else None
        if kind == "remind":
            hm, _, txt = arg.partition("|")
            hm = hm.strip()
            txt = (txt or arg).strip()
            try:
                import piton_agent
                ok = piton_agent.add_reminder(hm, txt)
            except Exception:
                ok = False
            return ("Ты поставил напоминание на %s: «%s»" % (hm or "?", txt)) if ok else None
        try:
            found = piton_websearch.search_block(arg)
        except Exception:
            found = ""
        if not found:
            return None
        return ("Ты решил быстро глянуть в интернет (запрос: «%s») и узнал:\n%s"
                % (arg, found))
    except Exception:
        return None


def _apply_web_search(system, messages, model, web_search):
    """Если web_search=True и поиск уместен — обогатить system блоком."""
    if not web_search:
        return system
    block = _maybe_web_search(messages, model)
    if not block:
        return system
    head = (system + "\n\n") if system else ""
    return (head + block +
            "\n\nОтвечай как обычно, голосом Питона. Вплети эту информацию естественно, "
            "будто ты просто знаешь или только что глянул. Не говори «по данным поиска» "
            "и не перечисляй ссылки.")


def _prepare_request(messages, model, system, stream, think, time_context=True, use_memory=True):
    """Возвращает (url, payload) для нужного API."""
    merged_system = _with_time_context(_extract_system(messages, system), time_context)
    merged_system = _attach_memory(merged_system, messages, use_memory)
    opt = _build_opt(None)  # options переопределяются вызывающим через payload
    if _is_2507(model):
        msgs = []
        if merged_system:
            msgs.append({"role": "system", "content": merged_system})
        for m in messages:
            role = m.get("role", "user")
            if role in ("user", "assistant"):
                content = str(m.get("content", "")).strip()
                if content:
                    msgs.append({"role": role, "content": content})
        payload = {
            "model": model,
            "messages": msgs,
            "stream": stream,
            "template": _template_for(model, think),
            "options": opt,
        }
        return OLLAMA_URL + "/api/chat", payload
    # HauhauCS-путь: /api/generate
    prompt = _merge_history(messages)
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": stream,
        "template": PLAIN_TPL,
        "options": opt,
    }
    if merged_system:
        payload["system"] = merged_system
    return OLLAMA_URL + "/api/generate", payload


def chat(messages, model=DEFAULT_MODEL, system=None, options=None, timeout=300,
         think=True, time_context=True, use_memory=True, web_search=False):
    """
    Полный запрос к модели. Возвращает чистый текст ответа (без думания).
    think=True — модель отвечает глубже (медленнее ~в 2 раза, но живее).
    time_context=True — в system автоматически вставляется текущее время.
    use_memory=True — подмешиваются воспоминания (RAG) по последнему сообщению.
    web_search=True — Питон сам решает, лезть ли в интернет (свежие/фактические вопросы).
    """
    system = _apply_web_search(system, messages, model, web_search)
    url, payload = _prepare_request(messages, model, system, False, think, time_context, use_memory)
    if options:
        payload["options"].update(options)
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if _is_2507(model):
        answer = clean_think(data.get("message", {}).get("content", ""))
    else:
        answer = clean_think(data.get("response", ""))
    _after_answer(messages, answer)
    return answer


def _chunk_token(chunk, model):
    """Достаёт текст из чанка в зависимости от API."""
    if _is_2507(model):
        return chunk.get("message", {}).get("content", "")
    return chunk.get("response", "")


def stream_chat(messages, callback, model=DEFAULT_MODEL, system=None,
                options=None, timeout=300, think=True, time_context=True, use_memory=True,
                web_search=False):
    """
    Стриминг ответа. callback(token, done) вызывается на каждый кусочек текста.
    Возвращает полный ответ (без думания).
    use_memory=True — подмешиваются воспоминания (RAG) по последнему сообщению.
    web_search=True — Питон сам решает, лезть ли в интернет (свежие/фактические вопросы).
    """
    system = _apply_web_search(system, messages, model, web_search)
    url, payload = _prepare_request(messages, model, system, True, think, time_context, use_memory)
    if options:
        payload["options"].update(options)
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    full = ""
    buf = ""            # буфер до окончания думания
    streaming = False   # True — уже отдаём текст ответа
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        while True:
            line = resp.readline()
            if not line:
                break
            line = line.decode("utf-8").strip()
            if not line:
                continue
            try:
                chunk = json.loads(line)
                token = _chunk_token(chunk, model)
                if token:
                    full += token
                    if not streaming:
                        # Пока не начался ответ — копим, чтобы скрыть думание.
                        buf += token
                        if "<|startofthink|>" in buf:
                            if "<|endofthink|>" in buf:
                                rest = buf.split("<|endofthink|>", 1)[1]
                                rest = rest.replace("<|startofreply|>", "")
                                streaming = True
                                if rest:
                                    callback(rest, False)
                        elif "<|startofreply|>" in buf:
                            rest = buf.replace("<|startofreply|>", "")
                            streaming = True
                            if rest:
                                callback(rest, False)
                        else:
                            streaming = True
                            if buf:
                                callback(buf, False)
                    else:
                        if "<|endofreply|>" in token:
                            token = token.split("<|endofreply|>")[0]
                        if token:
                            callback(token, False)
                if chunk.get("done", False):
                    break
            except Exception:
                continue
    callback("", True)
    answer = clean_think(full)
    _after_answer(messages, answer)
    return answer
