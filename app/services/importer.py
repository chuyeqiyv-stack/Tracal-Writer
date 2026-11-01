# -*- coding: utf-8 -*-
"""导入解析：把软件导出的 TXT / JSON / 图片文件解析回可入库的数据。

优先兼容催客写作（含历史版本）自己导出的文件格式：
    小说 TXT：首行书名、可选「作者：」行、◆ 卷名、章标题、正文
    设定 TXT：首行标题、空行、正文
其余普通 TXT 采用常见章节标记（第x章 / 序章 / 楔子…）尽力拆章。
"""
from __future__ import annotations

import re
from pathlib import Path


def read_text_file(path: str | Path) -> str:
    """按 utf-8 → utf-8-sig → gbk 顺序尝试读取文本文件。"""
    raw = Path(path).read_bytes()
    for encoding in ("utf-8", "utf-8-sig", "gbk"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


# 章节标题候选：常见标记（第x章 / 序章 / 楔子 / 番外 …）
_CHAPTER_RE = re.compile(
    r"^\s*(?:第\s*[0-9零一二两三四五六七八九十百千万]+\s*[章回节卷]"
    r"|序章|序幕|楔子|引子|前言|终章|尾声|结局|番外\S{0,6})\s*.*$"
)
# 结尾标点：以这些收尾的行更像正文段落，不当作章节标题
_TRAILING_PUNCT = tuple("。！？；，、…！”』」）》\"'")


def _looks_like_title(line: str) -> bool:
    text = line.strip()
    if not text or len(text) > 30:
        return False
    if text.endswith(_TRAILING_PUNCT):
        return False
    return True


def parse_novel_txt(text: str) -> dict:
    """把小说 TXT 解析为 {"title", "author", "volumes": [...]}。

    volumes: [{"title": 卷名, "chapters": [{"title": 章名, "content": 正文}]}]
    """
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    title = ""
    author = ""
    idx = 0
    non_empty: list[int] = []
    for i, line in enumerate(lines):
        if line.strip():
            non_empty.append(i)
    if non_empty:
        title = lines[non_empty[0]].strip()
        idx = non_empty[0] + 1
        if len(non_empty) > 1:
            second = lines[non_empty[1]].strip()
            if second.startswith(("作者：", "作者:")):
                author = second.split("：", 1)[-1].split(":", 1)[-1].strip()
                idx = non_empty[1] + 1

    has_volume_mark = any(line.lstrip().startswith("◆") for line in lines[idx:])
    if has_volume_mark:
        volumes = _parse_with_volume_marks(lines, idx)
    else:
        volumes = _parse_plain(lines, idx)

    # 兜底：至少一卷一章
    if not volumes:
        volumes = [{"title": "正文", "chapters": [{"title": "第一章", "content": ""}]}]
    for vol in volumes:
        vol.setdefault("title", "正文")
        if not vol.get("chapters"):
            vol["chapters"] = [{"title": "第一章", "content": ""}]
    return {"title": title or "导入的小说", "author": author, "volumes": volumes}


def _parse_with_volume_marks(lines: list[str], start: int) -> list[dict]:
    """按「◆ 卷名」分卷；卷内按空行分隔 + 短行识别章标题。"""
    volumes: list[dict] = []
    current_vol: dict | None = None
    current_chapter: dict | None = None
    pending_title = False          # 正在等待章标题（卷头 / 章内容结束之后）
    blank_run = 0

    def new_volume(name: str) -> None:
        nonlocal current_vol, current_chapter, pending_title, blank_run
        current_vol = {"title": name or "正文", "chapters": []}
        volumes.append(current_vol)
        current_chapter = None
        pending_title = True
        blank_run = 0

    for raw in lines[start:]:
        stripped = raw.strip()
        if stripped.startswith("◆"):
            new_volume(stripped.lstrip("◆").strip())
            continue
        if not stripped:
            blank_run += 1
            if current_chapter is not None:
                # 章内容中的空行保留（连续空行稍后判断是否章节分隔）
                current_chapter["content_lines"].append("")
            continue
        if current_vol is None:
            new_volume("正文")
        if pending_title:
            current_chapter = {
                "title": stripped or "未命名章节",
                "content_lines": [],
            }
            current_vol["chapters"].append(current_chapter)
            pending_title = False
            blank_run = 0
            continue
        # 空行之后遇到「短行 + 常见章节标记」→ 视为新的章标题
        if (
            blank_run >= 2
            and _looks_like_title(stripped)
            and _CHAPTER_RE.match(stripped)
        ):
            current_chapter = {"title": stripped, "content_lines": []}
            current_vol["chapters"].append(current_chapter)
            blank_run = 0
            continue
        if current_chapter is None:
            current_chapter = {"title": "第一章", "content_lines": []}
            current_vol["chapters"].append(current_chapter)
        current_chapter["content_lines"].append(raw.rstrip())
        blank_run = 0

    for vol in volumes:
        for ch in vol["chapters"]:
            ch["content"] = "\n".join(ch.pop("content_lines")).strip()
    return volumes


def _parse_plain(lines: list[str], start: int) -> list[dict]:
    """无 ◆ 标记：按常见章节标记（第x章 等）拆章，全部放入一卷。"""
    body = lines[start:]
    marks: list[int] = []
    for i, raw in enumerate(body):
        stripped = raw.strip()
        if stripped and _CHAPTER_RE.match(stripped) and len(stripped) <= 30:
            marks.append(i)
    if not marks:
        content = "\n".join(body).strip()
        return [
            {
                "title": "正文",
                "chapters": [{"title": "第一章", "content": content}],
            }
        ]
    volumes = [{"title": "正文", "chapters": []}]
    chapters = volumes[0]["chapters"]
    if marks[0] > 0:
        head = "\n".join(body[: marks[0]]).strip()
        if head:
            chapters.append({"title": "开头", "content": head})
    for n, mark in enumerate(marks):
        end = marks[n + 1] if n + 1 < len(marks) else len(body)
        title = body[mark].strip()
        content = "\n".join(body[mark + 1 : end]).strip()
        chapters.append({"title": title, "content": content})
    return volumes


def parse_note_txt(text: str) -> dict:
    """设定 TXT：首行标题，其余为正文。"""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    stripped = text.lstrip("\n")
    if "\n" in stripped:
        head, _, body = stripped.partition("\n")
        title = head.strip()
        content = body.strip("\n")
    else:
        title, content = stripped.strip(), ""
    if not title:
        title, content = (content.split("\n", 1) + [""])[:2] if content else ("", "")
        title = title.strip() or "导入的设定"
        content = content.strip("\n")
    return {"title": title[:60] or "导入的设定", "content": content}
