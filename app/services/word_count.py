# -*- coding: utf-8 -*-
"""字数统计工具。

规则：按字符计数，忽略所有空白字符（含全角空格、换行）；
开启“不计标点”后，再排除中英文标点与符号。
"""
from __future__ import annotations

import unicodedata

# 需要额外排除的符号（部分符号的 Unicode 类别不是 P*，这里显式列出）
_EXTRA_PUNCT = set(
    "~!@#$%^&*_-+=|\\/<>·—…‰′″‹›«»"
    "｡｢｣､･"
)

# Unicode 标点类别
_PUNCT_CATEGORIES = {"Po", "Pd", "Pi", "Pf", "Ps", "Pe", "Pc"}


def is_punctuation(ch: str) -> bool:
    """判断单个字符是否为标点 / 符号。"""
    if ch in _EXTRA_PUNCT:
        return True
    try:
        return unicodedata.category(ch) in _PUNCT_CATEGORIES
    except ValueError:
        return False


# 字符是否计数的缓存：小说用字高度重复，缓存后接近 C 速度
_KEEP_CACHE: dict[str, bool] = {}


def count_chars(text: str, exclude_punctuation: bool = False) -> int:
    """统计字数。空白不计，可选排除标点。"""
    if not text:
        return 0
    if not exclude_punctuation:
        return sum(1 for ch in text if not ch.isspace())
    cache = _KEEP_CACHE
    count = 0
    for ch in text:
        keep = cache.get(ch)
        if keep is None:
            keep = not (ch.isspace() or is_punctuation(ch))
            cache[ch] = keep
        if keep:
            count += 1
    return count


def format_word_count(count: int) -> str:
    """把字数格式化为易读文本，例如 12.3 万。"""
    if count >= 10000:
        return f"{count / 10000:.1f} 万"
    return f"{count}"
