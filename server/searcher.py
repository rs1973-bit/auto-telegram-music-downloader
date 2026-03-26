import asyncio
import random
import re
from pyrogram import Client
from pyrogram.types import Message
import os
from utils.manage.setup import cfg
from utils.manage.manager import Client_Manager
from utils.sql.sql_repo import SQL_REPO
from utils.sql.meta import SongTask
from utils.search.clean import *
from utils.request_api import request_api

class Search_in_TG:
    def __init__(self, app:Client, manager:Client_Manager, queue:asyncio.Queue, sql:SQL_REPO):
        self.app = app
        self.manager = manager
        self.sql = sql
        self.authers = cfg.author_list
        self.channels = cfg.targets
        self.queue = queue
    
    async def check(self, task: SongTask) -> bool:
        """校验任务是否已下载（status == 1）。
        如果未下载则将 `SongTask` 放入任务队列并返回 False；已下载则返回 True。
        """
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
    
    async def search_song_in_TG(self, band:str, chat_id:int, song:str) -> tuple[int, int, int]:
        """获取一张专辑可能的id区间"""
        print(f"  [搜索] 正在搜寻关键词: {song}")
        songs = await self.sql.get_songs_from_IDX(band, chat_id)
        album_len = len(songs)
        original_idx = songs.index(song)
        async for message in self.app.search_messages(chat_id=chat_id, query=song, limit=20):
                if message.id in cfg.collected_ids: continue
                file_obj = message.document or message.audio
                if not file_obj: continue

                if is_song_match(song, file_obj.file_name):
                    start = message.id - original_idx
                    end = message.id + (album_len - original_idx)
                    return (chat_id, start, end)
                return None
    
    async def submit_task(self, band:str, album:str, rate:float, 
                                 chat_id:int, start:int, end:int) -> None:
            print(f"""  [命中] {band} - {album} | 命中率: {rate:.2f} | 
                    ID: {start}-{end} CHAT_ID: {chat_id}""")
            songs = await self.sql.get_songs_from_IDX(band, album)
            pending_tasks = []
            first = start
            for song in songs:
                status = await self.sql.get_status_from_DATA(band, album, song)
                if status == 1:
                    print(f"  [跳过] 已下载: {band} - {album} - {song}")
                else:
                    task = SongTask(band=band, album=album, song=song, chat_id=chat_id, msg_id=first, status=0)
                    pending_tasks.append(task)
                first += 1

            if not pending_tasks:
                print(f"  [信息] 专辑 {band} - {album} 的所有曲目已下载，跳过任务。")
                return

            # 插入未存在的记录并将未下载的任务加入队列
            await self.sql.insert_for_DATA(pending_tasks)
            for t in pending_tasks:
                await self.queue.put(t)
    
    async def search_album_in_TG(self, band:str, album:str)->bool:
        for chat_id in self.channels:
            print(f"频道 {chat_id} 探测中...")
            songs = await self.sql.get_songs_from_IDX(band, album)
            sorted_songs = sorted(songs)
            for song in sorted_songs:
                if song.count(" ") < 1 or len(song) < 5: continue

                if await self.sql.is_song_exists(band, album, song):
                    print(f'{band}-{album}-{song}在数据库中存在...')
                    return True
                
                id_range = await request_api(self.search_song_in_TG, 4, band, chat_id, song)
                if id_range:
                    chat_id, start, end = id_range
                    rate, details = await self.validate_album_status(band, album, chat_id, start, end)
                    if rate >= 0.7:
                        print(f"  [验证] 命中率 {rate}, 逐首详情: {details}")
                        await self.submit_task(band, album, rate, chat_id, start, end)
                        return True
        return False
    
    async def GET_HISTORY_AUDIO(self):
        for auther in self.authers:
            albums = await self.sql.get_albums_from_IDX(auther)
            for album in albums:
                ok = self.search_album_in_TG(auther, album)
                if not ok:
                    print(f"[结果] 遍历完所有目标频道，未找到专辑: {album}")
            



