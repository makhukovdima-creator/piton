# -*- coding: utf-8 -*-
"""
Piton Mini Server — локальный веб-чат для APK-приложения Питона.
- Отдаёт веб-чат (тёмный терминальный стиль) по GET /
- POST /api/chat — отправляет сообщение в Ollama, пишет в общий chat_history.json
- GET /api/history — отдаёт историю (синхронизация с desktop/overlay/консолью)
- GET /api/health — проверка что сервер жив

Запуск: pythonw piton_mini_server.py
Порт: 8765 (поменять при необходимости)
"""
import http.server
import json
import os
import threading
import time
import urllib.request
import urllib.parse
import sys

# ============ CONFIG ============
MEMORY_DIR = os.environ.get("PITON_MEMORY") or r"D:\Piton_private\PitonMemory"
HISTORY_FILE = os.path.join(MEMORY_DIR, "chat_history.json")
SOUL_FILE = os.path.join(MEMORY_DIR, "SOUL.md")
OLLAMA_URL = os.environ.get("OLLAMA_URL") or "http://127.0.0.1:11434"
PITON_MODEL = "huihui_ai/qwen3-abliterated:8b"
VISION_MODEL = "huihui_ai/qwen2.5-vl-abliterated:3b-instruct"
PORT = 8765
MAX_HISTORY = 200
MAX_BODY = 8 * 1024 * 1024  # до 8 МБ — чтобы влезали фотки (base64)
ERROR_LOG = os.path.join(MEMORY_DIR, "mini_app_error.log")

os.makedirs(MEMORY_DIR, exist_ok=True)
sys.path.insert(0, MEMORY_DIR)
try:
    import PITON_BRAIN as piton_brain
except Exception:
    piton_brain = None

try:
    import piton_memory_vect as pmv
except Exception:
    pmv = None


def log_error(msg):
    try:
        with open(ERROR_LOG, "a", encoding="utf-8") as f:
            f.write(time.strftime("[%H:%M:%S] ") + str(msg) + "\n")
    except Exception:
        pass


# ============ AUTH ============
def get_auth_token():
    try:
        with open(os.path.join(MEMORY_DIR, "piton_config.json"), "r", encoding="utf-8") as f:
            return (json.load(f).get("mini_app_token") or "").strip()
    except Exception:
        return ""


def ensure_token():
    """Создаёт токен в конфиге, если его нет."""
    cfg_path = os.path.join(MEMORY_DIR, "piton_config.json")
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}
    if not cfg.get("mini_app_token"):
        import secrets
        cfg["mini_app_token"] = secrets.token_hex(16)
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    return cfg["mini_app_token"]


# ============ ИСТОРИЯ ============
def load_history():
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
    except Exception:
        pass
    return []


def save_history(history):
    # атомарная запись: сначала во временный файл, потом переименовать —
    # чтобы при сбое/выключении история не повредилась
    try:
        tmp = HISTORY_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(history[-MAX_HISTORY:], f, ensure_ascii=False, indent=2)
        os.replace(tmp, HISTORY_FILE)
    except Exception as e:
        log_error("save_history: " + str(e))


