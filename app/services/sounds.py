"""提示音播放：统一的 WAV 异步播放封装（Windows winsound，其他平台静默跳过）。

成就提示音与手动保存音效共用 play_wav，避免两套 winsound 调用逻辑。
"""
from __future__ import annotations

from pathlib import Path

from app import config

# key -> (显示名, wav 文件名)；off 不播放
MANUAL_SAVE_SOUNDS: dict[str, tuple[str, str]] = {
    "off": ("关闭", ""),
    "wood": ("木质轻叩", "manual_save_wood.wav"),
    "bell": ("一声清铃", "manual_save_bell.wav"),
    "paper": ("纸页沙沙", "manual_save_paper.wav"),
    "dingdong": ("叮咚", "manual_save_dingdong.wav"),
    "lowhum": ("低鸣（昂）", "manual_save_lowhum.wav"),
}


def play_wav(path: Path) -> bool:
    """异步播放一个 wav；文件不存在 / 非 Windows / 播放失败时静默返回 False。"""
    path = Path(path)
    if not path.exists():
        return False
    try:
        import winsound
    except ImportError:   # 非 Windows 平台静默跳过
        return False
    try:
        winsound.PlaySound(
            str(path),
            winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT,
        )
    except (OSError, RuntimeError):
        return False
    return True


def manual_save_sound_path(key: str) -> Path | None:
    filename = MANUAL_SAVE_SOUNDS.get(str(key), ("", ""))[1]
    if not filename:
        return None
    return config.ASSETS_DIR / "sounds" / filename


def play_manual_save_sound(key: str) -> bool:
    path = manual_save_sound_path(key)
    return play_wav(path) if path is not None else False
