import os
import json
import asyncio
import random
from concurrent.futures import ThreadPoolExecutor
from pyrogram.errors import RPCError, FileReferenceExpired, FloodWait
from pyrogram import client
from pyrogram.types import Message
from utils.manage.log import logger
from utils.manage.setup import cfg
from utils.sql.sql_repo import SQL_REPO
from utils.sql.meta import SongTask
from core.converter import converter
from utils.manage.manager import Client_Manager


dc_auth_lock = asyncio.Lock()


class Downloader:
    """下载消费者，按单曲 `SongTask` 为单位工作。"""

    def __init__(self, client:client, manager: Client_Manager, queue: asyncio.Queue, sql:SQL_REPO):
        self.client = client
        self.manager = manager
        self.queue = queue
        self.sql = sql
        self.song_semaphore = asyncio.Semaphore(cfg.workers)
        self.executor = ThreadPoolExecutor(max_workers=1)

    # ------------------------------------------------------------------ #
    #  robust_download — 私有辅助方法
    # ------------------------------------------------------------------ #

    def _calc_resume_offset(self, target_path: str, expected_size: int):
        """计算断点续传的对齐偏移量（MB 对齐），返回 (offset_index, aligned_size)。"""
        current_size = os.path.getsize(target_path) if os.path.exists(target_path) else 0
        offset_index = current_size // (1024 * 1024)
        aligned_size = offset_index * 1024 * 1024
        return current_size, offset_index, aligned_size

    def _truncate_to_aligned(self, target_path: str, file_name: str, offset_index: int, aligned_size: int):
        """将残缺文件截断到 MB 对齐边界，避免脏数据。"""
        if offset_index != 0:
            logger.warning(f'已对齐残片: {file_name} -> {offset_index}MB')
            with open(target_path, "r+b") as f:
                f.truncate(aligned_size)

    async def _stream_to_file(self, msg, target_path: str, offset_index: int):
        """从 Telegram 流式写入文件，支持全局暂停检测。"""
        downloaded_bytes = offset_index * 1024 * 1024
        with open(target_path, "ab") as f:
            async for chunk in self.client.stream_media(msg, offset=offset_index):
                if not self.manager.can_runs.is_set():
                    raise InterruptedError("Global pause triggered")
                f.write(chunk)
                downloaded_bytes += len(chunk)

    def _verify_and_report(self, target_path: str, file_name: str, expected_size: int, pre_download_size: int):
        """校验下载完整性，更新 manager 统计，失败则抛出异常。"""
        final_size = os.path.getsize(target_path)
        if final_size < expected_size:
            raise RuntimeError(f"流异常中断: 进度 {final_size}/{expected_size}")
        logger.info(f"文件 {file_name} 下载成功")
        self.manager.files += 1
        self.manager.report_size += final_size - pre_download_size

    async def _handle_flood_or_interrupt(self, e: Exception):
        """处理 FloodWait / InterruptedError：加锁后统一触发全局暂停再恢复。"""
        if self.manager.can_runs.is_set():
            async with dc_auth_lock:
                if self.manager.can_runs.is_set():
                    wait_time = e.value if hasattr(e, 'value') else 60
                    logger.error(f"触发风控/暂停，休眠 {wait_time}s")
                    self.manager.can_runs.clear()
                    self.manager.error_count += 1
                    await asyncio.sleep(wait_time)
                    self.manager.can_runs.set()

    async def _download_attempt(self, msg:Message, target_path: str, expected_size: int, file_name: str):
        """单次下载尝试：刷新链接 → 断点续传 → 流式写入 → 完整性校验。返回刷新后的 msg。"""
        logger.info('尝试获取最新链接')
        msg = await self.client.get_messages(msg.chat.id, msg.id)

        current_size, offset_index, aligned_size = self._calc_resume_offset(target_path, expected_size)
        if current_size == expected_size:
            return msg, True  # 文件已完整，提前退出

        self._truncate_to_aligned(target_path, file_name, offset_index, aligned_size)
        await self._stream_to_file(msg, target_path, offset_index)
        self._verify_and_report(target_path, file_name, expected_size, current_size)
        return msg, True

    async def robust_download(self, msg, target_path, expected_size):
        file_obj = msg.document or msg.audio
        for attempt in range(20):
            await self.manager.can_runs.wait()
            try:
                msg, done = await self._download_attempt(msg, target_path, expected_size, file_obj.file_name)
                if done:
                    return True

            except (FloodWait, InterruptedError) as e:
                await self._handle_flood_or_interrupt(e)
                continue

            except RPCError as e:
                self.manager.error_count += 1
                logger.error(f"Telegram RPC错误 [尝试 {attempt}]: {getattr(e, 'NAME', '')} - {getattr(e, 'MESSAGE', e)}")
                await asyncio.sleep(random.uniform(5, 10))

            except Exception as e:
                self.manager.error_count += 1
                logger.error(f"未预期下载异常 [尝试 {attempt}]: {type(e).__name__} - {e}")
                await asyncio.sleep(random.uniform(5, 15))
        return False

    # ------------------------------------------------------------------ #
    #  handle_song_task — 私有辅助方法
    # ------------------------------------------------------------------ #

    async def _fetch_msg_and_file(self, task: SongTask):
        """从 Telegram 拉取消息并校验附件存在，返回 (msg, file_obj)。"""
        msg = await self.client.get_messages(task.chat_id, task.msg_id)
        if not msg:
            raise RuntimeError("无法获取消息")
        file_obj = msg.document or msg.audio
        if not file_obj:
            raise RuntimeError("消息不包含可下载文件")
        return msg, file_obj

    def _resolve_save_paths(self, task: SongTask, file_obj):
        """根据是否需要转码决定保存目录，返回 (final_album_dir, target_path, need_conv)。"""
        final_album_dir = os.path.join(cfg.save_root, task.band, task.album)
        os.makedirs(final_album_dir, exist_ok=True)

        ext = os.path.splitext(file_obj.file_name)[1]
        need_conv = ext.lower()[1:] != cfg.tar_ext
        current_save_dir = cfg.temp_path if need_conv else final_album_dir
        os.makedirs(current_save_dir, exist_ok=True)

        target_path = os.path.join(current_save_dir, file_obj.file_name)
        return final_album_dir, target_path, need_conv

    async def _ensure_downloaded(self, msg, file_obj, target_path: str) -> bool:
        """若文件已完整则跳过，否则调用 robust_download，返回是否成功。"""
        if os.path.exists(target_path) and os.path.getsize(target_path) == file_obj.file_size:
            logger.info(f"跳过已存在文件: {file_obj.file_name}")
            return True
        return await self.robust_download(msg, target_path, file_obj.file_size)

    def _trigger_conversion(self, target_path: str, final_album_dir: str):
        """写入转码信号文件并在线程池中启动转换器。"""
        signal_path = f"{target_path}.txt"
        with open(signal_path, "w", encoding='utf-8') as t:
            t.write(final_album_dir)
        asyncio.get_event_loop().run_in_executor(self.executor, converter)

    async def _mark_success(self, task: SongTask, target_path: str, final_album_dir: str, need_conv: bool):
        """下载成功后：按需触发转码，并将数据库状态标记为已下载。"""
        if need_conv:
            self._trigger_conversion(target_path, final_album_dir)
        await self.sql.update_DATA_status([(task.band, task.album, task.song)], 1)
        logger.info(f"已标记为已下载: {task.band} - {task.album} - {task.song}")

    async def _mark_failure(self, task: SongTask, file_obj):
        """下载失败后：标记数据库状态并追加写入失败日志。"""
        await self.sql.update_DATA_status([(task.band, task.album, task.song)], -1)
        logger.error(f"下载失败并已标记: {task.band} - {task.album} - {task.song}")
        error_data = {
            "band": task.band,
            "album": task.album,
            "file_name": file_obj.file_name,
            "msg_id": task.msg_id,
            "chat_id": task.chat_id,
        }
        with open('failed_documents.jsonl', 'a', encoding='utf-8') as f:
            f.write(json.dumps(error_data, ensure_ascii=False) + "\n")

    async def handle_song_task(self, task: SongTask):
        """处理单首歌曲任务：下载、转换（如需）、并更新数据库状态。"""
        async with self.song_semaphore:
            status = await self.sql.get_status_from_DATA(task.band, task.album, task.song)
            if status == 1:
                logger.info(f"跳过已下载: {task.band} - {task.album} - {task.song}")
                return True

            try:
                msg, file_obj = await self._fetch_msg_and_file(task)
                final_album_dir, target_path, need_conv = self._resolve_save_paths(task, file_obj)
                success = await self._ensure_downloaded(msg, file_obj, target_path)

                if success:
                    await self._mark_success(task, target_path, final_album_dir, need_conv)
                    return True
                else:
                    await self._mark_failure(task, file_obj)
                    return False

            except Exception as e:
                logger.error(f"处理任务异常: {task} -> {e}")
                await self.sql.update_DATA_status([(task.band, task.album, task.song)], -1)
                return False

    # ------------------------------------------------------------------ #
    #  run — 私有辅助方法
    # ------------------------------------------------------------------ #

    async def _dispatch_song_task(self, task: SongTask):
        """直接派发 SongTask。"""
        await self.handle_song_task(task)

    async def _expand_legacy_album_task(self, task: dict):
        """将旧版 album dict 拆解为若干 SongTask 并重新入队。"""
        start = task['msg_ids'][0]
        songs = await self.sql.get_songs_from_IDX(task['band'], task['album'])
        for idx, song in enumerate(songs):
            st = SongTask(
                band=task['band'],
                album=task['album'],
                song=song,
                chat_id=task['chat_id'],
                msg_id=start + idx,
                status=0,
            )
            await self.queue.put(st)

    async def _dispatch_task(self, task):
        """根据任务类型路由到对应处理逻辑。"""
        if isinstance(task, SongTask):
            await self._dispatch_song_task(task)
        elif isinstance(task, dict):
            if 'band' in task and 'album' in task and 'msg_ids' in task and 'chat_id' in task:
                await self._expand_legacy_album_task(task)
            else:
                logger.warning(f"收到未知任务类型，跳过: {task}")
        else:
            logger.warning(f"收到不可识别任务，跳过: {task}")

    async def run(self):
        """持续从队列消费 `SongTask` 并处理。"""
        while True:
            task = await self.queue.get()
            try:
                if task is None:
                    return
                await self._dispatch_task(task)
            finally:
                try:
                    self.queue.task_done()
                except Exception:
                    pass

    # ------------------------------------------------------------------ #
    #  process_failed — 私有辅助方法
    # ------------------------------------------------------------------ #

    def _row_to_song_task(self, row) -> SongTask:
        """将数据库行（idx, band, album, song, chat_id, msg_id, status）转为 SongTask。"""
        _, band, album, song, chat_id, msg_id, *_ = row
        return SongTask(band=band, album=album, song=song, chat_id=chat_id, msg_id=msg_id, status=0)

    async def process_failed(self):
        await self.manager.can_runs.wait()
        failed_rows = await self.sql.get_failed_songs_in_DATA()
        if not failed_rows:
            return

        for row in failed_rows:
            st = self._row_to_song_task(row)
            await self.queue.put(st)