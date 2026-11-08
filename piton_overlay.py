# -*- coding: utf-8 -*-
"""
Piton Overlay — плавающая иконка поверх всех окон.

Чем отличается от "глухого" чата:
- читает и пишет ТОТ ЖЕ chat_history.json, что десктоп и телеграм-бот;
- следит за файлом и в реальном времени подхватывает сообщения,
  написанные в других интерфейсах (пока они запущены);
- та же личность (модель piton-gemma + SOUL.md), те же команды и фильтры.

Запуск:  pythonw D:/Piton/piton_overlay.py   (или start_piton_overlay.vbs)
"""
import tkinter as tk
import threading
import time
import json
import os
import re
import sys
import socket
import ctypes
import subprocess
import urllib.request
import urllib.parse
from PIL import Image, ImageTk, ImageDraw, ImageChops
import pystray
from pystray import MenuItem as TrayItem
try:
    from piton_snake import SnakeAvatar, emotion_for_text   # змея-аватар; лежит рядом с этим файлом
except Exception:
    SnakeAvatar = None
    emotion_for_text = None

# ============ CONFIG ============
MEMORY_DIR = os.environ.get("PITON_MEMORY") or r"D:\Piton_private\PitonMemory"
OLLAMA_URL = os.environ.get("OLLAMA_URL") or "http://127.0.0.1:11434"
PITON_MODEL = os.environ.get("PITON_MODEL") or "huihui_ai/qwen3-abliterated:8b"
MAX_HISTORY = 200

CHAT_HISTORY_FILE = os.path.join(MEMORY_DIR, "chat_history.json")
SOUL_FILE = os.path.join(MEMORY_DIR, "SOUL.md")
ERROR_LOG = os.path.join(MEMORY_DIR, "overlay_error.log")
POS_FILE = os.path.join(MEMORY_DIR, "overlay_pos.json")
AVATAR_FILE = r"D:\Piton\piton_icon.ico"
ICON_FILE = r"D:\Piton\piton_icon.ico"
SNAKE_DIR = r"D:\Piton\snake"   # папка со спрайтами змеи (manifest.json + png)
USE_SNAKE = True                  # False — вернуть круглую иконку вместо змеи
SNAKE_SCALE = 1.0                 # размер змеи: 1.0 = 116x154 px, 0.8 = меньше, 1.3 = больше
SNAKE_FOLLOW_CURSOR = True        # змея поворачивает голову за курсором мыши

# Цвет, который станет прозрачным (Windows -transparentcolor)
MAGIC = "#010203"

# ============ COLORS ============
BG_COLOR = "#0a0a0f"
CARD_COLOR = "#0f0f18"
TERMINAL_BG = "#000000"
TERMINAL_GREEN = "#00e676"
TERMINAL_BLUE = "#66b3ff"
TERMINAL_YELLOW = "#ffd54f"
TERMINAL_RED = "#ff6b6b"
TERMINAL_GRAY = "#a0a0a0"
TEXT_COLOR = "#e0e0e0"

BAD_WORDS = ["gemma", "google", "deepmind", "alibaba", "qwen", "mistral", "dolphin", "cloud", "языковая модель", "я — ai", "я — искусственный интеллект", "я — нейрость", "я — бот"]

ICON_SIZE = 56        # размер аватарки на экране
ICON_WIN = ICON_SIZE + 10
WATCH_INTERVAL = 2000 # мс — как часто проверять синхронизацию
AUTO_CLEAR_ON_START = False  # чат НЕ чистится при запуске — история сохраняется
MUTEX_NAME = "Global\\PitonOverlay_SingleInstance"
SIGNAL_PORT = 45713
sys.path.insert(0, MEMORY_DIR)
try:
    import PITON_BRAIN as piton_brain
except Exception:
    piton_brain = None

