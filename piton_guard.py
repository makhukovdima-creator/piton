# -*- coding: utf-8 -*-
"""
piton_guard.py — защита от дублей для сервисов Питона.

Каждый сервис вызывает ensure_single(имя_скрипта) при старте:
любой новый процесс убивает старые копии того же скрипта и остаётся один.
Так кто бы ни запускал сервис (хаб, watchdog, автозагрузка, руки) —
в системе всегда ровно один экземпляр.
"""
import os
import subprocess


def ensure_single(script_name, my_pid=None):
    """Убивает другие процессы с тем же скриптом. Возвращает True, если продолжить."""
    if my_pid is None:
        my_pid = os.getpid()
    cmd = (
        "Get-CimInstance Win32_Process | Where-Object { "
        "$_.Name -like 'python*' -and "
        "$_.CommandLine -like '*" + script_name + "*' -and "
        "$_.ProcessId -ne " + str(my_pid) +
        " } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", cmd],
            capture_output=True, timeout=30,
            creationflags=0x08000000,
        )
    except Exception:
        pass
