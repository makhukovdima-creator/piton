# -*- coding: utf-8 -*-
"""
piton_snake.py — анимированная змея-аватар для оверлея Питона.

Как это работает: змея заранее отрендерена в наборы кадров (папка snake/ с manifest.json и
PNG-лентами). Здесь мы только проигрываем нужный клип — поэтому почти не грузим процессор,
не нужно ставить никаких библиотек, и выглядит она ровно как в 3D-просмотре.

Состояния: idle (ждёт), think (думает), talk (говорит), happy (радуется),
           sad (грустит), shy (смущается), surprised (удивляется).

Использование из оверлея (все методы безопасно вызывать и из других потоков):
    snake = SnakeAvatar.load(r"D:\\Piton\\snake", magic="#010203")
    snake.attach(canvas)              # рисуется в левом верхнем углу canvas
    snake.set_state("think")          # держится, пока не сменят
    snake.finish("shy")               # закончил отвечать: коротко покажет эмоцию и вернётся
    snake.react("happy", 2.0)         # эмоция на 2 секунды поверх всего
    snake.set_baseline("sad")         # «фон» вместо idle (например, когда Ollama выключена)
"""
import json
import os
import re
import time

from PIL import Image, ImageTk

STATES = ("idle", "think", "talk", "happy", "sad", "shy", "surprised")

LOOK_ACTIVE_SEC = 2.5    # сколько секунд после движения мыши змея смотрит за курсором
LOOK_RANGE_X = 450.0     # на сколько пикселей от змеи курсор «максимально» отводит взгляд
LOOK_RANGE_Y = 320.0

# ---------- какую эмоцию показать по тексту ----------
_SAD = re.compile(r"грустн|печал|тоскл|одинок|расстро|обидно|плач|мне плохо|плохо мне|депресс|тоска|скучаю", re.I)
_SHY = re.compile(r"спасибо|благодар|молодец|умниц|красав|классн|обожаю|люблю тебя|милый|милая|лучший|лучшая|нравишься|хороший мальчик|ты супер", re.I)
_SUR = re.compile(r"\bого+\b|\bвау+\b|не может быть|неожиданно|ничего себе|офигеть|охренеть|жесть|серь[её]зно\?|\?!|!\?", re.I)
_HAPPY_AI = re.compile(r"хах|ахах|😂|🤣|😄|😆|😁|😊|👍|✨|🌟|😜|😎|восторг|классно же", re.I)
_ANGRY_AI = re.compile(r"злюс|злит|бесит|раздража|ненавиж|тупо|дурацк|достал|бесит меня|😠|😡|🤬|злой", re.I)
_LOVE_AI = re.compile(r"люблю тебя|обожаю|🥰|😍|❤|💕|😘|милый мой|милая моя|сердце моё|родн|обниму|целую", re.I)
_SLEEPY_AI = re.compile(r"сонн|устал|зев|дремл|😴|🥱|спать хочу|вырубает", re.I)
_SUR_AI = re.compile(r"\bого+\b|\bвау+\b|не может быть|ничего себе|офигеть|охренеть|жесть|😳|🤯|неожиданно", re.I)


def emotion_for_text(user_text, ai_text=""):
    """Эмоция по репликам пользователя и Питона или None (тогда змея просто закончит говорить)."""
    u, a = user_text or "", ai_text or ""
    # Питон сопереживает, если хозяину плохо
    if _SAD.search(u):
        return "sad"
    # эмоции Питона — по тому, как он отвечает
    if _ANGRY_AI.search(a):
        return "angry"
    if _LOVE_AI.search(a):
        return "love"
    # хозяин похвалил — Питон смущается
    if _SHY.search(u):
        return "shy"
    if _SLEEPY_AI.search(a):
        return "sleepy"
    if _SUR.search(u):
        return "surprised"
    if _SUR_AI.search(a):
        return "surprised"
    if _HAPPY_AI.search(a):
        return "happy"
    return None


def _hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