# ============ UTILS ============
def make_round_icon(img, size, inset_frac=0.06, ring=2):
    """Круглая иконка с плавным краем и тонким зелёным кольцом.
    Рисуем в 4x размере и уменьшаем — так край получается без зубцов."""
    S = 4
    big = size * S
    img = img.convert("RGBA")
    w, h = img.size
    side = min(w, h)
    inset = int(side * inset_frac)  # срезаем рамку исходной квадратной иконки
    img = img.crop(((w - side) // 2 + inset, (h - side) // 2 + inset,
                    (w + side) // 2 - inset, (h + side) // 2 - inset)).resize((big, big), Image.LANCZOS)
    mask = Image.new("L", (big, big), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, big - 1, big - 1), fill=255)
    img.putalpha(ImageChops.multiply(img.getchannel("A"), mask))
    if ring:
        ImageDraw.Draw(img).ellipse((0, 0, big - 1, big - 1), outline=(0, 230, 118, 255), width=ring * S)
    img = img.resize((size, size), Image.LANCZOS)
    # Окно прозрачно через -transparentcolor: полупрозрачные пиксели края смешались бы с цветом MAGIC
    # и дали тёмную «окантовку» на светлых обоях. Поэтому альфа только 0 или 255.
    img.putalpha(img.getchannel("A").point(lambda v: 255 if v >= 128 else 0))
    return img

def log_error(msg):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(ERROR_LOG, "a", encoding="utf-8") as f:
            f.write(ts + " " + msg + "\n")
    except:
        pass

def load_chat_history():
    if os.path.exists(CHAT_HISTORY_FILE):
        try:
            with open(CHAT_HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return []
    return []

def save_chat_history(history):
    try:
        with open(CHAT_HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log_error("save_chat_history: " + str(e))

def get_system_prompt():
    # Личность Питона зашита в модель; сверху добавляем только заметки SOUL.md
    if os.path.exists(SOUL_FILE):
        try:
            with open(SOUL_FILE, "r", encoding="utf-8") as f:
                soul = f.read().strip()
            if soul:
                return soul
        except:
            pass
    return ""

def check_ollama():
    try:
        req = urllib.request.Request(OLLAMA_URL, method="GET")
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.status == 200
    except:
        return False

def filter_bad_words(text):
    lower = text.lower()
    for word in BAD_WORDS:
        if word in lower:
            return True
    return False

def compress_history(history):
    if len(history) <= 20:
        return history
    recent = history[-20:]
    older = history[:-20]
    summary_points = []
    for entry in older[-20:]:
        u = entry.get("user", "")
        a = entry.get("ai", "")
        if u:
            summary_points.append("- Вопрос: " + u[:250])
        if a:
            summary_points.append("  Ответ: " + a[:250])
    summary_text = "[СЖАТАЯ ПАМЯТЬ] Предыдущие темы:\n" + "\n".join(summary_points)
    return [{"timestamp": "", "user": summary_text, "ai": ""}] + recent

def detect_auto_command(text):
    lower = text.lower().strip()
    commands = {
        "clear": [r"^очисти", r"^clear", r"^cls"],
        "memory": [r"^память", r"^memory", r"^сжать память"],
        "help": [r"^помощь", r"^help", r"^команды", r"^что ты умеешь"],
        "exit": [r"^выход[\s!.]*$", r"^exit", r"^quit", r"^пока[\s!.]*$"],
    }
    for cmd_name, patterns in commands.items():
        for pattern in patterns:
            if re.search(pattern, lower):
                return cmd_name
    return None

def should_search_online(text):
    lower = text.lower()
    triggers = [
        r"\bновости\b", r"\bсобытия\b", r"\bпогода\b", r"\bпрогноз\b",
        r"\bкурс\b", r"\bцена\b", r"\bбиткоин\b", r"\bдоллар\b", r"\bевро\b",
        r"\bпоследние\b", r"\bсвежие\b", r"\bсегодня\b", r"\bвчера\b",
        r"\b2024\b", r"\b2025\b", r"\b2026\b",
    ]
    for pattern in triggers:
        if re.search(pattern, lower):
            return True
    return False

def web_search(query):
    try:
        q = urllib.parse.quote(query)
        url = "https://html.duckduckgo.com/html/?q=" + q
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8")
        titles = re.findall(r'<a[^>]+class="result__a"[^>]*>(.*?)</a>', html)
        snippets = re.findall(r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>', html)
        results = []
        count = min(3, len(titles), len(snippets))
        for i in range(count):
            t = re.sub(r'<[^>]+>', '', titles[i])
            s = re.sub(r'<[^>]+>', '', snippets[i])
            results.append("- " + t + ": " + s)
        if results:
            return "\n".join(results)
        return None
    except Exception as e:
        log_error("web_search: " + str(e))
        return None

def ollama_stream(prompt, history, callback, model=None):
    use_model = model if model else PITON_MODEL
    try:
        try:
            import PITON_SOUL as _psoul
            _psoul.set_last_user_ts()
        except Exception:
            pass
        messages = []
        extra_prompt = get_system_prompt()
        if piton_brain is not None and extra_prompt:
            extra_prompt = piton_brain.get_dynamic_prompt(extra_prompt, prompt, user_id="owner")
        try:
            import PITON_SOUL as _psoul
            _self = _psoul.get_self_prompt(400)
            if _self:
                extra_prompt = (extra_prompt + chr(10)*2 + _self).strip() if extra_prompt else _self
        except Exception:
            pass
        if extra_prompt:
            messages.append({"role": "system", "content": extra_prompt})
        try:
            import os as _os2
            _summ2 = ""
            _sf2 = _os2.path.join(MEMORY_DIR, "summary.json")
            if _os2.path.exists(_sf2):
                with open(_sf2, "r", encoding="utf-8") as _f2:
                    _summ2 = json.load(_f2).get("summary", "").strip()
            if _summ2:
                messages.append({"role": "system", "content": "Память о старых разговорах (кратко): " + _summ2})
        except Exception:
            pass
        for h in history[-20:]:
            if "user" in h:
                messages.append({"role": "user", "content": h["user"]})
            if "ai" in h:
                messages.append({"role": "assistant", "content": h["ai"]})
        messages.append({"role": "user", "content": prompt})

        import piton_llm

        def _cb(token, done):
            callback(token, done)

        full_response = piton_llm.stream_chat(messages, _cb, model=use_model,
                                               options={"temperature": 0.6, "num_predict": 2048},
                                               timeout=300)

        if filter_bad_words(full_response):
            callback("\n\n[Фильтр: запрещённые слова, перегенерирую...]", False)
            return ollama_simple(prompt, history, callback, model=use_model)

        callback("", True)
        return full_response
    except Exception as e:
        callback("\n\n[ОШИБКА] " + str(e), True)
        log_error("ollama_stream: " + str(e))
        return ""

def ollama_simple(prompt, history, callback, model=None):
    use_model = model if model else PITON_MODEL
    try:
        try:
            import PITON_SOUL as _psoul
            _psoul.set_last_user_ts()
        except Exception:
            pass
        messages = []
        extra_prompt = get_system_prompt()
        if piton_brain is not None and extra_prompt:
            extra_prompt = piton_brain.get_dynamic_prompt(extra_prompt, prompt, user_id="owner")
        try:
            import PITON_SOUL as _psoul
            _self = _psoul.get_self_prompt(400)
            if _self:
                extra_prompt = (extra_prompt + chr(10)*2 + _self).strip() if extra_prompt else _self
        except Exception:
            pass
        if extra_prompt:
            messages.append({"role": "system", "content": extra_prompt})
        try:
            import os as _os2
            _summ2 = ""
            _sf2 = _os2.path.join(MEMORY_DIR, "summary.json")
            if _os2.path.exists(_sf2):
                with open(_sf2, "r", encoding="utf-8") as _f2:
                    _summ2 = json.load(_f2).get("summary", "").strip()
            if _summ2:
                messages.append({"role": "system", "content": "Память о старых разговорах (кратко): " + _summ2})
        except Exception:
            pass
        for h in history[-10:]:
            if "user" in h:
                messages.append({"role": "user", "content": h["user"]})
            if "ai" in h:
                messages.append({"role": "assistant", "content": h["ai"]})
        messages.append({"role": "user", "content": prompt})

        import piton_llm
        text = piton_llm.chat(messages, model=use_model,
                              options={"temperature": 0.6, "num_predict": 2048},
                              timeout=180)
        callback(text, True)
        return text
    except Exception as e:
        callback("[ОШИБКА] " + str(e), True)
        log_error("ollama_simple: " + str(e))
        return ""

# ============ SINGLE INSTANCE ============
def is_already_running():
    """True если оверлей уже запущен (Windows-мьютекс)."""
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
        if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            kernel32.CloseHandle(handle)
            return True
        return False
    except Exception:
        return False

def notify_running_instance():
    """Просим уже запущенный экземпляр показать основной чат."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.sendto(b"SHOW", ("127.0.0.1", SIGNAL_PORT))
        s.close()
        return True
    except Exception:
        return False

# ============ OVERLAY APP ============
class PitonOverlay:
    def __init__(self, root):
        self.root = root
        self.root.title("Piton")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        try:
            self.root.attributes("-transparentcolor", MAGIC)
        except Exception:
            pass
        self.root.configure(bg=MAGIC)

        self.history = load_chat_history()
        if AUTO_CLEAR_ON_START and self.history:
            # чат сам чистится при каждом запуске — свежий лист
            self.history = []
            save_chat_history(self.history)
        self.typing = False
        self.current_ai_response = ""
        self.chat_win = None
        self.chat_open = False
        self._drag = None
        self._last_mtime = self._file_mtime()
        self._tray_icon = None

        # Змея-аватар (кадры из папки snake). Нет папки или модуля — круглая иконка, как раньше.
        self.snake = None
        if USE_SNAKE and SnakeAvatar is not None:
            try:
                self.snake = SnakeAvatar.load(SNAKE_DIR, MAGIC, scale=SNAKE_SCALE, follow=SNAKE_FOLLOW_CURSOR)
            except Exception as e:
                log_error("snake load: " + str(e))
        self.icon_w = self.snake.w if self.snake else ICON_WIN
        self.icon_h = self.snake.h if self.snake else ICON_WIN

        # Аватарка (запасной вариант)
        self.avatar_img = None
        if not self.snake:
            try:
                img = make_round_icon(Image.open(AVATAR_FILE), ICON_SIZE)
                self.avatar_img = ImageTk.PhotoImage(img)
            except Exception as e:
                log_error("avatar load: " + str(e))

        # Окно-иконка
        self.icon_canvas = tk.Canvas(self.root, width=self.icon_w, height=self.icon_h,
                                     bg=MAGIC, highlightthickness=0, bd=0)
        self.icon_canvas.pack()
        self._icon_item = None
        if self.snake:
            self._icon_item = self.snake.attach(self.icon_canvas, 0, 0)
        elif self.avatar_img:
            self._icon_item = self.icon_canvas.create_image(ICON_WIN // 2, ICON_WIN // 2,
                                                            image=self.avatar_img)
        # Индикатор онлайн (точка)
        if self.snake:
            _dx, _dy = self.icon_w - 12, self.icon_h - 12   # правый нижний угол
        else:
            _c, _r = ICON_WIN // 2, ICON_SIZE // 2
            _dx = _dy = int(_r * 0.707) + _c  # точка на окружности под 45° (справа-снизу)
        self.dot = self.icon_canvas.create_oval(_dx - 6, _dy - 6, _dx + 6, _dy + 6,
                                                fill=TERMINAL_RED, outline=BG_COLOR, width=2)

        # Перетаскивание + клик
        self.icon_canvas.bind("<Button-1>", self._on_press)
        self.icon_canvas.bind("<B1-Motion>", self._on_drag)
        self.icon_canvas.bind("<ButtonRelease-1>", self._on_release)
        self.icon_canvas.bind("<Button-3>", self._on_right_click)

        # Контекстное меню
        self.menu = tk.Menu(self.root, tearoff=0, bg=CARD_COLOR, fg=TEXT_COLOR,
                            activebackground="#1a1a2e", activeforeground=TERMINAL_GREEN)
        self.menu.add_command(label="Открыть чат", command=self.open_chat)
        self.menu.add_command(label="Сжать память", command=self.do_memory)
        self.menu.add_command(label="Очистить чат", command=self.do_clear)
        self.menu.add_separator()
        self.menu.add_command(label="Свернуть в трей", command=self.hide_to_tray)
        self.menu.add_command(label="Выход", command=self.quit_app)

        self._load_pos()
        if self.snake:
            self.snake.react("happy", 2.5)   # поздоровалась
        self._update_status()

        # Сторож синхронизации
        self.root.after(WATCH_INTERVAL, self._watch_history)

        # Иконка в системном трее
        self._init_tray()

        # Слушаем сигнал "покажи основной чат" от повторных запусков
        self._start_signal_listener()

    # ---------- файлы ----------
    def _file_mtime(self):
        try:
            return os.path.getmtime(CHAT_HISTORY_FILE)
        except:
            return 0

    def _load_pos(self):
        x, y = 60, 120
        try:
            with open(POS_FILE, "r", encoding="utf-8") as f:
                pos = json.load(f)
                x, y = int(pos.get("x", x)), int(pos.get("y", y))
        except:
            pass
        self.root.geometry("+%d+%d" % (x, y))

    def _save_pos(self):
        try:
            with open(POS_FILE, "w", encoding="utf-8") as f:
                json.dump({"x": self.root.winfo_x(), "y": self.root.winfo_y()}, f)
        except:
            pass

    # ---------- иконка ----------
    def _on_press(self, e):
        self._drag = (e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y(), e.x, e.y)

    def _on_drag(self, e):
        if not self._drag:
            return
        ox, oy, px, py = self._drag
        if abs(e.x - px) > 4 or abs(e.y - py) > 4:
            self.root.geometry("+%d+%d" % (e.x_root - ox, e.y_root - oy))

    def _on_release(self, e):
        if not self._drag:
            return
        ox, oy, px, py = self._drag
        moved = abs(e.x - px) > 4 or abs(e.y - py) > 4
        self._drag = None
        if not moved:
            if self.snake:
                self.snake.react("happy", 2.0)
            self.toggle_chat()
        else:
            self._save_pos()

    def _on_right_click(self, e):
        try:
            self.menu.tk_popup(e.x_root, e.y_root)
        finally:
            self.menu.grab_release()

    # ---------- трей ----------
    def _start_signal_listener(self):
        """UDP-слушатель: когда пользователь запускает файл ещё раз,
        уже работающий экземпляр получает сигнал и показывает основной чат."""
        def listen():
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.bind(("127.0.0.1", SIGNAL_PORT))
                s.settimeout(1)
                while True:
                    try:
                        data, _ = s.recvfrom(64)
                        if data == b"SHOW":
                            self.root.after(0, self._show_main_chat)
                    except socket.timeout:
                        continue
                    except Exception:
                        break
            except Exception as e:
                log_error("signal_listener: " + str(e))
        threading.Thread(target=listen, daemon=True).start()

    def _show_main_chat(self):
        """Показать основной чат: вернуть иконку на экран и открыть окно."""
        self.show_icon()
        self.open_chat()

    def _init_tray(self):
        """Иконка в системном трее — приложение живёт даже когда окна закрыты."""
        try:
            img = Image.open(AVATAR_FILE).convert("RGBA")
            img = img.resize((64, 64), Image.LANCZOS)
        except Exception:
            img = Image.new("RGBA", (64, 64), (0, 230, 118, 255))
        menu = pystray.Menu(
            TrayItem("Открыть чат", lambda: self._tray_call(self.open_chat), default=True),
            TrayItem("Показать иконку", lambda: self._tray_call(self.show_icon)),
            TrayItem("Сжать память", lambda: self._tray_call(self.do_memory)),
            TrayItem("Очистить чат", lambda: self._tray_call(self.do_clear)),
            TrayItem("Выход", lambda: self._tray_call(self.quit_app)),
        )
        self._tray_icon = pystray.Icon("piton_overlay", img, "Piton 🐍", menu)
        threading.Thread(target=self._tray_icon.run, daemon=True).start()

    def _tray_call(self, func):
        """Вызов из трей-потока переносим в tk-поток."""
        try:
            self.root.after(0, func)
        except Exception:
            func()

    def hide_to_tray(self):
        """Спрятать и плавающую иконку, и чат — остаётся только трей."""
        try:
            self.close_chat()
        except Exception:
            pass
        try:
            self.root.withdraw()
        except Exception:
            pass

    def show_icon(self):
        """Вернуть плавающую иконку из трея."""
        try:
            self.root.deiconify()
            self.root.lift()
            self.root.attributes("-topmost", True)
        except Exception:
            pass

    # ---------- выход ----------
    def quit_app(self):
        self._save_pos()
        if self.snake:
            self.snake.stop()
        try:
            if self._tray_icon is not None:
                self._tray_icon.stop()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass

    def _update_status(self):
        online = check_ollama()
        color = TERMINAL_GREEN if online else TERMINAL_RED
        self.icon_canvas.itemconfig(self.dot, fill=color)
        if self.snake:
            was = getattr(self, "_was_online", None)
            self.snake.set_baseline("idle" if online else "sad")
            if online and was is False:
                self.snake.react("happy", 2.5)
            self._was_online = online
        if self.chat_open and self.chat_win is not None:
            try:
                self.status_label.configure(
                    text="[ONLINE]" if online else "[OFFLINE]",
                    fg=TERMINAL_GREEN if online else TERMINAL_RED)
            except:
                pass
        self.root.after(5000, self._update_status)

    # ---------- синхронизация ----------
    def _watch_history(self):
        """Если chat_history.json изменился извне (телеграм/десктоп) — перечитаем."""
        try:
            mtime = self._file_mtime()
            if mtime > self._last_mtime:
                self._last_mtime = mtime
                fresh = load_chat_history()
                if fresh != self.history:
                    self.history = fresh
                    self._refresh_chat_ui()
        except Exception as e:
            log_error("watch_history: " + str(e))
        self.root.after(WATCH_INTERVAL, self._watch_history)

    def _save_with_sync(self):
        """Сохраняем, не затирая чужие свежие записи из файла."""
        try:
            fresh = load_chat_history()
            if fresh != self.history:
                # склеим: наши + те, которых у нас нет (по паре user/ai)
                seen = {(h.get("user", ""), h.get("ai", "")) for h in self.history}
                merged = list(self.history)
                for h in fresh:
                    if (h.get("user", ""), h.get("ai", "")) not in seen:
                        merged.append(h)
                self.history = merged
        except:
            pass
        save_chat_history(self.history)
        self._last_mtime = self._file_mtime()

    # ---------- чат ----------
    def toggle_chat(self):
        if self.chat_open:
            self.close_chat()
        else:
            self.open_chat()

    def open_chat(self):
        if self.chat_open and self.chat_win is not None:
            try:
                self.chat_win.deiconify()
                self.chat_win.lift()
                self.chat_win.focus_force()
            except:
                pass
            return
        self.chat_win = tk.Toplevel(self.root)
        self.chat_open = True
        self.chat_win.title("Piton 🐍")
        self.chat_win.configure(bg=BG_COLOR)
        try:
            self.chat_win.iconbitmap(ICON_FILE)
        except:
            pass
        self.chat_win.geometry("400x560")
        # поставим рядом с иконкой
        ix, iy = self.root.winfo_x(), self.root.winfo_y()
        sw = self.chat_win.winfo_screenwidth()
        if ix + 480 > sw:
            self.chat_win.geometry("+%d+%d" % (ix - 470, iy))
        else:
            self.chat_win.geometry("+%d+%d" % (ix + self.icon_w + 6, iy))
        self.chat_win.protocol("WM_DELETE_WINDOW", self.close_chat)

        # Шапка
        header = tk.Frame(self.chat_win, bg=CARD_COLOR)
        header.pack(fill="x")
        tk.Label(header, text="🐍 Piton — живой друг", bg=CARD_COLOR,
                 fg=TERMINAL_GREEN, font=("Consolas", 13, "bold")).pack(side="left", padx=10, pady=6)
        self.status_label = tk.Label(header, text="", bg=CARD_COLOR,
                                     fg=TERMINAL_GRAY, font=("Consolas", 11))
        self.status_label.pack(side="right", padx=10)

        # Текст чата
        chat_frame = tk.Frame(self.chat_win, bg=TERMINAL_BG)
        chat_frame.pack(fill="both", expand=True, padx=8, pady=(6, 0))
        self.chat_text = tk.Text(chat_frame, bg=TERMINAL_BG, fg=TERMINAL_GREEN,
                                 wrap="word", font=("Consolas", 11),
                                 insertbackground=TERMINAL_GREEN,
                                 relief="flat", padx=6, pady=6,
                                 highlightthickness=1, highlightbackground="#1a1a2e")
        scroll = tk.Scrollbar(chat_frame, command=self.chat_text.yview)
        self.chat_text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.chat_text.pack(side="left", fill="both", expand=True)
        self.chat_text.configure(state="disabled")

        self.chat_text.tag_config("user_prefix", foreground=TERMINAL_BLUE)
        self.chat_text.tag_config("user_text", foreground=TEXT_COLOR)
        self.chat_text.tag_config("ai_prefix", foreground=TERMINAL_GREEN)
        self.chat_text.tag_config("ai_text", foreground=TERMINAL_GREEN)
        self.chat_text.tag_config("system_prefix", foreground=TERMINAL_GRAY)
        self.chat_text.tag_config("system_text", foreground=TERMINAL_GRAY)
        self.chat_text.tag_config("error_prefix", foreground=TERMINAL_RED)
        self.chat_text.tag_config("error_text", foreground=TERMINAL_RED)
        self.chat_text.tag_config("timestamp", foreground=TERMINAL_GRAY)

        self.print_system("Piton на связи 🐍 Просто пиши мне!")
        self.load_history_to_ui()

        # Ввод
        input_frame = tk.Frame(self.chat_win, bg=TERMINAL_BG)
        input_frame.pack(fill="x", padx=8, pady=6)
        self.entry = tk.Entry(input_frame, bg=TERMINAL_BG, fg=TERMINAL_GREEN,
                              insertbackground=TERMINAL_GREEN, relief="flat",
                              font=("Consolas", 11),
                              highlightthickness=1, highlightbackground="#1a1a2e")
        self.entry.pack(side="left", fill="x", expand=True, ipady=6)
        self.entry.insert(0, "просто пиши...")
        self.entry.config(fg=TERMINAL_GRAY)
        self.entry.bind("<FocusIn>", self._entry_focus_in)
        self.entry.bind("<FocusOut>", self._entry_focus_out)
        self.entry.bind("<Return>", lambda e: self.send_message())

        send_btn = tk.Button(input_frame, text="➤", bg=CARD_COLOR, fg=TERMINAL_GREEN,
                             relief="flat", font=("Consolas", 12),
                             activebackground="#1a1a2e", activeforeground=TERMINAL_GREEN,
                             command=self.send_message, bd=0, padx=10)
        send_btn.pack(side="left", padx=(6, 0))

        self.bottom = tk.Label(self.chat_win, text="", bg=BG_COLOR, fg=TERMINAL_GRAY,
                               font=("Consolas", 10), anchor="w")
        self.bottom.pack(fill="x", padx=10, pady=(0, 5))

        self.chat_open = True
        self._update_status()
        self.entry.focus_set()

    def close_chat(self):
        self.chat_open = False
        if self.chat_win is not None:
            try:
                self.chat_win.destroy()
            except:
                pass
            self.chat_win = None

    def _entry_focus_in(self, e):
        if self.entry.get() == "просто пиши...":
            self.entry.delete(0, "end")
            self.entry.config(fg=TERMINAL_GREEN)

    def _entry_focus_out(self, e):
        if not self.entry.get().strip():
            self.entry.insert(0, "просто пиши...")
            self.entry.config(fg=TERMINAL_GRAY)

    # ---------- вывод ----------
    def print_user(self, text, ts=""):
        if not self.chat_open or self.chat_win is None:
            return
        self.chat_text.configure(state="normal")
        if ts:
            self.chat_text.insert("end", "[" + ts + "] ", "timestamp")
        self.chat_text.insert("end", "You> ", "user_prefix")
        self.chat_text.insert("end", text + "\n", "user_text")
        self.chat_text.configure(state="disabled")
        self.chat_text.see("end")

    def print_ai(self, text, ts=""):
        if not self.chat_open or self.chat_win is None:
            return
        self.chat_text.configure(state="normal")
        if ts:
            self.chat_text.insert("end", "[" + ts + "] ", "timestamp")
        self.chat_text.insert("end", "Piton> ", "ai_prefix")
        self.chat_text.insert("end", text + "\n", "ai_text")
        self.chat_text.configure(state="disabled")
        self.chat_text.see("end")

    def print_system(self, text):
        if not self.chat_open or self.chat_win is None:
            return
        self.chat_text.configure(state="normal")
        self.chat_text.insert("end", "[SYS] ", "system_prefix")
        self.chat_text.insert("end", text + "\n", "system_text")
        self.chat_text.configure(state="disabled")
        self.chat_text.see("end")

    def print_error(self, text):
        if self.snake:
            self.snake.react("sad", 3.5)
        if not self.chat_open or self.chat_win is None:
            return
        self.chat_text.configure(state="normal")
        self.chat_text.insert("end", "[ERR] ", "error_prefix")
        self.chat_text.insert("end", text + "\n", "error_text")
        self.chat_text.configure(state="disabled")
        self.chat_text.see("end")

    def load_history_to_ui(self):
        if not self.chat_open or self.chat_win is None:
            return
        try:
            self.chat_text.configure(state="normal")
            self.chat_text.delete("1.0", "end")
            self.chat_text.configure(state="disabled")
            for entry in self.history[-30:]:
                ts = entry.get("timestamp", "")
                user = entry.get("user", "")
                ai = entry.get("ai", "")
                if user:
                    self.print_user(user, ts)
                if ai:
                    self.print_ai(ai, ts)
        except Exception as e:
            log_error("load_history_to_ui: " + str(e))

    def _refresh_chat_ui(self):
        """Внешняя синхронизация: перерисовать чат из обновлённой истории."""
        if self.chat_open and self.chat_win is not None:
            try:
                self.load_history_to_ui()
            except:
                pass

    # ---------- команды ----------
    def do_clear(self):
        self.history = []
        self._save_with_sync()
        if self.chat_open and self.chat_win is not None:
            self.chat_text.configure(state="normal")
            self.chat_text.delete("1.0", "end")
            self.chat_text.configure(state="disabled")
            self.print_system("[ОЧИСТКА] Чат очищен!")

    def do_memory(self):
        self.history = compress_history(self.history)
        self._save_with_sync()
        if self.chat_open and self.chat_win is not None:
            self.load_history_to_ui()
            self.print_system("[ПАМЯТЬ] История сжата! Записей: " + str(len(self.history)))

    # ---------- отправка ----------
    def send_message(self):
        if self.typing:
            return
        raw = self.entry.get()
        if raw == "просто пиши...":
            return
        text = raw.strip()
        if not text:
            return
        ts = time.strftime("%H:%M")
        self.entry.delete(0, "end")

        # Авто-сжатие памяти
        if len(self.history) > MAX_HISTORY:
            self.history = compress_history(self.history)
            self._save_with_sync()
            self.print_system("[ПАМЯТЬ] Авто-сжатие выполнено!")

        # Авто-команды
        auto_cmd = detect_auto_command(text)
        if auto_cmd:
            self.print_user(text, ts)
            if auto_cmd == "clear":
                self.do_clear()
            elif auto_cmd == "memory":
                self.do_memory()
            elif auto_cmd == "help":
                self.print_system(
                    "[ПОМОЩЬ] Просто пиши мне! Я твой друг Питон.\n"
                    "  • Если не знаю ответ — поищу в интернете сам\n"
                    "  • 'очисти' — стереть чат\n"
                    "  • 'память' — сжать историю\n"
                    "  • 'пока' — выйти")
            elif auto_cmd == "exit":
                self.print_system("[ВЫХОД] Питон отключается... Пока! 🐍")
                self.root.after(600, self.quit_app)
            return

        if not check_ollama():
            self.print_user(text, ts)
            self.print_error("Ollama не запущена! Запусти сначала...")
            return

        if self.snake:
            self.snake.set_state("think")   # думает, пока идёт запрос

        # Авто-поиск в интернете
        search_context = ""
        if should_search_online(text):
            self.bottom.configure(text="[ПОИСК] Ищу в интернете...")
            results = web_search(text)
            if results:
                search_context = (
                    "Я нашёл в интернете следующую информацию по вопросу пользователя:\n"
                    + results +
                    "\n\nИспользуй эту информацию для ответа. Отвечай как Питон — с характером, мемами, лёгкими ругательствами. "
                    "Если информация неполная — скажи что проверил интернет, но точного ответа нет.\n\n"
                    "Вопрос пользователя: " + text
                )
                self.print_system("[ПОИСК] Нашёл инфу в интернете! Анализирую...")
            else:
                self.print_system("[ПОИСК] Не смог найти в интернете, отвечу по своему...")

        self.print_user(text, ts)
        self.typing = True
        self.current_ai_response = ""
        self.bottom.configure(text="Piton печатает...")

        final_prompt = search_context if search_context else text

        def callback(token, done):
            if self.snake and not done and token and not self.current_ai_response:
                self.snake.set_state("talk")        # пошли первые слова — говорит
            if done:
                if self.snake:
                    if token and "[ОШИБКА]" in token:
                        self.snake.finish("sad")
                    else:
                        self.snake.finish(emotion_for_text(text, self.current_ai_response) if emotion_for_text else None)
                self.typing = False
                self.bottom.configure(text="")
                self.print_ai(self.current_ai_response, time.strftime("%H:%M"))
                self.history.append({"timestamp": ts, "user": text, "ai": self.current_ai_response})
                try:
                    if piton_brain is not None:
                        piton_brain.analyze_exchange(text, self.current_ai_response, user_id="owner")
                except Exception:
                    pass
                self._save_with_sync()
            else:
                self.current_ai_response += token

        threading.Thread(target=lambda: ollama_stream(final_prompt, self.history, callback), daemon=True).start()


# ============ MAIN ============
if __name__ == "__main__":
    try:
        # Оверлей уже запущен (иконка в трее)? Тогда просто показываем основной чат
        # и завершаем этот запуск — никаких вторых иконок и окон.
        if is_already_running():
            notify_running_instance()
            sys.exit(0)
        root = tk.Tk()
        app = PitonOverlay(root)
        root.mainloop()
    except Exception as e:
        log_error("FATAL: " + str(e))
        import traceback
        log_error(traceback.format_exc())
        raise
