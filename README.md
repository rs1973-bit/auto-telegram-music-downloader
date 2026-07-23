# Auto Telegram Music Downloader

从 Telegram 公开频道自动搜索并批量下载无损音乐（FLAC / WAV / DSF），支持并发下载、自动转码、封面/歌词/元数据写入。

---

## 目录

- [快速开始](#快速开始)
- [第一步：配置 config.json](#第一步配置-configjson)
- [第二步：运行](#第二步运行)
- [配置详解](#配置详解)
- [如何挑选频道](#如何挑选频道)
- [常见问题](#常见问题)

---

## 快速开始

### 依赖

| 环境 | 要求 |
|------|------|
| Python | ≥ 3.11 |
| FFmpeg | 任意版本 |
| Telegram 账号 | 需要 api_id + api_hash + bot_token(可选) |

```bash
# 安装 FFmpeg（Debian/Ubuntu）
sudo apt install ffmpeg

# 安装 Python 依赖
pip install -r requirements.txt
```

### 获取 Telegram 凭证

1. 前往 [my.telegram.org/apps](https://my.telegram.org/apps) → 创建应用 → 拿到 **api_id** 和 **api_hash**
2. 私信 [@BotFather](https://t.me/BotFather) → `/newbot` → 拿到 **bot_token**

---

## 第一步：配置 config.json

```bash
cp config.example.json config.json
```

只需要改这 5 个字段：

| 字段 | 你的值 |
|------|--------|
| `api_id` | 从 my.telegram.org 拿到的数字 ID |
| `api_hash` | 对应的 hash 字符串 |
| `bot_token` | @BotFather 给你的 bot token, 可不填 |
| `author_list` | 要下载的歌手，例如 `["The Beatles"]` |
| `target_channels` | 要从哪些频道搜，例如 `[-1002321822091]` |

完整示例：

```json
{
    "telegram": {
        "api_id": 12345678,
        "api_hash": "abc123def456",
        "bot_token": "12345678qwertyuioERTYU",
        "session_name": "my_session",
        "workers": 2,
        "author_list": ["The Beatles", "Pink Floyd", "Radiohead"]
    },
    "monitoring": {
        "target_channels": [-1002321822091,                -1001563715651],
        "exclude": ["live", "现场", "remake", "remix"]
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

> `workers` = 同时下载的并发数，建议 2–3，过高容易触发 Telegram 限流。

### author_list 写法

程序根据 `author_list` 从 Deezer 拉取专辑曲目列表，然后在频道里搜索匹配。

```json
"author_list": ["The Beatles"]
```

搜索全部专辑。也支持限定搜索范围：

```json
"author_list": ["The Beatles / Abbey Road"]
```

只搜索 Abbey Road 这一张专辑。

```json
"author_list": ["The Beatles / * / Come Together"]
```

在所有专辑中只搜索指定曲目。

### 频道 ID 从哪里来

`target_channels` 填的是 Telegram 频道 ID（负数格式，如 `-1002321822091`）。

本项目已经收集了 40 个无损音乐频道，详见 [channels.md](channels.md)（含语种/格式/曲风/音频数），直接复制 ID 即可。

---

## 第二步：运行

```bash
python main.py
```

**首次运行**会发生两件事：

1. **Pyrogram 登录** — 终端会提示输入手机号和 Telegram 验证码（仅首次，后续自动登录）
2. **Deezer 索引** — 自动查询 `author_list` 中歌手的录音室专辑曲目，缓存到 `songs.db`

索引完成后，程序自动开始扫描 `target_channels` 中的频道，匹配歌曲、下载、转码、写入元数据。

输出目录结构：

```
result/
└── The Beatles/
    ├── Abbey Road/
    │   ├── 01 Come Together (Remastered 2009).flac
    │   ├── 02 Something (Remastered 2009).flac
    │   └── ...
    ├── Revolver/
    └── ...
```

### 重新运行

第二次运行直接继续，不会重复下载已有文件。要清空重来：

```bash
rm -f songs.db data.db && rm -rf result temp
```

### 查看进度

程序运行时日志输出到终端和 `bot_running.log`。另外 bot 会向你的 Telegram 发送上线通知，回复 `/status` 查看实时状态（下载量、错误数、运行时长）。

---

## 配置详解

### audio — 转码参数

默认将原始文件（DSF/DFF/FLAC/WAV）统一转码为 FLAC（16-bit / 44100Hz）。

```json
{
    "sample_rate": 44100,
    "bit_depth": "s16",
    "format": "flac",
    "codec": "flac"
}
```

| 编码器 | 输出格式 | 用途 |
|--------|---------|------|
| `flac` | `.flac` | 默认，无损高压缩 |
| `alac` | `.m4a` | Apple 设备 |
| `pcm_s16le` | `.wav` | 16-bit PCM |
| `pcm_s24le` | `.wav` | 24-bit PCM |
| `aac` | `.m4a` | 有损压缩 |

### workers — 下载并发

```json
"workers": 2
```

每个 worker 独立下载一首歌。值越大速度越快，但也越容易被 Telegram 限流。建议 2–3。

### exclude — 文件名屏蔽

```json
"exclude": ["live", "现场", "remake", "remix"]
```

文件名包含这些词的音频会被搜索器跳过。

---

## 如何挑选频道

### 从 channels.md 选（推荐）

[channels.md](channels.md) 列出了 40 个已扫描频道，包含语种、格式、曲风、音频数量。

挑选逻辑：
- 挑语种匹配的（EN 适合欧美歌手）
- 挑音频数多的（内容更全）
- 挑曲风匹配的（Rock / Pop 频道适合 The Beatles）

把选中的频道 ID 填入 `target_channels`：

```json
"target_channels": [
    -1002321822091,
    -1001563715651,
    -1001356243397
]
```

### 验证频道是否可达

```bash
python3 -c "
import asyncio
from src.utils.config import cfg
from pyrogram import Client

async def main():
    app = Client(cfg.bot_name, api_id=cfg.api_id, api_hash=cfg.api_hash)
    await app.start()
    chat = await app.get_chat(-1002321822091)
    print(f'可达: {chat.title}')
    await app.stop()

asyncio.run(main())
"
```

### 自己找频道

1. 在 Telegram 里搜索无损音乐频道（关键词：FLAC、Hi-Res、Lossless）
2. 用 [@username_to_id_bot](https://t.me/username_to_id_bot) 获取频道 ID
3. 填入 `target_channels`
4. 如果频道是私密的，你的 userbot 账号必须先加入

---

## 常见问题

| 问题 | 原因 | 解决 |
|------|------|------|
| `CHAT_ID_INVALID` | 频道 ID 填错或 bot 不在频道里 | 检查 `target_channels` |
| 搜不到歌曲 | 频道里没有这个歌手的资源 | 换频道 / 检查 `author_list` 拼写 |
| 全部报 FloodWait | 并发太高或请求太密集 | 降低 `workers`；等待限流解除后重试 |
| 找不到某张专辑 | Deezer 未收录或索引时 API 超时 | 重新运行一次（索引已缓存到 songs.db） |
| `TgCrypto` 编译失败 | Windows + Python 3.12+ | 降级到 Python 3.11 |
| 结果目录空 | 还在搜索阶段或搜索未匹配 | 看终端日志是否在 `Probing channel...` |
| 想重新搜 | 清空索引 | `rm -f songs.db data.db` |

> 本项目**不适合古典音乐** — 搜索算法以"歌手→专辑→曲目"上下文区间为基础，古典乐的多乐章结构和混乱命名会导致匹配失败。
