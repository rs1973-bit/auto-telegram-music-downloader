import asyncio
import json
import random
import re
from pyrogram import Client
from rapidfuzz import fuzz
import os

# 加载配置
with open(r'config.json', 'r', encoding="utf-8") as c:
    data = json.load(c)
   

moniting = data["monitoring"]
targets = moniting["target_channels"]
author_list = moniting["author"]
exclud_list = moniting["exclude"]
collected_ids = set()

def clean_name(name):
    """清洗文件名逻辑保持不变"""
    name = name.lower()
    name = re.sub(r'\.(wav|flac|dsf|dff)$', '', name)
    name = re.sub(r'[\(\[\{].*?[\)\]\}]', '', name)
    name = re.sub(r'\d+\s?bit|\d+\s?khz', '', name)
    name = re.sub(r'^\d+[\.\s\-_]+', '', name)
    return name.strip()

def is_song_match(query_name: str = "", target_name: str = "", threshold: int = 70) -> bool:
    if not query_name or not target_name: return False
    for word in exclud_list:
        if word in query_name: return False
    q_clean = clean_name(query_name)
    t_clean = clean_name(target_name)
    score = fuzz.token_set_ratio(q_clean, t_clean)
    return score >= threshold

async def get_id_range(app: Client, chat_id, start_id, end_id, manager):
    limit = (end_id - start_id) + 1
    messages = []
    try:
        async for msg in app.get_chat_history(chat_id, offset_id=end_id + 1, limit=limit):
            await manager.can_runs.wait()
            if msg.id < start_id: break
            messages.append(msg)
    except Exception as e:
        print(f"拉取区间记录时出错: {e}")
    messages.reverse()
    return messages

async def find_album(account: Client, chat_id: int, album_name: str, song_list: list, band: str, q, manager):
    album_len = len(song_list)
    sorted_songs = sorted(song_list, key=len, reverse=True)
    for song in sorted_songs:
        await manager.can_runs.wait()
        original_idx = song_list.index(song)
        if song.count(" ") < 1 or len(song) < 5: continue

        print(f"  [搜索] 正在搜寻关键词: {song}")
        try:
            async for message in account.search_messages(chat_id=chat_id, query=song, limit=20):
                if message.id in collected_ids: continue
                
                file_obj = message.document or message.audio
                if not file_obj: continue

                if is_song_match(song, file_obj.file_name):
                    start = message.id - original_idx
                    end = message.id + (album_len - original_idx)
                    
                    # 避免重复拉取
                    if any(mid in range(start, end + 1) for mid in collected_ids):
                        continue

                    msg_range = await get_id_range(account, chat_id, start, end, manager)
                    
                    current_album_hits = []
                    for msg in msg_range:
                        m_file = msg.document or msg.audio
                        if not m_file: continue
                        for s in song_list:
                            if is_song_match(s, m_file.file_name):
                                current_album_hits.append(msg)
                                break
                    
                    hit_count = len(set(m.id for m in current_album_hits))
                    hit_rate = hit_count / album_len

                    if hit_rate >= 0.7:
                        print(f"  [命中] {band} - {album_name} | 命中率: {hit_rate:.2f} | ID: {start}-{end}")
                        
                        download_task = {
                            "band": band,
                            "album": album_name,
                            "chat_id": chat_id,
                            "msg_ids": [start, end]
                        }
                        
                        # 记录已处理的 ID，防止后续搜索再次触发
                        for m in msg_range:
                            collected_ids.add(m.id)
                        
                        await q.put(download_task)
                        return True
            
            await asyncio.sleep(random.randint(1, 2)) # 搜索翻页冷却
        except Exception as e:
            print(f"  [!] 搜索过程出错: {e}")
            await asyncio.sleep(5)
            
    return False

async def get_history_audio(account: Client, q, manager):
    """主生产者函数"""
    with open(rf'{os.path.join('handler', "songs.json")}', 'r', encoding="utf-8") as s:
         albums = json.load(s)

    await manager.can_runs.wait()
    for band in author_list:
        if band not in albums:
            print(f"警告: {band} 不在 songs.json 中")
            continue
            
        songs_dict = albums[band]
        for album_name, song_list in songs_dict.items():
            print(f'\n>>> 任务启动: {band} - {album_name}')
            
            album_found = False
            for chat_id in targets:
                print(f"频道 {chat_id} 探测中...")
                album_found = await find_album(account, chat_id, album_name, song_list, band, q, manager)
                if album_found:
                    break
                # 频道切换冷却
                await asyncio.sleep(1)
            
            if not album_found:
                print(f"[结果] 遍历完所有目标频道，未找到专辑: {album_name}")
            
            # 专辑切换冷却，防止 TG 频率限制
            await asyncio.sleep(random.randint(5, 8))