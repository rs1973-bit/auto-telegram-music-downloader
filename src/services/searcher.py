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
        # ── 快路径：文本搜索 caption（作者+曲名，去掉括号后缀以适配频道元数据） ──
        query_song = re.sub(r"\s*[\(\[].*?[\)\]]", "", song).strip()
        query = f"{band} {query_song}"
        print(f"  [Search] {query}")
        async for message in self.app.search_messages(chat_id=chat_id, query=query, limit=30):
            if message.id in cfg.collected_ids:
                continue
            file_obj = message.document or message.audio
            if not file_obj or not file_obj.file_name:
                continue
            if is_song_match(song, file_obj.file_name):
                start = message.id - original_idx
                end = message.id + (album_len - original_idx)
                cfg.collected_ids.add(message.id)
                return (chat_id, start, end)
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
            # 新 session 缺少 peer access_hash，search_messages 会报 PEER_ID_INVALID。
            # get_chat 负责解析 peer 并写入 Pyrogram 内部缓存，后续 search 才能工作。
            chat_info = await request_api(self.app.get_chat, 2, chat_id)
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
                id_range = await request_api(self.search_song_in_TG, 4, band, album, chat_id, song)
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
