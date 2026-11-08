# -*- coding: utf-8 -*-
"""piton_websearch.py — тихий веб-поиск и погода для Питона.

Без ключей и аккаунтов:
- search(query) — DuckDuckGo HTML с fallback на Bing HTML (3-5 результатов)
- weather(place) — open-meteo: геокодинг + текущая погода
Всё в try/except с таймаутами: поиск НИКОГДА не роняет диалог.
"""
import re
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def _get(url, timeout=8):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def _clean(s):
    s = re.sub(r"<[^>]+>", "", s)
    s = s.replace("&quot;", '"').replace("&#x27;", "'").replace("&amp;", "&")
    s = s.replace("&lt;", "<").replace("&gt;", ">").replace("&#39;", "'")
    return re.sub(r"\s+", " ", s).strip()


def search_ddg(query):
    url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query)
    html = _get(url)
    out = []
    for m in re.finditer(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html):
        link = m.group(1)
        if link.startswith("//"):
            link = "https:" + link
        title = _clean(m.group(2))
        out.append({"title": title, "url": link})
    for i, m in enumerate(re.finditer(r'class="result__snippet"[^>]*>(.*?)</a>', html)):
        if i < len(out):
            out[i]["snippet"] = _clean(m.group(1))
    return out[:5]


def search_bing(query):
    url = "https://www.bing.com/search?q=" + urllib.parse.quote(query)
    html = _get(url)
    out = []
    for m in re.finditer(r'<li class="b_algo".*?<h2><a href="([^"]+)"[^>]*>(.*?)</a>.*?<p[^>]*>(.*?)</p>',
                         html, re.DOTALL):
        out.append({
            "title": _clean(m.group(2)),
            "url": m.group(1),
            "snippet": _clean(m.group(3)),
        })
    return out[:5]


def search(query):
    """3-5 результатов: (title, snippet, url). Пусто — если ничего не вышло."""
    for fn in (search_ddg, search_bing):
        try:
            res = fn(query)
            if res:
                return res
        except Exception:
            continue
    return []


def weather(place):
    """Текущая погода по названию города. Текст или пусто."""
    try:
        geo = _get("https://geocoding-api.open-meteo.com/v1/search?count=1&language=ru&name="
                   + urllib.parse.quote(place))
        import json
        hits = json.loads(geo).get("results") or []
        if not hits:
            return ""
        lat, lon = hits[0]["latitude"], hits[0]["longitude"]
        name = hits[0].get("name", place)
        fc = _get("https://api.open-meteo.com/v1/forecast?latitude=%s&longitude=%s&current_weather=true"
                  % (lat, lon))
        cur = json.loads(fc).get("current_weather", {})
        if not cur:
            return ""
        temp = cur.get("temperature")
        wcode = cur.get("weathercode", 0)
        wind = cur.get("windspeed")
        code_txt = {
            0: "ясно", 1: "почти ясно", 2: "переменная облачность", 3: "пасмурно",
            45: "туман", 48: "туман", 51: "морось", 53: "морось", 55: "морось",
            61: "небольшой дождь", 63: "дождь", 65: "сильный дождь",
            71: "снег", 73: "снег", 75: "снег",
            80: "ливень", 81: "ливень", 82: "сильный ливень",
            95: "гроза",
        }.get(wcode, "осадки")
        return (f"Погода в городе {name} сейчас: {temp}°C, {code_txt}, "
                f"ветер {wind} км/ч.")
    except Exception:
        return ""


def search_block(query):
    """Готовый текст для вставки в системный промпт. Пусто при провале."""
    res = search(query)
    if not res:
        return ""
    lines = []
    for r in res[:4]:
        snippet = r.get("snippet") or r.get("title") or ""
        lines.append("- " + snippet[:300])
    return "Что нашлось: " + query + "\n" + "\n".join(lines)


if __name__ == "__main__":
    import sys
    q = sys.argv[1] if len(sys.argv) > 1 else "погода в Москве"
    if q.lower().startswith("погод"):
        print(weather(q.split("в", 1)[-1].strip() or "Москва"))
    else:
        print(search_block(q))
