# -*- coding: utf-8 -*-
"""码字时速统计。

只统计“新增的有效字符”，连续两分钟没有输入就视为暂停，
避免把挂机时间算进时速。
"""
from __future__ import annotations

import time


class WritingSpeedTracker:
    """简单的写作速度追踪器（本次会话）。"""

    IDLE_GRACE = 30.0   # 停止写作 30 秒后才开始记录空闲时长

    def __init__(self) -> None:
        self.paused = False          # v3：时速面板右键暂停统计（不清零已有数据）
        self.reset()

    def reset(self) -> None:
        self.total_chars = 0          # 本次会话累计新增字数
        self.active_seconds = 0.0     # 有效写作时长（秒）
        self.idle_accumulated = 0.0   # 已定格的累计空闲秒数（不清零，逐段累加）
        self._last_ts: float | None = None

    def record(self, delta: int) -> None:
        """记录一次新增字数（由编辑器发出）。

        空闲规则：停止写作超过 30 秒的部分才计入空闲时长；
        重新打字时立刻停止记录但不清零，下一次空闲接着累加。
        暂停（paused=True）时完全不计入，已有数据保留。
        """
        if delta <= 0 or self.paused:
            return
        now = time.monotonic()
        if self._last_ts is not None:
            gap = now - self._last_ts
            if gap <= 120:  # 两分钟内视为连续写作
                self.active_seconds += gap
            # 宽限期（30 秒）之后的闲置才计入空闲
            if gap > self.IDLE_GRACE:
                self.idle_accumulated += gap - self.IDLE_GRACE
        self._last_ts = now
        self.total_chars += delta

    # ------------------------------------------------------------------
    @property
    def speed_per_hour(self) -> float:
        """本次会话的平均时速（字/小时）。"""
        seconds = max(self.active_seconds, 60.0 if self.total_chars else 0.0)
        if seconds <= 0:
            return 0.0
        return self.total_chars / seconds * 3600

    @property
    def elapsed_text(self) -> str:
        seconds = int(self.active_seconds)
        hours, remain = divmod(seconds, 3600)
        minutes, secs = divmod(remain, 60)
        if hours:
            return f"{hours} 小时 {minutes} 分"
        if minutes:
            return f"{minutes} 分 {secs} 秒"
        return f"{secs} 秒"

    @staticmethod
    def _clock_text(seconds: float) -> str:
        seconds = max(0, int(seconds))
        hours, remain = divmod(seconds, 3600)
        minutes, secs = divmod(remain, 60)
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"

    @property
    def status(self) -> str:
        """当前状态：未开始 / 计时中 / 已暂停。"""
        if self.total_chars <= 0 or self._last_ts is None:
            return "未开始"
        if time.monotonic() - self._last_ts > 120:
            return "已暂停"
        return "计时中"

    @property
    def idle_seconds(self) -> float:
        """累计空闲秒数。

        停止写作 30 秒后开始累加；重新打字即停止累加但不清零，
        下一次空闲延续上一次继续累计。
        """
        now = time.monotonic()
        if self._last_ts is None:
            return self.idle_accumulated
        ongoing = max(0.0, now - self._last_ts - self.IDLE_GRACE)
        return self.idle_accumulated + ongoing

    @property
    def idle_text(self) -> str:
        return self._clock_text(self.idle_seconds)
