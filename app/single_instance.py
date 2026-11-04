"""单实例保护：同一数据目录只允许一个进程写入。

两个进程共用 `Documents\\催客` 时，后保存的一方会把另一方刚写入的内容
整份覆盖（时间轴新事件消失、书籍正文回退）。这里用一个 Qt 锁文件把同目录
的第二实例挡在启动阶段，不修改任何数据、也不悄悄换路径。
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QLockFile


class InstanceLock:
    """基于 QLockFile 的数据目录写锁（进程退出时自动释放）。"""

    LOCK_NAME = ".trecal_instance.lock"

    def __init__(self, data_dir: Path | str) -> None:
        self.path = Path(data_dir) / self.LOCK_NAME
        self._lock = QLockFile(str(self.path))
        # 5 秒：正常占用期间 PID 存活不会被误判；崩溃/kill 后重启最多等 5 秒
        # 即可自动接管，不会像 0（永不陈旧）那样把用户永久锁在门外。
        self._lock.setStaleLockTime(5_000)

    def acquire(self) -> bool:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            return bool(self._lock.tryLock(0))
        except OSError:
            return False

    def holder_pid(self) -> int:
        try:
            return int(self._lock.lockInfo() or 0)
        except Exception:
            return 0

    def release(self) -> None:
        try:
            self._lock.unlock()
        except Exception:
            pass
