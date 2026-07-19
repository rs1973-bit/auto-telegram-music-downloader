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

    bot = Client(
    "report",
    api_id = cfg.api_id,
    api_hash = cfg.api_hash,
    bot_token = cfg.bot_token,
    workers=5
    )
    # ── 队列 ──────────────────────────────────────────────────────── #
    song_queue: asyncio.Queue = asyncio.Queue()
    conv_queue: asyncio.Queue = asyncio.Queue()

    sql = SQL_REPO()
    await sql.inital()

    # 获取歌手索引信息
    # mb = MusicIndexer(sql)
    # await mb.GET_IDX()

    print((">>> 正在启动 Telegram Client..."))
    await app.start()
    print((">>> Client 已启动..."))
    await bot.start()
    print('>>> 汇报机器人 已启动..')

    manager = Client_Manager(app, cfg.save_path)
    report_bot = ReportBot(bot, manager, app.me.id)
    await report_bot.send_notice("脚本已上线...")

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
            await manager.restart(on_cooldown=report_bot.report)
        await asyncio.sleep(10)

    await search_task
    print((">>> [搜索器] 扫描完毕"))

    # ── 停止下载器 ──────────────────────────────────────────────── #
    for _ in range(num_workers):
        await song_queue.put(None)

    print((f">>> [下载器] 等待剩余 {song_queue.qsize()} 个任务完成..."))
    await asyncio.gather(*download_workers)
    print((">>> [下载器] 全部处理完毕"))

    # ── 等待转码消费完毕 ────────────────────────────────────────── #
    print(">>> [转码器] 等待剩余转码任务完成...")
    await conv_queue.join()
    print(">>> [转码器] 全部完成")

    # ── 最终重试 ──────────────────────────────────────────────────── #
    retry_tasks = await dl.process_failed()
    if retry_tasks:
        print(f">>> [重试] 有 {retry_tasks} 个失败任务待重试...")
        download_workers = [asyncio.create_task(dl.run()) for _ in range(num_workers)]
        await asyncio.sleep(2)
        await song_queue.join()
        for _ in range(num_workers):
            await song_queue.put(None)
        await asyncio.gather(*download_workers)
        print(">>> [重试] 全部完成")

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
            print(("所有预定任务执行完毕，程序将正常退出"))
            print(("========================================"))
            break 
            
        except ConnectionError:
            print(("网络连接中断，60秒后尝试重连..."))
            await asyncio.sleep(60)
            
        except Exception as e:
            print((f"发生未预期错误: {e}"))
            import traceback
            traceback.print_exc()
            
            print(("系统将在 1 分钟后尝试重启..."))
            await asyncio.sleep(60) 

if __name__ == "__main__":
    try:
        asyncio.run(main())

    except KeyboardInterrupt:
        print(("\n[!] 用户强制停止程序，正在清理环境..."))
        sys.exit(0)