class SnakeAvatar:
    @classmethod
    def load(cls, folder, magic="#010203", scale=1.0, follow=True):
        """Вернёт SnakeAvatar или None, если спрайтов нет (тогда оверлей покажет круглую иконку)."""
        mf = os.path.join(folder, "manifest.json")
        if not os.path.isfile(mf):
            return None
        with open(mf, "r", encoding="utf-8") as f:
            manifest = json.load(f)
        files = [c["file"] for c in manifest["clips"].values()] + [manifest["look"]["file"]]
        for name in files:
            if not os.path.isfile(os.path.join(folder, name)):
                return None
        return cls(folder, manifest, _hex_to_rgb(magic), scale, follow)

    def __init__(self, folder, manifest, magic_rgb, scale=1.0, follow=True):
        self.folder, self.m, self.magic, self.scale, self.follow = folder, manifest, magic_rgb, scale, follow
        self.fw, self.fh = manifest["w"], manifest["h"]
        self.w, self.h = max(1, int(round(self.fw * scale))), max(1, int(round(self.fh * scale)))
        self.fps = manifest.get("fps", 20)
        self._frames = {}                 # имя клипа -> [PhotoImage]
        self._canvas = None
        self._item = None
        self._stopped = False
        # управление (эти поля можно менять из любых потоков)
        self._baseline = "idle"
        self._manual = None               # держится, пока не сменят (например, think)
        self._react = None                # (имя, время_конца)
        # проигрыватель
        self.cur, self.phase, self.idx, self.dir = "idle", "idle", 0, 1
        self._last_ptr, self._last_move = None, 0.0
        self._look_idx = None
        self._preload_queue = [k for k in manifest["clips"] if k != "idle"] + ["look"]

    # ---------- внешнее управление ----------
    def set_state(self, name):
        self._manual = name if name in STATES and name != "idle" else None

    def release(self):
        self._manual = None

    def set_baseline(self, name):
        self._baseline = name if name in STATES else "idle"

    def react(self, name, seconds=2.0):
        if name in STATES:
            self._react = (name, time.time() + float(seconds))

    def finish(self, emotion=None):
        """Питон закончил отвечать: показать эмоцию (или чуть договорить) и вернуться в покой."""
        self._manual = None
        if emotion:
            self.react(emotion, 3.5)
        else:
            self.react("talk", 0.6)

    def stop(self):
        self._stopped = True

    # ---------- кадры ----------
    def _make_photo(self, im):
        if self.scale != 1.0:
            im = im.resize((self.w, self.h), Image.LANCZOS)
            im.putalpha(im.getchannel("A").point(lambda v: 255 if v >= 96 else 0))
        bg = Image.new("RGB", im.size, self.magic)              # прозрачное = цвет окна (-transparentcolor)
        bg.paste(im, (0, 0), im.getchannel("A"))
        return ImageTk.PhotoImage(bg)

    def _get(self, clip):
        fr = self._frames.get(clip)
        if fr is None:
            info = self.m["look"] if clip == "look" else self.m["clips"][clip]
            strip = Image.open(os.path.join(self.folder, info["file"])).convert("RGBA")
            fr = [self._make_photo(strip.crop((i * self.fw, 0, (i + 1) * self.fw, self.fh)))
                  for i in range(info["frames"])]
            self._frames[clip] = fr
        return fr

    def _preload_step(self):
        """Подгружаем остальные клипы по одному в фоне, чтобы не было подвисаний при первой эмоции."""
        if self._stopped or not self._preload_queue:
            return
        try:
            self._get(self._preload_queue.pop(0))
        except Exception:
            pass
        if self._preload_queue:
            self._canvas.after(250, self._preload_step)

    # ---------- логика состояний (без Tk — легко тестировать) ----------
    def _desired(self, now):
        if self._react and now < self._react[1]:
            return self._react[0]
        return self._manual or self._baseline

    def _step(self, now, pointer=None, origin=None):
        """Один шаг анимации. Возвращает (клип, номер кадра)."""
        want = self._desired(now)
        if self.phase == "idle":
            if want != "idle":
                self.cur, self.phase, self.idx = want, "enter", 0
                return self.cur, 0
            look = self._look_frame(now, pointer, origin)
            if look is not None:
                return "look", look
            n = self.m["clips"]["idle"]["frames"]
            self.idx += self.dir
            if self.idx >= n - 1:
                self.idx, self.dir = n - 1, -1
            elif self.idx <= 0:
                self.idx, self.dir = 0, 1
            return "idle", self.idx
        c = self.m["clips"][self.cur]
        E, L = c["enter"], c["loop"]
        if self.phase == "enter":
            self.idx += 1
            if self.idx >= E:
                self.phase, self.idx, self.dir = "loop", E, 1
            return self.cur, min(self.idx, c["frames"] - 1)
        if self.phase == "loop":
            if want != self.cur:
                self.phase, self.idx = "exit", E - 1
                return self.cur, self.idx
            self.idx += self.dir
            if self.idx >= E + L - 1:
                self.idx, self.dir = E + L - 1, -1
            elif self.idx <= E:
                self.idx, self.dir = E, 1
            return self.cur, self.idx
        # exit: проигрываем вход задом наперёд и возвращаемся в idle
        self.idx -= 1
        if self.idx < 0:
            self.cur, self.phase, self.idx, self.dir = "idle", "idle", 0, 1
            return "idle", 0
        return self.cur, self.idx

    def _look_frame(self, now, pointer, origin):
        """Кадр «смотрит на курсор» или None (курсор давно не двигали / слежение выключено)."""
        if not self.follow or pointer is None or origin is None:
            return None
        if pointer != self._last_ptr:
            self._last_ptr, self._last_move = pointer, now
        if now - self._last_move > LOOK_ACTIVE_SEC:
            self._look_idx = None
            return None
        info = self.m["look"]
        lx = max(-1.0, min(1.0, (pointer[0] - origin[0]) / LOOK_RANGE_X))
        ly = max(-1.0, min(1.0, -(pointer[1] - origin[1]) / LOOK_RANGE_Y))
        col = min(range(info["cols"]), key=lambda i: abs(info["xs"][i] - lx))
        row = min(range(info["rows"]), key=lambda j: abs(info["ys"][j] - ly))
        self._look_idx = row * info["cols"] + col
        return self._look_idx

    # ---------- Tk ----------
    def attach(self, canvas, x=0, y=0):
        self._canvas = canvas
        self._item = canvas.create_image(x, y, image=self._get("idle")[0], anchor="nw")
        canvas.after(400, self._preload_step)
        canvas.after(int(1000 / self.fps), self._tick)
        return self._item

    def _tick(self):
        if self._stopped:
            return
        cv, delay = self._canvas, int(1000 / self.fps)
        try:
            if not cv.winfo_viewable():           # окно спрятано в трей — почти не работаем
                cv.after(400, self._tick)
                return
            pointer = origin = None
            if self.follow:
                pointer = cv.winfo_pointerxy()
                origin = (cv.winfo_rootx() + self.w * 0.55, cv.winfo_rooty() + self.h * 0.25)
            clip, i = self._step(time.time(), pointer, origin)
            cv.itemconfig(self._item, image=self._get(clip)[i])
        except Exception:
            pass
        try:
            cv.after(delay, self._tick)
        except Exception:
            self._stopped = True
