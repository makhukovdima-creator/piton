# Питон

Локальный ИИ-компаньон: плавающая иконка поверх всех окон, долговременная память,
дневник, веб-морда и Android-клиент. Всё считается локально через
[Ollama](https://ollama.com) — ни одного облачного API.

```
 ┌─────────────┐   ┌──────────────┐   ┌─────────────┐
 │  оверлей    │   │  мини-апп    │   │  Android    │
 │  (tkinter)  │   │ (веб, SSE)   │   │  (клиент)   │
 └──────┬──────┘   └──────┬───────┘   └──────┬──────┘
        └──────────┬──────┴──────────────────┘
                   ▼
            ┌──────────────┐        ┌───────────────┐
            │  piton_llm   │───────▶│    Ollama     │
            └──────┬───────┘        └───────────────┘
                   ▼
        ┌─────────────────────┐
        │  память (RAG)       │  chat_history, vector_memory,
        │  дневник, рефлексия │  SOUL, ideas, diary
        └─────────────────────┘
```

## Что умеет

- **Память с RAG** — эмбеддинги разговоров в `vector_memory.npz`, подмешивание
  релевантных воспоминаний в промпт.
- **Оверлей** — прозрачное окно поверх всех, анимация-змея по эмоциям ответа,
  трей, стриминг токенов.
- **Синхронизация интерфейсов** — оверлей, веб-морда и мессенджер читают и пишут
  один `chat_history.json` и подхватывают чужие сообщения на лету.
- **Дневник и рефлексия** — раз в ~45 минут, если хозяин молчит, модель
  размышляет о себе и о разговорах, пишет мысли в `SOUL`.
- **Инициатива** — сам возвращается к отложенным идеям и темам.
- **Мини-приложение** — SSE-стриминг на голом `http.server` с токен-авторизацией.

## Требования

- Windows 10/11 (оверлей и трей прибиты к Win32)
- Python 3.11+ (проверено на 3.13)
- [Ollama](https://ollama.com) с моделью:
  ```
  ollama pull huihui_ai/qwen3-abliterated:8b
  ```

## Установка

```powershell
git clone <repo>
cd Piton
pip install -r requirements.txt
copy config.example.json D:\Piton_private\PitonMemory\piton_config.json
```

Данные (память, дневник, конфиг, токены) живут **вне репозитория** — по умолчанию
`D:\Piton_private\PitonMemory`. Путь задаётся переменной `PITON_MEMORY`:

```powershell
setx PITON_MEMORY "D:\Piton_private\PitonMemory"
```

## Запуск

```powershell
python piton_overlay.py     # плавающая иконка + чат
python piton_mini_server.py # веб-морда на http://127.0.0.1:8765
python piton_hub_server.py  # хаб для Android-клиента
```

Android: `cd PitonApp; gradle assembleDebug` (см. `PitonApp/app/src`).

## Переменные окружения

| Переменная | По умолчанию | Назначение |
|---|---|---|
| `PITON_MEMORY` | `D:\Piton_private\PitonMemory` | каталог данных |
| `PITON_MODEL` | `huihui_ai/qwen3-abliterated:8b` | модель Ollama |
| `OLLAMA_URL` | `http://127.0.0.1:11434` | адрес Ollama |

## Структура

| Файл | Роль |
|---|---|
| `piton_llm.py` | единая точка вызова модели, стриминг, шаблоны чата |
| `piton_overlay.py` | оверлей: окно, трей, чат, анимации |
| `piton_snake.py` | змея-аватар, эмоции по тексту |
| `piton_memory.py` / `piton_memory_vect.py` | память: история и векторный поиск |
| `piton_mini_server.py` | веб-мини-апп (SSE + авторизация) |
| `piton_hub_server.py` | хаб для Android-клиента |
| `piton_reflect.py` | фоновые «сны»: рефлексия и обновление SOUL |
| `piton_initiator.py` | инициатива: возврат к идеям |
| `piton_diary.py`, `piton_ideas.py` | дневник и копилка идей |
| `piton_websearch.py`, `piton_agent.py` | веб-поиск и агентные сценарии |
| `piton_beacon.py`, `ngrok_watchdog.py` | проброс наружу и слежение за туннелем |
| `mini_index.html`, `mini_snake.html` | интерфейс мини-аппа |
| `PitonApp/` | Android-клиент (Java, Gradle) |

## Статус

Проект личный, пишется под одного пользователя: нет тестов, нет миграций данных,
мультитенантность не предусмотрена. Секреты в репозиторий не попадают — см.
`.gitignore`.

## Лицензия

MIT — см. [LICENSE](LICENSE).
