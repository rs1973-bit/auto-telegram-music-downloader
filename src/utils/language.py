"""语种判定工具：拉丁 / 中文 / 日文 / 西里尔 检测。

供 index.py（数据源选择）、searcher.py（匹配策略分流）共用。"""

import re

_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3000-\u303f\u3040-\u309f\u30a0-\u30ff]")
_CYRILLIC_RE = re.compile(r"[\u0400-\u04ff]")
_LATIN_RE = re.compile(
    r"^[\x00-\x7F\u00C0-\u024F\u1E00-\u1EFF"
    r"\u2000-\u206F\s\.\,\;\:\!\?\"\'\(\)\[\]\-\#\d\+/&_@%*~]+$",
)


def has_cjk(text: str) -> bool:
    """文本是否含中日韩字符。"""
    return bool(_CJK_RE.search(text))


def has_cyrillic(text: str) -> bool:
    """文本是否含西里尔字符。"""
    return bool(_CYRILLIC_RE.search(text))


def is_latin(text: str) -> bool:
    """文本是否全部由拉丁字符组成（不含 CJK/西里尔）。"""
    return bool(_LATIN_RE.match(text))


def is_native_latin(text: str) -> bool:
    """文本的「母语字符」是否为拉丁——不含 CJK 也不含西里尔。"""
    return not has_cjk(text) and not has_cyrillic(text)


def script_type(text: str) -> str:
    """返回文本的主要书写系统：'latin' | 'cjk' | 'cyrillic'。"""
    if has_cjk(text):
        return "cjk"
    if has_cyrillic(text):
        return "cyrillic"
    return "latin"
