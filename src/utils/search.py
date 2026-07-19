import re
from rapidfuzz import fuzz
from src.utils.config import cfg
from pyrogram.types import Message

def clean_name(name: str) -> str:
        """清洗文件名"""
        name = name.lower()
        name = re.sub(r'\.(wav|flac|dsf|dff)$', '', name)
        # 重复移除括号内容直到稳定（处理嵌套括号）
        prev = None
        while prev != name:
            prev = name
            name = re.sub(r'[\(\[\{][^()\[\]{}]*[\)\]\}]', '', name)
        name = re.sub(r'\d+\s?bit|\d+\s?khz|\d+k', '', name, flags=re.I)
        name = re.sub(r'^\d+[\.\s\-_]+', '', name)
        name = re.sub(r"[_'\-\.\s]+", ' ', name)   # 统一分隔符（含引号/下划线/点）
        return name.strip()
    
def _hybrid_score(q_clean: str, t_clean: str) -> float:
    """混合评分: TSS 保证召回, TSR 在同分时做精度微调"""
    tss = fuzz.token_set_ratio(q_clean, t_clean)
    tsr = fuzz.token_sort_ratio(q_clean, t_clean)
    return tss * 0.6 + tsr * 0.4


def is_song_match(query_name: str = "", target_name: str = "", 
                  threshold: int = 70, return_score: bool = False) -> bool | float:
    """判断歌曲有无命中

    Args:
        query_name: 歌曲名
        target_name: 目标文件名
        threshold: 最低匹配分 (0-100)
        return_score: True 时返回浮点数评分而非 bool；
                      分数 < threshold 返回 0.0
    """
    if not query_name or not target_name:
        return 0.0 if return_score else False

    for word in cfg.exclude_list:
        if word in query_name.lower():
            return 0.0 if return_score else False

    q_clean = clean_name(query_name)
    t_clean = clean_name(target_name)
    score = _hybrid_score(q_clean, t_clean)

    if return_score:
        return score if score >= threshold else 0.0
    return score >= threshold

def is_album_match(songs: list[str], msgs: list[Message]) -> float:
    """判断可能的区间是否真正匹配, 返回一个区间的得分"""
    current_album_hits: list[Message] = []
    for idx, msg in enumerate(msgs):
        m_file = msg.document or msg.audio
        if not m_file:
            continue
        if is_song_match(m_file.file_name, songs[idx]):
            current_album_hits.append(m_file)

    hit_count = len(set(m.id for m in current_album_hits))
    hit_rate = hit_count / len(songs) if songs else 0.0
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