import asyncio
import sys
from pyrogram.client import Client
from core.downloader import Downloader
from server.searcher import Search_in_TG
from utils.manage.manager import Client_Manager
import os
from utils.sql.sql_repo import SQL_REPO
from server.get_idxs import MusicBrainzClient
from utils.manage.setup import cfg


async def run_session():
    """
    单个运行会话：负责初始化 Client 和 Queue
    """
    app = Client(
    name = cfg.bot_name, 
    api_id = cfg.api_id, 
    api_hash = cfg.api_hash, 
    workers = 16, # 这是通讯线程, 不是文件下载线程
    max_concurrent_transmissions = cfg.max_workers # 这才是文件下载线程
    )   

    bot = Client(
    "report",
    api_id = cfg.api_id,
    api_hash = cfg.api_hash,
    bot_token = cfg.bot_token,
    workers=5
    )
    local_queue = asyncio.Queue()
    sql = SQL_REPO()
    await sql.inital()

    # 获取歌手索引信息
    mb = MusicBrainzClient(sql)
    await mb.GET_IDX()
    
    print((">>> 正在启动 Telegram Client..."))
    await app.start()   
    print((">>> Client 已启动..."))
    await bot.start()
    print('>>> 汇报机器人 已启动..')
    
    manager = Client_Manager(app, bot, cfg.save_path)
    await bot.send_message(manager.report_id, "脚本已上线...")
    # 使用 Search_in_TG 作为生产者
    searcher = Search_in_TG(app, manager, local_queue, sql)
    search_task = asyncio.create_task(searcher.GET_HISTORY_AUDIO())

    # 启动 Downloader 消费者
    dl = Downloader(app, manager, local_queue, sql)
    num_workers = cfg.workers if getattr(cfg, 'workers', None) else 3
    workers = [asyncio.create_task(dl.run()) for _ in range(num_workers)]
    await asyncio.sleep(1)

    while not search_task.done() or not local_queue.empty():
        if manager.need_stop():
            await manager.restart()
        await asyncio.sleep(10)

    await search_task
    print((">>> [生产者] 搜索器已扫描完所有目标频道"))

    for _ in range(num_workers):
        await local_queue.put(None)

    print((f">>> [队列] 正在等待剩余 {local_queue.qsize()} 个任务下载完成..."))
    await asyncio.gather(*workers)
    print((">>> [会话] 当前批次任务全部处理完毕")) 
    print((f'尝试重新下载失败的文件'))
    await dl.process_failed()

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
