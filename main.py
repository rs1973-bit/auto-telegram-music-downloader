import asyncio
import sys
from pyrogram.client import Client
from src.services.downloader import Downloader
from src.services.searcher import Search_in_TG
from src.utils.manager import Client_Manager
from src.utils.report_bot import ReportBot
from src.database.sql_repo import SQL_REPO
from src.services.index import MusicIndexer
from src.utils.config import cfg
from src.services.converter import convert_worker

async def run_session():
    """
    单个运行会话：负责初始化 Client 和 Queue
    """
    app = Client(
    name = cfg.bot_name, 
    api_id = cfg.api_id, 
    api_hash = cfg.api_hash, 
    workers = 16, 
    max_concurrent_transmissions = cfg.max_workers # 文件下载线程
    )   

    # ── 队列 ──────────────────────────────────────────────────────── #
    song_queue: asyncio.Queue = asyncio.Queue()
    conv_queue: asyncio.Queue = asyncio.Queue()

    sql = SQL_REPO()
    await sql.inital()

    # 获取歌手索引信息
    mb = MusicIndexer(sql)
    await mb.GET_IDX()

    print((">>> Starting Telegram Client..."))
    await app.start()
    print((">>> Client started..."))

    manager = Client_Manager(app, cfg.save_path)

    # ── 汇报机器人（可选） ──────────────────────────────────────────── #
    report_bot = None
    if cfg.bot_token:
        bot = Client(
            "report",
            api_id=cfg.api_id,
            api_hash=cfg.api_hash,
            bot_token=cfg.bot_token,
            workers=5,
        )
        await bot.start()
        print(">>> Report bot started...")
        report_bot = ReportBot(bot, manager, app.me.id)
        await report_bot.send_notice("Bot is online...")

    # ── 1. 搜索器（生产者） ──────────────────────────────────────── #
    searcher:Search_in_TG = Search_in_TG(app, manager, song_queue, sql)
    search_task = asyncio.create_task(searcher.GET_HISTORY_AUDIO())

    # ── 2. 下载器（消费者） ──────────────────────────────────────── #
    dl = Downloader(app, manager, song_queue, sql, conv_queue=conv_queue)
    num_workers = cfg.workers if getattr(cfg, 'workers', None) else 3
    download_workers = [asyncio.create_task(dl.run()) for _ in range(num_workers)]

    # ── 3. 转码器（独立消费者，不占用下载槽） ──────────────────── #
    num_conv = 1
    conv_workers = [asyncio.create_task(convert_worker(conv_queue, sql)) for _ in range(num_conv)]

    await asyncio.sleep(1)

    # ── 等待搜索完成 ────────────────────────────────────────────── #
    while not search_task.done() or not song_queue.empty():
        if manager.need_stop():
            await manager.restart(on_cooldown=report_bot.report if report_bot else None)
        await asyncio.sleep(10)

    await search_task
    print((">>> [Scanner] scan complete"))

    # ── 停止下载器 ──────────────────────────────────────────────── #
    for _ in range(num_workers):
        await song_queue.put(None)

    print((f">>> [Downloader] waiting for {song_queue.qsize()} remaining tasks..."))
    await asyncio.gather(*download_workers)
    print((">>> [Downloader] all done"))

    # ── 等待转码消费完毕 ────────────────────────────────────────── #
    print(">>> [Converter] waiting for remaining tasks...")
    await conv_queue.join()
    print(">>> [Converter] all done")

    # ── 最终重试 ──────────────────────────────────────────────────── #
    retry_tasks = await dl.process_failed()
    if retry_tasks:
        print(f">>> [Retry] {retry_tasks} failed tasks to retry...")
        download_workers = [asyncio.create_task(dl.run()) for _ in range(num_workers)]
        await asyncio.sleep(2)
        await song_queue.join()
        for _ in range(num_workers):
            await song_queue.put(None)
        await asyncio.gather(*download_workers)
        print(">>> [Retry] all done")

    # ── 停止转码器 ──────────────────────────────────────────────── #
    for _ in range(num_conv):
        await conv_queue.put(None)
    await asyncio.gather(*conv_workers)

async def main():
    """
    主程序入口：负责崩溃重启逻辑
    """
    while True:
        try:
            # 运行业务会话
            await run_session()
            
            print(("========================================"))
            print(("All tasks completed, exiting normally"))
            print(("========================================"))
            break 
            
        except ConnectionError:
            print(("Connection lost, retrying in 60s..."))
            await asyncio.sleep(60)
            
        except Exception as e:
            print((f"Unexpected error: {e}"))
            import traceback
            traceback.print_exc()
            
            print(("Restarting in 1 minute..."))
            await asyncio.sleep(60) 

if __name__ == "__main__":
    try:
        asyncio.run(main())

    except KeyboardInterrupt:
        print(("\n[!] Interrupted by user, cleaning up..."))
        sys.exit(0)
