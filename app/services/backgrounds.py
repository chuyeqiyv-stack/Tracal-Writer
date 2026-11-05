# -*- coding: utf-8 -*-
"""自定义写作背景：图片归档、压缩、迁移与上限管理。

JSON 里只存文件名（位于 <数据目录>/backgrounds），不存绝对路径，
这样重装/换机只要数据目录还在就能恢复。
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PySide6.QtGui import QImage

from app import config

MAX_BACKGROUNDS = 5
MAX_SIDE = 1920          # 长边上限（像素）
JPEG_QUALITY = 82        # JPEG 体积/画质折中


def backgrounds_dir() -> Path:
    directory = Path(config.DATA_DIR) / "backgrounds"
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return directory


def resolve(name: str) -> str:
    """文件名（或旧版绝对路径）→ 可用的绝对路径；缺失返回空串。"""
    if not name:
        return ""
    path = Path(name)
    if path.is_absolute():
        return str(path) if path.exists() else ""
    target = backgrounds_dir() / name
    return str(target) if target.exists() else ""


def import_image(source: str | Path) -> str:
    """把图片压缩归档，返回文件名；同内容只存一份。"""
    image = QImage(str(source))
    if image.isNull():
        raise ValueError(f"无法读取图片：{source}")
    return _save(image)


def _save(image: QImage) -> str:
    if max(image.width(), image.height()) > MAX_SIDE:
        image = image.scaled(MAX_SIDE, MAX_SIDE,
                             Qt.KeepAspectRatio, Qt.SmoothTransformation)
    payload = QByteArray()
    buffer = QBuffer(payload)
    buffer.open(QIODevice.WriteOnly)
    has_alpha = image.hasAlphaChannel()
    if has_alpha:
        image.save(buffer, "PNG")
        suffix = ".png"
    else:
        image.save(buffer, "JPG", JPEG_QUALITY)
        suffix = ".jpg"
    buffer.close()
    data = bytes(payload)
    if not data:
        raise ValueError("图片编码失败")
    name = hashlib.sha1(data).hexdigest()[:16] + suffix
    dest = backgrounds_dir() / name
    if not dest.exists():
        dest.write_bytes(data)
    return name


def delete(name: str) -> None:
    path = resolve(name)
    if path:
        try:
            Path(path).unlink()
        except OSError:
            pass


def size_of(name: str) -> int:
    path = resolve(name)
    try:
        return Path(path).stat().st_size if path else 0
    except OSError:
        return 0


def migrate(settings) -> bool:
    """把旧版「单个绝对路径」平滑迁移成 1 个框；清理缺失文件。返回是否改动。"""
    changed = False
    names: list[str] = []
    for raw in list(getattr(settings, "backgrounds", []) or []):
        if not raw:
            continue
        path = Path(raw)
        if path.is_absolute():
            # 旧数据里的绝对路径：归档到 backgrounds/ 后改存文件名
            if not path.exists():
                changed = True
                continue
            try:
                name = import_image(path)
            except ValueError:
                changed = True
                continue
            changed = True
        else:
            if not resolve(raw):
                changed = True
                continue
            name = raw
        if name not in names:
            names.append(name)
    image = getattr(settings, "writing_image", "") or ""
    if image:
        path = Path(image)
        if path.is_absolute():
            if path.exists():
                try:
                    name = import_image(path)
                except ValueError:
                    name = ""
                if name and name not in names:
                    names.append(name)
                settings.writing_image = name
                changed = True
            else:
                settings.writing_image = ""
                changed = True
        elif not resolve(image):
            settings.writing_image = ""
            changed = True
        elif image not in names:
            # 只存了文件名、列表却为空（旧迁移中间态）：补成 1 个框
            names.append(image)
            changed = True
    if len(names) > MAX_BACKGROUNDS:
        names = names[:MAX_BACKGROUNDS]
        changed = True
    if names != list(getattr(settings, "backgrounds", []) or []):
        settings.backgrounds = names
        changed = True
    if getattr(settings, "writing_theme", "") == "custom" and not settings.writing_image:
        settings.writing_theme = "auto"
        changed = True
    return changed
