from __future__ import annotations
import os
import asyncio
from concurrent.futures import ThreadPoolExecutor
import ffmpeg
from src.utils.config import cfg
from src.utils.logger import logger
from src.database.sql_repo import SQL_REPO
from src.meta import ConvTask


def convert_file(source_path: str, tar_path: str) -> bool:
    """转码单个文件，返回是否成功。"""
    print(f">>> 正在转码: {os.path.basename(source_path)}")
    try:
        (ffmpeg
         .input(source_path)
         .output(
             tar_path,
             acodec=cfg.conv,
             ar=cfg.sample_rate,
             sample_fmt=cfg.bit_depth,
         )
         .overwrite_output()
         .run(capture_stdout=True, capture_stderr=True))

        print(f"--- 转码成功: {os.path.basename(tar_path)}")
        os.remove(source_path)
        return True

    except Exception as e:
        print(f' [!] 转码失败: {e} \n  源文件: {source_path}')
        with open(f"{tar_path}.FAILED", "w") as f:
            f.write(f"{source_path} CONVERT FAILED")
        return False


async def _write_metadata(tar_path: str, ct: ConvTask) -> None:
    """对转码后的文件写入封面 / 歌词 / 标签元数据。"""
    try:
        # mediafile 是可选依赖，运行时按需导入
        from src.metadata.insert import INSERT_METADATA  # noqa: PLC0415

        song = ct.song_task
        album = ct.album_task or (song.album if song else None)
        if not song or not album:
            logger.info("跳过元数据写入: ConvTask 未携带 SongTask/AlbumTask")
            return

        writer = INSERT_METADATA(tar_path, song, album)
        await writer.insert()
    except ImportError:
        logger.info("mediafile 未安装，跳过元数据写入")
    except Exception as e:
        logger.error(f"元数据写入异常: {e}")


async def convert_worker(
    conv_queue: asyncio.Queue[ConvTask | None],
    sql: SQL_REPO,
    max_workers: int = 1,
) -> None:
    """
    转码消费者：从 conv_queue 获取任务 → ffmpeg → 元数据写入 → 更新 DB。

    由 main.py 以独立 worker 形式启动，不占用下载并发槽。
    """
    executor = ThreadPoolExecutor(max_workers=max_workers)
    loop = asyncio.get_event_loop()

    while True:
        ct = await conv_queue.get()
        if ct is None:
            conv_queue.task_done()
            return

        try:
            name = os.path.splitext(os.path.basename(ct.source_path))[0]
            tar_path = os.path.join(ct.final_dir, f"{name}.{cfg.target_ext}")
            os.makedirs(ct.final_dir, exist_ok=True)

            ok = await loop.run_in_executor(executor, convert_file, ct.source_path, tar_path)

            if ok:
                await _write_metadata(tar_path, ct)
                await sql.update_DATA_status([(ct.band, ct.album, ct.song)], 1)
                logger.info(f"转码完成: {ct.band} - {ct.album} - {ct.song}")
            else:
                await sql.update_DATA_status([(ct.band, ct.album, ct.song)], -1)
                logger.error(f"转码失败: {ct.band} - {ct.album} - {ct.song}")
        except Exception as e:
            logger.error(f"转码消费者异常: {e}")
            await sql.update_DATA_status([(ct.band, ct.album, ct.song)], -1)
        finally:
            conv_queue.task_done()
