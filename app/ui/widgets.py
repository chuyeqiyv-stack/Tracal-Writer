# -*- coding: utf-8 -*-
"""通用自定义控件：背景容器、工具栏按钮、封面、色块、开关等。"""
from __future__ import annotations

import math
from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QEvent,
    QPointF,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    QTimer,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QPixmap,
    QCursor,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QButtonGroup,
    QColorDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.ui.theme import COVER_GRADIENTS, qc

_DEFAULT_COVER: QPixmap | None = None
_DEFAULT_COVER_LOADED = False


def _default_cover_pixmap() -> QPixmap | None:
    """默认封面（assets/default_cover.png 水墨山水），懒加载并缓存。"""
    global _DEFAULT_COVER, _DEFAULT_COVER_LOADED
    if not _DEFAULT_COVER_LOADED:
        _DEFAULT_COVER_LOADED = True
        from app.config import ASSETS_DIR

        path = ASSETS_DIR / "default_cover.png"
        if path.exists():
            pix = QPixmap(str(path))
            if not pix.isNull():
                _DEFAULT_COVER = pix
    return _DEFAULT_COVER


# 任务G：两级 LRU。源图解码缓存（书架频繁重建卡片时避免反复读盘解码）；
# 渲染缓存按 DIP 尺寸 × DPR 分桶（1x/1.5x/2x），paint 只取图不再 scale。
_COVER_SOURCE_MAX_DIM = 4000          # 与 storage 上传规范一致
_COVER_SRC_CACHE: "OrderedDict[tuple, QPixmap]" = OrderedDict()
_COVER_SRC_MAX_ITEMS = 12
_COVER_SRC_MAX_BYTES = 192 * 1024 * 1024
_COVER_RENDER_CACHE: "OrderedDict[tuple, QPixmap]" = OrderedDict()
_COVER_RENDER_MAX_BYTES = 64 * 1024 * 1024   # 任务G 选定的显示缓存上限：64MB
_COVER_STATS = {"src_hit": 0, "src_miss": 0, "render_hit": 0, "render_miss": 0}
_DEFAULT_COVER_KEY = ("default", 0)


def _pm_bytes(pm: QPixmap) -> int:
    """QPixmap 占用字节估算（ARGB32）。"""
    return max(0, pm.width()) * max(0, pm.height()) * 4


# 封面留边取色模式（常量开关）：
#   "mean" = 封面被留边那侧的边缘均值色（默认，与封面同调）
#   "card" = 卡片底色（跟随当前全局调色板 Window 色）
#   "blur" = 边缘轻虚化延展（取边缘窄带放大 + 收敛模糊，铺满留边）
COVER_PAD_MODE = "mean"


def cover_dpr_bucket(dpr: float) -> float:
    """DPR 向上归桶到 1.0 / 1.5 / 2.0（宁超勿欠，保证 1.25 等分数屏也锐利）。"""
    b = math.ceil(max(1.0, float(dpr or 1.0)) * 2.0) / 2.0
    return min(2.0, b)


def cover_cache_info() -> dict:
    """缓存实测：条目数、字节占用、命中 / 未命中计数。"""
    return {
        "src_items": len(_COVER_SRC_CACHE),
        "src_bytes": sum(_pm_bytes(p) for p in _COVER_SRC_CACHE.values()),
        "render_items": len(_COVER_RENDER_CACHE),
        "render_bytes": sum(_pm_bytes(p) for p in _COVER_RENDER_CACHE.values()),
        **_COVER_STATS,
    }


def cover_cache_clear() -> None:
    """云恢复 / 强制刷新时清空两级缓存（计数保留）。"""
    _COVER_SRC_CACHE.clear()
    _COVER_RENDER_CACHE.clear()


def _evict_lru(cache: "OrderedDict", max_bytes: int, max_items: int = 10**9) -> None:
    total = sum(_pm_bytes(p) for p in cache.values())
    while cache and (total > max_bytes or len(cache) > max_items):
        _k, old = cache.popitem(last=False)
        total -= _pm_bytes(old)


def _cover_source_key(cover_path: str) -> tuple | None:
    try:
        st = Path(cover_path).stat()
    except OSError:
        return None
    return ("file", str(cover_path), int(st.st_mtime))


def _cover_source(key: tuple) -> QPixmap | None:
    """取解码后的源图（LRU，上限 12 张 / 192MB；解码时 >4000px 收边保护内存）。"""
    if key[0] == "default":
        return _default_cover_pixmap()
    cached = _COVER_SRC_CACHE.get(key)
    if cached is not None:
        _COVER_SRC_CACHE.move_to_end(key)
        _COVER_STATS["src_hit"] += 1
        return cached
    _COVER_STATS["src_miss"] += 1
    loaded = QPixmap(key[1])
    if loaded.isNull():
        return None
    if max(loaded.width(), loaded.height()) > _COVER_SOURCE_MAX_DIM:
        loaded = loaded.scaled(
            _COVER_SOURCE_MAX_DIM, _COVER_SOURCE_MAX_DIM,
            Qt.KeepAspectRatio, Qt.SmoothTransformation)
    _COVER_SRC_CACHE[key] = loaded
    _COVER_SRC_CACHE.move_to_end(key)
    _evict_lru(_COVER_SRC_CACHE, _COVER_SRC_MAX_BYTES, _COVER_SRC_MAX_ITEMS)
    return loaded


