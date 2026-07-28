from __future__ import annotations
import os
import json
import asyncio
import random
from typing import Any
from pyrogram.errors import RPCError, FileReferenceExpired, FloodWait
from pyrogram import client
from pyrogram.types import Message
from src.utils.logger import logger
from src.utils.config import cfg
from src.database.sql_repo import SQL_REPO
from src.meta import SongTask, AlbumTask
from src.services.converter import ConvTask
from src.utils.manager import Client_Manager


dc_auth_lock = asyncio.Lock()


class Downloader:
    """下载消费者，按单曲 `SongTask` 为单位工作。"""

    def __init__(self, client: client, manager: Client_Manager, queue: asyncio.Queue, sql: SQL_REPO, conv_queue: asyncio.Queue | None = None):
        self.client = client
        self.manager = manager
        self.queue = queue
        self.sql = sql
        self.conv_queue = conv_queue
        self.song_semaphore = asyncio.Semaphore(cfg.workers)
        self._consecutive_floods = 0
        self._last_flood_time = 0.0

    # ------------------------------------------------------------------ #
    #  robust_download — 私有辅助方法
    # ------------------------------------------------------------------ #

    @staticmethod
    def _calc_chunk_state(target_path: str) -> tuple[int, int]:
        """
        计算文件已有大小和 1MiB 对齐偏移。

        Returns:
            (existing_bytes, aligned_bytes):
              existing_bytes — 文件当前字节数（0 表示不存在）
              aligned_bytes  — 对齐到 1MiB 边界的字节数，用作 stream_media offset
        """
        size = os.path.getsize(target_path) if os.path.exists(target_path) else 0
        aligned = (size // (1024 * 1024)) * (1024 * 1024)
        return size, aligned

    @staticmethod
    def _truncate(path: str, to_bytes: int):
        """
        将文件截断到 to_bytes（保留完整 1MiB 块，丢弃尾部脏数据）。

        安全处理文件不存在、空文件、及不足 1MiB 的碎片等情况。
        """
        if not os.path.exists(path):
            return
        if to_bytes == 0:
            # 不足 1 个完整块 → 清空重来
            with open(path, "w+b") as f:
                f.truncate(0)
        elif os.path.getsize(path) > to_bytes:
            with open(path, "r+b") as f:
                f.truncate(to_bytes)

    async def _stream(self, msg: Message, target_path: str, offset_bytes: int) -> None:
        """从 offset_bytes 处流式下载，写入文件尾部，响应全局暂停。"""
        chunk_offset = offset_bytes // (1024 * 1024)
        with open(target_path, "ab") as f:
            async for chunk in self.client.stream_media(msg, offset=chunk_offset):
                if not self.manager.can_runs.is_set():
                    raise InterruptedError("Global pause triggered")
                f.write(chunk)

    def _verify(self, target_path: str, expected_size: int, pre_size: int, file_name: str) -> None:
        """校验完整性并更新统计。"""
        final_size = os.path.getsize(target_path)
        if final_size < expected_size:
            raise RuntimeError(f"不完整: {final_size}/{expected_size}")
        logger.info(f"File {file_name} downloaded successfully")
        self.manager.files += 1
        self.manager.report_size += final_size - pre_size

    async def _handle_flood_or_interrupt(self, e: Exception) -> None:
        """处理 FloodWait：全局冻结所有 worker，指数退避。

        第一个遇到 FloodWait 的 worker 会：
          1. 清除 can_runs → 所有 worker 阻塞在 can_runs.wait()
          2. 指数退避休眠（按 Telegram 返回的 wait_time * 退避系数）
          3. 恢复 can_runs → 所有 worker 继续

        后续同时撞上 FloodWait 的 worker 不做重复处理，
        直接 fallthrough 到 can_runs.wait() 统一等待。
        """
        import time
        if not self.manager.can_runs.is_set():
            # 已经有 worker 在处理 FloodWait，直接等全局恢复
            return

        async with dc_auth_lock:
            if not self.manager.can_runs.is_set():
                return

            raw_wait = e.value if hasattr(e, 'value') else 30
            self._consecutive_floods += 1

            # 指数退避: Telegram 返回的等待时间 × 1.5^(连续次数-1), 最少 30s, 最多 300s
            factor = 1.5 ** (self._consecutive_floods - 1)
            wait_time = max(30, min(300, raw_wait * factor))

            self.manager.error_count += 1
            self.manager.can_runs.clear()
            self._last_flood_time = time.time()

            logger.error(
                f"FloodWait #{self._consecutive_floods}: "
                f"raw={raw_wait}s → backoff={wait_time:.0f}s, "
                f"freezing all workers"
            )
            await asyncio.sleep(wait_time)

            # 退避时间够长 → 重置连续计数
            if wait_time >= 120:
                self._consecutive_floods = 0

            self.manager.can_runs.set()

    async def _download_attempt(self, msg: Message, target_path: str, expected_size: int, file_name: str) -> tuple[Message, bool]:
        """单次下载尝试。返回 (msg, success)。"""
        # 先检查文件是否已完整，避免无谓的刷新请求
        pre_size, aligned = self._calc_chunk_state(target_path)
        if pre_size >= expected_size:
            self._verify(target_path, expected_size, pre_size, file_name)
            return msg, True

        # 清理脏尾部 → 刷新链接 → 续传
        self._truncate(target_path, aligned)
        logger.info('Refreshing message links...')
        msg = await self.client.get_messages(msg.chat.id, msg.id)
        await self._stream(msg, target_path, aligned)
        self._verify(target_path, expected_size, pre_size, file_name)
        return msg, True

    async def robust_download(self, msg: Message, target_path: str, expected_size: int) -> bool:
        """鲁棒下载，带 FloodWait 感知。

        FloodWait / InterruptedError 不计入重试次数（无限重试直到成功）；
        其他错误最多重试 20 次。
        """
        file_obj = msg.document or msg.audio
        retries = 0
        max_retries = 20
        while retries < max_retries:
            await self.manager.can_runs.wait()
            try:
                _, ok = await self._download_attempt(msg, target_path, expected_size, file_obj.file_name)
                if ok:
                    return True

            except (FloodWait, InterruptedError) as e:
                await self._handle_flood_or_interrupt(e)
                # FloodWait 不消耗重试次数，继续重试
                continue

            except RPCError as e:
                retries += 1
                self.manager.error_count += 1
                logger.error(f"RPC error [retry {retries}/{max_retries}]: {getattr(e, 'NAME', '')} - {getattr(e, 'MESSAGE', e)}")
                await asyncio.sleep(random.uniform(5, 10))

            except Exception as e:
                retries += 1
                self.manager.error_count += 1
                logger.error(f"Unexpected error [retry {retries}/{max_retries}]: {type(e).__name__} - {e}")
                await asyncio.sleep(random.uniform(5, 15))
        return False

    # ------------------------------------------------------------------ #
    #  handle_song_task — 私有辅助方法
    # ------------------------------------------------------------------ #

    async def _fetch_msg_and_file(self, task: SongTask) -> tuple[Message, Any]:
        """从 Telegram 拉取消息并校验附件存在，返回 (msg, file_obj)。"""
        msg = await self.client.get_messages(task.chat_id, task.msg_id)
        if not msg:
            raise RuntimeError("无法获取消息")
        file_obj = msg.document or msg.audio
        if not file_obj:
            raise RuntimeError("消息不包含可下载文件")
        return msg, file_obj

    def _resolve_save_paths(self, task: SongTask, file_obj: Any) -> tuple[str, str, bool]:
        """根据配置决定是否转码，返回 (final_album_dir, target_path, need_conv)。"""
        # file_obj: Document | Audio (duck-typing: .file_name, .file_size)
        final_album_dir = os.path.join(cfg.save_root, task.band, task.album.name)
        os.makedirs(final_album_dir, exist_ok=True)

        # 配置强制转码：只要 cfg.conv 有值就转
        need_conv = bool(cfg.conv)
        current_save_dir = cfg.temp_path if need_conv else final_album_dir
        os.makedirs(current_save_dir, exist_ok=True)

        # 用 SongTask 数据重命名：音轨号 + 标题
        orig_ext = os.path.splitext(file_obj.file_name)[1] or ".flac"
        safe_title = task.song.replace("/", "／").replace("\\", "∕")
        new_name = f"{task.idx:02d} {safe_title}{orig_ext}"
        target_path = os.path.join(current_save_dir, new_name)
        return final_album_dir, target_path, need_conv

    async def _ensure_downloaded(self, msg: Message, file_obj: Any, target_path: str, final_album_dir: str, need_conv: bool) -> bool:
        """若文件已完整则跳过，否则调用 robust_download，返回是否成功。

        转码模式下：
          - 优先检查最终文件（temp 在转码后会被删除）
          - 无最终文件时回退到检查 temp
        """
        if need_conv:
            name = os.path.splitext(os.path.basename(target_path))[0]
            final_path = os.path.join(final_album_dir, f"{name}.{cfg.target_ext}")
            if os.path.exists(final_path):
                logger.info(f"Skipping existing file: {os.path.basename(target_path)}")
                return True

        if os.path.exists(target_path) and os.path.getsize(target_path) == file_obj.file_size:
            logger.info(f"Skipping existing file: {os.path.basename(target_path)}")
            return True
        return await self.robust_download(msg, target_path, file_obj.file_size)

    async def _mark_success(self, task: SongTask, target_path: str, final_album_dir: str, need_conv: bool) -> None:
        """
        下载完成后：
          - 需转码 → status=2（待转码），入队 conv_queue，不阻塞
          - 不转码 → status=1（完成）
        """
        if need_conv and self.conv_queue is not None:
            await self.sql.update_DATA_status([(task.band, task.album.name, task.song)], 2)
            ct = ConvTask(
                source_path=target_path,
                final_dir=final_album_dir,
                band=task.band,
                album=task.album.name,
                song=task.song,
                song_task=task,
                album_task=task.album,
            )
            await self.conv_queue.put(ct)
            logger.info(f"Downloaded, pending conversion: {task.band} - {task.album.name} - {task.song}")
        else:
            await self.sql.update_DATA_status([(task.band, task.album.name, task.song)], 1)
            logger.info(f"Download complete: {task.band} - {task.album.name} - {task.song}")

    async def _mark_failure(self, task: SongTask, file_obj: Any) -> None:
        """下载失败后：标记数据库状态并追加写入失败日志。"""
        await self.sql.update_DATA_status([(task.band, task.album.name, task.song)], -1)
        logger.error(f"Download failed (marked): {task.band} - {task.album.name} - {task.song}")
        error_data = {
            "band": task.band,
            "album": task.album.name,
            "file_name": file_obj.file_name,
            "msg_id": task.msg_id,
            "chat_id": task.chat_id,
        }

    async def handle_song_task(self, task: SongTask) -> bool:
        """处理单首歌曲任务：下载、转换（如需）、并更新数据库状态。"""
        async with self.song_semaphore:
            status = await self.sql.get_status_from_DATA(task.band, task.album.name, task.song)
            if status == 1:
                logger.info(f"Skipping already downloaded: {task.band} - {task.album.name} - {task.song}")
                return True

            try:
                msg, file_obj = await self._fetch_msg_and_file(task)
                final_album_dir, target_path, need_conv = self._resolve_save_paths(task, file_obj)
                success = await self._ensure_downloaded(msg, file_obj, target_path, final_album_dir, need_conv)

                if success:
                    await self._mark_success(task, target_path, final_album_dir, need_conv)
                    return True
                else:
                    await self._mark_failure(task, file_obj)
                    return False

            except Exception as e:
                logger.error(f"Task error: {task} -> {e}")
                await self.sql.update_DATA_status([(task.band, task.album.name, task.song)], -1)
                return False

    # ------------------------------------------------------------------ #
    #  run — 私有辅助方法
    # ------------------------------------------------------------------ #

    async def _dispatch_song_task(self, task: SongTask) -> None:
        """直接派发 SongTask。"""
        await self.handle_song_task(task)

    async def _expand_legacy_album_task(self, task: dict) -> None:
        """将旧版 album dict 拆解为若干 SongTask 并重新入队。"""
        start = task['msg_ids'][0]
        songs = await self.sql.get_songs_from_IDX(task['band'], task['album'])
        for idx, song in enumerate(songs):
            st = SongTask(
                band=task['band'],
                album=AlbumTask(name=task['album'], band=task['band']),
                song=song,
                chat_id=task['chat_id'],
                msg_id=start + idx,
                status=0,
                idx=idx + 1,
            )
            await self.queue.put(st)

    async def _dispatch_task(self, task: SongTask | dict | None) -> None:
        """根据任务类型路由到对应处理逻辑。"""
        if isinstance(task, SongTask):
            await self._dispatch_song_task(task)
        elif isinstance(task, dict):
            if 'band' in task and 'album' in task and 'msg_ids' in task and 'chat_id' in task:
                await self._expand_legacy_album_task(task)
            else:
                logger.warning(f"Unknown task type, skipping: {task}")
        else:
            logger.warning(f"Unrecognized task, skipping: {task}")

    async def run(self) -> None:
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

    async def _row_to_song_task(self, row: tuple) -> SongTask:
        """将数据库行（idx, band, album, song, chat_id, msg_id, status）转为 SongTask。"""
        _, band, album_name, song, chat_id, msg_id, *_ = row
        idx = await self.sql.get_track_num(band, album_name, song)
        return SongTask(band=band, album=AlbumTask(name=album_name, band=band), song=song, chat_id=chat_id, msg_id=msg_id, status=0, idx=idx)

    async def process_failed(self) -> int:
        """
        将所有 status = -1 的任务重新入队。
        返回入队数量，供调用方判断是否需要启动重试 workers。
        """
        await self.manager.can_runs.wait()
        failed_rows = await self.sql.get_failed_songs_in_DATA()
        if not failed_rows:
            return 0

        for row in failed_rows:
            st = await self._row_to_song_task(row)
            await self.queue.put(st)

        logger.info(f"Re-queued {len(failed_rows)} failed tasks")
        return len(failed_rows)