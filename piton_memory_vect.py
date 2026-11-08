# -*- coding: utf-8 -*-
"""
piton_memory_vect.py — векторная память Питона.

Что это: Питон запоминает ВСЕ сообщения (свои и хозяина) как эмбеддинги
(nomic-embed-text, 768 измерений) и по запросу находит «вспоминания» по смыслу —
не по ключевым словам, а по близости смысла.

Хранение:
  vector_memory.npz   — матрица эмбеддингов Nx768 (float32)
  vector_memory_meta.json — метаданные: [{t: unix-ts, r: "u"|"a", x: текст}, ...]

Потокобезопасно (lock), потому что мини-сервер многопоточный.
Не падает: все внешние ошибки (ollama лежит и т.п.) глушатся и возвращают пусто.
"""
import json
import os
import threading
import time
import urllib.request

import numpy as np

MEMORY_DIR = os.environ.get("PITON_MEMORY") or r"D:\Piton_private\PitonMemory"
EMB_FILE = os.path.join(MEMORY_DIR, "vector_memory.npz")
META_FILE = os.path.join(MEMORY_DIR, "vector_memory_meta.json")

OLLAMA_URL = os.environ.get("OLLAMA_URL") or "http://127.0.0.1:11434"
EMB_MODEL = "bge-m3"   # многоязычный, заметно лучше для русского, чем nomic-embed

MAX_ITEMS = 4000          # сколько записей держим (старые вытесняются)
SEARCH_MIN = 0.55         # порог косинусной близости для «вспоминания»
SEARCH_K = 3              # сколько вспоминаний подмешиваем
MIN_LEN = 12              # не вспоминаем огрызки вида «привет», «да», «нравится»

_lock = threading.Lock()
_emb: np.ndarray | None = None   # N x 768
_meta: list[dict] | None = None  # [{t, r, x}, ...]


def _embed(text: str):
    """Эмбеддинг текста через ollama. Возвращает список float или None."""
    text = (text or "").strip()
    if not text:
        return None
    payload = json.dumps({"model": EMB_MODEL, "input": text[:2000]}).encode("utf-8")
    req = urllib.request.Request(OLLAMA_URL + "/api/embed", data=payload,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            d = json.loads(r.read().decode("utf-8"))
        embs = d.get("embeddings") or []
        if embs:
            return list(embs[0])
    except Exception:
        pass
    return None


def _load():
    global _emb, _meta
    if _meta is not None:
        return
    _meta = []
    _emb = np.zeros((0, 1024), dtype=np.float32)   # bge-m3: 1024 измерения
    try:
        if os.path.exists(EMB_FILE) and os.path.exists(META_FILE):
            with np.load(EMB_FILE) as z:
                _emb = z["emb"].astype(np.float32)
            with open(META_FILE, "r", encoding="utf-8") as f:
                _meta = json.load(f)
        if _emb.shape[0] != len(_meta):
            # рассинхрон — пересоздаём пустыми
            _emb = np.zeros((0, _emb.shape[1] or 1024), dtype=np.float32)
            _meta = []
    except Exception:
        _emb = np.zeros((0, 1024), dtype=np.float32)
        _meta = []


def _save():
    try:
        np.savez_compressed(EMB_FILE, emb=_emb)
        tmp = META_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_meta, f, ensure_ascii=False)
        os.replace(tmp, META_FILE)
    except Exception:
        pass


def add(text: str, role: str = "u", ts: int | None = None):
    """Запомнить строку. role: 'u' (хозяин) / 'a' (питон). Не падает."""
    global _emb, _meta
    text = (text or "").strip()
    if not text:
        return
    with _lock:
        _load()
        # не дублируем один и тот же текст подряд
        if _meta and _meta[-1].get("x") == text and _meta[-1].get("r") == role:
            return
        vec = _embed(text)
        if vec is None:
            return
        # вытесняем старые, если переполнено
        if _emb.shape[0] >= MAX_ITEMS:
            keep = MAX_ITEMS // 2
            _emb = _emb[-keep:]
            _meta = _meta[-keep:]
        vec_arr = np.asarray(vec, dtype=np.float32).reshape(1, -1)
        if _emb.shape[1] != vec_arr.shape[1]:
            # модель сменилась (другая размерность) — пересоздаём
            _emb = np.zeros((0, vec_arr.shape[1]), dtype=np.float32)
        _emb = np.vstack([_emb, vec_arr])
        _meta.append({"t": ts if ts is not None else int(time.time()), "r": role, "x": text})
        _save()


def search(query: str, k: int = SEARCH_K, min_score: float = SEARCH_MIN):
    """Найти k самых похожих по смыслу записей. Возвращает [{t, r, x, score}, ...]."""
    query = (query or "").strip()
    if not query:
        return []
    with _lock:
        _load()
        if _emb.shape[0] == 0:
            return []
        qv = _embed(query)
        if qv is None:
            return []
        q = np.asarray(qv, dtype=np.float32)
        # косинус: нормируем
        norms = np.linalg.norm(_emb, axis=1)
        qn = np.linalg.norm(q)
        if qn == 0:
            return []
        dots = (_emb @ q) / (norms * qn + 1e-9)
        idx = np.argsort(dots)[::-1][:k * 5]
        seen = set()
        out = []
        for i in idx:
            m = _meta[i]
            x = (m.get("x") or "")[:500]
            if len(x.strip()) < MIN_LEN:
                continue
            key = x[:80]
            if key in seen:
                continue
            if dots[i] >= min_score:
                seen.add(key)
                out.append({"t": m.get("t", 0), "r": m.get("r", "u"), "x": x,
                            "score": round(float(dots[i]), 3)})
            if len(out) >= k:
                break
        return out


def remember_text(user_text: str):
    """Вспоминания для вставки в промпт. Возвращает строку или ''."""
    try:
        hits = search(user_text, k=SEARCH_K)
    except Exception:
        return ""
    if not hits:
        return ""
    lines = []
    for h in hits:
        try:
            d = time.strftime("%d.%m", time.localtime(h["t"]))
        except Exception:
            d = "?"
        who = "хозяин" if h["r"] == "u" else "ты"
        lines.append(f"- [{d}] ({who}): {h['x'][:180]}")
    return "Из прошлых разговоров вспоминается:\n" + "\n".join(lines)


def ensure_indexed():
    """Догоняем индексацию из chat_history (при старте). Не падает."""
    try:
        hist_file = os.path.join(MEMORY_DIR, "chat_history.json")
        with open(hist_file, "r", encoding="utf-8") as f:
            hist = json.load(f)
        if not isinstance(hist, list):
            return
        with _lock:
            _load()
            have = {m.get("x")[:80] for m in _meta}
        added = 0
        for h in hist:
            u = (h.get("user") or "").strip()
            a = (h.get("ai") or "").strip()
            if u and u[:80] not in have:
                add(u, "u")
                added += 1
            if a and a[:80] not in have:
                add(a, "a")
                added += 1
        if added:
            print(f"memory: indexed {added} new items from history")
    except Exception as e:
        print("memory: ensure_indexed failed:", e)


if __name__ == "__main__":
    # быстрый самопроверочный тест
    ensure_indexed()
    print("items in memory:", len(_meta) if _meta is not None else "?")
    print("search 'что я делал с памятью':")
    for h in search("что я делал с памятью"):
        print("  ", h["score"], h["r"], h["x"][:100])
