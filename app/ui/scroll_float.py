# -*- coding: utf-8 -*-
"""隐形快速滚动滑块：给任意 QAbstractScrollArea 挂“滚动时浮现 3 秒”的可拖拽滑块。

与正文编辑器同款体验：平时完全不显示滚动条，滚轮 / 打字带动滚动 / 程序化滚动时
“=”形手柄淡入浮现，3 秒无活动后淡出；拖动手柄可快速滚动。支持纵向 / 横向两种
方向，画布类控件（大纲 / 地图）可同时挂两个方向的滑块。

实现要点（PySide6 6.12 兼容）：
- 滚轮走 viewport / 内嵌画布事件过滤；打字与程序化滚动走滚动条 valueChanged
  （经 40ms 防抖，避免逐像素重定位）；
- 拖拽期间 flash 只续期不挪手柄，避免与拖拽位置互相打架；
- 淡入淡出用构造时一次性挂好的 QGraphicsOpacityEffect（绝不动态挂卸）。
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, QPropertyAnimation, QEasingCurve
from PySide6.QtWidgets import QAbstractScrollArea, QGraphicsOpacityEffect

from app.ui.widgets import ScrollHandle

_FADE_IN_MS = 120
_FADE_OUT_MS = 220


def attach_flasher(
    area: QAbstractScrollArea,
    orientation: Qt.Orientation = Qt.Vertical,
    *,
    inset=6,
    edge_margin: int = 20,
    track_scroll: bool = True,
) -> "ScrollFlasher":
    """全站统一接线：常驻滚动条一律关闭 + 挂隐藏滑块（ScrollFlasher）。

    不留「两套并存」：原生纵 / 横常驻条一律关闭，只留滚动时淡入、3 秒后
    淡出的隐形滑块（画布类另再挂一个 Horizontal 滑块即可）。
    """
    area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    return ScrollFlasher(
        area, orientation, inset=inset, edge_margin=edge_margin,
        track_scroll=track_scroll,
    )


class ScrollFlasher(QObject):
    """为 QAbstractScrollArea 挂载隐形快速滚动滑块。

    - orientation: 滑块方向（Qt.Vertical 右缘 / Qt.Horizontal 底缘）；
    - inset: 手柄距视口右缘 / 底缘的间距；可传 callable 动态计算；
    - edge_margin: 手柄滑动轨道距视口上 / 左缘的留白；
    - track_scroll: 同时监听滚动条 valueChanged（打字带动滚动 / 程序化滚动也浮现）。
    """

    def __init__(
        self,
        area: QAbstractScrollArea,
        orientation: Qt.Orientation = Qt.Vertical,
        *,
        inset=12,
        edge_margin: int = 20,
        track_scroll: bool = False,
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
        self._enabled = True
        self.handle = ScrollHandle(area, orientation)
        self.handle.hide()
        self.handle.drag_to.connect(self._on_drag)

        # 淡入淡出（构造时挂一次，后续只动画 opacity，绝不动态挂卸 effect）
        self._opacity = QGraphicsOpacityEffect(area)
        self._opacity.setOpacity(0.0)
        self.handle.setGraphicsEffect(self._opacity)
        self._fade_in = QPropertyAnimation(self._opacity, b"opacity", self)
        self._fade_in.setDuration(_FADE_IN_MS)
        self._fade_in.setStartValue(0.0)
        self._fade_in.setEndValue(1.0)
        self._fade_in.setEasingCurve(QEasingCurve.OutCubic)
        self._fade_out = QPropertyAnimation(self._opacity, b"opacity", self)
        self._fade_out.setDuration(_FADE_OUT_MS)
        self._fade_out.setStartValue(1.0)
        self._fade_out.setEndValue(0.0)
        self._fade_out.setEasingCurve(QEasingCurve.InOutQuad)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(3000)
        self._timer.timeout.connect(self._begin_hide)

        # valueChanged 防抖（打字 / 程序化滚动）
        self._scroll_debounce = QTimer(self)
        self._scroll_debounce.setSingleShot(True)
        self._scroll_debounce.setInterval(40)
        self._scroll_debounce.timeout.connect(self.flash)
        if track_scroll:
            self.bar.valueChanged.connect(self._on_scroll_value)

        area.viewport().installEventFilter(self)
        # 内嵌画布（如时间轴 / 大纲的自绘画布）可能自己 accept 滚轮事件，
        # 滚轮就到不了 viewport，因此把画布也纳入监听（setWidget 后可再 watch 补挂）
        from PySide6.QtWidgets import QScrollArea

        self._widget = area.widget() if isinstance(area, QScrollArea) else None
        if self._widget is not None:
            self._widget.installEventFilter(self)

    def watch(self, widget) -> None:
        """补充监听内嵌画布（在 setWidget 之后调用，防止构造时 widget 尚未设置）。"""
        if widget is not None and widget is not self._widget:
            self._widget = widget
            widget.installEventFilter(self)

    def set_enabled(self, enabled: bool) -> None:
        """开关：关闭后不再浮现滑块（设置项「快速滚动滑块」使用）。"""
        self._enabled = bool(enabled)
        if not self._enabled:
            self._timer.stop()
            self._scroll_debounce.stop()
            self._fade_out.stop()
            self._opacity.setOpacity(0.0)
            self.handle.hide()

    # ------------------------------------------------------------------
    def _on_scroll_value(self, _value: int) -> None:
        if self._enabled and not self.handle.dragging:
            self._scroll_debounce.start()

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if self._enabled and event.type() == QEvent.Wheel and (
            obj is self.area.viewport() or obj is self._widget
        ):
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
        """滚动时显示滑块并同步到当前滚动位置，3 秒后自动淡出。"""
        if not self._enabled:
            return
        vp = self.area.viewport()
        if vp.isHidden() or not vp.isEnabled() or self.bar.maximum() <= 0:
            return
        # 拖拽中只续期，不重定位（否则会和手柄的鼠标位置互相抢）
        if not self.handle.dragging:
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
        if self.handle.isHidden():
            self._fade_out.stop()
            self.handle.show()
            self._fade_in.stop()
            self._fade_in.start()
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

    def _begin_hide(self) -> None:
        if self.handle.dragging:
            self._timer.start()
            return
        if not self.handle.isHidden():
            self._fade_in.stop()
            self._fade_out.start()
            QTimer.singleShot(_FADE_OUT_MS + 20, self._after_hide)

    def _after_hide(self) -> None:
        if not self.handle.dragging and self._opacity.opacity() <= 0.02:
            self.handle.hide()

    def reset(self) -> None:
        """立即隐藏（如切换章节 / 清空页面时）。"""
        self._timer.stop()
        self._scroll_debounce.stop()
        self._fade_out.stop()
        self._opacity.setOpacity(0.0)
        self.handle.hide()
