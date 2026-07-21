# Auto Telegram Music Downloader

基于 Pyrogram 的异步 Telegram Userbot，用于从公开频道批量下载无损音乐（FLAC / DSD / DFF）并自动转码归档。

## 功能

- 📡 **频道监控** — 监听指定公开频道的新消息，自动过滤音频文件
- 🔍 **智能搜索** — 三阶段模糊匹配，从文件名/描述中提取歌手+专辑信息
- ⬇️ **批量下载** — 并发下载，支持断点续传，自动跳过已下载文件
- 🔄 **自动转码** — DSF/DFF → FLAC，可配置采样率/位深
- 🏷️ **元数据写入** — 自动添加封面、歌手、专辑、曲目号等标签
- 🗃️ **索引缓存** — Deezer API 索引结果缓存到 `songs.db`，避免重复请求
- 📋 **失败重试** — 下载/转码失败自动重试

---

## 架构

整个程序是一个**异步生产者-消费者**管道：

```
Deezer Indexer (元数据索引)
        │
        ▼
  Searcher (扫描频道 → 模糊匹配 → song_queue)
        │
        ▼
  Downloader (song_queue 消费者 → 下载 → conv_queue)
        │
        ▼
  Converter (conv_queue 消费者 → ffmpeg → 归档)
```

| 阶段 | 职责 | 并发控制 |
|---|---|---|
| 索引器 | 从 Deezer API 获取录音室专辑曲目列表，写入 SQLite | 全异步，Semaphore(3) |
| 搜索器 | 扫描目标频道历史消息，三阶段模糊匹配，入队 `song_queue` | 1 个异步 Task |
| 下载器 | 消费 `SongTask`，支持断点续传、失败重试、全局流量控制 | `workers` 个异步 Task |
| 转码器 | 消费 `ConvTask`，ffmpeg 转码后更新数据库 | 1 个异步 Task |

**数据库：**
- **songs.db** — 索引缓存：歌手→专辑→曲目（Deezer 缓存）
- **data.db** — 状态表：每首歌曲下载/转码状态（0=待下载, 1=完成, 2=待转码, -1=失败）

---

## 项目结构

```
├── config.json              # 配置文件
├── main.py                  # 入口
├── songs.db                 # 索引缓存数据库
├── temp/                    # 临时文件（转码中转）
├── result/                  # 最终输出
└── src/
    ├── database/
    │   └── sql_repo.py      # 数据库操作
    ├── metadata/
    │   ├── cover.py         # 封面下载
    │   ├── insert.py        # 元数据写入
    │   └── lyr.py           # 歌词缓存
    ├── services/
    │   ├── converter.py     # 音频转码 (ffmpeg)
    │   ├── downloader.py    # 下载器
    │   ├── index.py         # 索引器 (Deezer API)
    │   └── searcher.py      # 频道消息搜索
    └── utils/
        ├── config.py        # 配置读取
        ├── language.py      # 语种检测
        ├── logger.py        # 日志
        ├── manager.py       # Client 管理器
        ├── report_bot.py    # 报告机器人
        └── search.py        # 搜索匹配逻辑
```

### 搜索算法

`Search_in_TG.search_album_in_TG()` 按以下顺序尝试：

1. **文本快路径** — 按歌曲名搜索频道 caption，逐首匹配
2. **全量扫描回落** — 扫描频道所有音频文件名缓存，逐首做模糊匹配
3. **区间验证** — 匹配到第一首后，根据该消息 ID 推算整张专辑的区间，验证区间内所有曲目的命中率 ≥ 70%

拉丁曲目用 token 级交集 + 长词包含检查，非拉丁曲目用全串模糊匹配（WRatio + partial_ratio），两者使用不同的评分算法。

---

## 快速开始

### 1. 获取 Telegram 凭证

