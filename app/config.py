# -*- coding: utf-8 -*-
"""全局配置：应用信息、数据目录与常量。

本软件是纯本地单机应用，所有数据都保存在本机 JSON 文件中，
不包含任何云同步、账号登录相关逻辑。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "催客写作"
APP_NAME_EN = "Trecal"   # 英文品牌展示名
APP_VERSION = "1.0.0"
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
        base = Path(os.environ.get("APPDATA") or Path.home())
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path.home() / ".config"
    return base / ORG_NAME


POINTER_FILE_NAME = "data_dir.txt"   # 记录自定义数据目录的指针文件


def _user_data_dir() -> Path:
    """计算本地数据目录。

    优先级：环境变量 MOJI_DATA_DIR > 指针文件 data_dir.txt > 系统默认目录。
    """
    custom = os.environ.get("MOJI_DATA_DIR")
    if custom:
        return Path(custom).expanduser().resolve()

    default = _default_data_dir()
    pointer = default / POINTER_FILE_NAME
    try:
        if pointer.exists():
            target = Path(pointer.read_text(encoding="utf-8").strip())
            if str(target) and target.is_absolute():
                return target
    except OSError:
        pass
    return default


def set_custom_data_dir(path: Path | str) -> None:
    """把自定义数据目录写入指针文件（重启后生效）。"""
    default = _default_data_dir()
    default.mkdir(parents=True, exist_ok=True)
    (default / POINTER_FILE_NAME).write_text(str(Path(path)), encoding="utf-8")


def data_dir_source() -> str:
    """当前数据目录来源：custom（指针/环境变量）或 default。"""
    if os.environ.get("MOJI_DATA_DIR"):
        return "custom"
    return "custom" if (_default_data_dir() / POINTER_FILE_NAME).exists() else "default"


DATA_DIR = _user_data_dir()
LIBRARY_FILE = DATA_DIR / "library.json"
SETTINGS_FILE = DATA_DIR / "settings.json"
BOOKS_DIR = DATA_DIR / "books"
HISTORY_DIR = DATA_DIR / "history"

# 每本书最多保留的历史快照数量（获得「坚持不懈」成就后 +2）
MAX_SNAPSHOTS = 5

# 两次“自动保存”快照之间的最小间隔（秒）——自动保存很频繁，
# 按此间隔去重；手动保存（Ctrl+S）与切章/关窗始终立即记录。
AUTO_SNAPSHOT_GAP = 10 * 60

# 自动保存防抖时间（毫秒）
AUTOSAVE_DELAY_MS = 1500
