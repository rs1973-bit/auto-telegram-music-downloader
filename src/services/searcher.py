import asyncio
from pyrogram import Client, enums
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
            print(f"拉取区间记录时出错: {e}")
        messages.reverse()
        return messages

    async def _scan_channel_audio(self, chat_id: int, limit: int = 3000) -> dict[str, tuple[int, str]]:
        """扫描频道音频/文档消息，返回 {clean_name: (msg_id, file_name)}。"""
        cache: dict[str, tuple[int, str]] = {}
        for mf in (enums.MessagesFilter.AUDIO, enums.MessagesFilter.DOCUMENT):
            async for message in self.app.search_messages(
                chat_id=chat_id, query="", filter=mf, limit=limit
            ):
                file_obj = message.document or message.audio
                if not file_obj or not file_obj.file_name:
                    continue
                fname = file_obj.file_name
                if not any(fname.lower().endswith(ext) for ext in ('.flac', '.dsf', '.dff', '.wav')):
                    continue
                key = clean_name(fname)
                if key not in cache:  # 保留先出现的（更新的）
                    cache[key] = (message.id, fname)
        return cache

    async def search_song_in_TG(self, band: str, album: str, chat_id: int, song: str,
                                 scanned: dict[str, tuple[int, str]] | None = None) -> tuple[int, int, int] | None:
        """在频道中搜索一首歌，匹配到即返回 (chat_id, start, end) 区间。"""
        print(f"  [搜索] 正在搜寻: {song}")
        songs = await self.sql.get_songs_from_IDX(band, album)
        album_len = len(songs)
        original_idx = songs.index(song)

        # ── 快路径：文本搜索 caption ──
        async for message in self.app.search_messages(chat_id=chat_id, query=song, limit=30):
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

        # ── 慢路径：从预扫描缓存中匹配 ──
        if scanned is None:
            return None

        best_id, best_score = 0, 0
        for key, (mid, fname) in scanned.items():
            score = is_song_match(song, fname, return_score=True)
            if score > best_score:
                best_score, best_id = score, mid

        if best_id and best_score >= 70:
            start = best_id - original_idx
            end = best_id + (album_len - original_idx)
            cfg.collected_ids.add(best_id)
            return (chat_id, start, end)
        return None

    async def submit_task(self, band: str, album: str, rate: float, chat_id: int, start: int, end: int) -> None:
        print(f"""  [命中] {band} - {album} | 命中率: {rate:.2f} | ID: {start}-{end} CHAT_ID: {chat_id}""")
        songs = await self.sql.get_songs_from_IDX(band, album)
        pending_tasks = []
        first = start
        for idx, song in enumerate(songs, start=1):
            status = await self.sql.get_status_from_DATA(band, album, song)
            if status == 1:
                print(f"  [跳过] 已下载: {band} - {album} - {song}")
            else:
                task = SongTask(
                    band=band, album=AlbumTask(name=album, band=band),
                    song=song, chat_id=chat_id, msg_id=first, status=0, idx=idx,
                )
                pending_tasks.append(task)
            first += 1
        if not pending_tasks:
            print(f"  [信息] 专辑 {band} - {album} 的所有曲目已下载，跳过任务。")
            return
        await self.sql.insert_for_DATA(pending_tasks)
        for t in pending_tasks:
            await self.queue.put(t)

    # ------------------------------------------------------------------ #
    #  专辑搜索：单阶段逐首匹配
    # ------------------------------------------------------------------ #

    async def search_album_in_TG(self, band: str, album: str) -> bool:
        """
        单阶段搜索：逐首搜索，文本快路径优先。
        若快路径失败则扫描频道一次缓存所有音频文件名，后续曲目复用。

        拉丁与非拉丁曲目使用统一入口；is_song_match 内部按语种切换策略。
        """
        for chat_id in self.channels:
            print(f"频道 {chat_id} 探测中...")
            songs = await self.sql.get_songs_from_IDX(band, album)
            sorted_songs = sorted(songs)
            scanned: dict[str, tuple[int, str]] | None = None
            all_done = True

            for song in sorted_songs:
                if await self.sql.is_song_exists(band, album, song):
                    status = await self.sql.get_status_from_DATA(band, album, song)
                    if status == 1:
                        print(f"  [跳过] {band} - {album} - {song} 已下载")
                        continue
                all_done = False

                id_range = await request_api(self.search_song_in_TG, 4, band, album, chat_id, song, scanned)
                if id_range is None and scanned is None:
                    scanned = await self._scan_channel_audio(chat_id)
                    id_range = await request_api(self.search_song_in_TG, 4, band, album, chat_id, song, scanned)

                if id_range:
                    chat_id, start, end = id_range
                    rate, details = await self.validate_album_status(band, album, chat_id, start, end)
                    if rate >= 0.7:
                        print(f"  [验证] 命中率 {rate}, 逐首详情: {details}")
                        await self.submit_task(band, album, rate, chat_id, start, end)
                        return True

            if all_done:
                print(f"  [信息] 专辑 {band} - {album} 的所有曲目已下载，跳过任务。")
                return True
        return False

    async def GET_HISTORY_AUDIO(self) -> None:
        for auther in self.authers:
            albums = await self.sql.get_albums_from_IDX(auther)
            for album in albums:
                ok = await self.search_album_in_TG(auther, album)
                if not ok:
                    print(f"[结果] 遍历完所有目标频道，未找到专辑: {album}")
            



