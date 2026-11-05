# -*- coding: utf-8 -*-
"""本地字体文件：归档到数据目录，并在每次启动时重新注册。

QFontDatabase.addApplicationFont 只在运行时生效，重启必须重新注册；
把字体文件复制到 <数据目录>/fonts 后，即使原文件被删除或换机重装也能恢复。
"""
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from PySide6.QtGui import QFontDatabase

from app import config

FONT_EXTS = {".ttf", ".otf", ".ttc"}


def fonts_dir() -> Path:
    """数据目录下的字体归档目录（不存在则创建）。"""
    directory = Path(config.DATA_DIR) / "fonts"
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return directory


def _digest(path: Path) -> str:
    hasher = hashlib.sha1()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()[:16]


def archive(source: str | Path) -> Path:
    """把字体文件归档到数据目录（同内容只存一份），返回归档后的路径。"""
    src = Path(source)
    if src.suffix.lower() not in FONT_EXTS:
        raise ValueError(f"不支持的字体格式：{src.suffix}")
    target_dir = fonts_dir()
    try:
        if src.resolve().parent == target_dir.resolve():
            return src
    except OSError:
        pass
    dest = target_dir / f"{_digest(src)}{src.suffix.lower()}"
    if not dest.exists():
        shutil.copy2(src, dest)
    return dest


def register(source: str | Path) -> tuple[Path, int]:
    """归档并注册字体，返回 (归档路径, QFontDatabase id)；id 为 -1 表示失败。"""
    dest = archive(source)
    return dest, QFontDatabase.addApplicationFont(str(dest))


def family_names(font_id: int) -> list[str]:
    if font_id < 0:
        return []
    return [name for name in QFontDatabase.applicationFontFamilies(font_id) if name]


def load_all(paths: list[str]) -> list[str]:
    """启动时重新注册已导入字体；返回去重后的归档路径列表。

    旧版本存的是原始绝对路径，这里会顺带迁移进数据目录；
    已失效的条目会被丢弃。
    """
    result: list[str] = []
    seen: set[str] = set()
    for raw in list(paths or []):
        if not raw:
            continue
        try:
            dest, font_id = register(raw)
        except (OSError, ValueError):
            continue
        if font_id < 0:
            continue
        key = str(dest)
        if key in seen:
            continue
        seen.add(key)
        result.append(key)
    return result
