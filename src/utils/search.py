import os
import re
from rapidfuzz import fuzz
from src.utils.config import cfg
from src.utils.language import is_latin, normalize_cjk, has_cjk
from pyrogram.types import Message

AUDIO_EXTENSIONS = cfg.allowed_extensions


def is_audio_file(filename: str) -> bool:
    _, ext = os.path.splitext(filename)
    return ext.lower() in AUDIO_EXTENSIONS


def clean_name(name: str) -> str:
    """清洗文件名。

    只剥离已知的元数据后缀（remastered、deluxe edition、mono/stereo 等），
    保留有含义的括号内容如 (Reprise)、(Single Version) ——
    否则 Sgt. Pepper's (Reprise) 与 Sgt. Pepper's 无法区分。
    """
    name = name.lower()
    # 繁简归一化：iTunes TW 店铺返回繁体，CN 频道可能用简体
    if has_cjk(name):
        name = normalize_cjk(name)
    name = re.sub(r'\.(wav|flac|dsf|dff)$', '', name)
    # 只剥离已知元数据括号，不碰有含义的括号内容
    name = re.sub(
        r'\s*[\(\[][^\)\]]*('
        r'remaster(?:ed)?(?:\s*\d{4})?|'
        r'deluxe|super deluxe|edition|anniversary|'
        r'mono|stereo|'
        r'explicit|bonus|bonus track|'
        r'\d{4} remaster|\d{4} remix'
        r')[^\)\]]*[\)\]]?\s*',
        '', name, flags=re.I
    )
    name = re.sub(r'\d+\s?bit|\d+\s?khz|\d+k', '', name, flags=re.I)
    name = re.sub(r'^\d+[\.\s\-_]+', '', name)
    name = re.sub(r"[_'\-\.\s,!?;:]+", ' ', name)   # 统一分隔符
    return name.strip()


def _hybrid_score(q_clean: str, t_clean: str, lang_latin: bool) -> float:
    """混合评分。拉丁语种用 token 级匹配，非拉丁用全串匹配。"""
    if lang_latin:
        tss = fuzz.token_set_ratio(q_clean, t_clean)
        tsr = fuzz.token_sort_ratio(q_clean, t_clean)
        return tss * 0.6 + tsr * 0.4
    else:
        wr = fuzz.WRatio(q_clean, t_clean)
        pr = fuzz.partial_ratio(q_clean, t_clean)
        return wr * 0.7 + pr * 0.3


def is_song_match(query_name: str = "", target_name: str = "",
                  threshold: int = 80, return_score: bool = False) -> bool | float:
    """判断歌曲是否命中。

    拉丁曲目：token 级交集 + 长词包含检查。
    非拉丁曲目：全串模糊匹配（不分词），跳过词集检查。

    Args:
        query_name: 歌曲名
        target_name: 目标文件名
        threshold: 最低匹配分 (0-100)
        return_score: True 时返回浮点数评分而非 bool
    """
    if not query_name or not target_name:
        return 0.0 if return_score else False

    for word in cfg.exclude_list:
        if word in query_name.lower():
            return 0.0 if return_score else False

    q_clean = clean_name(query_name)
    t_clean = clean_name(target_name)

    lang_latin = is_latin(query_name)

    # 拉丁：较长方所有长词必须被较长方包含
    if lang_latin:
        q_words = {w for w in q_clean.split() if len(w) > 3}
        t_words = {w for w in t_clean.split() if len(w) > 3}
        if q_words and t_words:
            shorter, longer = (q_words, t_words) if len(q_words) <= len(t_words) else (t_words, q_words)
            if not shorter.issubset(longer):
                return 0.0 if return_score else False

    score = _hybrid_score(q_clean, t_clean, lang_latin)

    if return_score:
        return score if score >= threshold else 0.0
    return score >= threshold


def is_album_match(songs: list[str], msgs: list[Message]) -> float:
    """判断区间是否匹配，返回命中率。"""
    current_album_hits: list[Message] = []
    for idx, msg in enumerate(msgs):
        m_file = msg.document or msg.audio
        if not m_file:
            continue
        if idx < len(songs) and is_song_match(songs[idx], m_file.file_name):
            current_album_hits.append(m_file)

    hit_count = len(set(m.id for m in current_album_hits))
    hit_rate = hit_count / len(songs) if songs else 0.0
    return round(hit_rate, 2)

async def evaluate_album_messages(songs:list[str], msgs:list[Message]) -> tuple[float, list[bool]]:
    """对传入的消息区间逐首比对，返回命中率和每首歌的命中布尔列表。"""
    file_msgs = [(msg.document or msg.audio) for msg in msgs]
    file_msgs = [f for f in file_msgs if f and f.file_name and is_audio_file(f.file_name)]
    hits = []
    for song in songs:
        hits.append(any(is_song_match(song, f.file_name) for f in file_msgs))

    hit_count = len([h for h in hits if h])
    rate = round(hit_count / len(songs), 2) if songs else 0.0
    return rate, hits