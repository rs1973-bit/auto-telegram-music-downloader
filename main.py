import asyncio
import json
import sys
from pyrogram import Client
from core.downloader import process_single_album, process_failed
from handler.moniter import get_history_audio 
from manage.manager import Client_Manager
import os
from handler.get_idx import get_idx

# --- 基础配置加载 ---
with open('config.json', 'r', encoding='utf-8') as f:
    config_data = json.load(f)

api_id = config_data["telegram"]["api_id"]
api_hash = config_data["telegram"]["api_hash"]
bot_token = config_data["telegram"]["bot_token"]
save_path = config_data["paths"]["final_library"]
workers = config_data["telegram"]['workers']

async def worker(worker_id, app_instance, q, manager):
    """
    下载消费者：从队列 q 中获取任务
    """
    print((f"[{worker_id}] 下载消费者已就绪，等待任务..."))
    while True:
        await manager.can_runs.wait()
        info = await q.get()
        if info is None:  # 收到退出信号
            q.task_done()
            break

        if not manager.can_runs.is_set():
            await q.put(info) # 当触发风控时, 把任务写回队列
            continue
        
        try:
            album_name = info.get("album", "Unknown")
            band_name = info.get("band", "Unknown")
            print((f"[{worker_id}] 正在处理任务: {band_name} - {album_name}"))
            
            # 构造保存路径
            save_path = os.path.join(band_name, album_name)
            
            await process_single_album(app_instance, info, save_path, manager)
            
            print((f"[{worker_id}] 任务完成: {album_name}"))
        except Exception as e:
            print((f"[{worker_id}] 下载过程中发生错误: {e}"))
        finally:
            # 必须标记任务完成
            q.task_done()
    print((f"[{worker_id}] 下载消费者已正常退出"))  

async def run_session():
    """
    单个运行会话：负责初始化 Client 和 Queue
    """
    app = Client(
    "rs1973", 
    api_id=api_id, 
    api_hash=api_hash, 
    workers=16, # 这是通讯线程, 不是文件下载线程
    max_concurrent_transmissions=workers # 这才是文件下载线程
    )   

    bot = Client(
    "report",
    api_id = api_id,
    api_hash=api_hash,
    bot_token=bot_token,
    workers=5
    )
    local_queue = asyncio.Queue()
    print((">>> 正在启动 Telegram Client..."))
    await app.start()   
    print((">>> Client 已启动..."))
    await bot.start()
    print('>>> 汇报机器人 已启动..')
    
    manager = Client_Manager(app, bot, save_path)
    await bot.send_message(manager.report_id, "脚本已上线...")
    
    search_task = asyncio.create_task(get_history_audio(app, local_queue, manager))
    
    num_workers = 3
    workers = []
    for i in range(num_workers):
        workers.append(asyncio.create_task(worker(i, app, local_queue, manager)))
        await asyncio.sleep(1) # 防止协程冲突

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
    await process_failed(app, manager)

async def main():
    """
    主程序入口：负责崩溃重启逻辑
    """
    get_idx()
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
            
            print(("系统将在 10 分钟后尝试重启..."))
            await asyncio.sleep(60) 

if __name__ == "__main__":
    try:
        asyncio.run(main())

    except KeyboardInterrupt:
        print(("\n[!] 用户强制停止程序，正在清理环境..."))
        sys.exit(0)
