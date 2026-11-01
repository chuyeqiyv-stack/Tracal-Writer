# -*- coding: utf-8 -*-
"""通用自定义控件：背景容器、工具栏按钮、封面、色块、开关等。"""
from __future__ import annotations

import random
from pathlib import Path

from PySide6.QtCore import (
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
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QPixmap,
    QCursor,
    QTransform,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QButtonGroup,
    QColorDialog,
    QFrame,
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


# 封面位图缓存：path+mtime → 降采样后的 QPixmap（书架频繁重建卡片时避免反复读盘解码）
_COVER_CACHE: dict[tuple[str, int], QPixmap] = {}
_COVER_CACHE_MAX = 64


def _load_cover_pixmap(cover_path: str) -> QPixmap | None:
    """加载封面并降采样到显示足够的尺寸（有缓存）。

    封面卡片显示宽约 200px，降采样到 1024px（1K）宽以内可满足 2~3 倍屏，
    同时避免相机原图（可能数十 MB 解码内存）常驻；原图不足 1K 时保持原样不放大。
    """
    try:
        mtime = int(Path(cover_path).stat().st_mtime)
    except OSError:
        return None
    key = (str(cover_path), mtime)
    pix = _COVER_CACHE.get(key)
    if pix is not None:
        return pix
    loaded = QPixmap(str(cover_path))
    if loaded.isNull():
        return None
    if loaded.width() > 1024 or loaded.height() > 1365:
        loaded = loaded.scaled(1024, 1365, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    if len(_COVER_CACHE) >= _COVER_CACHE_MAX:
        _COVER_CACHE.clear()
    _COVER_CACHE[key] = loaded
    return loaded


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
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(self._theme["window"]))
        painter.end()


class ToolbarButton(QAbstractButton):
    """顶部工具栏按钮：上图下字，悬停浅灰圆角 + 轻微放大动画。"""

    def __init__(self, label: str, theme: dict, parent=None) -> None:
        super().__init__(parent)
        self._label = label
        self._theme = theme
        self._icon = None
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

    def set_icon(self, icon) -> None:
        self._icon = icon
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
        if self._icon is not None:
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


class CardFrame(QFrame):
    """带常驻柔和阴影的卡片基类：悬停时阴影加深，产生“上浮”感。

    重要：PySide6 6.12 下在 enter/leave 里挂/卸 QGraphicsDropShadowEffect
    或做几何动画，控件随后被隐藏会访问冲突闪退。这里只在构造时挂一次
    effect，悬停动画只平滑改变 effect 的 offset / blurRadius 属性。
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._lift_enabled = False
        self._lift_dy = 3.0
        self._lift_blur = 16.0
        self._offset_anim: QPropertyAnimation | None = None
        self._blur_anim: QPropertyAnimation | None = None

    def enable_lift(self, blur: int = 16, alpha: int = 22, dy: int = 3) -> None:
        """挂上常驻阴影并启用悬停上浮（只调用一次）。"""
        apply_shadow(self, blur=blur, alpha=alpha, dy=dy)
        self._lift_dy = float(dy)
        self._lift_blur = float(blur)
        self._lift_enabled = True
        self.setAttribute(Qt.WA_Hover, True)

    def childEvent(self, event) -> None:  # noqa: N802
        """子控件（封面/文字）上方的悬停也要触发行卡上浮，统一装事件过滤。"""
        super().childEvent(event)
        if self._lift_enabled and event.added() and event.child().isWidgetType():
            event.child().installEventFilter(self)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        etype = event.type()
        if self._lift_enabled and etype == QEvent.Type.Enter:
            self._animate_lift(True)
        elif self._lift_enabled and etype == QEvent.Type.Leave:
            # 可能只是从子控件移到卡片另一处，先判断光标是否仍在卡片内
            inside = self.rect().contains(self.mapFromGlobal(QCursor.pos()))
            self._animate_lift(inside)
        return super().eventFilter(obj, event)

    def _animate_lift(self, entered: bool) -> None:
        if not self._lift_enabled:
            return
        effect = self.graphicsEffect()
        if effect is None:
            return
        dy = self._lift_dy + (5.0 if entered else 0.0)
        blur = self._lift_blur + (12.0 if entered else 0.0)

        if self._offset_anim is None:
            self._offset_anim = QPropertyAnimation(effect, b"offset", self)
            self._offset_anim.setDuration(150)
            self._offset_anim.setEasingCurve(QEasingCurve.OutCubic)
        if self._blur_anim is None:
            self._blur_anim = QPropertyAnimation(effect, b"blurRadius", self)
            self._blur_anim.setDuration(150)
            self._blur_anim.setEasingCurve(QEasingCurve.OutCubic)

        self._offset_anim.stop()
        self._offset_anim.setStartValue(effect.offset())
        self._offset_anim.setEndValue(QPointF(0.0, dy))
        self._offset_anim.start()
        self._blur_anim.stop()
        self._blur_anim.setStartValue(effect.blurRadius())
        self._blur_anim.setEndValue(blur)
        self._blur_anim.start()

    def enterEvent(self, event) -> None:  # noqa: N802
        self._animate_lift(True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._animate_lift(False)
        super().leaveEvent(event)


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
        self._size = size
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
        self._pixmap: QPixmap | None = None
        self._gradient = COVER_GRADIENTS[0]
        self._new = False

    def set_data(self, title: str, cover_path: str, cover_index: int, is_new: bool) -> None:
        self._title = title
        self._gradient = COVER_GRADIENTS[int(cover_index) % len(COVER_GRADIENTS)]
        self._new = is_new
        self._pixmap = None
        self._scaled = None
        if cover_path:
            pix = _load_cover_pixmap(cover_path)
            if pix is not None:
                self._pixmap = pix
        self.update()

    def _paint_scaled(self, painter: QPainter, source: QPixmap) -> None:
        """按控件尺寸缩放绘制（带缓存：固定尺寸控件只在源变化时重算）。"""
        cached = getattr(self, "_scaled", None)
        if cached is not None and cached[0] is source:
            scaled = cached[1]
        else:
            scaled = source.scaled(
                self.width(), self.height(),
                Qt.KeepAspectRatioByExpanding,
                Qt.SmoothTransformation,
            )
            self._scaled = (source, scaled)
        x = (scaled.width() - self.width()) / 2
        y = (scaled.height() - self.height()) / 2
        painter.drawPixmap(QPointF(0, 0), scaled,
                           QRectF(x, y, self.width(), self.height()))

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

        if self._pixmap is not None:
            self._paint_scaled(painter, self._pixmap)
        else:
            # 默认封面：优先使用水墨山水图（assets/default_cover.png），缺失时退回渐变底纹
            default_pix = _default_cover_pixmap()
            if default_pix is not None:
                self._paint_scaled(painter, default_pix)
            else:
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
    """轻提示：显示后 1.4 秒自动淡出。"""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("NameToast")
        self.setAlignment(Qt.AlignCenter)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.hide()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

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
        self._timer.start(ms)


def apply_shadow(widget: QWidget, blur: int = 18, alpha: int = 26, dy: int = 4) -> None:
    """给控件添加柔和阴影（需要 QtWidgets 的 QGraphicsDropShadowEffect）。"""
    from PySide6.QtWidgets import QGraphicsDropShadowEffect

    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(blur)
    effect.setOffset(0, dy)
    effect.setColor(QColor(0, 0, 0, alpha))
    widget.setGraphicsEffect(effect)
