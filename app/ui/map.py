# -*- coding: utf-8 -*-
"""世界地图（势力分布图）：大尺寸独立窗口。

- 全局唯一，不绑定任何小说；数据保存为独立的 map_global.json；
- 底图、势力多边形（点集 + 名字 + 颜色）、点位图钉、场景边界全部持久化；
- 画布可无限扩展：拖到边缘（或向边缘添加势力）时出现「＋」按钮，
  点击后 QGraphicsScene.setSceneRect 沿该方向扩展 1000 像素并立即保存；
- 已画好的多边形与图钉使用绝对坐标，扩展后位置保持不变；
- QGraphicsView 滚轮缩放（以光标为锚点）、拖动平移，扩展不影响流畅度。
"""
from __future__ import annotations

import os

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QImage,
    QKeySequence,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
    QShortcut,
)
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsLineItem,
    QGraphicsPixmapItem,
    QGraphicsPolygonItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.models import new_id
from app.ui.dialogs import MessageBox

EXTEND_STEP = 1000.0        # 每次沿一个方向扩展的像素数
DEFAULT_SCENE = (0.0, 0.0, 4000.0, 3000.0)   # 默认场景边界（左、上、宽、高）
NEAR_EDGE = 260.0           # 绘制时接近边界多少像素内显示「＋」
ZOOM_MIN, ZOOM_MAX = 0.2, 6.0

# 势力 / 图钉预设颜色（饱和度适中，半透明叠加后仍可辨识）
MAP_COLORS: list[tuple[str, str]] = [
    ("#E57373", "赤红"),
    ("#F06292", "桃粉"),
    ("#BA68C8", "紫罗兰"),
    ("#7986CB", "黛蓝"),
    ("#4FC3F7", "天青"),
    ("#4DB6AC", "青碧"),
    ("#81C784", "苍绿"),
    ("#DCE775", "嫩黄"),
    ("#FFB74D", "暖橙"),
    ("#A1887F", "赭石"),
    ("#90A4AE", "灰蓝"),
    ("#B0BEC5", "烟灰"),
]


def _luma(color: QColor) -> float:
    return (0.299 * color.red() + 0.587 * color.green() + 0.114 * color.blue()) / 255.0


def _text_color_for(fill: QColor) -> QColor:
    return QColor("#333333") if _luma(fill) > 0.55 else QColor("#FFFFFF")


def _pin_tooltip(name: str, desc: str) -> str:
    return f"{name}\n{desc}" if desc else name


# ======================================================================
# 场景元素：势力多边形
# ======================================================================
class FactionItem(QGraphicsPolygonItem):
    """势力范围多边形：半透明填充 + 同色描边 + 中心势力名。"""

    def __init__(self, fid: str, name: str, color: str, points, canvas: "MapCanvas") -> None:
        super().__init__(QPolygonF([QPointF(float(p[0]), float(p[1])) for p in points]))
        self.fid = fid
        self.name = name
        self.color = color
        self.canvas = canvas

        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setAcceptHoverEvents(True)
        self.setZValue(0)

        fill = QColor(color)
        fill.setAlpha(78)
        self._fill = fill
        self.setBrush(fill)

        self._label = QGraphicsSimpleTextItem(self)
        self._update_label()
        self._apply_pen()

    # ------------------------------------------------------------------
    def _apply_pen(self) -> None:
        pen = QPen(QColor(self.color).darker(115), 3.4 if self.isSelected() else 2.0)
        pen.setCosmetic(True)   # 缩放时描边粗细稳定
        self.setPen(pen)

    def _update_label(self) -> None:
        poly = self.polygon()
        count = max(1, len(poly))
        cx = sum(p.x() for p in poly) / count
        cy = sum(p.y() for p in poly) / count
        font = QFont()
        font.setPixelSize(14)
        font.setBold(True)
        self._label.setFont(font)
        self._label.setText(self.name)
        self._label.setBrush(_text_color_for(QColor(self.color)))
        outline = QPen(QColor(0, 0, 0, 90), 2.4)
        outline.setCosmetic(True)
        self._label.setPen(outline)
        rect = self._label.boundingRect()
        self._label.setPos(cx - rect.width() / 2, cy - rect.height() / 2)

    def set_style(self, name: str, color: str) -> None:
        self.name = name
        self.color = color
        fill = QColor(color)
        fill.setAlpha(78)
        self._fill = fill
        self.setBrush(fill)
        self._update_label()
        self._apply_pen()
        self.update()

    def scene_points(self) -> list[list[float]]:
        return [[p.x(), p.y()] for p in self.mapToScene(self.polygon())]

    # ------------------------------------------------------------------
    def itemChange(self, change, value):  # noqa: N802
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            self.canvas.on_item_moved()
        elif change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            self._apply_pen()
        return super().itemChange(change, value)

    def paint(self, painter, option, widget=None):  # noqa: N802
        self._apply_pen()
        super().paint(painter, option, widget)