1. 前往 [my.telegram.org/apps](https://my.telegram.org/apps) 获取 `api_id` 和 `api_hash`
2. 私信 [@BotFather](https://t.me/BotFather) 创建机器人，获取 `bot_token`

### 2. 安装 FFmpeg

```bash
# Debian / Ubuntu
sudo apt update && sudo apt install ffmpeg

# Termux
pkg install ffmpeg
```

### 3. 安装依赖

推荐 Python ≥ 3.11。

```bash
pip install -r requirements.txt
```

| 包 | 用途 |
|---|---|
| pyrogram | Telegram MTProto 客户端 |
| TgCrypto | 加解密加速 |
| rapidfuzz | 模糊匹配 |
| httpx | 异步 HTTP（Deezer API） |
| aiosqlite | 异步 SQLite |
| mutagen | 音频元数据读写 |
| psutil | 系统资源监控 |

### 4. 配置

复制示例文件并根据自己的信息编辑：

```bash
cp config.example.json config.json
```

`config.json` 结构：

```json
{
    "telegram": {
        "api_id": "12345678",
        "api_hash": "your_api_hash_here",
        "bot_token": "your_bot_token",
        "session_name": "my_session",
        "workers": 2,
        "author_list": ["The Beatles"]
    },
    "monitoring": {
        "exclude": ["live", "现场", "remake", "remix"],
        "target_channels": [-1001234567890]
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

### 5. 运行

```bash
python main.py
```

首次运行时 Pyrogram 会要求输入手机号和二次验证码；登录成功后生成 `.session` 文件，后续不再重复提示。
首次运行且 `songs.db` 为空时，会自动通过 Deezer API 索引所有配置歌手的专辑曲目。

---

## 配置说明

### telegram

| 字段 | 说明 |
|---|---|
| `api_id` / `api_hash` | 从 my.telegram.org 获取 |
| `bot_token` | 从 @BotFather 获取 |
| `session_name` | Session 文件名（任意） |
| `workers` | 下载并发数，建议 2–5 |
| `author_list` | 要索引的歌手列表 |

### monitoring

| 字段 | 说明 |
|---|---|
| `target_channels` | 要扫描的频道 ID 列表（负数） |
| `exclude` | 文件名含这些关键词时跳过 |

### paths

| 字段 | 说明 |
|---|---|
| `temp` | 下载临时目录（非目标格式先下载到此待转码） |
| `result` | 最终归档目录 |
| `log` | 日志文件名 |

### audio

| 字段 | 说明 |
|---|---|
| `sample_rate` | 输出采样率（如 44100, 88200） |
| `bit_depth` | 输出位深：`s16`、`s24`、`s32` |
| `format` | 输出文件扩展名（如 `flac`, `wav`） |
| `codec` | FFmpeg 音频编码器（见下表） |

---

## 转码器配置

| 编码器 | 输出格式 | 特性 |
|---|---|---|
| `flac` | `.flac` | 无损，高压缩比，推荐 |
| `alac` | `.m4a` | 无损，Apple 设备友好 |
| `pcm_s16le` | `.wav` | 16-bit 未压缩 PCM |
| `pcm_s24le` | `.wav` | 24-bit 未压缩 PCM |
| `pcm_s32le` | `.wav` | 32-bit 未压缩 PCM |
| `wavpack` | `.wv` | 无损 |
| `aac` | `.m4a` | 有损 |

> 若用 WAV 格式，确保 `bit_depth` 与编码器匹配（如 `s24` → `pcm_s24le`）。

---

## 索引系统

项目使用 **Deezer 公开 API + iTunes 纠正** 作为索引源，无需 API 密钥。

流程：`search artist` → `get albums`（过滤录音室专辑） → `get tracks`

- 混合索引：Deezer 为主源，曲目含非拉丁字符时自动回退到 iTunes（同语种版本）
- 中日韩 / 西里尔语系歌手直接使用 iTunes 对应地区店（TW/JP/RU）
- 专辑自动去重（剥离 `(Remastered)` 等后缀）
- 非录音室专辑（Live / Compilation / Anthology）自动过滤
- 全异步并发，Semaphore(3) 控制速率
- 结果缓存至 `songs.db`，后续运行零等待

---

## 汇报机器人

启动时 `ReportBot` 向用户发送上线通知，回复 `/status` 查看实时状态：

- CPU / 内存占用
- 程序运行时长
- 下载器状态（激活/冷却）
- 已下载数据量 & 文件数
- 错误计数

---

## 常见错误

| 错误 | 原因 | 处理 |
|---|---|---|
| `TgCrypto` 编译失败 | Windows + Python 3.12/3.13 | 降级到 Python 3.11 |
| `Bad Request: CHAT_ID_INVALID` | 频道 ID 错误或被封禁 | 检查 `target_channels` 配置 |
| Deezer API 超时 | 网络/代理问题 | 内置 3 次重试 + 退避 |
| 未找到录音室专辑 | 歌手不在 Deezer 数据库 | 检查歌手名拼写 |

---

## 特别说明

- 索引器支持拉丁、**中日韩**、**西里尔**三种语系的歌手名，自动选择正确的数据源地区。
- 搜索匹配对不同语种使用不同策略：拉丁用 token 级交集，非拉丁用全串模糊匹配。
- 本项目**不适合古典音乐** — 搜索算法以"歌手→专辑→曲目"上下文区间为基础，古典乐的多乐章结构和混乱命名会导致匹配失败。
- 数据库文件 `songs.db` 和 `data.db` 存储所有状态，删除它们将丢失索引和进度。
- 欢迎 Issue 和 PR。
