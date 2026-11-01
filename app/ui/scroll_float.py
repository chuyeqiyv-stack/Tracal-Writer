# -*- coding: utf-8 -*-
"""隐形快速滚动滑块：给任意 QAbstractScrollArea 挂“滚轮时浮现 3 秒”的可拖拽滑块。

与正文编辑器同款体验：平时完全不显示滚动条，滚动滚轮时“=”形手柄浮现 3 秒，
拖动手柄可快速滚动；超时或松手后淡出（直接隐藏）。支持纵向 / 横向两种方向，
画布类控件（大纲 / 世界地图）可同时挂两个方向的滑块。
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtWidgets import QAbstractScrollArea

from app.ui.widgets import ScrollHandle


class ScrollFlasher(QObject):
    """为 QAbstractScrollArea 挂载隐形快速滚动滑块。

    - orientation: 滑块方向（Qt.Vertical 右缘 / Qt.Horizontal 底缘）；
    - inset: 手柄距视口右缘 / 底缘的间距；可传 callable 动态计算；
    - edge_margin: 手柄滑动轨道距视口上 / 左缘的留白。
    """

    def __init__(
        self,
        area: QAbstractScrollArea,
        orientation: Qt.Orientation = Qt.Vertical,
        *,
        inset=12,
        edge_margin: int = 20,
        parent=None,
    ) -> None:
        super().__init__(parent or area)
        self.area = area
        self.horizontal = orientation == Qt.Horizontal
        self.bar = (
            area.horizontalScrollBar() if self.horizontal else area.verticalScrollBar()
        )
        self._inset = inset
        self._edge = int(edge_margin)
        self.handle = ScrollHandle(area, orientation)
        self.handle.hide()
        self.handle.drag_to.connect(self._on_drag)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(3000)
        self._timer.timeout.connect(self._hide)
        area.viewport().installEventFilter(self)

    # ------------------------------------------------------------------
    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is self.area.viewport() and event.type() == QEvent.Wheel:
            self.flash()
        return False

    def _inset_value(self) -> int:
        return int(self._inset()) if callable(self._inset) else int(self._inset)

    def _track(self) -> tuple[int, int, int]:
        """返回 (轨道最小坐标, 轨道最大坐标, 横向时的 y / 纵向时的 x)。"""
        vp = self.area.viewport()
        origin = vp.mapTo(self.area, vp.rect().topLeft())
        h = self.handle
        if self.horizontal:
            top = origin.x() + self._edge
            bottom = origin.x() + vp.width() - self._edge - h.width()
            cross = origin.y() + vp.height() - h.height() - self._inset_value()
        else:
            top = origin.y() + self._edge
            bottom = origin.y() + vp.height() - self._edge - h.height()
            cross = origin.x() + vp.width() - h.width() - self._inset_value()
        return top, max(top, bottom), cross

    def flash(self) -> None:
        """滚轮滚动时显示滑块并同步到当前滚动位置，3 秒后自动隐藏。"""
        vp = self.area.viewport()
        if vp.isHidden() or not vp.isEnabled() or self.bar.maximum() <= 0:
            return
        top, bottom, cross = self._track()
        if bottom <= top:
            return
        ratio = self.bar.value() / self.bar.maximum()
        pos = int(top + ratio * (bottom - top))
        if self.horizontal:
            self.handle.move(pos, cross)
        else:
            self.handle.move(cross, pos)
        self.handle.raise_()
        self.handle.show()
        self._timer.start()

    def _on_drag(self, v: int) -> None:
        """拖动手柄：按比例映射到滚动条，实现快速滑动。"""
        top, bottom, _ = self._track()
        if bottom <= top or self.bar.maximum() <= 0:
            return
        v = max(top, min(int(v), bottom))
        if self.horizontal:
            self.handle.move(v, self.handle.y())
        else:
            self.handle.move(self.handle.x(), v)
        ratio = (v - top) / (bottom - top)
        self.bar.setValue(int(round(ratio * self.bar.maximum())))
        self._timer.start()   # 拖动期间保持显示

    def _hide(self) -> None:
        if not self.handle.dragging:
            self.handle.hide()

    def reset(self) -> None:
        """立即隐藏（如切换章节 / 清空页面时）。"""
        self._timer.stop()
        self.handle.hide()
