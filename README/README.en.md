# Auto Telegram Music Downloader

An async Telegram music downloader (Userbot) built with Pyrogram. It bulk-downloads lossless music from public channels, optionally transcodes it, and archives everything under an **Artist → Album → Track** directory structure.

## Features

- 📡 **Channel monitoring** — Watch public channels for new audio messages
- 🔍 **Smart search** — 3-phase fuzzy matching to identify artist/album/track from filenames
- ⬇️ **Batch download** — Concurrent downloads with resume support, skip already-downloaded files
- 🔄 **Auto transcode** — DSF/DFF → FLAC, configurable sample rate and bit depth
- 🏷️ **Metadata injection** — Album art, artist, album, track number via mutagen
- 🗃️ **Index cache** — Deezer API index cached in `songs.db`, zero-wait on subsequent runs
- 📋 **Retry on failure** — Failed downloads/transcodes are retried automatically

---

## Architecture

The program is an **async producer-consumer pipeline**:

```
Deezer Indexer (metadata)
        │
        ▼
  Searcher (scan channels → fuzzy match → song_queue)
        │
        ▼
  Downloader (song_queue consumer → download → conv_queue)
        │
        ▼
  Converter (conv_queue consumer → ffmpeg → archive)
```

| Stage | Responsibility | Concurrency |
|---|---|---|
| Indexer | Fetch studio album tracklists via Deezer API, write to SQLite | Fully async, Semaphore(3) |
| Searcher | Scan channel history, 3-phase fuzzy matching, enqueue to `song_queue` | 1 async Task |
| Downloader | Consume `SongTask`s with resume support, retries, rate limiting | `workers` async Tasks |
| Converter | Consume `ConvTask`s, run ffmpeg, update DB | 1 async Task |

**Databases:**
- **songs.db** — track index: artist → album → tracks (cached from Deezer)
- **data.db** — per-track download/transcode status (0=pending, 1=done, 2=downloaded-pending-conversion, -1=failed)

---

## Project Structure

```
├── config.json              # Configuration
├── main.py                  # Entry point
├── songs.db                 # Index cache
├── temp/                    # Temporary files
├── result/                  # Final output
└── src/
    ├── database/
    │   └── sql_repo.py      # SQLite operations
    ├── metadata/
    │   ├── cover.py         # Album art download
    │   ├── insert.py        # Metadata injection
    │   └── lyr.py           # Lyrics cache
    ├── services/
    │   ├── converter.py     # Audio transcoding (ffmpeg)
    │   ├── downloader.py    # Download worker
    │   ├── index.py         # Indexer (Deezer API)
    │   └── searcher.py      # Channel message scanner
    └── utils/
        ├── config.py        # Config loader
        ├── logger.py        # Logging
        ├── manager.py       # Client manager
        ├── report_bot.py    # Status reporter
        └── search.py        # Search matching logic
```

### Search Algorithm (3-Phase)

`Search_in_TG.search_album_in_TG()` tries in order:

1. **Phase 1 — Quick Match** — Search messages by artist name in author field, check filename against tracklist
2. **Phase 2 — Fallback** — Fetch all audio files, look for album title mentions, build context window
3. **Phase 3 — Precision** — Isolate all messages by artist, match filenames one-by-one against tracklist

Each window is scored by `is_album_match()`; accepted only when hit rate ≥ 80%.

---

## Quick Start

### 1. Get Telegram Credentials

