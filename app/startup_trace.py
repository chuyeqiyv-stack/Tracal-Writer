# -*- coding: utf-8 -*-
"""启动分段打点（默认关闭，仅测量时启用）。

启用方式：设置环境变量 MOJI_STARTUP_TRACE=1。
关闭时 mark() 只做一次布尔判断，不进入任何计时 / 字符串拼接热路径。
"""
from __future__ import annotations

import os
import time

ENABLED = os.environ.get("MOJI_STARTUP_TRACE") == "1"
_T0 = time.perf_counter()
_LAST = _T0
_MARKS: list[tuple[str, float]] = []


def reset() -> None:
    """开始新一轮测量（用于同一进程连测多次构造）。"""
    if not ENABLED:
        return
    global _T0, _LAST
    _T0 = time.perf_counter()
    _LAST = _T0
    _MARKS.clear()


def mark(name: str) -> None:
    """记录距上一次 mark 的耗时。"""
    if not ENABLED:
        return
    global _LAST
    now = time.perf_counter()
    _MARKS.append((name, (now - _LAST) * 1000.0))
    _LAST = now


def dump(prefix: str = "startup") -> None:
    """输出本轮全部分段（单行，便于收集）。"""
    if not ENABLED or not _MARKS:
        return
    total = (time.perf_counter() - _T0) * 1000.0
    body = " ".join(f"{name}={ms:.1f}" for name, ms in _MARKS)
    print(f"[{prefix}] total={total:.1f}ms {body}", flush=True)