def _cover_pad_color(pix: QPixmap, side: str) -> QColor:
    """留边底色：取图像该侧边缘 2px 边带的均值色，与封面同调、不会出现硬黑边。

    用均匀底色而不是拉伸边缘像素：后者会把边缘的碎光/图案拉成条纹（显得花哨）。
    """
    img = pix.toImage().convertToFormat(QImage.Format_RGB32)
    w, h = img.width(), img.height()
    r = g = b = n = 0
    if side in ("left", "right"):
        xs = (0, 1) if side == "left" else (max(0, w - 1), max(0, w - 2))
        step = max(1, h // 24)
        for x in xs:
            for y in range(0, h, step):
                c = img.pixelColor(x, y)
                r += c.red(); g += c.green(); b += c.blue(); n += 1
    else:
        ys = (0, 1) if side == "top" else (max(0, h - 1), max(0, h - 2))
        step = max(1, w // 24)
        for y in ys:
            for x in range(0, w, step):
                c = img.pixelColor(x, y)
                r += c.red(); g += c.green(); b += c.blue(); n += 1
    if n <= 0:
        return QColor(128, 128, 128)
    return QColor(r // n, g // n, b // n)


def _cover_fit_size(sw: int, sh: int, tw: int, th: int) -> tuple[int, int]:
    """contain 适配尺寸（设备像素）：等比放进 tw×th，短边至少 2px（极端长图不出 1px 细条）。"""
    scale = min(tw / sw, th / sh)
    dw = max(2 if tw >= 2 else 1, min(tw, int(round(sw * scale))))
    dh = max(2 if th >= 2 else 1, min(th, int(round(sh * scale))))
    return dw, dh


def _cover_blur_bg(fitted: QPixmap, tw: int, th: int, side: str) -> QPixmap:
    """边缘轻虚化延展：取被留边一侧的窄带，放大铺满槽位后做两级收敛模糊。

    只取窄带（而不是整图）避免侧边出现「重影缩略图」；两级缩小再放大把条带抹成
    柔和渐变，不会像直接拉伸边缘那样出现竖条纹。
    """
    w, h = fitted.width(), fitted.height()
    band = max(2, min(w, h) // 32)
    if side in ("left", "right"):
        x = 0 if side == "left" else max(0, w - band)
        strip = fitted.copy(x, 0, min(band, w), h)
    else:
        y = 0 if side == "top" else max(0, h - band)
        strip = fitted.copy(0, y, w, min(band, h))
    base = strip.scaled(tw, th, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    tiny = base.scaled(max(2, tw // 20), max(2, th // 20),
                       Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    return tiny.scaled(tw, th, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)


def render_cover(key: tuple, w_dip: int, h_dip: int, dpr: float) -> QPixmap | None:
    """按 DIP 目标尺寸 + DPR 分桶渲染封面（LRU 64MB）；不在 paint 内重复缩放。

    v4（封面 v4）：整图适配（contain）——等比缩放到能完整放进槽位，不裁切不拉伸；
    多余的一侧按 COVER_PAD_MODE 铺底（默认边缘均值色，无硬黑边）。
    清晰度不降：contain 后的实际像素尺寸仍按 DPR 桶取（窄边限制时另一侧取满）。
    """
    bucket = cover_dpr_bucket(dpr)
    mode = COVER_PAD_MODE if COVER_PAD_MODE in ("mean", "card", "blur") else "mean"
    pad_key = (QApplication.palette().color(QPalette.Window).name()
               if mode == "card" else "")
    cache_key = (key, int(w_dip), int(h_dip), bucket, mode, pad_key)
    cached = _COVER_RENDER_CACHE.get(cache_key)
    if cached is not None:
        _COVER_RENDER_CACHE.move_to_end(cache_key)
        _COVER_STATS["render_hit"] += 1
        return cached
    _COVER_STATS["render_miss"] += 1
    source = _cover_source(key)
    if source is None or source.isNull():
        return None
    tw, th = max(1, int(round(w_dip * bucket))), max(1, int(round(h_dip * bucket)))
    sw, sh = int(source.width()), int(source.height())
    if sw <= 0 or sh <= 0:
        return None
    # 整图适配（contain）：等比缩放到能完整放进槽位（不裁切、不拉伸）。
    # 尺寸全部按 DPR 桶的设备像素算，窄边受限时另一侧仍取满，清晰度不回退。
    dw, dh = _cover_fit_size(sw, sh, tw, th)
    fitted = source.scaled(dw, dh, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    # 留边：三种可选取色方式（默认均值色），均不提亮/加纹，绝不出现硬黑边
    side = "left" if dw < tw else "top"
    canvas = QPixmap(tw, th)
    if mode == "card":
        canvas.fill(QApplication.palette().color(QPalette.Window))
    elif mode == "blur":
        canvas.fill(_cover_pad_color(fitted, side))
        p_bg = QPainter(canvas)
        p_bg.drawPixmap(0, 0, _cover_blur_bg(fitted, tw, th, side))
        p_bg.end()
    else:
        canvas.fill(_cover_pad_color(fitted, side))
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
    ox, oy = (tw - dw) // 2, (th - dh) // 2      # 居中；整数像素，不留 1px 缝隙
    painter.drawPixmap(QRectF(ox, oy, dw, dh), fitted, QRectF(0, 0, dw, dh))
    painter.end()
    scaled = canvas
    scaled.setDevicePixelRatio(bucket)
    _COVER_RENDER_CACHE[cache_key] = scaled
    _evict_lru(_COVER_RENDER_CACHE, _COVER_RENDER_MAX_BYTES)
    return scaled


class ScrollHandle(QToolButton):
    """隐形快速滚动滑块的拖拽手柄（“=”形）：滚动滚轮时短暂显示，拖动可快速滚动。"""

    drag_to = Signal(int)   # 垂直: 手柄顶边 y；水平: 手柄左边 x（父控件坐标）

    def __init__(self, parent=None, orientation: Qt.Orientation = Qt.Vertical) -> None:
        super().__init__(parent)
        self.setObjectName("ScrollHandle")
        self.setText("=")
        self._horizontal = orientation == Qt.Horizontal
        self.setFixedSize(48, 34) if self._horizontal else self.setFixedSize(34, 48)
        self.setCursor(Qt.OpenHandCursor)
        self._dragging = False
        self._press_global = 0.0
        self._press_pos = 0

    @property
    def dragging(self) -> bool:
        return self._dragging

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._dragging = True
            pos = event.globalPosition()
            self._press_global = pos.x() if self._horizontal else pos.y()
            self._press_pos = self.x() if self._horizontal else self.y()
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._dragging:
            pos = event.globalPosition()
            delta = (pos.x() if self._horizontal else pos.y()) - self._press_global
            self.drag_to.emit(int(self._press_pos + delta))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton and self._dragging:
            self._dragging = False
            self.setCursor(Qt.OpenHandCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)


class BackgroundWidget(QWidget):
    """主窗口根容器：纯色背景，不再使用灰色原生外观。"""

    def __init__(self, theme: dict, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("Root")
        self._theme = theme

    def set_theme(self, theme: dict) -> None:
        self._theme = theme
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        from app.ui import surface

        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(self._theme["window"]))
        # 磨砂秩序：整窗统一的极淡颗粒；玻璃 / 默认 / 纸感保持纯色底
        if surface.is_matte():
            surface.paint_matte(painter, self.rect())
        painter.end()


class ToolbarButton(QAbstractButton):
    """顶部工具栏按钮：上图下字，悬停浅灰圆角 + 轻微放大动画。"""

    def __init__(self, label: str, theme: dict, parent=None) -> None:
        super().__init__(parent)
        self._label = label
        self._theme = theme
        self._icon = None
        self._icon_spec = None
        self._progress = 0.0
        self.setCheckable(False)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(58, 52)
        self.setFocusPolicy(Qt.NoFocus)

        self._anim = QVariantAnimation(self)
        self._anim.setDuration(150)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._on_anim)

    def _on_anim(self, value) -> None:
        self._progress = float(value)
        self.update()

    def set_theme(self, theme: dict) -> None:
        self._theme = theme
        self.update()

    def set_compact(self, compact: bool) -> None:
        """窄窗口紧凑模式：只收窄按钮宽度，高度 / hover / 字号保持不变。"""
        self.setFixedSize(48 if compact else 58, 52)

    def set_icon(self, icon) -> None:
        self._icon = icon
        self._icon_spec = None
        self.update()

    def set_icon_spec(self, factory, name: str, size: int = 20) -> None:
        """挂图标规范（工厂 + 名字）：自绘时按屏幕 DPR 与选中态取精确帧，不拉伸。"""
        self._icon = None
        self._icon_spec = (factory, name, int(size))
        self.update()

    def enterEvent(self, event) -> None:  # noqa: N802
        if self.isEnabled():
            self._animate(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._animate(0.0)
        super().leaveEvent(event)

    def _animate(self, target: float) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._progress)
        self._anim.setEndValue(target)
        self._anim.start()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        theme = self._theme

        hover = self._progress
        if self.isChecked() or hover > 0.01:
            bg = qc(theme["accent_soft"] if self.isChecked() else theme["hover"])
            bg.setAlphaF(0.65 + 0.35 * hover if not self.isChecked() else 1.0)
            painter.setPen(Qt.NoPen)
            painter.setBrush(bg)
            painter.drawRoundedRect(QRectF(2, 2, self.width() - 4, self.height() - 4), 8, 8)

        text_color = QColor(theme["topbar_text"])
        if not self.isEnabled():
            text_color.setAlpha(110)

        painter.save()
        scale = 1.0 + 0.06 * hover
        painter.translate(self.width() / 2, 15)
        painter.scale(scale, scale)
        if self._icon_spec is not None:
            factory, icon_name, icon_size = self._icon_spec
            screen = self.screen()
            dpr = screen.devicePixelRatio() if screen is not None else 1.0
            from PySide6.QtGui import QIcon as _QIcon

            pix = factory.pixmap(
                icon_name, icon_size,
                state=_QIcon.On if self.isChecked() else _QIcon.Off,
                disabled=not self.isEnabled(),
                dpr=dpr,
            )
            half = icon_size / 2
            painter.drawPixmap(QPointF(-half, -half), pix)
        elif self._icon is not None:
            pix = self._icon.pixmap(QSize(20, 20))
            painter.drawPixmap(-10, -10, pix)
        painter.restore()

        painter.setPen(QPen(text_color))
        font = QFont(self.font())
        font.setPixelSize(11)
        painter.setFont(font)
        painter.drawText(
            QRectF(0, self.height() - 21, self.width(), 16),
            Qt.AlignHCenter | Qt.AlignVCenter,
            self._label,
        )
        painter.end()


# ======================================================================
# 悬停玻璃质感
# 半透明叠加模拟毛玻璃（不做逐帧模糊）：顶部→中部高光渐变 + 内侧 1px
# 高光线 + 底部轻微暗部（玻璃厚度）。所有颜色走集中令牌，深浅各一套。
# ======================================================================
_ui_radius = 8.0          # 全局小圆角令牌，apply_theme 时由 theme.radius 同步


def set_ui_radius(radius: float) -> None:
    global _ui_radius
    _ui_radius = float(radius)


# 顺序：顶部高光 / 中部微光 / 底部暗部 / 内侧高光线 / 静止发丝边 / 悬停加强边
_GLASS_TOKENS = {
    False: (
        (255, 255, 255, 0.30), (255, 255, 255, 0.08), (16, 22, 34, 0.05),
        (255, 255, 255, 0.55), (30, 40, 60, 0.10), (24, 32, 48, 0.30),
    ),
    True: (
        (255, 255, 255, 0.12), (255, 255, 255, 0.04), (0, 0, 0, 0.22),
        (255, 255, 255, 0.26), (255, 255, 255, 0.10), (255, 255, 255, 0.30),
    ),
}


def _rgba(spec: tuple, scale: float = 1.0) -> QColor:
    return QColor(spec[0], spec[1], spec[2], max(0, min(255, int(spec[3] * 255 * scale))))


def paint_glass_overlay(
    painter: QPainter,
    rect,
    radius: float,
    t: float,
    dark: bool,
    focus: bool = False,
    on_accent: bool = False,
) -> None:
    """在已完成自绘的控件上层画玻璃质感。t=静止(0)→悬停(1)；focus 画独立焦点环。

    on_accent：画在品牌色填充的按钮上——静止不加中性描边，悬停用白色高亮边，
    焦点环也用白色（强调色描边在同色底上不可见）。
    """
    sheen_top, sheen_mid, bottom, hair, rest_border, hover_border = _GLASS_TOKENS[dark]
    r = QRectF(rect)

    painter.save()
    painter.setRenderHint(QPainter.Antialiasing, True)

    # 1) 顶部到中部的高光渐变 + 底部暗部（裁在圆角内）
    clip = QPainterPath()
    clip.addRoundedRect(r, radius, radius)
    painter.setClipPath(clip, Qt.ClipOperation.IntersectClip)
    gradient = QLinearGradient(0.0, r.top(), 0.0, r.bottom())
    gradient.setColorAt(0.0, _rgba(sheen_top, t))
    gradient.setColorAt(0.42, _rgba(sheen_mid, t))
    gradient.setColorAt(0.82, QColor(0, 0, 0, 0))
    gradient.setColorAt(1.0, _rgba(bottom, min(1.0, t * (2.0 if on_accent else 1.0))))
    painter.fillRect(r, QBrush(gradient))
    painter.restore()

    # 2) 边框：中性底上静止发丝弱边→悬停加强；强调色按钮静止无描边、悬停白色亮边
    if on_accent:
        border = QColor(255, 255, 255, int(165 * t))
    else:
        border = QColor(
            int(rest_border[0] + (hover_border[0] - rest_border[0]) * t),
            int(rest_border[1] + (hover_border[1] - rest_border[1]) * t),
            int(rest_border[2] + (hover_border[2] - rest_border[2]) * t),
            int((rest_border[3] + (hover_border[3] - rest_border[3]) * t) * 255),
        )
    pen = QPen(border)
    pen.setWidthF(1.0)
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    border_path = QPainterPath()
    border_path.addRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), max(0.0, radius - 0.5),
                               max(0.0, radius - 0.5))
    painter.drawPath(border_path)

    if t > 0.0:
        # 3) 内侧 1px 高光线（玻璃厚边）
        hi = QPen(_rgba(hair, t))
        hi.setWidthF(1.0)
        painter.setPen(hi)
        inner = QPainterPath()
        inner.addRoundedRect(r.adjusted(1.5, 1.5, -1.5, -1.5), max(0.0, radius - 1.5),
                             max(0.0, radius - 1.5))
        painter.drawPath(inner)
        # 4) 底部一条轻暗部，做出玻璃厚度
        bottom_edge = (0, 0, 0, 0.14) if on_accent else bottom
        painter.setPen(QPen(_rgba(bottom_edge, min(1.0, t * 2.2))))
        y = r.bottom() - 1.0
        painter.drawLine(QPointF(r.left() + radius, y), QPointF(r.right() - radius, y))

    if focus:
        # 键盘焦点环：强调色（中性底）或白色（强调色底）实线，与悬停高光明显区分
        if on_accent:
            ring_color = QColor(255, 255, 255, 228)
        else:
            ring_color = QColor(QApplication.palette().color(QPalette.Highlight))
            ring_color.setAlpha(215)
        ring = QPen(ring_color)
        ring.setWidthF(1.8)
        painter.setPen(ring)
        focus_path = QPainterPath()
        focus_path.addRoundedRect(r.adjusted(1.0, 1.0, -1.0, -1.0), max(0.0, radius - 1.0),
                                  max(0.0, radius - 1.0))
        painter.drawPath(focus_path)
    painter.restore()


def _paint_selected(painter: QPainter, rect, radius: float, t: float) -> None:
    """选中态：极淡强调色底 + 左侧 2px 强调色竖条（比 hover 明显更强一档）。"""
    if t <= 0.0:
        return
    painter.save()
    painter.setPen(Qt.PenStyle.NoPen)
    accent = QColor(QApplication.palette().color(QPalette.ColorRole.Highlight))
    clip = QPainterPath()
    clip.addRoundedRect(QRectF(rect), radius, radius)
    painter.setClipPath(clip, Qt.ClipOperation.IntersectClip)
    wash = QColor(accent)
    wash.setAlpha(int(26 * t))
    painter.fillRect(rect, wash)
    bar = QColor(accent)
    bar.setAlpha(int(255 * 0.55 * t))
    painter.setBrush(bar)
    painter.drawRect(QRectF(rect.left(), rect.top() + 1.0, 2.5, rect.height() - 2.0))
    painter.restore()


class _GlassOverlay(QWidget):
    """盖在卡片上的透明状态层：不接收鼠标；hover 玻璃强度 / selected 选中态各自自绘。"""

    def __init__(self, parent: QWidget, radius: float = 8.0) -> None:
        super().__init__(parent)
        self._glass = 0.0
        self._selected = 0.0
        self.radius = radius
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.show()
        self.raise_()

    def get_glass(self) -> float:
        return self._glass

    def set_glass(self, value: float) -> None:
        v = max(0.0, min(1.0, float(value)))
        if v != self._glass:
            self._glass = v
            self.update()

    glass = Property(float, get_glass, set_glass)

    def get_selected(self) -> float:
        return self._selected

    def set_selected(self, value: float) -> None:
        v = max(0.0, min(1.0, float(value)))
        if v != self._selected:
            self._selected = v
            self.update()

    selected = Property(float, get_selected, set_selected)

    def paintEvent(self, event) -> None:  # noqa: N802
        from app.ui import surface
        from app.ui.motion import app_is_dark

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        # 玻璃 / 磨砂的覆层圆角跟随风格 radius_lg；默认 / 纸感保持历史 8px 不动
        radius = surface.card_radius() if surface.kind() != "plain" else self.radius
        if surface.is_glass():
            # 选中态由 _paint_selected 接管，不再叠玻璃高光（避免强调色被冲淡）
            if not self._is_selected_state():
                surface.paint_liquid_overlay(
                    painter, self.rect(), radius, self._glass,
                    getattr(self, "_sheen_pos", None))
        elif surface.is_matte():
            clip = QPainterPath()
            clip.addRoundedRect(QRectF(self.rect()), radius, radius)
            painter.setClipPath(clip, Qt.ClipOperation.IntersectClip)
            surface.paint_matte(painter, self.rect())
        else:
            paint_glass_overlay(painter, self.rect(), radius, self._glass, app_is_dark())
        _paint_selected(painter, self.rect(), radius, self._selected)
        painter.end()

    def _is_selected_state(self) -> bool:
        return self._selected > 0.99


class CardFrame(QFrame):
    """卡片基类：rest / hover / selected 三态柔和阴影梯级 + 玻璃高光 + 选中竖条。

    重要：PySide6 6.12 下在 enter/leave 里挂/卸 QGraphicsDropShadowEffect
    或做几何动画，控件随后被隐藏会访问冲突闪退。这里只在构造时挂一次
    effect，状态切换只平滑改变 effect 的 offset / blurRadius / color 属性。
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._lift_enabled = False
        self._is_selected = False
        self._hovered = False
        self._offset_anim: QPropertyAnimation | None = None
        self._blur_anim: QPropertyAnimation | None = None
        self._color_anim: QPropertyAnimation | None = None
        self._glass_anim: QPropertyAnimation | None = None
        self._sel_anim: QPropertyAnimation | None = None
        self._overlay: _GlassOverlay | None = None

    def enable_lift(self, blur: int = 0, alpha: int = 0, dy: int = 0) -> None:
        """挂上常驻阴影（rest 档）并启用 hover/selected 反馈（幂等，只挂一次）。

        历史参数 blur/alpha/dy 已废弃，三档阴影统一取自 motion.CARD_ELEVATION。
        """
        from app.ui.motion import CARD_ELEVATION

        if self._lift_enabled:
            # 重复调用不得重建 effect：旧 effect 销毁会让已缓存的属性动画指向
            # 已删除对象而失效（阴影永远停在 rest 档）
            if self._overlay is not None:
                self._overlay.setGeometry(self.rect())
                self._overlay.raise_()
            return
        # 液态玻璃风：卡片本体不加外阴影（仅浮层有 0 8 24 阴影）；其余风格保留梯级阴影
        from app.ui import surface

        if not surface.is_glass():
            b, d, a = CARD_ELEVATION["rest"]
            apply_shadow(self, blur=int(b), alpha=int(a), dy=int(d))
        self._lift_enabled = True
        self.setAttribute(Qt.WA_Hover, True)
        # 玻璃覆层（构造一次常驻；只改属性，绝不动态挂卸 effect）
        if self._overlay is None:
            self._overlay = _GlassOverlay(self, 8.0)
            if surface.is_glass():
                # 高光斑 180ms 跟随指针：overlay 自绘，卡片本体负责命中
                surface.attach_sheen(self._overlay, self)
        self._overlay.setGeometry(self.rect())
        self._overlay.raise_()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._overlay is not None:
            self._overlay.setGeometry(self.rect())
            self._overlay.raise_()

    def childEvent(self, event) -> None:  # noqa: N802
        """子控件（封面/文字）上方的悬停也要触发行卡上浮，统一装事件过滤。"""
        super().childEvent(event)
        if self._lift_enabled and event.added() and event.child().isWidgetType():
            event.child().installEventFilter(self)

    def set_selected(self, selected: bool) -> None:
        """选中态：更强一档扩散阴影 + 左侧强调竖条。"""
        if selected == self._is_selected:
            return
        self._is_selected = selected
        if self._lift_enabled:
            self._retarget_shadow()
        if self._overlay is not None:
            if self._sel_anim is None:
                self._sel_anim = QPropertyAnimation(self._overlay, b"selected", self)
                self._sel_anim.setDuration(180)
                self._sel_anim.setEasingCurve(QEasingCurve.OutCubic)
            self._sel_anim.stop()
            self._sel_anim.setStartValue(self._overlay.selected)
            self._sel_anim.setEndValue(1.0 if selected else 0.0)
            self._sel_anim.start()

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        etype = event.type()
        if self._lift_enabled and etype == QEvent.Type.Enter:
            self._hovered = True
            self._retarget_shadow()
            self._animate_glass(True)
        elif self._lift_enabled and etype == QEvent.Type.Leave:
            # 可能只是从子控件移到卡片另一处，先判断光标是否仍在卡片内
            inside = self.rect().contains(self.mapFromGlobal(QCursor.pos()))
            self._hovered = inside
            self._retarget_shadow()
            self._animate_glass(inside)
        return super().eventFilter(obj, event)

    def _retarget_shadow(self) -> None:
        """按 selected > hover > rest 取当前阴影档（柔和扩散梯级，令牌来自 motion）。"""
        from app.ui.motion import CARD_ELEVATION

        if not self._lift_enabled:
            return
        effect = self.graphicsEffect()
        if effect is None:
            return
        state = "selected" if self._is_selected else ("hover" if self._hovered else "rest")
        blur, dy, alpha = CARD_ELEVATION[state]
        dark = QApplication.palette().color(QPalette.ColorRole.Window).lightness() < 128
        color = QColor(15, 18, 30, int(alpha)) if not dark else QColor(0, 0, 0, int(alpha))

        if self._offset_anim is None:
            self._offset_anim = QPropertyAnimation(effect, b"offset", self)
            self._offset_anim.setDuration(180)
            self._offset_anim.setEasingCurve(QEasingCurve.OutCubic)
        if self._blur_anim is None:
            self._blur_anim = QPropertyAnimation(effect, b"blurRadius", self)
            self._blur_anim.setDuration(180)
            self._blur_anim.setEasingCurve(QEasingCurve.OutCubic)
        if self._color_anim is None:
            self._color_anim = QPropertyAnimation(effect, b"color", self)
            self._color_anim.setDuration(180)
            self._color_anim.setEasingCurve(QEasingCurve.OutCubic)

        self._offset_anim.stop()
        self._offset_anim.setStartValue(effect.offset())
        self._offset_anim.setEndValue(QPointF(0.0, dy))
        self._offset_anim.start()
        self._blur_anim.stop()
        self._blur_anim.setStartValue(effect.blurRadius())
        self._blur_anim.setEndValue(float(blur))
        self._blur_anim.start()
        self._color_anim.stop()
        self._color_anim.setStartValue(effect.color())
        self._color_anim.setEndValue(color)
        self._color_anim.start()

    def _animate_glass(self, entered: bool) -> None:
        if self._overlay is None or self._is_selected:
            return
        if self._glass_anim is None:
            self._glass_anim = QPropertyAnimation(self._overlay, b"glass", self)
            self._glass_anim.setDuration(180)
            self._glass_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._glass_anim.stop()
        self._glass_anim.setStartValue(self._overlay.glass)
        self._glass_anim.setEndValue(1.0 if entered else 0.0)
        self._glass_anim.start()

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hovered = True
        self._retarget_shadow()
        self._animate_glass(True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hovered = False
        self._retarget_shadow()
        self._animate_glass(False)
        super().leaveEvent(event)


class GlassButton(QPushButton):
    """主页 CTA 按钮：悬停时叠加半透明玻璃层（高光 / 高光线 / 底部暗部）。

    - 未悬停：仅 QSS 原色，无额外描边阴影；
    - 悬停：180ms ease-out 叠玻璃层，配合 QSS 的背景提亮；
    - 键盘焦点：画强调色实线焦点环，与悬停高光互不混淆。
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._glass = 0.0
        self._glass_anim: QPropertyAnimation | None = None
        self._blur_anim: QPropertyAnimation | None = None
        self._color_anim: QPropertyAnimation | None = None
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        # 轻阴影：构造时挂一次（绝不动态挂卸），静止时全透明、悬停时缓出
        self._shadow = QGraphicsDropShadowEffect(self)
        self._shadow.setBlurRadius(8.0)
        self._shadow.setOffset(0.0, 2.0)
        self._shadow.setColor(QColor(15, 18, 30, 0))
        self.setGraphicsEffect(self._shadow)

    def get_glass(self) -> float:
        return self._glass

    def set_glass(self, value: float) -> None:
        v = max(0.0, min(1.0, float(value)))
        if v != self._glass:
            self._glass = v
            self.update()

    glass = Property(float, get_glass, set_glass)

    def _animate_glass(self, entered: bool) -> None:
        if self._glass_anim is None:
            self._glass_anim = QPropertyAnimation(self, b"glass", self)
            self._glass_anim.setDuration(180)
            self._glass_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._glass_anim.stop()
        self._glass_anim.setStartValue(self._glass)
        self._glass_anim.setEndValue(1.0 if entered else 0.0)
        self._glass_anim.start()

        # 同步轻阴影（取卡片 hover 档令牌，静止透明、悬停柔影）
        from app.ui.motion import CARD_ELEVATION, app_is_dark

        h_blur, _h_dy, h_alpha = CARD_ELEVATION["hover"]
        alpha = min(120, int(h_alpha) + (24 if app_is_dark() else 8))
        if self._blur_anim is None:
            self._blur_anim = QPropertyAnimation(self._shadow, b"blurRadius", self)
            self._blur_anim.setDuration(180)
            self._blur_anim.setEasingCurve(QEasingCurve.OutCubic)
        if self._color_anim is None:
            self._color_anim = QPropertyAnimation(self._shadow, b"color", self)
            self._color_anim.setDuration(180)
            self._color_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._blur_anim.stop()
        self._blur_anim.setStartValue(self._shadow.blurRadius())
        self._blur_anim.setEndValue(h_blur if entered else 8.0)
        self._blur_anim.start()
        self._color_anim.stop()
        self._color_anim.setStartValue(QColor(self._shadow.color()))
        self._color_anim.setEndValue(QColor(15, 18, 30, alpha if entered else 0))
        self._color_anim.start()

    def enterEvent(self, event) -> None:  # noqa: N802
        self._animate_glass(True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._animate_glass(False)
        super().leaveEvent(event)

    def focusInEvent(self, event) -> None:  # noqa: N802
        self.update()
        super().focusInEvent(event)

    def focusOutEvent(self, event) -> None:  # noqa: N802
        self.update()
        super().focusOutEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        from app.ui.motion import app_is_dark

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        paint_glass_overlay(
            painter, self.rect(), _ui_radius, self._glass,
            app_is_dark(), self.hasFocus(), on_accent=True,
        )
        painter.end()


class ToggleSwitch(QAbstractButton):
    """自绘的开关按钮，带滑动动画。"""

    def __init__(self, checked: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(44, 24)
        self._progress = 1.0 if checked else 0.0

        self._anim = QVariantAnimation(self)
        self._anim.setDuration(150)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._on_anim)
        self.toggled.connect(self._on_toggled)

    def _on_anim(self, value) -> None:
        self._progress = float(value)
        self.update()

    def _on_toggled(self, checked: bool) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._progress)
        self._anim.setEndValue(1.0 if checked else 0.0)
        self._anim.start()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        pal = self.palette()

        off = QColor(pal.color(QPalette.Mid))
        off.setAlpha(110 if self.isEnabled() else 60)
        on = pal.color(QPalette.Highlight)
        track = QColor(
            int(off.red() + (on.red() - off.red()) * self._progress),
            int(off.green() + (on.green() - off.green()) * self._progress),
            int(off.blue() + (on.blue() - off.blue()) * self._progress),
            int(off.alpha() + (255 - off.alpha()) * self._progress),
        )

        rect = QRectF(1, 1, self.width() - 2, self.height() - 2)
        painter.setPen(Qt.NoPen)
        painter.setBrush(track)
        painter.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)

        knob_radius = rect.height() / 2 - 3
        left = rect.left() + 3 + knob_radius
        right = rect.right() - 3 - knob_radius
        center_x = left + (right - left) * self._progress
        painter.setBrush(QColor(255, 255, 255, 240))
        painter.drawEllipse(QPointF(center_x, rect.center().y()), knob_radius, knob_radius)
        painter.end()


class SegmentedControl(QWidget):
    """分段选择控件（例如“天 / 月 / 年”）。"""

    value_changed = Signal(str)

    def __init__(self, options: list[tuple[str, str]], value: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("SegContainer")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._value = value
        self._buttons: list[QPushButton] = []

        layout = QHBoxLayout(self)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(2)

        group = QButtonGroup(self)
        group.setExclusive(True)
        for text, key in options:
            button = QPushButton(text)
            button.setObjectName("SegButton")
            button.setCheckable(True)
            button.setProperty("seg_value", key)
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(lambda _checked=False, b=button: self._on_clicked(b))
            group.addButton(button)
            layout.addWidget(button)
            self._buttons.append(button)
        self.set_value(value, emit=False)

    def _on_clicked(self, button: QPushButton) -> None:
        key = str(button.property("seg_value"))
        if key == self._value:
            return
        self._value = key
        self.value_changed.emit(key)

    def value(self) -> str:
        return self._value

    def set_value(self, value: str, emit: bool = False) -> None:
        self._value = value
        for button in self._buttons:
            button.setChecked(str(button.property("seg_value")) == value)
        if emit:
            self.value_changed.emit(value)


class SettingRow(QWidget):
    """设置项一行：左侧标题，右侧控件，可选下方说明。"""

    def __init__(self, title: str, control: QWidget, hint: str = "", parent=None) -> None:
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(3)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        label = QLabel(title)
        label.setObjectName("RowTitle")
        row.addWidget(label)
        row.addStretch(1)
        row.addWidget(control)
        outer.addLayout(row)

        if hint:
            hint_label = QLabel(hint)
            hint_label.setObjectName("DrawerHint")
            hint_label.setWordWrap(True)
            outer.addWidget(hint_label)


class HLine(QFrame):
    """1px 分隔线。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("HLine")
        self.setFixedHeight(1)


class ColorButton(QPushButton):
    """显示色块与十六进制值的颜色选择按钮。空值表示跟随主题。"""

    value_changed = Signal(str)

    def __init__(self, color: str = "", parent=None) -> None:
        super().__init__(parent)
        self._color = color
        self.setFixedHeight(28)
        self.setMinimumWidth(96)
        self.setCursor(Qt.PointingHandCursor)
        self.clicked.connect(self._pick)

    def set_value(self, color: str) -> None:
        self._color = color or ""
        self.update()

    def value(self) -> str:
        return self._color

    def _pick(self) -> None:
        initial = QColor(self._color) if self._color else QColor("#FFFFFF")
        color = QColorDialog.getColor(initial, self, "选择颜色")
        if color.isValid():
            self._color = color.name().upper()
            self.update()
            self.value_changed.emit(self._color)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        pal = self.palette()
        rect = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        painter.setPen(QPen(QColor(pal.color(QPalette.Mid).name()), 1))
        painter.setBrush(QColor(pal.color(QPalette.Base)))
        painter.drawRoundedRect(rect, 6, 6)

        dot = QRectF(8, 7, 14, 14)
        painter.setPen(QPen(QColor(pal.color(QPalette.Mid).name()), 1))
        if self._color:
            painter.setBrush(QColor(self._color))
        else:
            painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(dot)

        painter.setPen(QPen(QColor(pal.color(QPalette.WindowText).name())))
        font = QFont(self.font())
        font.setPixelSize(11)
        painter.setFont(font)
        painter.drawText(
            QRectF(28, 0, self.width() - 34, self.height()),
            Qt.AlignVCenter | Qt.AlignLeft,
            self._color or "跟随主题",
        )
        painter.end()


class AvatarView(QWidget):
    """圆形头像：有图片时显示图片，否则显示昵称首字。"""

    def __init__(self, size: int = 34, parent=None) -> None:
        super().__init__(parent)
        self._pixmap: QPixmap | None = None
        self._text = "墨"
        self.setFixedSize(size, size)

    def set_data(self, image_path: str, nickname: str) -> None:
        self._text = (nickname.strip()[:1] or "墨")
        if image_path:
            pix = QPixmap(image_path)
            if not pix.isNull():
                # 内存优化：头像显示尺寸很小，加载时先降采样，避免原图常驻内存
                if pix.width() > 128 or pix.height() > 128:
                    pix = pix.scaled(128, 128, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                self._pixmap = pix
            else:
                self._pixmap = None
        else:
            self._pixmap = None
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(1, 1, self.width() - 2, self.height() - 2)
        path = QPainterPath()
        path.addEllipse(rect)
        painter.setClipPath(path)

        if self._pixmap is not None:
            scaled = self._pixmap.scaled(
                int(rect.width()),
                int(rect.height()),
                Qt.KeepAspectRatioByExpanding,
                Qt.SmoothTransformation,
            )
            x = (scaled.width() - rect.width()) / 2
            y = (scaled.height() - rect.height()) / 2
            painter.drawPixmap(rect.topLeft(), scaled, QRectF(x, y, rect.width(), rect.height()))
        else:
            gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
            accent = self.palette().color(QPalette.Highlight)
            gradient.setColorAt(0, accent.lighter(125))
            gradient.setColorAt(1, accent.darker(125))
            painter.fillRect(rect, gradient)
            painter.setPen(QPen(QColor(255, 255, 255, 235)))
            font = QFont(self.font())
            font.setPixelSize(max(12, int(self.height() * 0.42)))
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(rect, Qt.AlignCenter, self._text)
        painter.end()


class CoverView(QWidget):
    """书籍封面：3:4 比例，支持自定义图片、默认底纹与「新作品」角标。"""

    def __init__(self, width: int = 150, height: int = 200, parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(width, height)
        self._title = ""
        self._cover_key: tuple | None = None   # ("file", path, mtime) / 默认封面 / None
        self._gradient = COVER_GRADIENTS[0]
        self._new = False

    def set_data(self, title: str, cover_path: str, cover_index: int, is_new: bool) -> None:
        self._title = title
        self._gradient = COVER_GRADIENTS[int(cover_index) % len(COVER_GRADIENTS)]
        self._new = is_new
        self._rendered = None
        self._cover_key = _cover_source_key(cover_path) if cover_path else None
        self.update()

    def _paint_cover(self, painter: QPainter, key: tuple) -> bool:
        """按 DPR 分桶取渲染图（全局 LRU + 实例快路径），返回是否成功。"""
        dpr = float(self.devicePixelRatioF() or 1.0)
        bucket = cover_dpr_bucket(dpr)
        cached = getattr(self, "_rendered", None)
        if cached is not None and cached[0] == (key, self.width(), self.height(), bucket):
            pm = cached[1]
        else:
            pm = render_cover(key, self.width(), self.height(), dpr)
            if pm is None:
                return False
            self._rendered = ((key, self.width(), self.height(), bucket), pm)
        # 目标必须给矩形（QPointF 目标点在 pixmap DPR≠控件 DPR 时会错尺寸留白条）。
        # 源矩形必须用 pixmap 的设备像素：Qt 的 sourceRect 按 pixmap 像素解释，
        # 若传 DIP（width()/devicePixelRatio）在 DPR 桶 >1 时只会取画布左上角
        # 1/bucket，把封面放大并裁掉右侧与底部（125%/150%/200% 缩放下的裁切真因）。
        painter.drawPixmap(QRectF(0, 0, self.width(), self.height()), pm,
                           QRectF(0, 0, pm.width(), pm.height()))
        return True

    def _paint_fallback_cover(self, painter: QPainter, rect: QRectF) -> None:
        """默认封面图缺失时的兜底：渐变 + 斜线纹理 + 书名。"""
        c1, c2 = self._gradient
        gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
        gradient.setColorAt(0, QColor(c1))
        gradient.setColorAt(1, QColor(c2))
        painter.fillRect(rect, gradient)

        painter.save()
        painter.setClipRect(rect)
        pen = QPen(QColor(255, 255, 255, 26))
        pen.setWidth(6)
        painter.setPen(pen)
        step = 22
        start = -self.height()
        while start < self.width() + self.height():
            painter.drawLine(
                QPointF(start, self.height()),
                QPointF(start + self.height(), 0),
            )
            start += step
        painter.restore()

        painter.setPen(QPen(QColor(255, 255, 255, 232)))
        font = QFont(self.font())
        font.setPixelSize(max(14, int(self.width() * 0.12)))
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(
            rect.adjusted(12, 12, -12, -12),
            Qt.AlignCenter | Qt.TextWordWrap,
            self._title,
        )

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        rect = QRectF(0, 0, self.width(), self.height())
        # 书籍造型：上圆角（贴合卡片顶部），下直角
        radius = 8.0
        path = QPainterPath()
        path.moveTo(0, rect.bottom())
        path.lineTo(0, radius)
        path.quadTo(0, 0, radius, 0)
        path.lineTo(rect.right() - radius, 0)
        path.quadTo(rect.right(), 0, rect.right(), radius)
        path.lineTo(rect.right(), rect.bottom())
        path.closeSubpath()
        painter.setClipPath(path)

        drawn = False
        if self._cover_key is not None:
            # 自定义封面：按 DPR 分桶渲染；文件失效时逐级回退
            drawn = self._paint_cover(painter, self._cover_key)
        if not drawn:
            # 默认封面：水墨山水图（assets/default_cover.png），同样走 DPR 管线
            if _default_cover_pixmap() is not None:
                drawn = self._paint_cover(painter, _DEFAULT_COVER_KEY)
            if not drawn:
                self._paint_fallback_cover(painter, rect)

        # ---- 书籍质感 ----
        # 左缘书脊：深色渐变 + 内折痕高光
        spine_w = max(9.0, rect.width() * 0.055)
        spine = QLinearGradient(rect.topLeft(), rect.topLeft() + QPointF(spine_w + 3, 0))
        spine.setColorAt(0.0, QColor(0, 0, 0, 96))
        spine.setColorAt(0.45, QColor(0, 0, 0, 34))
        spine.setColorAt(0.82, QColor(255, 255, 255, 40))
        spine.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.fillRect(QRectF(rect.left(), rect.top(), spine_w + 3, rect.height()), spine)
        # 右缘书页：两道亮线夹一道暗缝，模拟纸页叠边
        page_pen = QPen(QColor(255, 255, 255, 165), 1.2)
        painter.setPen(page_pen)
        painter.drawLine(QPointF(rect.right() - 4.0, rect.top() + 2), QPointF(rect.right() - 4.0, rect.bottom() - 1))
        painter.drawLine(QPointF(rect.right() - 1.5, rect.top() + 2), QPointF(rect.right() - 1.5, rect.bottom() - 1))
        painter.setPen(QPen(QColor(0, 0, 0, 46), 1.0))
        painter.drawLine(QPointF(rect.right() - 2.8, rect.top() + 3), QPointF(rect.right() - 2.8, rect.bottom() - 2))
        # 顶部轻微压暗，增强"封面受光"立体感
        top_shade = QLinearGradient(rect.topLeft(), rect.topLeft() + QPointF(0, 26))
        top_shade.setColorAt(0.0, QColor(0, 0, 0, 34))
        top_shade.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.fillRect(QRectF(rect.left(), rect.top(), rect.width(), 26), top_shade)

        # 「新作品」斜角缎带：直接压在封面左上角（跨角斜向，参考主流书城样式）
        if self._new:
            band_h = max(11.0, rect.height() * 0.068)
            band_d = min(rect.width(), rect.height()) * 0.19
            band_len = band_d * 2.0 + rect.width() * 0.34
            painter.save()
            painter.translate(rect.left(), rect.top())
            painter.rotate(-45)
            # 局部 +y 在设备坐标里指向封面内部（角平分线方向），把缎带推进去
            painter.translate(0.0, band_d)
            band = QRectF(-band_len / 2, -band_h / 2, band_len, band_h)
            ribbon_grad = QLinearGradient(band.topLeft(), band.bottomLeft())
            ribbon_grad.setColorAt(0.0, QColor("#8A6FF0"))
            ribbon_grad.setColorAt(1.0, QColor("#6C4FE0"))
            painter.setPen(Qt.NoPen)
            painter.setBrush(ribbon_grad)
            painter.drawRect(band)
            painter.setPen(QPen(QColor(255, 255, 255, 235)))
            font = QFont(self.font())
            font.setPixelSize(max(8, int(band_h * 0.58)))
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(band, Qt.AlignCenter, "新作品")
            painter.restore()

        # 极淡描边
        painter.setClipping(False)
        painter.setPen(QPen(QColor(0, 0, 0, 28), 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)
        painter.end()


class CircleSwatch(QAbstractButton):
    """写作主题面板中的圆形色块（上方色圆 + 下方名称）。"""

    def __init__(self, name: str, color: str, selected: bool = False, parent=None) -> None:
        super().__init__(parent)
        self._name = name
        self._color = color
        self._selected = selected
        self._hover = False
        self.setFixedSize(78, 88)
        self.setCursor(Qt.PointingHandCursor)
        self.setCheckable(False)

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        self.update()

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        cx, cy = self.width() / 2, 36
        radius = 25 if not self._hover else 27

        if self._selected:
            painter.setPen(QPen(QColor(self.palette().color(QPalette.Highlight)), 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(QPointF(cx, cy), radius + 3, radius + 3)

        painter.setPen(QPen(QColor(0, 0, 0, 40), 1))
        painter.setBrush(QColor(self._color))
        painter.drawEllipse(QPointF(cx, cy), radius, radius)

        if self._selected:
            painter.setPen(QPen(QColor("#FFFFFF"), 2.4))
            painter.drawLine(QPointF(cx - 7, cy), QPointF(cx - 2, cy + 5))
            painter.drawLine(QPointF(cx - 2, cy + 5), QPointF(cx + 8, cy - 6))

        painter.setPen(QPen(QColor(self.palette().color(QPalette.WindowText))))
        font = QFont(self.font())
        font.setPixelSize(11)
        painter.setFont(font)
        painter.drawText(
            QRectF(0, 68, self.width(), 18),
            Qt.AlignHCenter | Qt.AlignVCenter,
            self._name,
        )
        painter.end()


class ToastLabel(QLabel):
    """轻提示：淡入显示，1.4 秒后淡出。"""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("NameToast")
        self.setAlignment(Qt.AlignCenter)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.hide()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._fade_out)
        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity)
        self._anim: QPropertyAnimation | None = None

    def show_message(self, text: str, ms: int = 1400, center: bool = False) -> None:
        """显示轻提示。center=True 时显示在父控件垂直居中位置（保存提示等）。"""
        self.setText(text)
        self.adjustSize()
        parent = self.parentWidget()
        if parent is not None:
            x = (parent.width() - self.width()) // 2
            if center:
                y = (parent.height() - self.height()) // 2
            else:
                y = 16
            self.move(max(8, x), max(8, y))
        self.raise_()
        self.show()
        # 统一 150–300ms ease-out 淡入
        self._anim = QPropertyAnimation(self._opacity, b"opacity", self)
        self._anim.setDuration(180)
        self._anim.setStartValue(self._opacity.opacity())
        self._anim.setEndValue(1.0)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.start()
        self._timer.start(ms)

    def _fade_out(self) -> None:
        self._anim = QPropertyAnimation(self._opacity, b"opacity", self)
        self._anim.setDuration(180)
        self._anim.setStartValue(self._opacity.opacity())
        self._anim.setEndValue(0.0)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.finished.connect(self.hide)
        self._anim.start()


def apply_shadow(widget: QWidget, blur: int = 18, alpha: int = 26, dy: int = 4) -> None:
    """给控件添加柔和阴影（需要 QtWidgets 的 QGraphicsDropShadowEffect）。"""
    from PySide6.QtWidgets import QGraphicsDropShadowEffect

    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(blur)
    effect.setOffset(0, dy)
    effect.setColor(QColor(0, 0, 0, alpha))
    widget.setGraphicsEffect(effect)
