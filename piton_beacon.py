# -*- coding: utf-8 -*-
"""
Piton Beacon — публикует текущий адрес мини-сервера в GitHub gist.
Приложение-маяк: мини-сервер (8765) -> ngrok -> gist raw URL.
При каждом изменении адреса туннеля скрипт обновляет содержимое gist,
чтобы телефон всегда мог найти Питона.
"""
import json
import os
import sys
import time
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
MEMORY = os.environ.get("PITON_MEMORY") or r"D:\Piton_private\PitonMemory"
LOG_PATH = os.path.join(MEMORY, "logs", "piton_beacon.log")

GIST_RAW = "https://gist.githubusercontent.com/makhukovdima-creator/635d1b396f60e490a77d4db63f30cc1f/raw"
GIST_API = "https://api.github.com/gists/635d1b396f60e490a77d4db63f30cc1f"
TOKEN_FILE = os.path.join(MEMORY, "github_token.txt")
ADDR_FILE = os.path.join(MEMORY, "beacon_addr.txt")

CHECK_INTERVAL = 30  # сек


def log(msg):
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        line = time.strftime("[%Y-%m-%d %H:%M:%S] ") + msg
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def get_token():
    try:
        with open(TOKEN_FILE, "r", encoding="utf-8-sig") as f:
            return f.read().strip()
    except Exception:
        return ""


def _opener():
    """opener без прокси — чтобы localhost-запросы не уходили в VPN/прокси"""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def get_tunnel_url():
    try:
        req = urllib.request.Request("http://127.0.0.1:4040/api/tunnels")
        data = json.load(_opener().open(req, timeout=5))
        for t in data.get("tunnels", []):
            u = t.get("public_url", "")
            if u.startswith("https://"):
                return u.rstrip("/")
    except Exception:
        pass
    # запасной вариант: последний известный адрес
    try:
        with open(ADDR_FILE, "r", encoding="utf-8-sig") as f:
            return f.read().strip()
    except Exception:
        return ""


def publish(addr):
    token = get_token()
    if not token:
        log("НЕТ ТОКЕНА — пропускаю публикацию")
        return False
    body = json.dumps({
        "files": {"piton-addr.txt": {"content": addr}}
    }).encode("utf-8")
    req = urllib.request.Request(GIST_API, data=body, method="PATCH")
    req.add_header("Authorization", "Bearer " + token)
    req.add_header("User-Agent", "piton-beacon")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("Content-Type", "application/json")
    try:
        with _opener().open(req, timeout=20) as r:
            r.read()
        with open(ADDR_FILE, "w", encoding="utf-8") as f:
            f.write(addr)
        log("адрес опубликован: " + addr)
        return True
    except Exception as e:
        log("ошибка публикации: " + str(e))
        return False


def main():
    log("маяк запущен (gist: " + GIST_RAW + ")")
    last = None
    while True:
        try:
            addr = get_tunnel_url()
            if addr and addr != last:
                publish(addr)
                last = addr
        except Exception as e:
            log("ошибка цикла: " + str(e))
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
