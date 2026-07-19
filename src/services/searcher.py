from __future__ import annotations
import asyncio
import random
from pyrogram import Client
from pyrogram.types import Message
import os
from src.utils.config import cfg
from src.utils.manager import Client_Manager
from src.database.sql_repo import SQL_REPO
from src.meta import SongTask, AlbumTask
from src.utils.search import *
from src.utils.request_api import request_api

class Search_in_TG:
    def __init__(self, app:Client, manager:Client_Manager, queue:asyncio.Queue, sql:SQL_REPO):
        self.app = app
        self.manager = manager
        self.sql = sql
        self.authers = cfg.author_list
        self.channels = cfg.targets
        self.queue = queue
    
    async def check(self, task: SongTask) -> bool:
        """校验任务是否已下载。已下载返回 True，否则入队并返回 False。"""
        exists = await self.sql.is_song_exists(task.band, task.album, task.song)
        if exists:
            status = await self.sql.get_status_from_DATA(task.band, task.album, task.song)
            if status == 1:
                return True

        await self.queue.put(task)
        return False

    async def validate_album_status(self, band:str, album:str, chat_id:int, start:int, end:int) -> tuple[float, list[dict]]:
        """拉取区间消息并校验专辑中每首歌的命中情况与数据库状态。
        返回 (命中率, [{"song":str, "hit":bool, "db_status":int|None}...])
        """
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
    
    async def get_id_range(self, chat_id:int, start_id:int, end_id:int) -> list[Message]:
        """拉取由命中歌曲推断的区间中的所有msg"""
        limit = (end_id - start_id) + 1
        messages = []
        try:
            async for msg in self.app.get_chat_history(chat_id, offset_id=end_id + 1, limit=limit):
                await self.manager.can_runs.wait()
                if msg.id < start_id: break
                messages.append(msg)
        except Exception as e:
            print(f"拉取区间记录时出错: {e}")
        messages.reverse()
        return messages
    
    async def search_song_in_TG(self, band:str, album:str, chat_id:int, song:str) -> tuple[int, int, int] | None:
        """获取一张专辑可能的id区间"""
        print(f"  [搜索] 正在搜寻关键词: {song}")
        await asyncio.sleep(0.5)  # 单曲搜索间隔
        songs = await self.sql.get_songs_from_IDX(band, album)
        album_len = len(songs)
        original_idx = songs.index(song)
        async for message in self.app.search_messages(chat_id=chat_id, query=song, limit=20):
            if message.id in cfg.collected_ids: continue
            file_obj = message.document or message.audio
            if not file_obj: continue

            if is_song_match(song, file_obj.file_name):
                start = message.id - original_idx
                end = message.id + (album_len - original_idx)
                cfg.collected_ids.add(message.id)
                return (chat_id, start, end)
        return None
    
    async def submit_task(self, band: str, album: str, rate: float, chat_id: int, start: int, end: int) -> None:
            print(f"""  [命中] {band} - {album} | 命中率: {rate:.2f} | 
                    ID: {start}-{end} CHAT_ID: {chat_id}""")
            songs = await self.sql.get_songs_from_IDX(band, album)
            pending_tasks = []
            first = start
            for idx, song in enumerate(songs, start=1):
                status = await self.sql.get_status_from_DATA(band, album, song)
                if status == 1:
                    print(f"  [跳过] 已下载: {band} - {album} - {song}")
                else:
                    task = SongTask(band=band, album=AlbumTask(name=album, band=band), song=song, chat_id=chat_id, msg_id=first, status=0, idx=idx)
                    pending_tasks.append(task)
                first += 1

            if not pending_tasks:
                print(f"  [信息] 专辑 {band} - {album} 的所有曲目已下载，跳过任务。")
                return

            # 插入未存在的记录并将未下载的任务加入队列
            await self.sql.insert_for_DATA(pending_tasks)
            for t in pending_tasks:
                await self.queue.put(t)
    
    # ------------------------------------------------------------------ #
    #  专辑搜索：快速匹配 → 回落整段 → 精确单曲
    # ------------------------------------------------------------------ #

    async def search_album_in_TG(self, band: str, album: str) -> bool:
        """
        在目标频道中查找专辑，三阶段递进：

        Phase 1 — 快速匹配：搜索 "歌手 + 专辑" 字符串，以一条消息为锚点估算区间。
        Phase 2 — 回落整段：逐首搜索，找到锚点后校验连续区间。
        Phase 3 — 精确单曲：逐首独立匹配，不依赖区间连续性（适用于完全无序的频道）。
        """
        await asyncio.sleep(0.5)  # 专辑级间隔，避免搜索过快触发风控
        if await self._quick_search_album(band, album):
            return True
        if await self._fallback_search_album(band, album):
            return True
        return await self._precision_search_album(band, album)

    async def _quick_search_album(self, band: str, album: str) -> bool:
        """
        快速匹配：用 "band + album" 作为查询词搜索消息，
        以匹配消息为锚点估算专辑区间并校验。
        """
        query = f"{band} {album}"
        qlow = query.lower()

        for chat_id in self.channels:
            print(f"  [快速] 频道 {chat_id} 搜索: {query}")
            await asyncio.sleep(0.3)  # 频道间间隔
            songs = await self.sql.get_songs_from_IDX(band, album)
            if not songs:
                continue
            album_len = len(songs)

            async for msg in self.app.search_messages(chat_id, query=query, limit=20):
                if msg.id in cfg.collected_ids:
                    continue

                file_obj = msg.document or msg.audio
                if not file_obj:
                    continue

                fname = (file_obj.file_name or "").lower()
                if not any(w in fname for w in qlow.split()):
                    continue

                for ratio in (0, 0.25, 0.5, 0.75):
                    start = msg.id - int(album_len * ratio)
                    end = start + album_len - 1
                    if start < 1:
                        continue

                    rate, details = await self.validate_album_status(
                        band, album, chat_id, start, end
                    )
                    if rate >= 0.7:
                        cfg.collected_ids.add(msg.id)
                        print(f"  [快速✅] 命中率 {rate:.0%}, 区间 {start}–{end}")
                        await self.submit_task(band, album, rate, chat_id, start, end)
                        return True
        return False

    async def _fallback_search_album(self, band: str, album: str) -> bool:
        """
        回落算法：遍历专辑每首歌，逐首在频道中搜索文件名，
        找到一张专辑的锚点后按连续区间校验。
        """
        for chat_id in self.channels:
            print(f"  [回落] 频道 {chat_id} 逐首探测中...")
            await asyncio.sleep(0.3)  # 频道间间隔
            songs = await self.sql.get_songs_from_IDX(band, album)
            sorted_songs = sorted(songs)
            for song in sorted_songs:
                if song.count(" ") < 1 or len(song) < 5:
                    continue

                status = await self.sql.get_status_from_DATA(band, album, song)
                if status == 1:
                    print(f'  [回落] {band}-{album}-{song} 已下载跳过')
                    return True

                id_range = await request_api(
                    self.search_song_in_TG, 4, band, album, chat_id, song
                )
                if id_range:
                    chat_id, start, end = id_range
                    rate, details = await self.validate_album_status(
                        band, album, chat_id, start, end
                    )
                    if rate >= 0.7:
                        print(f"  [回落✅] 命中率 {rate:.0%}, 逐首详情: {details}")
                        await self.submit_task(band, album, rate, chat_id, start, end)
                        return True
        return False

    async def _precision_search_album(self, band: str, album: str) -> bool:
        """
        精确单曲匹配：对每首歌单独搜索并独立匹配文件名，
        不依赖区间连续性，适用于歌曲散落在频道各处的场景。

        对每首歌最多搜索 10 条结果，用 is_song_match 验证。
        收集到足够的单曲后直接提交独立任务。
        """
        for chat_id in self.channels:
            print(f"  [精确] 频道 {chat_id} 逐首独立匹配中...")
            await asyncio.sleep(0.3)  # 频道间间隔
            songs = await self.sql.get_songs_from_IDX(band, album)
            if not songs:
                continue
            total = len(songs)

            pending_tasks: list[SongTask] = []
            skip_count = 0

            for idx, song in enumerate(songs, start=1):
                # 跳过已下载的
                if await self.sql.is_song_exists(band, album, song):
                    status = await self.sql.get_status_from_DATA(band, album, song)
                    if status == 1:
                        skip_count += 1
                        continue

                await asyncio.sleep(0.5)  # 精确搜索单曲间隔
                # 搜索当前歌曲 — 取所有结果中匹配分最高的（避免最新优先导致的误匹配）
                best_msg = None
                best_score = 0
                async for msg in self.app.search_messages(chat_id, query=song, limit=20):
                    file_obj = msg.document or msg.audio
                    if not file_obj:
                        continue
                    score = is_song_match(song, file_obj.file_name, return_score=True)
                    if score and score > best_score:
                        best_score = score
                        best_msg = msg
                if best_msg is not None:
                    task = SongTask(
                        band=band, album=AlbumTask(name=album, band=band), song=song,
                        chat_id=chat_id, msg_id=best_msg.id, status=0, idx=idx,
                    )
                    pending_tasks.append(task)

            hit_rate = (skip_count + len(pending_tasks)) / total
            print(f"  [精确] 已下载跳过: {skip_count}, 新匹配: {len(pending_tasks)}/{total}, 命中率: {hit_rate:.0%}")

            if pending_tasks:
                await self.sql.insert_for_DATA(pending_tasks)
                for t in pending_tasks:
                    await self.queue.put(t)
                print(f"  [精确✅] 提交 {len(pending_tasks)} 首独立任务")
                return True
        return False
    
    async def GET_HISTORY_AUDIO(self) -> None:
        for auther in self.authers:
            albums = await self.sql.get_albums_from_IDX(auther)
            for album in albums:
                ok = await self.search_album_in_TG(auther, album)
                if not ok:
                    print(f"[结果] 遍历完所有目标频道，未找到专辑: {album}")
            



