import os
import json
import asyncio
import random
from core.converter import converter
from pyrogram.errors import RPCError, FileReferenceExpired, FloodWait
from manage.log import logger

# --- 配置加载 ---
with open('config.json', 'r') as f:
    conf = json.load(f)

temp_path = conf["paths"]["temp_memory"]
save_root = conf["paths"]["final_library"]
tar_ext = conf["audio_settings"]["target_ext"]
workers = conf["telegram"]['workers']

# --- 并发控制 ---
album_semaphore = asyncio.Semaphore(1)
song_semaphore = asyncio.Semaphore(workers)
dc_auth_lock = asyncio.Lock()

async def robust_download(client, msg, target_path, expected_size, manager):
    file_obj = msg.document or msg.audio
    
    for attempt in range(20):
        await manager.can_runs.wait()
        try:
            logger.info(f'尝试获取最新链接')
            msg = await client.get_messages(msg.chat.id, msg.id)
            current_size = os.path.getsize(target_path) if os.path.exists(target_path) else 0
            if current_size == expected_size:
                return True

            # 断点续传对齐逻辑
            offset_index = current_size // (1024 * 1024)
            aligned_size = offset_index * 1024 * 1024
            if offset_index != 0:
                logger.warning(f'已对齐残片: {file_obj.file_name} -> {offset_index}MB')
                with open(target_path, "r+b") as f:
                    f.truncate(aligned_size)
            
            # 开始流式下载
            downloaded_bytes = aligned_size
            with open(target_path, "ab") as f:
                async for chunk in client.stream_media(msg, offset=offset_index):
                    if not manager.can_runs.is_set(): # 如下载中途被全局暂停
                        raise InterruptedError("Global pause triggered")
                    f.write(chunk)
                    downloaded_bytes += len(chunk)

            final_size = os.path.getsize(target_path)
            if final_size < expected_size:
                raise RuntimeError(f"流异常中断: 进度 {final_size}/{expected_size}")
            else:
                logger.info(f"文件{file_obj.file_name} 下载成功")
                manager.files += 1

            manager.report_size += os.path.getsize(target_path) - current_size

            return True

        except (FloodWait, InterruptedError) as e:
            if manager.can_runs.is_set():
                async with dc_auth_lock:
                    if manager.can_runs.is_set(): 
                        wait_time = e.value if hasattr(e, 'value') else 60
                        logger.error(f"触发风控/暂停，休眠 {wait_time}s")
                        manager.can_runs.clear()
                        manager.error_count += 1
                        await asyncio.sleep(wait_time)
                        manager.can_runs.set()
            continue

        except RPCError as e:
            # 捕获所有 Telegram 相关的 RPC 错误
            manager.error_count += 1
            logger.error(f"Telegram RPC错误 [尝试 {attempt}]: {e.NAME} - {e.MESSAGE}")
            await asyncio.sleep(random.uniform(5, 10))

        except Exception as e:
            # 兜底所有其他错误（IOError, Runtime等）
            manager.error_count += 1
            logger.error(f"未预期下载异常 [尝试 {attempt}]: {type(e).__name__} - {e}")
            await asyncio.sleep(random.uniform(5, 15))
            
    return False

async def download_worker(client, msg, final_album_dir, need_conv, info, manager):
    async with song_semaphore:
        await manager.can_runs.wait()
        file_obj = msg.audio or msg.document
        current_save_dir = temp_path if need_conv else final_album_dir
        target_path = os.path.join(current_save_dir, file_obj.file_name)
        base_name = os.path.splitext(file_obj.file_name)[0]
        logger.info(f'正在下载: {file_obj.file_name}')
        
        # 预检查：如果文件已存在且大小正确，直接跳过，不进重试循环
        if os.path.exists(target_path) and os.path.getsize(target_path) == file_obj.file_size:
            logger.info(f"跳过已存在文件: {file_obj.file_name}")
            success = True

        elif os.path.exists(os.path.join(final_album_dir, f'{base_name}.{tar_ext}')):
            logger.info(f'文件已被转码: {file_obj.file_name}')
            return
            
        else:
            success = await robust_download(client, msg, target_path, file_obj.file_size, manager)
        
        if success:
            if need_conv:
                with open(f"{target_path}.txt", "w", encoding='utf-8') as t:
                    t.write(final_album_dir)
                asyncio.get_event_loop().run_in_executor(None, converter)
        else:
            # 记录失败
            logger.error(f'{file_obj.file_name} 下载失败')
            error_data = {"band": info["band"], "album": info["album"], "file_name": file_obj.file_name, "msg_id": msg.id, "chat_id": info["chat_id"]}
            with open('failed_documents.jsonl', 'a', encoding='utf-8') as f:
                f.write(json.dumps(error_data, ensure_ascii=False) + "\n")

async def process_single_album(client, info, save_path, manager):
    async with album_semaphore:
        start, end, chat_id = info["msg_ids"][0], info["msg_ids"][1], info["chat_id"]
        final_album_dir = os.path.join(save_root, save_path)
        os.makedirs(final_album_dir, exist_ok=True)

        messages = []
        async for msg in client.get_chat_history(chat_id, offset_id=end + 1, limit=end - start + 1):
            await manager.can_runs.wait()
            if msg.id < start: break
            if msg.audio or msg.document:
                messages.append(msg)

        tasks = []
        for msg in messages:
            file_obj = msg.audio or msg.document
            _, ext = os.path.splitext(file_obj.file_name)
            need_conv = ext.lower()[1:] != tar_ext
            tasks.append(download_worker(client, msg, final_album_dir, need_conv, info, manager))

        if tasks:
            await asyncio.gather(*tasks)
            logger.info(f"专辑 [{info['album']}] 批次处理完成")

async def process_failed(client, manager):
    await manager.can_runs.wait()
    if not os.path.exists('failed_documents.jsonl'):
        return
    
    with open('failed_documents.jsonl', 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    # 清空失败列表准备重新运行
    open('failed_documents.jsonl', 'w').close()
    
    for line in lines:
        if line.strip():
            task = json.loads(line)
            # 构造简易 info 对象重新调用
            await process_single_album(client, {
                "band": task["band"], 
                "album": task["album"], 
                "msg_ids": [task["msg_id"], task["msg_id"]],
                "chat_id": task["chat_id"]
            }, f"{os.path.join(task['band'], task['album'])}")