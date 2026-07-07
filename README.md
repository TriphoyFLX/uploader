# BeatMachine

Автозагрузка type beat'ов на YouTube: кидаешь биты и картинки → получаешь видео на канале.

## Как работает

1. Кидаешь **любые биты** в `beats/` (имя файла не важно)
2. Кидаешь **любые картинки** в `image/`
3. Названия берутся из `titles.txt` — по порядку, одно на бит
4. BPM и тональность — из имени файла, тегов или анализа аудио
5. Картинки сопоставляются по кругу: бит 1 → картинка 1, бит 2 → картинка 2, и т.д.

## Структура папки артиста

```
artists/che/
├── beats/           ← все биты (любые имена)
├── image/           ← все обложки (рандомные)
├── titles.txt       ← названия для YouTube ("hearts", "encore"...)
├── tags.txt         ← длинный список тегов в описание
├── description.txt  ← шаблон описания
└── config.json      ← title_template, контакты, API-теги
```

## Пример titles.txt

```
hearts
dark
void
encore
```

Бит №1 → `(free) che type beat "hearts"`
Бит №2 → `(free) che type beat "dark"`

## Шаблоны

| Переменная | Что подставляется |
|---|---|
| `{title}` | из titles.txt |
| `{bpm}` | 145 |
| `{key}` | A# min |
| `{producer}` | triphoy |
| `{purchase_link}` | из config.json |
| `{tags_line}` | из tags.txt |
| `{hashtags}` | из config.json |

## Варианты названий (config.json)

```json
"title_template": "(free) che type beat \"{title}\""
"title_template": "(free) osamason type beat \"{title}\""
"title_template": "(free) osamason + che type beat \"{title}\""
```

Папка `osamason+che/` уже настроена под комбинированный формат.

## Команды

```bash
python uploader.py --dry-run       # превью всех битов
python uploader.py --artist che    # только che
python uploader.py --render-only   # только видео
python uploader.py                   # загрузка на YouTube
```

## Автопубликация (scheduler)

Запусти один раз — публикует **каждый день в 21:00 МСК**:

```bash
python scheduler.py --daemon
```

### Недельное расписание (`schedule_config.json`)

| Артист | Битов в неделю |
|--------|----------------|
| osamason | 4 |
| che | 2 |
| osamason+che | 1 |

**Итого: 7 битов/неделю = 1 в день**

### Что происходит после публикации

1. Бит удаляется из `beats/`
2. Обложка удаляется из `image/`
3. Название переносится в `archive_titles.txt`
4. Название убирается из `titles.txt`

### Если биты закончились

Если у osamason нет битов/картинок/названий — система берёт che или osamason+che (кто готов).

### Команды

```bash
python scheduler.py --daemon    # работает постоянно
python scheduler.py --status      # статус и что будет дальше
python scheduler.py --now         # опубликовать 1 бит сейчас (тест)
python scheduler.py --dry-run     # превью без загрузки и удаления
```

### Запуск в фоне (macOS)

```bash
nohup python scheduler.py --daemon > scheduler.log 2>&1 &
```


```bash
cd BeatMachine
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
brew install ffmpeg
```

YouTube API: положи `credentials/client_secrets.json` (см. Google Cloud Console → YouTube Data API v3).
