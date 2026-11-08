# -*- coding: utf-8 -*-
"""piton_hub_server.py — Питон-сервер (headless хаб).

Веб-панель управления всеми сервисами Питона + супервизор:
- при старте сам поднимает серверные сервисы (mini, ngrok, beacon, initiator, diary, reflect)
- раз в ~25 секунд проверяет, что они живы, и перезапускает упавшие
- веб-панель на http://<ip>:8890 : статусы, старт/стоп по кнопке, логи, ollama, RAM/CPU

Запуск: pythonw piton_hub_server.py
"""
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = r"D:\Piton"
MEMORY = os.environ.get("PITON_MEMORY") or r"D:\Piton_private\PitonMemory"
LOGS_DIR = os.path.join(MEMORY, "logs")
PYW = r"C:\Users\Honor\AppData\Local\Programs\Python\Python313\pythonw.exe"
HIDDEN = subprocess.CREATE_NO_WINDOW
PORT = 8890

os.makedirs(LOGS_DIR, exist_ok=True)

SERVICES = [
    {"key": "mini",      "name": "Мини-сервер",           "icon": "🌐", "script": "piton_mini_server.py", "core": True},
    {"key": "ngrok",     "name": "Туннель ngrok",         "icon": "🔗", "script": "ngrok_watchdog.py",    "core": True},
    {"key": "beacon",    "name": "Маяк (адрес для телефона)", "icon": "📡", "script": "piton_beacon.py",   "core": True},
    {"key": "initiator", "name": "Инициатор",             "icon": "💬", "script": "piton_initiator.py",   "core": True},
    {"key": "diary",     "name": "Дневник",               "icon": "📔", "script": "piton_diary.py",        "core": True},
    {"key": "reflect",   "name": "Демон снов",            "icon": "💭", "script": "piton_reflect.py",     "core": True},
    {"key": "overlay",   "name": "Оверлей",               "icon": "🖥️", "script": "piton_overlay.py",     "core": False},
    {"key": "chess",     "name": "Шахматы (сам с собой)", "icon": "♞", "script": "piton_chess_ai.py",    "core": False},
]
SCRIPTS = [s["script"] for s in SERVICES]

# ---------- логи ----------
def hub_log(m):
    try:
        with open(os.path.join(LOGS_DIR, "hub_server.log"), "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now():%H:%M:%S}] {m}\n")
    except Exception:
        pass


# ---------- процессы / запуск (как в старом хабе) ----------
def running_counts():
    counts = {s["script"]: 0 for s in SERVICES}
    try:
        import psutil
        for p in psutil.process_iter(["name", "cmdline"]):
            try:
                if "ngrok_watchdog.py" in counts and p.info["name"] == "ngrok.exe":
                    counts["ngrok_watchdog.py"] += 1
                    continue
                if p.info["name"] not in ("pythonw.exe", "python.exe"):
                    continue
                cmd = " ".join(p.info["cmdline"] or [])
                for script in counts:
                    if script in cmd:
                        counts[script] += 1
            except Exception:
                continue
    except Exception:
        pass
    return counts


def start_service(script):
    logf = os.path.join(LOGS_DIR, script.replace(".py", ".log"))
    try:
        log_handle = open(logf, "a", encoding="utf-8", errors="replace")
    except Exception:
        log_handle = None
    subprocess.Popen([PYW, os.path.join(BASE, script)], cwd=BASE,
                     creationflags=HIDDEN,
                     stdout=log_handle, stderr=subprocess.STDOUT)


def stop_service(script):
    try:
        import psutil
        for p in psutil.process_iter(["name", "cmdline"]):
            try:
                if p.info["name"] in ("pythonw.exe", "python.exe"):
                    cmd = " ".join(p.info["cmdline"] or [])
                    if script in cmd:
                        p.kill()
            except Exception:
                continue
    except Exception:
        pass
    if script == "ngrok_watchdog.py":
        try:
            import psutil
            for p in psutil.process_iter(["name"]):
                try:
                    if p.info["name"] == "ngrok.exe":
                        p.kill()
                except Exception:
                    continue
        except Exception:
            pass


# ---------- ollama ----------
def ollama_alive():
    try:
        urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=2)
        return True
    except Exception:
        return False


_last_ollama_start = 0.0