# ======================================================================
# 场景元素：点位图钉
# ======================================================================
class PinItem(QGraphicsItem):
    """地图图钉：针尖为保存坐标（场景绝对位置），上方带名称标签。"""

    def __init__(self, pid: str, name: str, desc: str, color: str, canvas: "MapCanvas") -> None:
        super().__init__()
        self.pid = pid
        self.name = name
        self.desc = desc
        self.color = color
        self.canvas = canvas

        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setAcceptHoverEvents(True)
        self.setZValue(10)
        self.setToolTip(_pin_tooltip(name, desc))

    # ------------------------------------------------------------------
    def boundingRect(self) -> QRectF:  # noqa: N802
        return QRectF(-74, -60, 148, 78)

    def shape(self) -> QPainterPath:  # noqa: N802
        path = QPainterPath()
        path.addEllipse(QPointF(0, -20), 12, 12)
        path.moveTo(-9, -12)
        path.lineTo(9, -12)
        path.lineTo(0, 2)
        path.closeSubpath()
        return path

    def paint(self, painter, option, widget=None):  # noqa: N802
        painter.setRenderHint(QPainter.Antialiasing, True)
        color = QColor(self.color)

        if self.isSelected():
            ring = QPen(QColor("#7C5CE6"), 2.4)
            ring.setCosmetic(True)
            painter.setPen(ring)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPointF(0, -20), 12.5, 12.5)

        # 针身（水滴形）
        painter.setPen(QPen(QColor(0, 0, 0, 60), 1))
        painter.setBrush(color)
        painter.drawEllipse(QPointF(0, -20), 9, 9)
        painter.setPen(QPen(Qt.PenStyle.NoPen))
        painter.drawPolygon(
            [QPointF(-7.4, -13.5), QPointF(7.4, -13.5), QPointF(0, 1.5)]
        )
        painter.setPen(QPen(QColor(255, 255, 255, 220), 1.6))
        painter.setBrush(QColor(255, 255, 255, 230))
        painter.drawEllipse(QPointF(0, -20), 3.2, 3.2)

        # 名称标签（顶部，带描边保证任何底色可读）
        font = QFont(painter.font())
        font.setPixelSize(12)
        font.setBold(True)
        painter.setFont(font)
        text_rect = QRectF(-70, -56, 140, 16)
        painter.setPen(QPen(QColor(0, 0, 0, 100), 2.6))
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter, self.name)
        painter.setPen(QPen(QColor("#1F2329")))
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter, self.name)

    def itemChange(self, change, value):  # noqa: N802
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            self.canvas.on_item_moved()
        return super().itemChange(change, value)


