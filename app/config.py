# -*- coding: utf-8 -*-
"""全局配置：应用信息、数据目录与常量。

本软件是纯本地单机应用，所有数据都保存在本机 JSON 文件中，
不包含任何云同步、账号登录相关逻辑。
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

APP_NAME = "催客写作"
APP_NAME_EN = "Trecal"   # 英文品牌展示名
APP_VERSION = "1.4.0"
ORG_NAME = "MojiWriter"  # 数据目录沿用旧名，避免升级后找不到已有数据

# 默认正文字体（Windows 下若缺失会自动回退）
DEFAULT_FONT_FAMILY = "Microsoft YaHei"


def _resource_dir() -> Path:
    """资源目录：打包后位于 _MEIPASS，开发时位于项目根目录。"""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return Path(__file__).resolve().parent.parent


RESOURCE_DIR = _resource_dir()
ASSETS_DIR = RESOURCE_DIR / "assets"


def _default_data_dir() -> Path:
    """系统默认数据目录（不跟随用户自定义）。"""
    if sys.platform.startswith("win"):
        # 1.1 起：默认存到「文档\催客」，方便用户自己备份与查找
        return Path.home() / "Documents" / "催客"
    elif sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / ORG_NAME
    else:
        return Path.home() / ".config" / ORG_NAME


def _legacy_default_data_dir() -> Path:
    """1.0 及更早版本的默认数据目录（仅用于老数据迁移 / 指针兼容）。"""
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA") or Path.home())
        return base / ORG_NAME
    elif sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / ORG_NAME
    return Path.home() / ".config" / ORG_NAME


POINTER_FILE_NAME = "data_dir.txt"   # 记录自定义数据目录的指针文件


def _read_pointer(pointer: Path) -> Path | None:
    try:
        if pointer.exists():
            target = Path(pointer.read_text(encoding="utf-8").strip())
            if str(target) and target.is_absolute():
                return target
    except OSError:
        pass
    return None


def _migrate_legacy_data(target: Path, legacy: Path) -> None:
    """把旧默认目录的数据搬到新目录（仅当新目录尚无 library.json 时执行一次）。"""
    if target == legacy or not legacy.exists():
        return
    if (target / "library.json").exists() or (target / "settings.json").exists():
        return
    try:
        target.mkdir(parents=True, exist_ok=True)
        for item in legacy.iterdir():
            if item.name in {POINTER_FILE_NAME}:
                continue
            dest = target / item.name
            if dest.exists():
                continue
            if item.is_dir():
                shutil.copytree(item, dest, dirs_exist_ok=True)
            else:
                if not item.name.endswith(".tmp"):
                    shutil.copy2(item, dest)
    except OSError:
        # 迁移失败不阻塞启动：旧数据仍在原目录，用户可手动搬移或设置自定义路径
        pass


def _user_data_dir() -> Path:
    """计算本地数据目录。

    优先级：环境变量 MOJI_DATA_DIR > 指针文件 data_dir.txt（新目录或旧目录）
            > 系统默认目录（首次启动自动迁移旧版数据）。
    """
    custom = os.environ.get("MOJI_DATA_DIR")
    if custom:
        return Path(custom).expanduser().resolve()

    new_default = _default_data_dir()
    legacy_default = _legacy_default_data_dir()

    # 优先看新默认目录的指针，再看旧默认目录的指针（兼容 1.0 已设自定义目录的用户）
    for pointer_dir in (new_default, legacy_default):
        pointer = pointer_dir / POINTER_FILE_NAME
        target = _read_pointer(pointer)
        if target is not None:
            return target

    # 无自定义：老用户首次升级到 1.1 → 自动把旧目录数据搬进 Documents\催客
    _migrate_legacy_data(new_default, legacy_default)
    return new_default


def set_custom_data_dir(path: Path | str) -> None:
    """把自定义数据目录写入指针文件（重启后生效）。"""
    default = _default_data_dir()
    default.mkdir(parents=True, exist_ok=True)
    (default / POINTER_FILE_NAME).write_text(str(Path(path)), encoding="utf-8")


DATA_DIR = _user_data_dir()

# 每本书最多保留的历史快照数量（获得「坚持不懈」成就后 +2）
MAX_SNAPSHOTS = 5

# 两次“自动保存”快照之间的最小间隔（秒）——自动保存很频繁，
# 按此间隔去重；手动保存（Ctrl+S）与切章/关窗始终立即记录。
AUTO_SNAPSHOT_GAP = 10 * 60

# 自动保存防抖时间（毫秒）
AUTOSAVE_DELAY_MS = 1500