def ollama_ensure_up():
    global _last_ollama_start
    if ollama_alive():
        return
    now = time.time()
    if now - _last_ollama_start < 60:
        return
    _last_ollama_start = now
    exe = r"D:\Ollama\ollama.exe"
    if not os.path.exists(exe):
        hub_log("⚠ ollama: бинарь не найден: " + exe)
        return
    try:
        subprocess.Popen([exe, "serve"], creationflags=0x08000000,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        hub_log("↑ ollama: поднял serve (была упала)")
    except Exception as e:
        hub_log("⚠ ollama: не смог поднять: " + str(e))


# ---------- ошибки из логов сервисов ----------
ERROR_MARKERS = ("error", "traceback", "exception", "failed", "fatal")
NOISE_MARKERS = ("10053", "10054", "getaddrinfo", "nameresolution",
                 "max retries", "connectionerror", "connectionreset",
                 "connectionaborted", "broken pipe", "timed out", "timeout")

_log_pos = {}


def collect_errors(counts):
    out = []
    for svc in SERVICES:
        if counts.get(svc["script"], 0) <= 0:
            continue
        logf = os.path.join(LOGS_DIR, svc["script"].replace(".py", ".log"))
        if not os.path.exists(logf):
            continue
        try:
            with open(logf, "r", encoding="utf-8", errors="replace") as f:
                f.seek(_log_pos.get(logf, 0))
                data = f.read()
                _log_pos[logf] = f.tell()
            for line in data.splitlines():
                low = line.lower()
                if any(k in low for k in NOISE_MARKERS):
                    continue
                if any(k in low for k in ERROR_MARKERS):
                    out.append((svc["name"], line.strip()))
        except Exception:
            continue
    return out


# ---------- супервизор ----------
def supervisor_loop():
    hub_log("супервизор запущен")
    while True:
        try:
            counts = running_counts()
            ollama_ensure_up()
            for svc in SERVICES:
                if not svc["core"]:
                    continue
                if counts.get(svc["script"], 0) <= 0:
                    hub_log("↻ рестарт: " + svc["name"])
                    start_service(svc["script"])
        except Exception as e:
            hub_log("supervisor err: " + str(e))
        time.sleep(25)


def bootstrap():
    """Поднять все серверные сервисы при старте."""
    counts = running_counts()
    for svc in SERVICES:
        if not svc["core"]:
            continue
        if counts.get(svc["script"], 0) <= 0:
            hub_log("▶ старт при загрузке: " + svc["name"])
            start_service(svc["script"])


# ---------- веб-панель ----------
PAGE = """<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Piton Server 🐍</title>
<style>
 body{background:#0d1117;color:#e6edf3;font:14px/1.5 'Segoe UI',sans-serif;margin:0;padding:20px}
 h1{font-size:22px;margin:0 0 4px}.sub{color:#8b949e;margin-bottom:16px}
 .cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:10px}
 .card{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:12px}
 .card h3{margin:0 0 4px;font-size:15px}
 .st{color:#8b949e;font:12px Consolas,monospace}.up{color:#3fb950}.down{color:#f85149}
 button{margin-top:8px;padding:6px 14px;border:0;border-radius:6px;cursor:pointer;color:#fff;font-weight:600}
 .bstart{background:#2ea043}.bstop{background:#f85149}
 .bar{display:flex;gap:14px;margin:14px 0;flex-wrap:wrap}
 .pill{background:#161b22;border:1px solid #30363d;border-radius:20px;padding:6px 14px;font:12px Consolas,monospace}
 .err{color:#ff7b72;font:12px Consolas,monospace;white-space:pre-wrap}
 .log{background:#11151d;border:1px solid #30363d;border-radius:8px;padding:10px;font:12px Consolas,monospace;color:#3fb950;max-height:260px;overflow:auto;white-space:pre-wrap}
</style></head><body>
<h1>🐍 Piton Server</h1><div class="sub" id="sub">headless-хаб: статусы, старт/стоп, логи</div>
<div class="bar" id="bar"></div>
<div class="cards" id="cards"></div>
<div class="bar"><div class="pill">Логи</div></div>
<div class="log" id="log"></div>
<script>
const tok = new URL(location.href).searchParams.get('token') || '';
const H = tok ? {'X-Auth-Token':tok} : {};
async function api(p, opt){ const r = await fetch(p, Object.assign({headers:H}, opt||{})); return r.json(); }
async function refresh(){
  const s = await api('/api/status');
  document.getElementById('sub').textContent = 'ollama: ' + (s.ollama?'РАБОТАЕТ':'НЕ ДОСТУПЕН') + ' · CPU ' + s.cpu + '% · RAM ' + s.ram + '%';
  let bar = '<div class="pill">🐍 Сервисов: '+s.running+'/'+s.total+'</div>';
  bar += '<div class="pill">'+new Date(s.ts)+'</div>';
  document.getElementById('bar').innerHTML = bar;
  let c = '';
  for (const x of s.services){
    const up = x.count > 0;
    c += '<div class="card"><h3>'+x.icon+' '+x.name+'</h3><div class="st">'+(up?'<span class="up">● работает</span>':'<span class="down">● остановлен</span>')+' · процессов: '+x.count+'</div>'
      + '<button class="'+(up?'bstop':'bstart')+'" onclick="tog(\''+x.script+'\')">'+(up?'■ Стоп':'▶ Старт')+'</button></div>';
  }
  document.getElementById('cards').innerHTML = c;
  let e = '';
  for (const [n,t] of (s.errors||[])) e += n+': '+t+'\\n';
  document.getElementById('log').textContent = e || 'ошибок нет';
}
async function tog(script){
  await api('/api/toggle', {method:'POST', headers:Object.assign({'Content-Type':'application/json'}, H), body:JSON.stringify({script})});
  setTimeout(refresh, 1200);
}
refresh(); setInterval(refresh, 4000);
</script></body></html>"""


def get_token():
    try:
        with open(os.path.join(MEMORY, "piton_config.json"), "r", encoding="utf-8") as f:
            return (json.load(f).get("mini_app_token") or "").strip()
    except Exception:
        return ""


class HubHandler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _auth_ok(self):
        tok = get_token()
        if not tok:
            return True
        return self.headers.get("X-Auth-Token") == tok or self.path.split("token=")[-1].split("&")[0] == tok

    def do_GET(self):
        if not self._auth_ok():
            self._send(403, b'{"error":"auth"}')
            return
        path = self.path.split("?")[0]
        if path == "/":
            self._send(200, PAGE.encode("utf-8"), "text/html")
            return
        if path == "/api/status":
            counts = running_counts()
            ollama_ensure_up()
            try:
                import psutil
                ram = psutil.virtual_memory().percent
                cpu = psutil.cpu_percent(interval=0.3)
            except Exception:
                ram = cpu = -1
            services = [{"key": s["key"], "name": s["name"], "icon": s["icon"],
                         "script": s["script"], "count": counts.get(s["script"], 0)}
                        for s in SERVICES]
            n_running = sum(1 for s in services if s["count"] > 0)
            data = json.dumps({
                "services": services, "running": n_running, "total": len(SERVICES),
                "ollama": ollama_alive(), "ram": ram, "cpu": cpu,
                "errors": collect_errors(counts), "ts": datetime.now().strftime("%H:%M:%S"),
            }, ensure_ascii=False).encode("utf-8")
            self._send(200, data)
            return
        self._send(404, b'{"error":"not found"}')

    def do_POST(self):
        if not self._auth_ok():
            self._send(403, b'{"error":"auth"}')
            return
        if self.path.split("?")[0] != "/api/toggle":
            self._send(404, b'{"error":"not found"}')
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length).decode("utf-8"))
            script = (data.get("script") or "").strip()
            if script not in SCRIPTS:
                self._send(400, b'{"error":"bad script"}')
                return
            counts = running_counts()
            if counts.get(script, 0) > 0:
                stop_service(script)
                hub_log("■ остановлен: " + script)
            else:
                start_service(script)
                hub_log("▶ запущен: " + script)
            self._send(200, b'{"ok":true}')
        except Exception as e:
            self._send(500, json.dumps({"error": str(e)}).encode("utf-8"))

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    try:
        import piton_guard
        piton_guard.ensure_single("piton_hub_server.py")
    except Exception:
        pass
    try:
        probe = ThreadingHTTPServer(("0.0.0.0", PORT), HubHandler)
        probe.server_close()
    except OSError:
        print("Port", PORT, "already in use — hub server уже запущен?")
        sys.exit(0)
    hub_log("=== Питон-сервер (веб-хаб) запущен на :" + str(PORT) + " ===")
    bootstrap()
    threading.Thread(target=supervisor_loop, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", PORT), HubHandler).serve_forever()
