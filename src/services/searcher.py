import asyncio
import re
from pyrogram import Client
from pyrogram.types import Message
from src.utils.config import cfg
from src.utils.manager import Client_Manager
from src.utils.language import is_latin
from src.database.sql_repo import SQL_REPO
from src.meta import SongTask, AlbumTask
from src.utils.search import *
from src.utils.request_api import request_api
class Search_in_TG:
    def __init__(self, app: Client, manager: Client_Manager, queue: asyncio.Queue, sql: SQL_REPO):
        self.app = app
        self.manager = manager
        self.sql = sql
        self.authers = cfg.author_list
        self.channels = cfg.targets
        self.queue = queue
    async def check(self, task: SongTask) -> bool:
        exists = await self.sql.is_song_exists(task.band, task.album, task.song)
        if exists:
            status = await self.sql.get_status_from_DATA(task.band, task.album, task.song)
            if status == 1:
                return True
        await self.queue.put(task)
        return False
    async def validate_album_status(self, band: str, album: str, chat_id: int, start: int, end: int) -> tuple[float, list[dict]]:
        msgs = await request_api(self.get_id_range, 3, chat_id, start, end)
        if msgs is None:
            return 0.0, []
        songs = await self.sql.get_songs_from_IDX(band, album)
        rate, hits = await evaluate_album_messages(songs, msgs)
        details = []
        for song, hit in zip(songs, hits):
            db_status = await self.sql.get_status_from_DATA(band, album, song)
            details.append({"song": song, "hit": bool(hit), "db_status": db_status})
        return rate, details
    async def get_id_range(self, chat_id: int, start_id: int, end_id: int) -> list[Message]:
        limit = (end_id - start_id) + 1
        messages = []
        try:
            async for msg in self.app.get_chat_history(chat_id, offset_id=end_id + 1, limit=limit):
                await self.manager.can_runs.wait()
                if msg.id < start_id:
                    break
                messages.append(msg)
        except Exception as e:
            print(f"Error fetching record range: {e}")
        messages.reverse()
        return messages
    async def search_song_in_TG(self, band: str, album: str, chat_id: int, song: str) -> tuple[int, int, int] | None:
        """在频道中搜索一首歌，匹配到即返回 (chat_id, start, end) 区间。"""
        songs = await self.sql.get_songs_from_IDX(band, album)
        album_len = len(songs)
        original_idx = songs.index(song)

        query_song = re.sub(r"\s*[\(\[].*?[\)\]]", "", song).strip()
        full_query = f"{band} {query_song}"

        print(f"  [Search] {full_query}")
        match = await self._try_search(chat_id, full_query, band, album, song, album_len, original_idx)
        if match:
            return match

        # 快路径没结果 → 降级为只搜 band 名，
        # 在文字公告附近找无 caption 的文件（如 Music In DSD 频道）
        print(f"  [Search] fallback: just band '{band}'")
        seen = set()
        async for msg in self.app.search_messages(chat_id=chat_id, query=band, limit=30):
            if msg.id in cfg.collected_ids or msg.id in seen:
                continue
            seen.add(msg.id)
            file_obj = msg.document or msg.audio
            if file_obj and file_obj.file_name:
                # 直接匹配到文件
                if is_song_match(song, file_obj.file_name):
                    start = msg.id - original_idx
                    end = msg.id + (album_len - original_idx)
                    cfg.collected_ids.add(msg.id)
                    return (chat_id, start, end)
            elif (msg.text or msg.caption) and self._is_album_announcement(band, album, msg):
                track = await self._find_nearby_file(chat_id, msg.id, band, album, song)
                if track:
                    start = track.id - original_idx
                    end = track.id + (album_len - original_idx)
                    cfg.collected_ids.add(track.id)
                    return (chat_id, start, end)
        return None

    async def _try_search(
        self, chat_id: int, query: str, band: str, album: str, song: str,
        album_len: int, original_idx: int,
    ) -> tuple[int, int, int] | None:
        """用 query 搜索频道，尝试匹配文件或文字公告。"""
        async for message in self.app.search_messages(chat_id=chat_id, query=query, limit=30):
            if message.id in cfg.collected_ids:
                continue
            file_obj = message.document or message.audio
            if file_obj and file_obj.file_name:
                if is_song_match(song, file_obj.file_name):
                    start = message.id - original_idx
                    end = message.id + (album_len - original_idx)
                    cfg.collected_ids.add(message.id)
                    return (chat_id, start, end)
            elif message.text or message.caption:
                if not self._is_album_announcement(band, album, message):
                    continue
                track = await self._find_nearby_file(chat_id, message.id, band, album, song)
                if track:
                    start = track.id - original_idx
                    end = track.id + (album_len - original_idx)
                    cfg.collected_ids.add(track.id)
                    return (chat_id, start, end)
        return None

    def _is_album_announcement(self, band: str, album: str, message: Message) -> bool:
        """粗略判断消息是否可能是某专辑的文字公告。"""
        text = (message.text or message.caption or "").lower()
        band_low = band.lower()
        # 只要文本中包含作者名即视为公告候选
        return band_low in text

    async def _find_nearby_file(
        self, chat_id: int, anchor_id: int, band: str, album: str, song: str
    ) -> Message | None:
        """搜索聊天历史中新的消息找到匹配的文件。"""
        songs = await self.sql.get_songs_from_IDX(band, album)
        album_len = len(songs)
        buffer = album_len * 5  # 缓冲区大小
        try:
            # get_chat_history 返回 offset 之前的消息（由新到旧）。
            # anchor 是文字公告，其后的文件 ID 更大，
            # 所以从 anchor + buffer 开始取，只保留 ID > anchor 的消息。
            async for msg in self.app.get_chat_history(
                chat_id, offset_id=anchor_id + buffer + 1, limit=buffer
            ):
                if msg.id <= anchor_id:
                    break
                f = msg.document or msg.audio
                if f and f.file_name and is_song_match(song, f.file_name):
                    return msg
        except Exception:
            pass
        return None
    async def submit_task(self, band: str, album: str, rate: float, chat_id: int, start: int, end: int) -> None:
        print(f"""  [Hit] {band} - {album} | hit rate: {rate:.2f} | ID: {start}-{end} CHAT_ID: {chat_id}""")
        songs = await self.sql.get_songs_from_IDX(band, album)
        pending_tasks = []
        first = start
        for idx, song in enumerate(songs, start=1):
            status = await self.sql.get_status_from_DATA(band, album, song)
            if status == 1:
                print(f"  [Skip] Already downloaded: {band} - {album} - {song}")
            else:
                task = SongTask(
                    band=band, album=AlbumTask(name=album, band=band),
                    song=song, chat_id=chat_id, msg_id=first, status=0, idx=idx,
                )
                pending_tasks.append(task)
            first += 1
        if not pending_tasks:
            print(f"  [Info] Album {band} - {album} fully downloaded, skipping.")
            return
        await self.sql.insert_for_DATA(pending_tasks)
        for t in pending_tasks:
            await self.queue.put(t)
    # ------------------------------------------------------------------ #
    #  专辑搜索：单阶段逐首匹配
    # ------------------------------------------------------------------ #
    async def search_album_in_TG(self, band: str, album: str) -> bool:
        """
        逐首搜索：对每首歌发起文本搜索，命中后估算区间并打分。
        快路径失败则跳过当前频道，不进行全量扫描兜底。
        拉丁与非拉丁曲目使用统一入口；is_song_match 内部按语种切换策略。
        """
        for chat_id in self.channels:
            print(f"Probing channel {chat_id}...")
            chat_info = await request_api(self.app.get_chat, 1, chat_id)
            if chat_info is None:
                continue
            songs = await self.sql.get_songs_from_IDX(band, album)
            sorted_songs = sorted(songs)
            all_done = True
            for song in sorted_songs:
                if await self.sql.is_song_exists(band, album, song):
                    status = await self.sql.get_status_from_DATA(band, album, song)
                    if status == 1:
                        print(f"  [Skip] {band} - {album} - {song} already downloaded")
                        continue
                all_done = False
                id_range = await request_api(self.search_song_in_TG, 2, band, album, chat_id, song)
                if id_range:
                    chat_id, start, end = id_range
                    rate, details = await self.validate_album_status(band, album, chat_id, start, end)
                    if rate >= 0.7:
                        print(f"  [Verify] Hit rate {rate}, details: {details}")
                        await self.submit_task(band, album, rate, chat_id, start, end)
                        return True
            if all_done:
                print(f"  [Info] Album {band} - {album} fully downloaded, skipping.")
                return True
        return False
    async def GET_HISTORY_AUDIO(self) -> None:
        import time
        overall_start = time.time()
        for auther in self.authers:
            albums = await self.sql.get_albums_from_IDX(auther)
            for album in albums:
                album_start = time.time()
                ok = await self.search_album_in_TG(auther, album)
                elapsed = time.time() - album_start
                if not ok:
                    print(f"[Result] Scanned all channels, album not found: {album} [{elapsed:.1f}s]")
                else:
                    print(f"[Result] Album done: {auther} - {album} [{elapsed:.1f}s]")
        total = time.time() - overall_start
        print(f"[Result] All albums completed in {total:.1f}s")
