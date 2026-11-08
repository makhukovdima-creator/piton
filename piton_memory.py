# -*- coding: utf-8 -*-
"""
piton_memory.py — векторная память Питона (RAG).

Принцип:
1) Берём chat_history.json, режем на «воспоминания» (пары user+ai).
2) Каждое воспоминание эмбеддим через nomic-embed-text (Ollama) → вектор.
3) По запросу эмбеддим вопрос и ищем ближайшие векторы (косинус).
4) Топ-N воспоминаний подмешиваем в промпт — Питон «вспоминает по смыслу».

Кэш: пересчитываем только если chat_history.json изменился (mtime + размер).
Эмбеддинг-модель маленькая (~280МБ), поиск — миллисекунды, ответ почти не грузит.
"""
import json
import os
import time
import urllib.request

MEMORY_DIR = os.environ.get("PITON_MEMORY") or r"D:\Piton_private\PitonMemory"
CHAT_FILE = os.path.join(MEMORY_DIR, "chat_history.json")
INDEX_FILE = os.path.join(MEMORY_DIR, "memory_index.json")
OLLAMA_URL = os.environ.get("OLLAMA_URL") or "http://127.0.0.1:11434"
EMBED_MODEL = "nomic-embed-text"
TOP_N = 3          # сколько воспоминаний подмешивать
MAX_LEN = 160      # обрезаем каждое воспоминание до ~N символов (токены!)

# разделители: «воспоминание» = строка с user + ai (или только ai от инициатора)
def _chunks(hist):
    out = []
    for m in hist:
        user = str(m.get("user", "") or "").strip()
        ai = str(m.get("ai", "") or "").strip()
        if not ai:
            continue
        if user:
            out.append("Хозяин: " + user + "\nПитон: " + ai)
        else:
            out.append("Питон (сам): " + ai)
    return out


def _embed(texts):
    """Эмбеддит список текстов через Ollama. Возвращает список векторов."""
    vecs = []
    for t in texts:
        body = json.dumps({"model": EMBED_MODEL, "prompt": t}).encode("utf-8")
        req = urllib.request.Request(
            OLLAMA_URL + "/api/embeddings", data=body,
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            vecs.append(data.get("embedding", []))
        except Exception:
            vecs.append([])
    return vecs


def _cos(a, b):
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _needs_rebuild():
    try:
        with open(INDEX_FILE, "r", encoding="utf-8") as f:
            idx = json.load(f)
        return idx.get("mtime", 0) != os.path.getmtime(CHAT_FILE)
    except Exception:
        return True


def build_index(force=False):
    """Пересобирает индекс, если файл истории изменился.
    Инкрементально: эмбеддим только НОВЫЕ записи (хвост), старые векторы не трогаем.
    Полная пересборка — только если история укоротилась или force=True."""
    if not force and not _needs_rebuild():
        return
    try:
        with open(CHAT_FILE, "r", encoding="utf-8") as f:
            hist = json.load(f)
    except Exception:
        return
    chunks = _chunks(hist)
    if not chunks:
        return
    # грузим старый индекс, если есть
    old_chunks, old_vecs, old_count = [], [], 0
    try:
        with open(INDEX_FILE, "r", encoding="utf-8") as f:
            old = json.load(f)
        old_chunks = old.get("chunks", [])
        old_vecs = old.get("vecs", [])
        old_count = old.get("count", len(old_chunks))
    except Exception:
        pass
    # история стала короче — пересобираем всё
    if len(chunks) < old_count:
        vecs = []
        for i in range(0, len(chunks), 16):
            vecs += _embed(chunks[i:i + 16])
        new_chunks, new_vecs, new_count = chunks, vecs, len(chunks)
    else:
        # добавляем только новые записи
        new_count = len(chunks)
        new_vecs = list(old_vecs)
        new_chunks = list(old_chunks)
        start = old_count
        if new_count > start:
            added = chunks[start:new_count]
            for i in range(0, len(added), 16):
                new_vecs += _embed(added[i:i + 16])
            new_chunks += added
        # подстраховка: если по какой-то причине векторов меньше — добиваем
        if len(new_vecs) < new_count:
            missing = chunks[len(new_vecs):new_count]
            for i in range(0, len(missing), 16):
                new_vecs += _embed(missing[i:i + 16])
            new_chunks = chunks[:new_count]
    idx = {
        "mtime": os.path.getmtime(CHAT_FILE),
        "count": new_count,
        "chunks": new_chunks,
        "vecs": new_vecs,
    }
    try:
        with open(INDEX_FILE, "w", encoding="utf-8") as f:
            json.dump(idx, f, ensure_ascii=False)
    except Exception:
        pass


def search(query, top_n=TOP_N):
    """Возвращает список релевантных воспоминаний (текст) по смыслу."""
    try:
        with open(INDEX_FILE, "r", encoding="utf-8") as f:
            idx = json.load(f)
    except Exception:
        return []
    chunks = idx.get("chunks", [])
    vecs = idx.get("vecs", [])
    if not chunks or not vecs:
        return []
    qv = _embed([query])
    if not qv or not qv[0]:
        return []
    scored = []
    for c, v in zip(chunks, vecs):
        s = _cos(qv[0], v)
        if s > 0.18:          # порог: слабые совпадения не тянем в промпт
            scored.append((s, c))
    scored.sort(key=lambda x: -x[0])
    return [c[:MAX_LEN] for _, c in scored[:top_n]]


def memory_block(query, top_n=TOP_N):
    """Готовый блок для вставки в system-промпт. Пустая строка — если ничего не нашли."""
    try:
        build_index()
        mem = search(query, top_n)
    except Exception:
        return ""
    if not mem:
        return ""
    lines = "\n".join("— " + m for m in mem)
    return ("[Твои воспоминания о прошлых разговорах с хозяином] (используй их, "
            "если они уместны к теме, но не выдумывай деталей):\n" + lines)


if __name__ == "__main__":
    build_index(force=True)
    print("индекс собран:", os.path.getsize(INDEX_FILE), "байт")
    q = input("вопрос: ")
    while q:
        for m in search(q):
            print("·", m.replace("\n", " / ")[:120])
        q = input("вопрос: ")
