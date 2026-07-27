import asyncio
import re
from pyrogram import Client
from pyrogram.types import Message
from pyrogram.enums import MessagesFilter
from src.utils.config import cfg
from src.utils.manager import Client_Manager
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
    
    async def _validate_hit_rate(self, band: str, album: str, chat_id: int, start: int, end: int) -> tuple[float, list[dict]]:
        songs = await self.sql.get_songs_from_IDX(band, album)
        album_len = len(songs)

        best_rate, best_details = 0.0, []
        for multiplier in (1, 2, 3):
            expanded_end = start + album_len * multiplier - 1
            msgs = await request_api(self._fetch_messages, 3, chat_id, start, expanded_end)
            if msgs is None:
                continue
            rate, hits = await evaluate_album_messages(songs, msgs)
            details = []
            for song, hit in zip(songs, hits):
                db_status = await self.sql.get_status_from_DATA(band, album, song)
                details.append({"song": song, "hit": bool(hit), "db_status": db_status})
            if rate > best_rate:
                best_rate, best_details = rate, details
            if best_rate >= 0.7:
                break
        return best_rate, best_details
    
    async def _fetch_messages(self, chat_id: int, start_id: int, end_id: int) -> list[Message]:
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
    
    async def _search_song(self, band: str, album: str, chat_id: int, song: str) -> tuple[int, int, int] | None:
        """在频道中搜索一首歌，匹配到即返回 (chat_id, start, end) 区间。"""
        songs = await self.sql.get_songs_from_IDX(band, album)
        album_len = len(songs)
        original_idx = songs.index(song)

        query_song = re.sub(r"\s*[\(\[].*?[\)\]]", "", song).strip()
        full_query = f"{band} {query_song}"

        print(f"  [Search] {full_query}")
        match = await self._search_with_filters(chat_id, full_query, band, album, song, album_len, original_idx)
        if match:
            return match
        return None

    async def _match_message(
            self, chat_id: int, message: Message, 
            band: str, album: str, song: str,
            album_len: int, original_idx: int
        ) -> tuple[int, int, int] | None:
        """处理一个Message对象"""
        if message.id in cfg.collected_ids:
                return  
        file_obj = message.document or message.audio
        if file_obj and file_obj.file_name and is_audio_file(file_obj.file_name):
            if is_song_match(song, file_obj.file_name):
                start = message.id - original_idx
                end = message.id + (album_len - original_idx)
                cfg.collected_ids.add(message.id)
                return (chat_id, start, end)
        elif message.text or message.caption:
            if not self._is_announcement(band, album, message):
                return
            track = await self._find_nearby_file(chat_id, message.id, band, album, song)
            if track:
                start = track.id - original_idx
                end = track.id + (album_len - original_idx)
                cfg.collected_ids.add(track.id)
                return (chat_id, start, end)
                
    async def _search_with_filters(
        self, chat_id: int, query: str, band: str, album: str, song: str,
        album_len: int, original_idx: int
    ) -> tuple[int, int, int] | None:
        """用 AUDIO + DOCUMENT 两种 filter 搜索频道，收集候选并验证。"""
        candidates = []

        async for message in self.app.search_messages(chat_id=chat_id, filter=MessagesFilter.AUDIO, query=query, limit=100):
            result = await self._match_message(
                chat_id, message, band, album, song,
                album_len, original_idx
                )
            if result: candidates.append(result)

        async for message in self.app.search_messages(chat_id=chat_id, filter=MessagesFilter.DOCUMENT, query=query, limit=100):
            result = await self._match_message(
                chat_id, message, band, album, song,
                album_len, original_idx
                )
            if result: candidates.append(result)

        seen = set()
        for cid, start, end in candidates:
            key = (cid, start, end)
            if key in seen:
                continue
            seen.add(key)
            rate, _ = await self._validate_hit_rate(band, album, cid, start, end)
            if rate >= 0.7:
                return (cid, start, end)
        return None

    def _is_announcement(self, band: str, album: str, message: Message) -> bool:
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
                if f and f.file_name and is_audio_file(f.file_name) and is_song_match(song, f.file_name):
                    return msg
        except Exception:
            pass
        return None

        
    async def _submit_album(self, band: str, album: str, rate: float, chat_id: int, start: int, end: int) -> None:
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
    async def _resolve_chat(self, chat_id: int):
        """安全包装 get_chat 避免 raw coroutine 泄漏。"""
        return await self.app.get_chat(chat_id)

    # ------------------------------------------------------------------ #
    #  专辑搜索
    # ------------------------------------------------------------------ #
    async def _search_album(self, band: str, album: str) -> bool:
        """
        逐首搜索：对每首歌发起文本搜索，命中后估算区间并打分。
        快路径失败则跳过当前频道，不进行全量扫描兜底。
        拉丁与非拉丁曲目使用统一入口；is_song_match 内部按语种切换策略。
        """
        for chat_id in self.channels:
            print(f"Probing channel {chat_id}...")
            chat_info = await request_api(self._resolve_chat, 1, chat_id)
            if chat_info is None:
                continue
            songs = await self.sql.get_songs_from_IDX(band, album)
            album_len = len(songs)

            # ── 专辑级搜索：搜 "作者 专辑名" 找文字公告，命中后提取全部曲目 ──
            album_range = await request_api(self._search_announcement, 2, chat_id, band, album, album_len)
            if album_range:
                start, end = album_range
                rate, details = await self._validate_hit_rate(band, album, chat_id, start, end)
                if rate >= 0.8:
                    print(f"  [Verify] Album-level hit rate {rate}, details: {details}")
                    await self._submit_album(band, album, rate, chat_id, start, end)
                    return True

             # ── 单曲搜索：取前 3 首最长歌名 ──
            search_songs = sorted(songs, key=len, reverse=True)[:3]

            for song in search_songs:
                id_range = await request_api(self._search_song, 2, band, album, chat_id, song)
                if id_range:
                    chat_id, start, end = id_range
                    rate, details = await self._validate_hit_rate(band, album, chat_id, start, end)
                    if rate >= 0.7:
                        print(f"  [Verify] Hit rate {rate}, details: {details}")
                        await self._submit_album(band, album, rate, chat_id, start, end)
                        return True
        return False

    async def _search_announcement(
        self, chat_id: int, band: str, album: str, album_len: int
    ) -> tuple[int, int] | None:
        """搜作者+专辑名，找到文字公告后提取附近文件区间。"""
        query = f"{band} {album}"
        print(f"  [AlbumSearch] {query}")
        async for msg in self.app.search_messages(chat_id=chat_id, query=query, limit=20):
            if msg.id in cfg.collected_ids:
                continue
            text = (msg.text or msg.caption or "").lower()
            if band.lower() not in text:
                continue
            tracks = await self._find_nearby_files(chat_id, msg.id, band, album, album_len)
            if not tracks:
                continue
            first = min(t.id for t in tracks)
            songs = await self.sql.get_songs_from_IDX(band, album)
            first_song_idx = songs.index(sorted(songs)[0])
            start = first - first_song_idx
            end = start + album_len - 1
            return (start, end)
        return None

    async def _find_nearby_files(
        self, chat_id: int, anchor_id: int, band: str, album: str, album_len: int
    ) -> list[Message]:
        """从 anchor 附近收集匹配本专辑曲目的文件消息。"""
        buffer = album_len * 5
        hits: list[Message] = []
        try:
            async for msg in self.app.get_chat_history(
                chat_id, offset_id=anchor_id + buffer + 1, limit=buffer
            ):
                if msg.id <= anchor_id:
                    break
                f = msg.document or msg.audio
                if not f or not f.file_name or not is_audio_file(f.file_name):
                    continue
                songs = await self.sql.get_songs_from_IDX(band, album)
                for song in songs:
                    if is_song_match(song, f.file_name):
                        hits.append(msg)
                        break
        except Exception:
            pass
        return hits
    
    async def run(self) -> None:
        import time
        overall_start = time.time()

        # ── 重启时：将上一轮未下载完成的 task（status=0）重入队列 ──
        pending = await self.sql.get_pending_tasks()
        if pending:
            print(f"[Resume] Re-queuing {len(pending)} pending task(s) from previous run")
            for t in pending:
                await self.queue.put(t)

        for auther in self.authers:
            albums = await self.sql.get_albums_from_IDX(auther)
            for album in albums:
                album_start = time.time()
                ok = await self._search_album(auther, album)
                elapsed = time.time() - album_start
                if not ok:
                    print(f"[Result] Scanned all channels, album not found: {album} [{elapsed:.1f}s]")
                else:
                    print(f"[Result] Album done: {auther} - {album} [{elapsed:.1f}s]")
        total = time.time() - overall_start
        print(f"[Result] All albums completed in {total:.1f}s")
