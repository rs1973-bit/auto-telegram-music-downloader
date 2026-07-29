import asyncio
import re
from pyrogram import Client
from pyrogram.types import Message
from pyrogram.enums import MessagesFilter
from src.utils.config import cfg
from src.utils.manager import Client_Manager
from src.database.sql_repo import SQL_REPO
from src.meta import SongTask, AlbumTask
from src.utils.search import is_audio_file, is_song_match
from src.utils.request_api import request_api
from src.utils.logger import logger


class Search_in_TG:
    def __init__(self, app: Client, manager: Client_Manager, queue: asyncio.Queue, sql: SQL_REPO):
        self.app = app
        self.manager = manager
        self.sql = sql
        self.channels = cfg.targets
        self.queue = queue

    # ------------------------------------------------------------------ #
    #  low-level Telegram helpers
    # ------------------------------------------------------------------ #

    async def _resolve_chat(self, chat_id: int):
        return await self.app.get_chat(chat_id)

    async def _fetch_nearby_messages(
        self, chat_id: int, anchor_id: int, side: int,
        *, forward_only: bool = False
    ) -> list[Message]:
        """从 anchor_id 附近拉取最多 side*2 条消息。

    参数:
        forward_only=True  → 只取 msg.id > anchor_id（公告后跟的文件）
        forward_only=False → 双向（msg.id 在 [anchor_id-side, anchor_id+side] 内）
    """
        msgs: list[Message] = []
        try:
            async for msg in self.app.get_chat_history(
                chat_id, offset_id=anchor_id + side + 1, limit=side * 2
            ):
                await self.manager.can_runs.wait()
                if forward_only:
                    if msg.id <= anchor_id:
                        break
                else:
                    if msg.id < anchor_id - side:
                        break
                msgs.append(msg)
        except Exception as e:
            logger.error(f"Error fetching messages near anchor {anchor_id}: {e}", exc_info=True)
        return msgs

    async def _build_track_map(self, band: str, album: str, msgs: list[Message]) -> dict[str, int]:
        """从一批消息中检出属于本专辑的音频文件，返回 song→msg_id 映射。

        采用"位置优先"策略：
          1. 收集区间内所有音频文件
          2. 如果恰好 album_len 个且每个都能匹配至少一首歌 → 按位置分配
             否则回退到文件名匹配

        这解决了 Sgt. Pepper's (Reprise) 这种两首歌名几乎一样、
        模糊匹配无法可靠区分的问题——因为位置本身就是信号。
        """
        songs = await self.sql.get_songs_from_IDX(band, album)
        album_len = len(songs)

        # ── 收集区间内所有音频消息 ──
        audio_msgs: list[Message] = []
        for msg in msgs:
            f = msg.document or msg.audio
            if f and f.file_name and is_audio_file(f.file_name):
                audio_msgs.append(msg)

        # ── 位置优先：恰好 album_len 个 → 按 msg_id 升序分配 ──
        if len(audio_msgs) == album_len:
            all_match = True
            for msg in audio_msgs:
                f = msg.document or msg.audio
                if not any(is_song_match(s, f.file_name) for s in songs):
                    all_match = False
                    break
            if all_match:
                audio_msgs.sort(key=lambda m: m.id)  # 确保从小到大
                return {songs[i]: audio_msgs[i].id for i in range(album_len)}

        # ── 回退：文件名模糊匹配 ──
        track_map: dict[str, int] = {}
        for msg in audio_msgs:
            f = msg.document or msg.audio
            for song in songs:
                if is_song_match(song, f.file_name):
                    track_map[song] = msg.id
                    break
        return track_map

    # ------------------------------------------------------------------ #
    #  single-song search
    # ------------------------------------------------------------------ #

    @staticmethod
    def _make_query(band: str, album: str, song: str) -> str:
        """构建 Telegram search_messages 查询串。

        只剥离元数据括号（Remastered、Deluxe 等），
        保留有含义的括号如 (Reprise)，否则 Sgt. Pepper's (Reprise)
        与 Sgt. Pepper's 在搜索层面无法区分。

        非拉丁艺人（如周杰伦）会繁简归一化，确保 Telecom 搜索命中。
        """
        from src.utils.language import normalize_cjk, has_cjk
        cleaned = re.sub(
            r'\s*[\(\[][^\)\]]*('
            r'remaster(?:ed)?(?:\s*\d{4})?|'
            r'deluxe|super deluxe|edition|anniversary|'
            r'mono|stereo|bonus|bonus track|'
            r'\d{4} remaster|\d{4} remix'
            r')[^\)\]]*[\)\]]?\s*',
            '', song, flags=re.I
        ).strip()
        # 非拉丁搜索词繁简归一化（iTunes TW 繁体 → 简体）
        if has_cjk(cleaned):
            cleaned = normalize_cjk(cleaned)
        if has_cjk(band):
            band = normalize_cjk(band)
        if has_cjk(album):
            album = normalize_cjk(album)
        return f"{band} {cleaned}"

    async def _search_one_by_filter(
        self, chat_id: int, query: str, song: str, filter_type: MessagesFilter
    ) -> int | None:
        """在一种 filter（AUDIO 或 DOCUMENT）下搜一首歌，匹配到返回 msg_id。"""
        async for message in self.app.search_messages(
            chat_id=chat_id, filter=filter_type, query=query, limit=20
        ):
            f = message.document or message.audio
            if f and f.file_name and is_audio_file(f.file_name) and is_song_match(song, f.file_name):
                return message.id
        return None

    async def _search_one_song(
        self, band: str, album: str, chat_id: int, song: str
    ) -> int | None:
        """在频道中搜一首歌，依次尝试 AUDIO 和 DOCUMENT 两种 filter，匹配到即返回 msg_id。"""
        query = self._make_query(band, album, song)
        logger.info(f"  [Search] {query}")

        for filter_type in (MessagesFilter.AUDIO, MessagesFilter.DOCUMENT):
            result = await self._search_one_by_filter(chat_id, query, song, filter_type)
            if result is not None:
                logger.info(f"  [Match] msg_id={result}")
                return result
        return None

    # ------------------------------------------------------------------ #
    #  per-channel scan
    # ------------------------------------------------------------------ #

    async def _scan_announcement(
        self, chat_id: int, anchor_id: int, band: str, album: str, album_len: int
    ) -> dict[str, int]:
        """从文字公告向后扫，收集公告后紧跟的专辑曲目。"""
        msgs = await self._fetch_nearby_messages(
            chat_id, anchor_id, album_len * 2, forward_only=True
        )
        return await self._build_track_map(band, album, msgs)

    async def _scan_around_anchor(
        self, chat_id: int, anchor_id: int, band: str, album: str, album_len: int
    ) -> dict[str, int]:
        """以搜到的某首歌 msg_id 为中心，向两侧各扫 album_len*2 条消息收集曲目。

    用于单曲补搜：搜到一首歌后，用它当锚点往两边展开，覆盖整张专辑。
    """
        msgs = await self._fetch_nearby_messages(
            chat_id, anchor_id, album_len * 2, forward_only=False
        )
        return await self._build_track_map(band, album, msgs)

    async def _search_album_in_channel(
        self, band: str, album: str, chat_id: int,
        songs: list[str], album_len: int, missing: set[str]
    ) -> dict[str, tuple[int, int]]:
        """在单个频道中搜索一张专辑，返回该频道命中的 song→(chat_id,msg_id)。

    会直接修改 missing 集合（从中移除找到的曲目），
    这样上层循环能追踪全局还有哪些歌没搜到。
    """
        track_map: dict[str, tuple[int, int]] = {}

        # ── 1. 公告路径：搜「作者 专辑名」找到文字公告 → 向后扫出全部曲目 ──
        query = f"{band} {album}"
        logger.info(f"  [Channel {chat_id}] AlbumSearch: {query}")
        async for msg in self.app.search_messages(chat_id=chat_id, query=query, limit=20):
            text = (msg.text or msg.caption or "").lower()
            if band.lower() not in text:
                continue
            found = await self._scan_announcement(chat_id, msg.id, band, album, album_len)
            for song, msg_id in found.items():
                if song in missing:
                    track_map[song] = (chat_id, msg_id)
                    missing.remove(song)
            if not missing:
                break

        # ── 2. 单曲补搜：公告没覆盖到的歌，挑最长的 3 首分别搜一次 ──
            #    用最长歌名是因为 Telegram 短词匹配容易误召回
        if missing:
            logger.info(f"  [Channel {chat_id}] Fill: {len(missing)} song(s) remaining")
            for song in sorted(missing, key=len, reverse=True)[:3]:
                anchor = await request_api(self._search_one_song, 2, band, album, chat_id, song)
                if not anchor:
                    continue
                found = await self._scan_around_anchor(chat_id, anchor, band, album, album_len)
                for song_name, msg_id in found.items():
                    if song_name in missing:
                        missing.remove(song_name)
                        track_map.setdefault(song_name, (chat_id, msg_id))
                if not missing:
                    break

        return track_map

    # ------------------------------------------------------------------ #
    #  album orchestrator
    # ------------------------------------------------------------------ #

    async def _search_album(self, band: str, album: str) -> bool:
        """遍历所有频道搜索一张专辑，直到全部找到或频道耗尽。"""
        songs = await self.sql.get_songs_from_IDX(band, album)
        if not songs:
            return False

        album_len = len(songs)
        track_map: dict[str, tuple[int, int]] = {}
        missing: set[str] = set(songs)

        # 跳过已在 data 表中的曲目（无论 status=0/1/2/-1，都已有完整下载信息）
        statuses = await self.sql.get_statuses_in_album(band, album, songs)
        for song in statuses:
            missing.discard(song)
        if not missing:
            logger.info(f"  [Skip] All songs already in data: {band} - {album}")
            return True

        for chat_id in self.channels:
            if not missing:
                break

            logger.info(f"Probing channel {chat_id}... ({len(missing)} song(s) still missing)")
            chat_info = await request_api(self._resolve_chat, 1, chat_id)
            if chat_info is None:
                continue

            found = await self._search_album_in_channel(band, album, chat_id, songs, album_len, missing)
            track_map.update(found)

        if not track_map:
            return False

        hit_rate = len(track_map) / album_len
        logger.info(f"  [Hit] {band} - {album} | {len(track_map)}/{album_len} ({int(hit_rate*100)}%)")
        await self._submit_tracks(band, album, track_map, songs)
        return True

    # ------------------------------------------------------------------ #
    #  submit
    # ------------------------------------------------------------------ #

    async def _submit_tracks(
        self, band: str, album: str,
        track_map: dict[str, tuple[int, int]], songs: list[str]
    ) -> None:
        """将 track_map 中命中的曲目写入数据库并送入下载队列。

        track_map: song → (chat_id, msg_id)
        """
        pending_tasks: list[SongTask] = []
        for idx, song in enumerate(songs, start=1):
            found = track_map.get(song)
            if found is None:
                logger.warning(f"  [Miss] Not found: {band} - {album} - {song}")
                continue
            chat_id, msg_id = found
            status = await self.sql.get_status_from_DATA(band, album, song)
            if status == 1:
                logger.info(f"  [Skip] Already downloaded: {band} - {album} - {song}")
                continue
            task = SongTask(
                band=band, album=AlbumTask(name=album, band=band),
                song=song, chat_id=chat_id, msg_id=msg_id, status=0, idx=idx,
            )
            pending_tasks.append(task)

        if not pending_tasks:
            logger.info(f"  [Info] All {len(songs)} songs already downloaded, skipping.")
            return

        await self.sql.insert_for_DATA(pending_tasks)
        for t in pending_tasks:
            await self.queue.put(t)

    # ------------------------------------------------------------------ #
    #  run
    # ------------------------------------------------------------------ #

    async def run(self) -> None:
        """启动搜索流程：恢复未完成任务 → 遍历数据库中的艺人/专辑 → 搜索。"""
        import time
        overall_start = time.time()

        # 重启时将上次未完成的 status=0 任务重新入队
        pending = await self.sql.get_pending_tasks()
        if pending:
            logger.info(f"[Resume] Re-queuing {len(pending)} pending task(s) from previous run")
            for t in pending:
                await self.queue.put(t)

        artists = await self.sql.get_bands_from_IDX()
        for band in artists:
            albums = await self.sql.get_albums_from_IDX(band)
            for album in albums:
                album_start = time.time()
                ok = await self._search_album(band, album)
                elapsed = time.time() - album_start
                if not ok:
                    logger.warning(f"[Result] Scanned all channels, album not found: {album} [{elapsed:.1f}s]")
                else:
                    logger.info(f"[Result] Album done: {band} - {album} [{elapsed:.1f}s]")
        total = time.time() - overall_start
        logger.info(f"[Result] All albums completed in {total:.1f}s")