# ======================================================================
# 画布视图
# ======================================================================
class MapCanvas(QGraphicsView):
    """地图画布：滚轮缩放、拖动平移、三种工具模式与边界无限扩展。"""

    changed = Signal()            # 数据变化（移动 / 增删 / 编辑，防抖后发出）
    mode_changed = Signal(str)    # 工具模式变化（同步工具条按钮选中态）
    start_draw_requested = Signal()   # 右键菜单请求进入绘制模式
    start_pin_requested = Signal()    # 右键菜单请求进入点位模式

    def __init__(self, theme: dict, parent=None) -> None:
        self._scene = QGraphicsScene(0.0, 0.0, DEFAULT_SCENE[2], DEFAULT_SCENE[3])
        super().__init__(self._scene, parent)
        self.theme = theme
        self._bg_data: dict | None = None
        self._bg_item: QGraphicsPixmapItem | None = None

        self.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.setOptimizationFlag(QGraphicsView.OptimizationFlag.DontSavePainterState, True)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.MinimalViewportUpdate)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setBackgroundBrush(QColor(theme["panel_alt"]))
        self._zoom = 1.0

        self._mode = "pan"
        self._apply_mode()

        # 拖动 / 编辑防抖保存：移动过程中的高频信号只在停顿后落一次盘
        self._change_timer = QTimer(self)
        self._change_timer.setSingleShot(True)
        self._change_timer.setInterval(350)
        self._change_timer.timeout.connect(self.changed.emit)

        # 绘制势力进行中的临时元素
        self._pending_points: list[list[float]] = []
        self._pending_poly: QGraphicsPolygonItem | None = None
        self._pending_line: QGraphicsLineItem | None = None
        self._pending_dots: list[QGraphicsEllipseItem] = []

        self._mid_panning = False
        self._mid_last = QPointF()

        # 边缘「＋」按钮（上下左右四个方向）
        self._plus_buttons: dict[str, QToolButton] = {}
        for direction, tip in (
            ("left", "向左扩展地图"),
            ("right", "向右扩展地图"),
            ("top", "向上扩展地图"),
            ("bottom", "向下扩展地图"),
        ):
            button = QToolButton(self.viewport())
            button.setObjectName("FloatButton")
            button.setText("＋")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setFixedSize(36, 36)
            button.setToolTip(tip)
            button.hide()
            button.clicked.connect(lambda _c=False, d=direction: self.expand(d))
            self._plus_buttons[direction] = button

        self.horizontalScrollBar().valueChanged.connect(self._update_edge_buttons)
        self.verticalScrollBar().valueChanged.connect(self._update_edge_buttons)
        self.viewport().installEventFilter(self)

        # 世界地图：横 / 纵双向隐形快速滚动滑块（原生滚动条隐藏）
        from app.ui.scroll_float import ScrollFlasher

        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._h_flasher = ScrollFlasher(self, Qt.Horizontal, inset=8)
        self._v_flasher = ScrollFlasher(self, Qt.Vertical, inset=8)

    # ------------------------------------------------------------------
    # 数据载入 / 收集
    # ------------------------------------------------------------------
    def set_theme(self, theme: dict) -> None:
        self.theme = theme
        self.setBackgroundBrush(QColor(theme["panel_alt"]))
        self._scene.update()

    def set_data(self, data: dict) -> None:
        self._scene.clear()
        self._bg_item = None
        self._pending_poly = None
        self._pending_line = None
        self._pending_dots = []
        self._pending_points = []

        scene = data.get("scene") or {}
        try:
            rect = QRectF(
                float(scene.get("left", DEFAULT_SCENE[0])),
                float(scene.get("top", DEFAULT_SCENE[1])),
                max(1000.0, float(scene.get("width", DEFAULT_SCENE[2]))),
                max(1000.0, float(scene.get("height", DEFAULT_SCENE[3]))),
            )
        except (TypeError, ValueError):
            rect = QRectF(*DEFAULT_SCENE)
        self._scene.setSceneRect(rect)

        bg = data.get("bg")
        if isinstance(bg, dict):
            self._bg_data = dict(bg)
            self._load_bg_item()
        else:
            self._bg_data = None

        for f in data.get("factions", []):
            points = f.get("points") or []
            if len(points) < 3:
                continue
            item = FactionItem(
                str(f.get("id") or new_id()),
                str(f.get("name") or "未命名势力"),
                str(f.get("color") or "#7986CB"),
                points,
                self,
            )
            try:
                item.setPos(float(f.get("x") or 0), float(f.get("y") or 0))
            except (TypeError, ValueError):
                item.setPos(0.0, 0.0)
            self._scene.addItem(item)

        for p in data.get("pins", []):
            item = PinItem(
                str(p.get("id") or new_id()),
                str(p.get("name") or "未命名点位"),
                str(p.get("desc") or ""),
                str(p.get("color") or "#E57373"),
                self,
            )
            try:
                item.setPos(float(p.get("x") or 0), float(p.get("y") or 0))
            except (TypeError, ValueError):
                item.setPos(0.0, 0.0)
            self._scene.addItem(item)

        self.reset_view()

    def collect(self) -> dict:
        factions: list[dict] = []
        pins: list[dict] = []
        for item in self._scene.items():
            if isinstance(item, FactionItem):
                pos = item.pos()
                factions.append(
                    {
                        "id": item.fid,
                        "name": item.name,
                        "color": item.color,
                        "x": round(pos.x(), 2),
                        "y": round(pos.y(), 2),
                        "points": [
                            [round(px, 2), round(py, 2)] for px, py in item.scene_points()
                        ],
                    }
                )
            elif isinstance(item, PinItem):
                pos = item.pos()
                pins.append(
                    {
                        "id": item.pid,
                        "name": item.name,
                        "desc": item.desc,
                        "color": item.color,
                        "x": round(pos.x(), 2),
                        "y": round(pos.y(), 2),
                    }
                )
        rect = self._scene.sceneRect()
        return {
            "bg": dict(self._bg_data) if self._bg_data else None,
            "factions": factions,
            "pins": pins,
            "scene": {
                "left": round(rect.left(), 2),
                "top": round(rect.top(), 2),
                "width": round(rect.width(), 2),
                "height": round(rect.height(), 2),
            },
        }

    # ------------------------------------------------------------------
    # 底图
    # ------------------------------------------------------------------
    def _load_bg_item(self) -> None:
        """根据 _bg_data 重建底图元素（文件缺失时静默跳过，数据保留）。"""
        data = self._bg_data
        if not data:
            return
        path = str(data.get("path") or "")
        if not path or not os.path.exists(path):
            return
        pixmap = QPixmap(path)
        if pixmap.isNull():
            return
        try:
            width = float(data.get("w") or 0)
            height = float(data.get("h") or 0)
        except (TypeError, ValueError):
            width = height = 0.0
        if width <= 0 or height <= 0:
            # 首次导入：限制最大边长，避免超大图片拖慢视图
            max_side = 4000.0
            scale = min(1.0, max_side / max(pixmap.width(), pixmap.height()))
            width = pixmap.width() * scale
            height = pixmap.height() * scale
            self._bg_data["w"] = round(width, 2)
            self._bg_data["h"] = round(height, 2)
        pixmap = pixmap.scaled(
            int(width), int(height), Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        item = QGraphicsPixmapItem(pixmap)
        item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        item.setZValue(-10)
        try:
            item.setPos(float(data.get("x") or 0), float(data.get("y") or 0))
        except (TypeError, ValueError):
            item.setPos(0.0, 0.0)
        item.setOffset(-width / 2, -height / 2)   # 以中心为定位点
        self._bg_item = item
        self._scene.addItem(item)

    def import_bg(self) -> None:
        path, _f = QFileDialog.getOpenFileName(
            self, "选择底图", "", "图片文件 (*.png *.jpg *.jpeg *.webp *.bmp)"
        )
        if not path:
            return
        if self._bg_item is not None:
            self._scene.removeItem(self._bg_item)
            self._bg_item = None
        self._bg_data = {"path": path}
        self._load_bg_item()
        if self._bg_item is not None:
            center = self.mapToScene(self.viewport().rect().center())
            self._bg_item.setPos(center)
            self._bg_data["x"] = round(center.x(), 2)
            self._bg_data["y"] = round(center.y(), 2)
        self.changed.emit()

    def clear_bg(self) -> None:
        if self._bg_item is None:
            return
        self._scene.removeItem(self._bg_item)
        self._bg_item = None
        self._bg_data = None
        self.changed.emit()

    # ------------------------------------------------------------------
    # 工具模式
    # ------------------------------------------------------------------
    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str) -> None:
        if mode not in {"pan", "draw", "pin"}:
            mode = "pan"
        if mode == self._mode:
            return
        self._cancel_pending()
        self._mode = mode
        self._apply_mode()
        self.mode_changed.emit(mode)

    def _apply_mode(self) -> None:
        if self._mode == "pan":
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            self.setDragMode(QGraphicsView.DragMode.NoDrag)
            self.setCursor(Qt.CursorShape.CrossCursor)

    # ------------------------------------------------------------------
    # 无限扩展
    # ------------------------------------------------------------------
    def expand(self, direction: str) -> None:
        """沿指定方向扩展场景边界 1000 像素；已有元素坐标不变。"""
        rect = self._scene.sceneRect()
        if direction == "left":
            rect.adjust(-EXTEND_STEP, 0, 0, 0)
        elif direction == "right":
            rect.adjust(0, 0, EXTEND_STEP, 0)
        elif direction == "top":
            rect.adjust(0, -EXTEND_STEP, 0, 0)
        elif direction == "bottom":
            rect.adjust(0, 0, 0, EXTEND_STEP)
        else:
            return
        # 向左 / 向上扩展时保持当前视野稳定（元素坐标是绝对值，本身不动）
        if direction in ("left", "top"):
            center = self.mapToScene(self.viewport().rect().center())
            self._scene.setSceneRect(rect)
            self.centerOn(center)
        else:
            self._scene.setSceneRect(rect)
        self._update_edge_buttons()
        self.changed.emit()   # 扩展后立即持久化场景边界

    def _update_edge_buttons(self) -> None:
        viewport = self.viewport()
        width, height = viewport.width(), viewport.height()
        h_bar = self.horizontalScrollBar()
        v_bar = self.verticalScrollBar()

        at = {
            "left": h_bar.value() <= h_bar.minimum(),
            "right": h_bar.value() >= h_bar.maximum(),
            "top": v_bar.value() <= v_bar.minimum(),
            "bottom": v_bar.value() >= v_bar.maximum(),
        }

        # 绘制势力时，顶点贴近某条边界也提前亮出对应「＋」
        if self._mode == "draw" and self._pending_points:
            rect = self._scene.sceneRect()
            for px, py in self._pending_points:
                if px - rect.left() <= NEAR_EDGE:
                    at["left"] = True
                if rect.right() - px <= NEAR_EDGE:
                    at["right"] = True
                if py - rect.top() <= NEAR_EDGE:
                    at["top"] = True
                if rect.bottom() - py <= NEAR_EDGE:
                    at["bottom"] = True

        places = {
            "left": (10, height // 2 - 18),
            "right": (width - 46, height // 2 - 18),
            "top": (width // 2 - 18, 10),
            "bottom": (width // 2 - 18, height - 46),
        }
        for direction, button in self._plus_buttons.items():
            if at.get(direction):
                button.move(*places[direction])
                button.show()
                button.raise_()
            else:
                button.hide()

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        from PySide6.QtCore import QEvent

        if obj is self.viewport() and event.type() == QEvent.Type.Resize:
            self._update_edge_buttons()
        return super().eventFilter(obj, event)

    # ------------------------------------------------------------------
    # 保存信号
    # ------------------------------------------------------------------
    def on_item_moved(self) -> None:
        """元素被拖动（高频）：防抖后统一发出。"""
        self._change_timer.start()
        self._update_edge_buttons()

    def _emit_changed(self) -> None:
        self._change_timer.stop()
        self.changed.emit()

    # ------------------------------------------------------------------
    # 视图操作
    # ------------------------------------------------------------------
    def reset_view(self) -> None:
        """复位：100% 缩放并居中到场景中心。"""
        self._zoom = 1.0
        self.resetTransform()
        self.centerOn(self._scene.sceneRect().center())

    def zoom_steps(self, steps: float) -> None:
        """按滚轮格数缩放（以视口中心为锚，供 Ctrl+滚轮调用）。"""
        if not steps:
            return
        factor = 1.25 ** steps
        new_zoom = self._zoom * factor
        if ZOOM_MIN <= new_zoom <= ZOOM_MAX:
            self._zoom = new_zoom
            self.scale(factor, factor)

    def wheelEvent(self, event) -> None:  # noqa: N802
        if not (event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            # 普通滚轮不缩放（避免误触），交给默认行为
            super().wheelEvent(event)
            return
        steps = event.angleDelta().y() / 120.0
        if steps == 0:
            steps = event.angleDelta().x() / 120.0
        self.zoom_steps(steps)
        event.accept()

    # ------------------------------------------------------------------
    # 鼠标交互
    # ------------------------------------------------------------------
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.MiddleButton:
            self._mid_panning = True
            self._mid_last = QPointF(event.position())
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return

        scene_pos = self.mapToScene(event.position().toPoint())

        if self._mode == "draw" and event.button() == Qt.MouseButton.LeftButton:
            self._add_vertex(scene_pos)
            event.accept()
            return

        if self._mode == "pin" and event.button() == Qt.MouseButton.LeftButton:
            self._place_pin(scene_pos)
            event.accept()
            return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._mid_panning:
            delta = QPointF(event.position()) - self._mid_last
            self._mid_last = QPointF(event.position())
            h_bar = self.horizontalScrollBar()
            v_bar = self.verticalScrollBar()
            h_bar.setValue(int(h_bar.value() - delta.x()))
            v_bar.setValue(int(v_bar.value() - delta.y()))
            event.accept()
            return

        if self._mode == "draw" and self._pending_line is not None and self._pending_points:
            start = QPointF(self._pending_points[-1][0], self._pending_points[-1][1])
            end = self.mapToScene(event.position().toPoint())
            self._pending_line.setLine(start.x(), start.y(), end.x(), end.y())
            event.accept()
            return

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.MiddleButton and self._mid_panning:
            self._mid_panning = False
            self.setCursor(
                Qt.CursorShape.CrossCursor if self._mode != "pan" else Qt.CursorShape.OpenHandCursor
            )
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if self._mode == "draw":
            self._finish_pending()
            event.accept()
            return
        item = self._top_item_at(event.position().toPoint())
        if isinstance(item, (FactionItem, PinItem)):
            self._edit_item(item)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        item = self._top_item_at(event.pos())
        if isinstance(item, (FactionItem, PinItem)):
            item.setSelected(True)
            menu = QMenu(self)
            if isinstance(item, FactionItem):
                menu.addAction("编辑势力", lambda: self._edit_item(item))
                menu.addSeparator()
                menu.addAction("删除势力", lambda: self._remove_item(item))
            else:
                menu.addAction("编辑点位", lambda: self._edit_item(item))
                menu.addSeparator()
                menu.addAction("删除点位", lambda: self._remove_item(item))
            menu.exec(event.globalPos())
            return

        if self._mode == "draw":
            self._finish_pending()
            return

        menu = QMenu(self)
        menu.addAction("添加势力", self.start_draw_requested.emit)
        menu.addAction("添加点位", self.start_pin_requested.emit)
        menu.addSeparator()
        menu.addAction("复位视图", self.reset_view)
        menu.exec(event.globalPos())

    # ------------------------------------------------------------------
    # 绘制势力（多边形点集）
    # ------------------------------------------------------------------
    def start_draw(self) -> None:
        self.set_mode("draw")
        pen = QPen(QColor(self.theme["accent"]), 2, Qt.PenStyle.DashLine)
        pen.setCosmetic(True)
        self._pending_poly = QGraphicsPolygonItem()
        self._pending_poly.setPen(pen)
        self._pending_poly.setBrush(QColor(0, 0, 0, 0))
        self._pending_poly.setZValue(5)
        self._scene.addItem(self._pending_poly)
        self._pending_line = QGraphicsLineItem()
        self._pending_line.setPen(pen)
        self._pending_line.setZValue(5)
        self._scene.addItem(self._pending_line)

    def _add_vertex(self, scene_pos: QPointF) -> None:
        self._pending_points.append([scene_pos.x(), scene_pos.y()])
        dot = QGraphicsEllipseItem(scene_pos.x() - 4, scene_pos.y() - 4, 8, 8)
        dot.setBrush(QColor(self.theme["accent"]))
        dot.setPen(QPen(Qt.PenStyle.NoPen))
        dot.setZValue(6)
        self._scene.addItem(dot)
        self._pending_dots.append(dot)

        points = [QPointF(px, py) for px, py in self._pending_points]
        if self._pending_poly is not None:
            self._pending_poly.setPolygon(QPolygonF(points))
        self._update_edge_buttons()

    def _finish_pending(self) -> None:
        if self._mode != "draw" or not self._pending_points:
            return
        points = list(self._pending_points)
        self._cancel_pending(keep_mode=False)
        if len(points) >= 3:
            dialog = FactionEditDialog(self)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                name, color = dialog.values()
                item = FactionItem(new_id(), name, color, points, self)
                self._scene.addItem(item)
                self._emit_changed()
        else:
            self.set_mode("pan")

    def _cancel_pending(self, keep_mode: bool = True) -> None:
        if self._pending_poly is not None:
            self._scene.removeItem(self._pending_poly)
            self._pending_poly = None
        if self._pending_line is not None:
            self._scene.removeItem(self._pending_line)
            self._pending_line = None
        for dot in self._pending_dots:
            self._scene.removeItem(dot)
        self._pending_dots = []
        self._pending_points = []
        if not keep_mode:
            self.set_mode("pan")
        self._update_edge_buttons()

    # ------------------------------------------------------------------
    # 点位图钉
    # ------------------------------------------------------------------
    def start_pin(self) -> None:
        self.set_mode("pin")

    def _place_pin(self, scene_pos: QPointF) -> None:
        dialog = PinEditDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            name, desc, color = dialog.values()
            item = PinItem(new_id(), name, desc, color, self)
            item.setPos(scene_pos)
            self._scene.addItem(item)
            self._emit_changed()
        self.set_mode("pan")

    # ------------------------------------------------------------------
    # 元素编辑 / 删除
    # ------------------------------------------------------------------
    def _top_item_at(self, view_pos) -> QGraphicsItem | None:
        for item in self.items(view_pos):
            if isinstance(item, (FactionItem, PinItem)):
                return item
        return None

    def _edit_item(self, item) -> None:
        if isinstance(item, FactionItem):
            dialog = FactionEditDialog(self, item.name, item.color)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                name, color = dialog.values()
                item.set_style(name, color)
                self._emit_changed()
        else:
            dialog = PinEditDialog(self, item.name, item.desc, item.color)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                name, desc, color = dialog.values()
                item.name = name
                item.desc = desc
                item.color = color
                item.setToolTip(_pin_tooltip(name, desc))
                item.update()
                self._emit_changed()

    def _remove_item(self, item) -> None:
        self._scene.removeItem(item)
        self._emit_changed()

    # ------------------------------------------------------------------
    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            if self._mode == "draw":
                self._cancel_pending(keep_mode=False)
            else:
                self.set_mode("pan")
            event.accept()
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self._mode == "draw":
            self._finish_pending()
            event.accept()
            return
        super().keyPressEvent(event)


# ======================================================================
# 编辑小窗
# ======================================================================
class FactionEditDialog(QDialog):
    """势力编辑：名称 + 预设颜色。"""

    def __init__(self, parent=None, name: str = "", color: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle("势力设置")
        self.setModal(True)
        self.resize(400, 230)
        self._color = color or MAP_COLORS[3][0]

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 16)
        layout.setSpacing(10)

        self.name_edit = QLineEdit(name, self)
        self.name_edit.setPlaceholderText("势力名称，例如：北境王朝")
        layout.addWidget(self.name_edit)

        layout.addWidget(QLabel("颜色", self))
        color_box = QHBoxLayout()
        color_box.setSpacing(8)
        for value, tip in MAP_COLORS:
            button = QPushButton(self)
            button.setFixedSize(26, 26)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setToolTip(tip)
            button.setStyleSheet(
                f"QPushButton{{background:{value};border-radius:13px;border:2px solid transparent;}}"
                "QPushButton:hover{border:2px solid #1F2329;}"
            )
            button.clicked.connect(lambda _c=False, v=value: self._pick(v))
            color_box.addWidget(button)
        color_box.addStretch(1)
        layout.addLayout(color_box)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel_button = QPushButton("取消", self)
        cancel_button.clicked.connect(self.reject)
        buttons.addWidget(cancel_button)
        ok_button = QPushButton("确定", self)
        ok_button.setObjectName("Primary")
        ok_button.setCursor(Qt.CursorShape.PointingHandCursor)
        ok_button.clicked.connect(self.accept)
        buttons.addWidget(ok_button)
        layout.addLayout(buttons)

        self.name_edit.setFocus()

    def _pick(self, value: str) -> None:
        self._color = value

    def values(self) -> tuple[str, str]:
        return (self.name_edit.text().strip() or "未命名势力", self._color)


class PinEditDialog(QDialog):
    """点位编辑：名称 + 描述 + 预设颜色。"""

    def __init__(self, parent=None, name: str = "", desc: str = "", color: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle("点位设置")
        self.setModal(True)
        self.resize(400, 310)
        self._color = color or MAP_COLORS[0][0]

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 16)
        layout.setSpacing(10)

        self.name_edit = QLineEdit(name, self)
        self.name_edit.setPlaceholderText("点位名称，例如：落雁城")
        layout.addWidget(self.name_edit)

        self.desc_edit = QPlainTextEdit(self)
        self.desc_edit.setObjectName("Preview")
        self.desc_edit.setPlaceholderText("点位描述（可选）……")
        self.desc_edit.setPlainText(desc)
        self.desc_edit.setFixedHeight(88)
        layout.addWidget(self.desc_edit)

        layout.addWidget(QLabel("颜色", self))
        color_box = QHBoxLayout()
        color_box.setSpacing(8)
        for value, tip in MAP_COLORS:
            button = QPushButton(self)
            button.setFixedSize(26, 26)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setToolTip(tip)
            button.setStyleSheet(
                f"QPushButton{{background:{value};border-radius:13px;border:2px solid transparent;}}"
                "QPushButton:hover{border:2px solid #1F2329;}"
            )
            button.clicked.connect(lambda _c=False, v=value: self._pick(v))
            color_box.addWidget(button)
        color_box.addStretch(1)
        layout.addLayout(color_box)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel_button = QPushButton("取消", self)
        cancel_button.clicked.connect(self.reject)
        buttons.addWidget(cancel_button)
        ok_button = QPushButton("确定", self)
        ok_button.setObjectName("Primary")
        ok_button.setCursor(Qt.CursorShape.PointingHandCursor)
        ok_button.clicked.connect(self.accept)
        buttons.addWidget(ok_button)
        layout.addLayout(buttons)

        self.name_edit.setFocus()

    def _pick(self, value: str) -> None:
        self._color = value

    def values(self) -> tuple[str, str, str]:
        return (
            self.name_edit.text().strip() or "未命名点位",
            self.desc_edit.toPlainText().strip(),
            self._color,
        )


# ======================================================================
# 主窗口
# ======================================================================
class MapDialog(QDialog):
    """世界地图主窗口：工具条 + QGraphicsView 画布 + 边缘扩展按钮。"""

    changed = Signal()  # 数据变化（由主窗口负责持久化到 map_global.json）

    def __init__(self, data: dict, theme: dict, parent=None) -> None:
        super().__init__(parent)
        self.data = dict(data)
        self.setWindowTitle(f"世界地图 · {self.data.get('title') or '未命名地图'}")
        self.resize(1280, 800)
        self.setModal(False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 顶部工具条
        bar = QWidget(self)
        bar.setObjectName("TimelineBar")
        bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        bar.setFixedHeight(56)
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(18, 8, 18, 8)
        bar_layout.setSpacing(8)

        title = QLabel(str(self.data.get("title") or "未命名地图"), bar)
        title.setObjectName("NameGenTitle")
        bar_layout.addWidget(title)
        subtitle = QLabel("世界地图 · 独立保存", bar)
        subtitle.setObjectName("DrawerHint")
        bar_layout.addWidget(subtitle)
        bar_layout.addStretch(1)

        def add_button(text: str, slot, checkable: bool = False) -> QPushButton:
            button = QPushButton(text, bar)
            button.setObjectName("GhostButton")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setCheckable(checkable)
            button.clicked.connect(slot)
            bar_layout.addWidget(button)
            return button

        self.draw_button = add_button("添加势力", self._start_draw, checkable=True)
        self.pin_button = add_button("添加点位", self._start_pin, checkable=True)
        add_button("导入底图", self._import_bg)
        add_button("清除底图", self._clear_bg)
        add_button("导出图片", self._export_png)
        self.save_button = add_button("保存", self._flush_save)

        hint = QLabel("Ctrl+滚轮缩放 · 拖动平移 · 右键编辑 · 到边缘点「＋」扩展", bar)
        hint.setObjectName("DrawerHint")
        bar_layout.addWidget(hint)
        layout.addWidget(bar)

        # Ctrl+S 立即保存；关闭窗口前兜底保存
        save_sc = QShortcut(QKeySequence("Ctrl+S"), self)
        save_sc.activated.connect(self._flush_save)

        self.canvas = MapCanvas(theme, self)
        self.canvas.set_data(self.data)
        self.canvas.changed.connect(self._collect)
        self.canvas.mode_changed.connect(self._sync_tool_buttons)
        self.canvas.start_draw_requested.connect(self._start_draw)
        self.canvas.start_pin_requested.connect(self._start_pin)
        layout.addWidget(self.canvas, 1)

        # Esc 取消当前工具 / 回到漫游（不关闭窗口）
        esc_sc = QShortcut(QKeySequence("Escape"), self)
        esc_sc.activated.connect(lambda: self.canvas.set_mode("pan"))

        self._sync_tool_buttons(self.canvas.mode())
        self._collect_silent()

    # ------------------------------------------------------------------
    def _start_draw(self) -> None:
        if self.canvas.mode() == "draw":
            self.canvas.set_mode("pan")
        else:
            self.canvas.start_draw()
        self._sync_tool_buttons(self.canvas.mode())

    def _start_pin(self) -> None:
        if self.canvas.mode() == "pin":
            self.canvas.set_mode("pan")
        else:
            self.canvas.start_pin()
        self._sync_tool_buttons(self.canvas.mode())

    def _sync_tool_buttons(self, mode: str) -> None:
        self.draw_button.setChecked(mode == "draw")
        self.pin_button.setChecked(mode == "pin")

    def _import_bg(self) -> None:
        self.canvas.import_bg()

    def _clear_bg(self) -> None:
        self.canvas.clear_bg()

    def reload_meta(self, item: dict) -> None:
        """外部重命名后刷新标题显示。"""
        self.data["title"] = str(item.get("title") or "")
        self.setWindowTitle(f"世界地图 · {self.data.get('title') or '未命名地图'}")

    # ------------------------------------------------------------------
    def _collect_silent(self) -> None:
        self.data.update(self.canvas.collect())

    def _collect(self) -> None:
        """画布变化后同步数据并交给主窗口持久化。"""
        self._collect_silent()
        self.changed.emit()

    def _flush_save(self) -> None:
        self._collect()
        self.save_button.setText("已保存 ✓")
        QTimer.singleShot(1400, lambda: self.save_button.setText("保存"))

    def closeEvent(self, event) -> None:  # noqa: N802
        self._collect()   # 关闭前兜底：势力 / 图钉 / 场景边界全部落盘
        super().closeEvent(event)

    def data_dict(self) -> dict:
        return dict(self.data)

    def set_theme(self, theme: dict) -> None:
        self.canvas.set_theme(theme)

    # ------------------------------------------------------------------
    def _export_png(self) -> None:
        """把整个场景渲染为 PNG 并保存（导出路径实时选择）。"""
        self._collect_silent()
        rect = self.canvas.sceneRect()
        max_side = 8000.0
        scale = min(1.0, max_side / max(rect.width(), rect.height()))
        image = QImage(int(rect.width() * scale), int(rect.height() * scale), QImage.Format.Format_ARGB32)
        image.fill(QColor("#F7F8FA"))
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.canvas.scene().render(
            painter, QRectF(image.rect()), rect, Qt.AspectRatioMode.IgnoreAspectRatio
        )
        painter.end()

        path, _f = QFileDialog.getSaveFileName(
            self, "导出地图图片", "势力分布图.png", "PNG 图片 (*.png)"
        )
        if not path:
            return
        if not path.lower().endswith(".png"):
            path += ".png"
        if not image.save(path):
            MessageBox.warning(self, "导出失败", "图片保存失败，请重试。")