1. Get `api_id` and `api_hash` at [my.telegram.org/apps](https://my.telegram.org/apps)
2. Create a bot via [@BotFather](https://t.me/BotFather) to get a `bot_token`

### 2. Install FFmpeg

```bash
# Debian / Ubuntu
sudo apt update && sudo apt install ffmpeg

# Termux
pkg install ffmpeg
```

### 3. Install Dependencies

Python ≥ 3.11 recommended.

```bash
pip install -r requirements.txt
```

| Package | Purpose |
|---|---|
| pyrogram | Telegram MTProto client |
| TgCrypto | Crypto acceleration |
| rapidfuzz | Fuzzy string matching |
| httpx | Async HTTP client (Deezer API) |
| aiosqlite | Async SQLite |
| mutagen | Audio metadata reader/writer |
| psutil | System resource monitor |

### 4. Configure

Edit `config.json`:

```json
{
    "telegram": {
        "api_id": "12345",
        "api_hash": "your_api_hash",
        "bot_token": "your_bot_token",
        "session_name": "rs1973",
        "workers": 2
    },
    "monitoring": {
        "exclude": ["live", "现场", "remake", "remix"],
        "target_channels": [-1001234567890],
        "author": ["The Beatles"]
    },
    "paths": {
        "temp": "temp",
        "result": "result",
        "log": "bot_running.log"
    },
    "audio": {
        "sample_rate": 44100,
        "bit_depth": "s16",
        "format": "flac",
        "codec": "flac"
    }
}
```

### 5. Run

```bash
python main.py
```

On first run, Pyrogram will prompt for phone number and 2FA password. Session files (`.session`) are created and reused on subsequent runs.

On first run with an empty `songs.db`, the Deezer indexer will automatically fetch album tracklists for all configured artists.

---

## Configuration Reference

### telegram

| Field | Description |
|---|---|
| `api_id` / `api_hash` | From my.telegram.org |
| `bot_token` | From @BotFather |
| `session_name` | Session file name (arbitrary) |
| `workers` | Download concurrency, recommended 2–5 |

### monitoring

| Field | Description |
|---|---|
| `target_channels` | List of channel IDs to scan (negative numbers) |
| `author` | List of artist names to index and download |
| `exclude` | Skip files whose names contain any of these keywords |

### paths

| Field | Description |
|---|---|
| `temp` | Temporary download directory (non-target formats land here before conversion) |
| `result` | Final archive directory |
| `log` | Log file name |

### audio

| Field | Description |
|---|---|
| `sample_rate` | Output sample rate (e.g. 44100, 88200) |
| `bit_depth` | Output bit depth: `s16`, `s24`, `s32` |
| `format` | Output file extension (e.g. `flac`, `wav`) |
| `codec` | FFmpeg audio encoder (see table below) |

---

## Encoder Reference

| Encoder | Output Format | Characteristics |
|---|---|---|
| `flac` | `.flac` | Lossless, high compression ratio, recommended |
| `alac` | `.m4a` | Lossless, Apple-friendly |
| `pcm_s16le` | `.wav` | 16-bit uncompressed PCM |
| `pcm_s24le` | `.wav` | 24-bit uncompressed PCM |
| `pcm_s32le` | `.wav` | 32-bit uncompressed PCM |
| `wavpack` | `.wv` | Lossless |
| `aac` | `.m4a` | Lossy |

> When using WAV output, ensure `bit_depth` matches the encoder (e.g. `s24` → `pcm_s24le`).

---

## Index System

The project uses the **Deezer public API** (no API key required) as its index source.

Flow: `search artist` → `get albums` (filtered to studio albums) → `get tracks`

- Albums are de-duplicated by normalized title (stripping `(Remastered)` suffixes)
- Non-studio albums (Live / Compilation / Anthology) are filtered out via regex
- Fully async with Semaphore(3) rate control
- Results cached in `songs.db` — zero waiting on subsequent runs

---

## Report Bot

On startup, `ReportBot` sends an online notification to the configured user. Reply with `/status` at any time to see:

- CPU / memory usage
- Uptime
- Downloader state (active / cooling)
- Total data & file count
- Error count

---

## Troubleshooting

| Error | Cause | Workaround |
|---|---|---|
| `TgCrypto` build failure | Windows + Python 3.12/3.13 | Downgrade to Python 3.11 |
| `Bad Request: CHAT_ID_INVALID` | Invalid or banned channel ID | Check `target_channels` config |
| Deezer API timeout | Network/proxy issue | Built-in 3 retries with backoff |
| No studio albums found | Artist not in Deezer DB | Check artist name spelling |

---

## Notes

- This project is **not suitable for classical music** — the search algorithm relies on "artist → album → track" context windows, which don't work well with classical music's multi-movement structure and inconsistent naming.
- The `songs.db` and `data.db` files store all state. Deleting them will wipe the index and download progress.
- Issues and PRs are welcome!
