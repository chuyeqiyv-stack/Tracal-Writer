# -*- coding: utf-8 -*-
"""智能排版：按排版设置重新整理正文段落。"""
from __future__ import annotations

import re

INDENT = "\u3000\u3000"  # 两个全角空格，作为段首缩进


def _clean_punctuation_spacing(line: str) -> str:
    """去掉中文标点两侧多余的空格，让排版更像正式出版稿。"""
    # 标点前的空格
    line = re.sub(r"[\t \u3000]*([，。！？；：、）】》」』”’…—])", r"\1", line)
    # 标点后的空格
    line = re.sub(r"([（【《「『“‘])[\t \u3000]*", r"\1", line)
    # 连续空格压缩为一个
    line = re.sub(r"[\t \u3000]{2,}", " ", line)
    return line


_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def _unify_punctuation(line: str) -> str:
    """把中文段落里的半角标点转为全角。

    避开数字之间的小数点（3.5）、千分位（1,000）与时间冒号（12:30），
    纯英文/数字行不做任何替换。
    """
    if not _CJK_RE.search(line):
        return line
    line = re.sub(r"(?<!\d)\.(?!\d)", "。", line)
    line = re.sub(r",(?!\d)", "，", line)
    line = re.sub(r"(?<!\d):(?!\d)", "：", line)
    for half, full in (("!", "！"), ("?", "？"), (";", "；")):
        line = line.replace(half, full)
    return line.replace("(", "（").replace(")", "）")


def _fix_ellipsis(line: str) -> str:
    """规范省略号与破折号：。。。→……、......→……、三个以上连字符→——。"""
    line = re.sub(r"。{2,}", "……", line)
    line = re.sub(r"\.{6,}", "……", line)
    line = re.sub(r"—{3,}", "——", line)
    line = re.sub(r"-{3,}", "——", line)
    return line


def format_text(
    text: str,
    indent: str = "two",
    blank_line: str = "blank",
    clean_spaces: bool = True,
    unify_punct: bool = False,
    ellipsis_fix: bool = False,
) -> str:
    """按设置整理整段文本。

    - indent: "two" 每个段落前加两个全角空格；"none" 不缩进
    - blank_line: "blank" 段落之间空一行；"none" 段落之间不空行
    - clean_spaces: 清除行尾空格、连续空格以及标点两侧的空格
    - unify_punct: 中文段落里的半角标点统一为全角
    - ellipsis_fix: 「。。。」「......」规范为「……」，三个以上连字符规范为「——」
    """
    if not text:
        return ""

    # 统一换行符与制表符
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\t", " ")

    paragraphs: list[str] = []
    for raw in text.split("\n"):
        line = raw.strip()
        if not line:
            continue
        if clean_spaces:
            line = _clean_punctuation_spacing(line)
        if ellipsis_fix:
            line = _fix_ellipsis(line)
        if unify_punct:
            line = _unify_punctuation(line)
        line = line.strip()
        if not line:
            continue
        # 先去掉原来可能存在的段首缩进，再按设置重新添加
        line = line.lstrip(" \u3000")
        if indent == "two":
            line = INDENT + line
        paragraphs.append(line)

    separator = "\n\n" if blank_line == "blank" else "\n"
    return separator.join(paragraphs)
