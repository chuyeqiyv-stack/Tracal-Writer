# -*- coding: utf-8 -*-
"""全文查找与替换的纯文本工具。"""
from __future__ import annotations

import re


def find_all(text: str, needle: str, case_sensitive: bool = False) -> list[tuple[int, int]]:
    """返回所有匹配位置 (起点, 长度)。"""
    if not needle:
        return []
    haystack = text if case_sensitive else text.lower()
    target = needle if case_sensitive else needle.lower()
    result: list[tuple[int, int]] = []
    start = 0
    step = max(1, len(target))
    while True:
        index = haystack.find(target, start)
        if index < 0:
            break
        result.append((index, len(needle)))
        start = index + step
    return result


def replace_all_text(
    text: str, needle: str, replacement: str, case_sensitive: bool = False
) -> tuple[str, int]:
    """替换全部匹配，返回 (新文本, 替换次数)。"""
    if not needle:
        return text, 0
    if case_sensitive:
        count = text.count(needle)
        if count == 0:
            return text, 0
        return text.replace(needle, replacement), count

    pattern = re.compile(re.escape(needle), re.IGNORECASE)
    return pattern.subn(lambda m: replacement, text)


def line_number_at(text: str, position: int) -> int:
    """计算某个字符位置所在的行号（从 1 开始）。"""
    if position <= 0:
        return 1
    return text.count("\n", 0, min(position, len(text))) + 1


def line_snippet(text: str, position: int, max_length: int = 80) -> str:
    """截取匹配位置所在行作为搜索结果预览。"""
    start = text.rfind("\n", 0, max(0, position)) + 1
    end = text.find("\n", position)
    if end < 0:
        end = len(text)
    line = text[start:end].strip()
    if len(line) > max_length:
        line = line[:max_length] + "…"
    return line
