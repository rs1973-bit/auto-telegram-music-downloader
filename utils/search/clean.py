import re
from rapidfuzz import fuzz
from manage.setup import cfg
from pyrogram.types import Message

def clean_name(name) -> str:
        """清洗文件名"""
        name = name.lower()
        name = re.sub(r'\.(wav|flac|dsf|dff)$', '', name)
        name = re.sub(r'[\(\[\{].*?[\)\]\}]', '', name)
        name = re.sub(r'\d+\s?bit|\d+\s?khz', '', name)
        name = re.sub(r'^\d+[\.\s\-_]+', '', name)
        return name.strip()
    
def is_song_match(query_name: str = "", target_name: str = "", 
                                                threshold: int = 70) -> bool:
    """判断歌曲有无命中"""
    if not query_name or not target_name: return False
    
    for word in cfg.exclude_list:
        if word in query_name.lower(): return False

    q_clean = clean_name(query_name)
    t_clean = clean_name(target_name)
    score = fuzz.token_set_ratio(q_clean, t_clean)
    return score >= threshold

def is_album_match(songs:list[str], msgs:list[Message]) -> float:
    """判断可能的区间是否真正匹配, 返回一个区间的得分"""
    current_album_hits = []
    for idx, msg in enumerate(msgs):
        m_file = msg.document or msg.audio
        if not m_file: continue
        if is_song_match(m_file.file_name, songs[idx]):
            current_album_hits.append(m_file)

    hit_count = len(set(m.id for m in current_album_hits))
    hit_rate = hit_count / len(songs)
    return round(hit_rate, 2)

async def evaluate_album_messages(songs:list[str], msgs:list[Message]) -> tuple[float, list[bool]]:
    """对传入的消息区间逐首比对，返回命中率和每首歌的命中布尔列表。"""
    hits = []
    for idx, song in enumerate(songs):
        try:
            msg = msgs[idx]
        except IndexError:
            hits.append(False)
            continue
        m_file = msg.document or msg.audio
        if not m_file:
            hits.append(False)
        else:
            hits.append(is_song_match(song, m_file.file_name))

    hit_count = len([h for h in hits if h])
    rate = round(hit_count / len(songs), 2) if songs else 0.0
    return rate, hits