def get_system_prompt():
    try:
        with open(SOUL_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


# ============ OLLAMA ============
def _mark_owner_active():
    """Питон запоминает: хозяин только что писал (для демона снов/инициатора)."""
    try:
        import PITON_SOUL as _psoul
        _psoul.set_last_user_ts()
    except Exception:
        pass


VISION_NOTE = ("Ты смотришь на изображение, которое прислал хозяин, — видишь его прямо сейчас, своими глазами. "
               "Расскажи о нём естественно и живо, как в обычном разговоре: что на нём, что происходит, детали, "
               "настроение. Шути и комментируй как человек. Никогда не используй канцелярские шаблоны вида "
               "«на данном изображении изображено», «на фото показано», «я вижу изображение». Просто говори, что видишь.")


def _ask_vision(user_text, image):
    """Vision-запрос напрямую в Ollama (с картинкой), мимо piton_llm (тот режет images)."""
    history = load_history()
    messages = []
    sp = get_system_prompt()
    if sp:
        cut = sp.find("══")
        sp = (sp[:cut].strip() if cut > 0 else sp[:600].strip())
    sp = (sp + "\n\n" + VISION_NOTE).strip() if sp else VISION_NOTE
    if pmv is not None:
        try:
            mem = pmv.remember_text(user_text)
            if mem:
                sp = (sp + "\n\n" + mem).strip()
        except Exception:
            pass
    messages.append({"role": "system", "content": sp})
    for h in history[-2:]:
        u = h.get("user", "")
        a = h.get("ai", "")
        if u and not u.startswith("📷"):
            messages.append({"role": "user", "content": u})
        if a:
            messages.append({"role": "assistant", "content": a})
    messages.append({"role": "user", "content": user_text, "images": [image]})
    payload = {"model": VISION_MODEL, "messages": messages, "stream": False,
               "options": {"temperature": 0.6, "num_predict": 1024}}
    req = urllib.request.Request(OLLAMA_URL + "/api/chat",
                                 data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data.get("message", {}).get("content", "").strip()


def ask_ollama(user_text, image=None):
    """Отправляет запрос в Ollama со всей историей + системным промтом."""
    if image:
        return _ask_vision(user_text, image)
    _mark_owner_active()
    history = load_history()
    messages = []
    sp = get_system_prompt()
    # Компактный промпт: модель уже знает свою личность (вшита в piton-gemma),
    # полный SOUL дублировал её и раздувал контекст. Берём только вступление.
    if sp:
        cut = sp.find("══")
        sp = (sp[:cut].strip() if cut > 0 else sp[:600].strip())
    if piton_brain is not None and sp:
        sp = piton_brain.get_dynamic_prompt(sp, user_text, user_id="owner")
    try:
        import PITON_SOUL as _psoul
        _self = _psoul.get_self_prompt(300)
        if _self:
            sp = (sp + chr(10)*2 + _self).strip() if sp else _self
    except Exception:
        pass
    if sp:
        messages.append({"role": "system", "content": sp})
    # выжимка старых разговоров (память о прошлом)
    try:
        with open(os.path.join(MEMORY_DIR, "summary.json"), "r", encoding="utf-8") as f:
            summ = json.load(f).get("summary", "").strip()
    except Exception:
        summ = ""
    if summ:
        messages.append({"role": "system", "content": "Память о старых разговорах (кратко): " + summ[:250]})
    # векторные воспоминания: Питон сам находит похожие по смыслу прошлые реплики
    if pmv is not None:
        try:
            mem = pmv.remember_text(user_text)
            if mem:
                messages.append({"role": "system", "content": mem})
        except Exception:
            pass
    for h in history[-2:]:
        u = h.get("user", "")
        a = h.get("ai", "")
        if u:
            messages.append({"role": "user", "content": u})
        if a:
            messages.append({"role": "assistant", "content": a})
    messages.append({"role": "user", "content": user_text})

    import piton_llm
    return piton_llm.chat(messages, model=PITON_MODEL,
                          options={"temperature": 0.6, "num_predict": 4096},
                          timeout=180, web_search=True)


def ask_ollama_stream(user_text, wfile):
    """Стриминг ответа в SSE (data: {...}), в конце сохраняет в общую историю."""
    _mark_owner_active()
    history = load_history()
    messages = []
    sp = get_system_prompt()
    if sp:
        cut = sp.find("══")
        sp = (sp[:cut].strip() if cut > 0 else sp[:600].strip())
    if piton_brain is not None and sp:
        sp = piton_brain.get_dynamic_prompt(sp, user_text, user_id="owner")
    try:
        import PITON_SOUL as _psoul
        _self = _psoul.get_self_prompt(300)
        if _self:
            sp = (sp + chr(10)*2 + _self).strip() if sp else _self
    except Exception:
        pass
    if sp:
        messages.append({"role": "system", "content": sp})
    try:
        with open(os.path.join(MEMORY_DIR, "summary.json"), "r", encoding="utf-8") as f:
            summ = json.load(f).get("summary", "").strip()
    except Exception:
        summ = ""
    if summ:
        messages.append({"role": "system", "content": "Память о старых разговорах (кратко): " + summ[:250]})
    # векторные воспоминания: Питон сам находит похожие по смыслу прошлые реплики
    if pmv is not None:
        try:
            mem = pmv.remember_text(user_text)
            if mem:
                messages.append({"role": "system", "content": mem})
        except Exception:
            pass
    for h in history[-2:]:
        u = h.get("user", "")
        a = h.get("ai", "")
        if u:
            messages.append({"role": "user", "content": u})
        if a:
            messages.append({"role": "assistant", "content": a})
    messages.append({"role": "user", "content": user_text})

    import piton_llm

    def _cb(token, done):
        if token:
            try:
                sse = "data: " + json.dumps({"message": {"content": token}, "done": done}) + "\n\n"
                wfile.write(sse.encode("utf-8"))
                wfile.flush()
            except Exception:
                pass
    text = piton_llm.stream_chat(messages, _cb, model=PITON_MODEL,
                                 options={"temperature": 0.6, "num_predict": 4096},
                                 timeout=180, web_search=True)
    try:
        import piton_snake
        emo = piton_snake.emotion_for_text(user_text, text)
    except Exception:
        emo = None
    try:
        wfile.write(("data: " + json.dumps({"done": True, "emotion": emo}) + "\n\n").encode("utf-8"))
        wfile.flush()
    except Exception:
        pass
    # после стрима сохраняем в историю (текст вернулся из stream_chat)
    if text:
        try:
            ts = time.strftime("%H:%M")
            history = load_history()
            history.append({"timestamp": ts, "user": user_text, "ai": text})
            save_history(history)
            try:
                if pmv is not None:
                    pmv.add(user_text, "u")
                    pmv.add(text, "a")
            except Exception:
                pass
            try:
                if piton_brain is not None:
                    piton_brain.analyze_exchange(user_text, text, user_id="owner")
            except Exception:
                pass
        except Exception as e:
            log_error("stream save: " + str(e))
    return text


# ============ HTML ============
HTML_PAGE = """
<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no">
<title>Piton Mini</title>
<style>
  :root { --bg:#0b0b12; --card:#14141f; --green:#4ade80; --gray:#6b7280; --blue:#60a5fa; --text:#e5e7eb; }
  * { margin:0; padding:0; box-sizing:border-box; -webkit-tap-highlight-color:transparent; }
  html,body { height:100%; }
  body { background:var(--bg); color:var(--text); font-family:Consolas,Menlo,monospace;
         display:flex; flex-direction:column; height:100vh; overflow:hidden; }
  header { background:var(--card); padding:10px 14px; display:flex; align-items:center; gap:10px;
           border-bottom:1px solid #1f1f2e; flex-shrink:0; }
  header .dot { width:10px; height:10px; border-radius:50%; background:var(--green); box-shadow:0 0 8px var(--green); }
  header .dot.off { background:#ef4444; box-shadow:0 0 8px #ef4444; }
  header .title { color:var(--green); font-weight:bold; font-size:15px; }
  header .sub { color:var(--gray); font-size:11px; margin-left:auto; }
  #chat { flex:1; overflow-y:auto; padding:12px 14px; display:flex; flex-direction:column; gap:10px; }
  .msg { max-width:85%; padding:8px 12px; border-radius:10px; font-size:14px; line-height:1.45;
         white-space:pre-wrap; word-wrap:break-word; }
  .msg.user { align-self:flex-end; background:#1e2a3a; color:var(--text); }
  .msg.user .who { color:var(--blue); font-weight:bold; }
  .msg.ai { align-self:flex-start; background:#0f1a12; color:var(--green); border:1px solid #1a2e1f; }
  .msg.ai .who { color:var(--green); font-weight:bold; }
  .msg .ts { display:block; font-size:10px; color:var(--gray); margin-top:4px; }
  .sys { align-self:center; color:var(--gray); font-size:12px; text-align:center; padding:4px 8px; }
  #inputbar { flex-shrink:0; display:flex; align-items:center; gap:6px; padding:8px 10px;
              background:var(--card); border-top:1px solid #1f1f2e; }
  #inp { flex:1; min-width:0; background:var(--bg); color:var(--text); border:1px solid #2a2a3d;
         border-radius:10px; padding:9px 10px; font-size:15px; font-family:inherit; outline:none; }
  #inp:focus { border-color:var(--green); }
  #send { width:44px; height:40px; flex-shrink:0; padding:0; background:var(--green); color:#0b0b12;
          border:none; border-radius:10px; font-size:18px; font-weight:bold; line-height:1; cursor:pointer; }
  #send:active { opacity:0.7; }
  #send:disabled { opacity:0.4; }
  .typing { color:var(--gray); font-size:12px; padding:2px 4px; align-self:flex-start; }
</style>
</head>
<body>
<header>
  <span class="dot" id="dot"></span>
  <span class="title">🐍 Piton — живой друг</span>
  <span class="sub" id="status">...</span>
</header>
<div id="chat"></div>
<div id="inputbar">
  <input id="inp" placeholder="просто пиши..." autocomplete="off">
  <button id="send">➤</button>
</div>
<script>
var chat=document.getElementById('chat');
var inp=document.getElementById('inp');
var send=document.getElementById('send');
var statusEl=document.getElementById('status');
var dot=document.getElementById('dot');
var busy=false;
var AUTH=(location.search.match(/[?&]token=([^&]+)/)||[])[1]||'';
function authFetch(url,opts){
  opts=opts||{};
  opts.headers=opts.headers||{};
  opts.headers['X-Auth-Token']=AUTH;
  return fetch(url,opts);
}

function addMsg(who,text,ts){
  var div=document.createElement('div');
  div.className='msg '+who;
  var whoLabel=who==='user'?'You':'Piton';
  div.innerHTML='<span class="who">'+whoLabel+'&gt;</span> '+escapeHtml(text)+(ts?'<span class="ts">'+ts+'</span>':'');
  chat.appendChild(div);
  chat.scrollTop=chat.scrollHeight;
}
function addSys(text){
  var div=document.createElement('div');
  div.className='sys';
  div.textContent=text;
  chat.appendChild(div);
  chat.scrollTop=chat.scrollHeight;
}
function escapeHtml(s){
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}
function getTs(){
  var d=new Date();
  return ('0'+d.getHours()).slice(-2)+':'+('0'+d.getMinutes()).slice(-2);
}
function loadHistory(){
  authFetch('/api/history').then(function(r){return r.json();}).then(function(data){
    if(data&&data.history){
      data.history.forEach(function(h){
        if(h.user)addMsg('user',h.user,h.timestamp||'');
        if(h.ai)addMsg('ai',h.ai,h.timestamp||'');
      });
    }
    chat.scrollTop=chat.scrollHeight;
  }).catch(function(){addSys('[связь с ПК потеряна]');});
}
function doSend(text,retries){
  busy=true; send.disabled=true;
  var typing=document.createElement('div');
  typing.className='typing'; typing.id='typing';
  typing.textContent='Piton печатает...';
  chat.appendChild(typing); chat.scrollTop=chat.scrollHeight;
  authFetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:text})})
  .then(function(r){return r.json();})
  .then(function(data){
    var t=document.getElementById('typing'); if(t)t.remove();
    busy=false; send.disabled=false;
    if(data&&data.response){ addMsg('ai',data.response,getTs()); }
    else if(data&&data.error){ addSys('[ошибка] '+data.error); }
  }).catch(function(e){
    var t=document.getElementById('typing'); if(t)t.remove();
    if(retries>0){ addSys('[сеть капризничает, пробую ещё раз...]'); busy=false; send.disabled=true;
      setTimeout(function(){doSend(text,retries-1);},3500); }
    else { busy=false; send.disabled=false; addSys('[ошибка сети — Питон недоступен, проверь соединение]'); }
  });
}
function sendMsg(){
  var text=inp.value.trim();
  if(!text||busy)return;
  inp.value='';
  addMsg('user',text,getTs());
  doSend(text,2);
}
send.addEventListener('click',sendMsg);
inp.addEventListener('keydown',function(e){if(e.key==='Enter')sendMsg();});
function checkHealth(){
  authFetch('/api/health').then(function(r){return r.json();}).then(function(d){
    if(d.ok){statusEl.textContent='[ONLINE]';dot.className='dot';}
    else{statusEl.textContent='[OFFLINE]';dot.className='dot off';}
  }).catch(function(){statusEl.textContent='[OFFLINE]';dot.className='dot off';});
}
loadHistory();
checkHealth();
setInterval(checkHealth,10000);
inp.focus();
</script>
</body>
</html>
"""


# ============ HTTP HANDLER ============
class MiniHandler(http.server.BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        pass  # тихо

    def handle_error(self, request, client_address):
        """Клиент оборвал соединение (10053) или таймаут — это норма,
        пишем коротко вместо полного трейсбека."""
        import sys
        etype, evalue, etb = sys.exc_info()
        try:
            s = str(evalue)
            low = s.lower()
            if any(k in low for k in ("10053", "10054", "timed out", "timeout",
                                      "connectionaborted", "connectionreset",
                                      "broken pipe", "connectionerror")):
                log_error("сеть: клиент оборвал соединение: " + s[:120])
            else:
                log_error("http: " + s[:200])
        except Exception:
            pass

    def _auth_ok(self):
        """Токен обязателен: заголовок X-Auth-Token или ?token= в query."""
        try:
            expected = get_auth_token()
            if not expected:
                return False
            got = self.headers.get("X-Auth-Token", "")
            if got == expected:
                return True
            # fallback: ?token= в строке запроса
            q = urllib.parse.urlparse(self.path).query
            params = urllib.parse.parse_qs(q)
            return params.get("token", [""])[0] == expected
        except Exception:
            return False

    # ---- мягкий лимит частоты (скользящее окно на весь сервер) ----
    _hits = {}

    def _limited(self, key, limit, window=60):
        now = time.time()
        buf = self._hits.setdefault(key, [])
        buf[:] = [t for t in buf if now - t < window]
        if len(buf) >= limit:
            return True
        buf.append(now)
        return False

    def _send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if not self._auth_ok() and self.path != "/api/health" and path != "/snake.html":
            self._send_json({"error": "auth required"}, 403)
            return
        if path == "/" or path == "/index.html":
            try:
                with open(r"D:\Piton\mini_index.html", "rb") as f:
                    body = f.read()
            except Exception as e:
                body = ("index not found: " + str(e)).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        elif path == "/snake.html":
            try:
                with open(r"D:\Piton\mini_snake.html", "rb") as f:
                    body = f.read()
            except Exception as e:
                body = ("snake not found: " + str(e)).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        elif path == "/api/history":
            if self._limited("history", 30, 60):
                self._send_json({"error": "слишком много запросов"}, 429)
                return
            full = load_history()
            self._send_json({"history": full[-30:], "total": len(full)})
        elif path == "/api/health":
            ok = False
            try:
                urllib.request.urlopen(OLLAMA_URL + "/api/tags", timeout=3)
                ok = True
            except Exception:
                pass
            self._send_json({"ok": ok, "model": PITON_MODEL})
        else:
            self._send_json({"error": "not found"}, 404)

    def do_POST(self):
        if not self._auth_ok():
            self._send_json({"error": "auth required"}, 403)
            return
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/chess":
            try:
                import piton_chess
                length = int(self.headers.get("Content-Length", 0))
                if length > MAX_BODY:
                    self._send_json({"error": "body too large"}, 413)
                    return
                data = json.loads(self.rfile.read(length).decode("utf-8"))
                mv = (data.get("move") or "").strip()
                if mv == "new" or "owner" not in piton_chess.games:
                    text = piton_chess.start_game("owner")
                    self._send_json({"board": text})
                    return
                text = piton_chess.move("owner", mv)
                self._send_json({"board": text})
            except Exception as e:
                self._send_json({"error": str(e)})
            return
        if path != "/api/chat":
            self._send_json({"error": "not found"}, 404)
            return
        if self._limited("chat", 60, 60):
            self._send_json({"error": "слишком много запросов"}, 429)
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length > MAX_BODY:
                self._send_json({"error": "body too large"}, 413)
                return
            raw = self.rfile.read(length).decode("utf-8")
            data = json.loads(raw)
            user_text = (data.get("message") or "").strip()
            image = (data.get("image") or "").strip()
            if image and "," in image:
                image = image.split(",", 1)[-1]
            if not user_text and not image:
                self._send_json({"error": "пустое сообщение"})
                return
            if data.get("stream") and not image:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                try:
                    ask_ollama_stream(user_text, self.wfile)
                except Exception as e:
                    log_error("stream: " + str(e))
                    try:
                        self.wfile.write(b'data: {"error": "stream failed"}\n\n')
                        self.wfile.flush()
                    except Exception:
                        pass
                return
            ts = time.strftime("%H:%M")
            response = ask_ollama(user_text, image=image or None)
            # Записываем в общую историю (синхронизация со всеми окнами)
            history = load_history()
            history.append({"timestamp": ts, "user": user_text, "ai": response})
            save_history(history)
            try:
                if pmv is not None:
                    pmv.add(user_text, "u")
                    pmv.add(response, "a")
            except Exception:
                pass
            try:
                if piton_brain is not None:
                    piton_brain.analyze_exchange(user_text, response, user_id="owner")
            except Exception:
                pass
            try:
                import piton_snake
                emo = piton_snake.emotion_for_text(user_text, response)
            except Exception:
                emo = None
            self._send_json({"response": response, "ts": ts, "emotion": emo})
        except Exception as e:
            log_error("do_POST: " + str(e))
            self._send_json({"error": "Ollama недоступна или ответ не получен: " + str(e)}, 500)


# ============ НАПОМИНАНИЯ (фоновый воркер) ============
def _reminder_worker():
    """Раз в 30 сек: наступившие напоминания дописываются в историю от имени Питона."""
    while True:
        try:
            import piton_agent
            due = piton_agent.due_reminders()
            if due:
                history = load_history()
                for x in due:
                    history.append({"timestamp": time.strftime("%H:%M"),
                                    "user": "⏰", "ai": "Напоминание: " + x})
                save_history(history)
        except Exception:
            pass
        time.sleep(30)


# ============ MAIN ============
if __name__ == "__main__":
    try:
        import piton_guard
        piton_guard.ensure_single("piton_mini_server.py")
    except Exception:
        pass
    ensure_token()
    if pmv is not None:
        # фоновая догонка индексации (не блокирует старт сервера)
        threading.Thread(target=pmv.ensure_indexed, daemon=True).start()
    # воркер напоминаний
    threading.Thread(target=_reminder_worker, daemon=True).start()
    # Защита от дублей: если порт уже занят — выходим тихо
    try:
        probe = http.server.ThreadingHTTPServer(("0.0.0.0", PORT), MiniHandler)
        probe.server_close()
    except OSError:
        print("Port", PORT, "already in use, another mini-server is running. Exiting.")
        sys.exit(0)
    server = http.server.ThreadingHTTPServer(("0.0.0.0", PORT), MiniHandler)
    print("Piton Mini App server on http://localhost:" + str(PORT))
    print("History:", HISTORY_FILE)
    print("Model:", PITON_MODEL)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopped.")
