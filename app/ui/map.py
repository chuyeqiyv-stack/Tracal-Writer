# -*- coding: utf-8 -*-
"""地图（势力分布图）：大尺寸独立窗口。

- 全局唯一，不绑定任何小说；数据保存为独立的 map_global.json；
- 底图、势力多边形（点集 + 名字 + 颜色）、点位图钉、场景边界全部持久化；
- 画布可无限扩展：拖到边缘（或向边缘添加势力）时出现「＋」按钮，
  点击后 QGraphicsScene.setSceneRect 沿该方向扩展 1000 像素并立即保存；
- 已画好的多边形与图钉使用绝对坐标，扩展后位置保持不变；
- QGraphicsView 滚轮缩放（以光标为锚点）、拖动平移，扩展不影响流畅度。
"""
from __future__ import annotations

import math
import os
import time
from collections import OrderedDict

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QImage,
    QKeySequence,
    QPainter,
    QPainterPath,
    QPainterPathStroker,
    QPen,
    QPixmap,
    QPolygonF,
    QShortcut,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsLineItem,
    QGraphicsPixmapItem,
    QGraphicsPolygonItem,
    QGraphicsScene,
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
from app.ui.icons import IconFactory, flash_saved

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


# 地形标注类型（图标 + 名称 + 默认颜色），颜色可在编辑窗里逐标记修改
TERRAIN_TYPES: list[tuple[str, str, str]] = [
    ("mountain", "山脉", "#A17750"),
    ("river", "河流", "#3E9BE0"),
    ("forest", "森林", "#3E7D43"),
    ("lake", "湖泊", "#26A6C9"),
    ("desert", "沙漠", "#D9A441"),
    ("plain", "平原", "#8FB64B"),
    ("city", "城市", "#6D7B8D"),
    ("pass", "关隘", "#C9712B"),
]
TERRAIN_TYPE_MAP = {key: (label, color) for key, label, color in TERRAIN_TYPES}

# 势力框文字：按框的「屏幕尺寸」自动铺满（文本框语义），最多 2 行。
# aw/ah 为文字可用区（留内边距，不贴边、不压边框）；字号在屏幕空间算出后换算回场景。
FACTION_LABEL_MIN_PX = 10           # 旧常量保留（其它模块/脚本仍引用）
FACTION_LABEL_MAX_PX = 28           # 旧常量保留
FACTION_LABEL_RATIO = 0.052         # 旧常量保留
FACTION_LABEL_PAD_RATIO = 0.04      # 旧常量保留
FACTION_LABEL_SCREEN_MIN_PX = 12.0  # 屏幕下限
FACTION_LABEL_OVERFLOW_RATIO = 0.92  # 文字可用宽 = 框屏幕宽 × 0.92
FACTION_LABEL_DOWNGRADE_STEPS = 2    # 旧常量保留
FACTION_LABEL_MIN_SCREEN_PX = 12.0   # 屏幕字号下限（可调）
FACTION_LABEL_MAX_SCREEN_PX = 40.0   # 屏幕字号上限，避免极小名字出现夸张大字
FACTION_LABEL_TEXT_W_RATIO = 0.92    # 文字可用宽比例
FACTION_LABEL_TEXT_H_RATIO = 0.88    # 文字可用高比例
FACTION_LABEL_LINE_FACTOR = 1.15     # 行距 = 1.15 × 字号
LABEL_BITMAP_MAX_ITEMS = 32          # 文字位图缓存：普通 LRU，最多 32 张
LABEL_BITMAP_MAX_BYTES = 4 * 1024 * 1024   # 硬上限 4MB
LABEL_PREWARM_BUDGET_MS = 5.0        # 首次开图预生成预算
LABEL_ZOOM_IDLE_MS = 140             # 缩放停手多久后补新字号档

# 地形路径：连续点击的时间窗、拖拽落点的最小间距、端点半径
TERRAIN_CONTINUE_MS = 3000
TERRAIN_MIN_STEP = 12.0
TERRAIN_NODE_R = 5.5
TERRAIN_UNDO_MAX = 40


def terrain_label(key: str) -> str:
    return TERRAIN_TYPE_MAP.get(str(key or ""), ("地形", ""))[0]


def terrain_color(key: str) -> str:
    entry = TERRAIN_TYPE_MAP.get(str(key or ""))
    return entry[1] if entry else "#6D7B8D"


def draw_terrain_glyph(painter: QPainter, kind: str, color: str, size: float = 30.0) -> None:
    """在 (0,0) 附近按地形类型画矢量小图标（类型选择器与画布共用同一画法）。"""
    c = QColor(color)
    painter.save()
    pen = QPen(c.darker(112), max(1.4, size * 0.07))
    pen.setCosmetic(True)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    painter.setPen(pen)
    fill = QColor(c)
    fill.setAlpha(92)
    painter.setBrush(fill)
    s = size
    if kind == "mountain":
        path = QPainterPath()
        path.moveTo(-s / 2, s * 0.28)
        path.lineTo(-s * 0.12, -s * 0.22)
        path.lineTo(s * 0.1, s * 0.02)
        path.lineTo(s * 0.3, -s * 0.3)
        path.lineTo(s / 2, s * 0.28)
        path.closeSubpath()
        painter.drawPath(path)
    elif kind == "river":
        path = QPainterPath()
        path.moveTo(-s / 2, -s * 0.22)
        path.cubicTo(-s * 0.18, -s * 0.42, s * 0.12, s * 0.02, s / 2, -s * 0.16)
        painter.drawPath(path)
        path2 = QPainterPath()
        path2.moveTo(-s / 2, s * 0.12)
        path2.cubicTo(-s * 0.16, -s * 0.06, s * 0.16, s * 0.34, s / 2, s * 0.18)
        painter.drawPath(path2)
    elif kind == "forest":
        for dx in (-s * 0.22, 0.0, s * 0.22):
            painter.drawPolygon([
                QPointF(dx, -s * 0.32),
                QPointF(dx - s * 0.13, s * 0.05),
                QPointF(dx + s * 0.13, s * 0.05),
            ])
            painter.setBrush(Qt.NoBrush)
            painter.drawLine(QPointF(dx, s * 0.05), QPointF(dx, s * 0.22))
            painter.setBrush(fill)
    elif kind == "lake":
        painter.drawEllipse(QPointF(0, 0.02 * s), s / 2, s * 0.28)
    elif kind == "desert":
        path = QPainterPath()
        path.moveTo(-s / 2, s * 0.2)
        path.cubicTo(-s * 0.24, -s * 0.05, s * 0.02, s * 0.4, s / 2, s * 0.08)
        painter.drawPath(path)
        path2 = QPainterPath()
        path2.moveTo(-s / 2, -s * 0.14)
        path2.cubicTo(-s * 0.24, -s * 0.32, s * 0.02, s * 0.06, s / 2, -s * 0.22)
        painter.drawPath(path2)
    elif kind == "plain":
        painter.setBrush(Qt.NoBrush)
        painter.drawLine(QPointF(-s / 2, s * 0.18), QPointF(s / 2, s * 0.18))
        for dx in (-0.34, -0.05, 0.24):
            painter.drawLine(QPointF(dx * s, s * 0.18),
                             QPointF(dx * s - s * 0.05, -s * 0.02))
            painter.drawLine(QPointF(dx * s, s * 0.18),
                             QPointF(dx * s + s * 0.05, -s * 0.02))
    elif kind == "city":
        painter.drawRoundedRect(QRectF(-s * 0.32, -s * 0.22, s * 0.64, s * 0.5), 2.0, 2.0)
        painter.setBrush(Qt.NoBrush)
        painter.drawLine(QPointF(-s * 0.32, -s * 0.22), QPointF(-s * 0.32, -s * 0.34))
        painter.drawLine(QPointF(s * 0.32, -s * 0.22), QPointF(s * 0.32, -s * 0.34))
        painter.drawLine(QPointF(-s * 0.32, -s * 0.34), QPointF(s * 0.32, -s * 0.34))
        painter.drawLine(QPointF(0, -s * 0.22), QPointF(0, s * 0.28))
    elif kind == "pass":
        painter.setBrush(Qt.NoBrush)
        painter.drawLine(QPointF(-s / 2, -s * 0.05), QPointF(-s * 0.18, -s * 0.05))
        painter.drawLine(QPointF(s * 0.18, -s * 0.05), QPointF(s / 2, -s * 0.05))
        painter.setBrush(fill)
        painter.drawRoundedRect(QRectF(-s * 0.18, -s * 0.3, s * 0.36, s * 0.34), 2.0, 2.0)
        painter.drawRoundedRect(QRectF(-s * 0.07, -s * 0.14, s * 0.14, s * 0.18), 1.0, 1.0)
    else:
        painter.drawEllipse(QPointF(0, 0), s / 3, s / 3)
    painter.restore()


def terrain_pixmap(kind: str, color: str, size: int = 22) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    draw_terrain_glyph(p, kind, color, size * 0.82)
    p.end()
    return pm


# ======================================================================
# 场景元素：地形标注（路径式）
# ======================================================================
def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _path_length(points: list[QPointF]) -> float:
    total = 0.0
    for i in range(len(points) - 1):
        delta = points[i + 1] - points[i]
        total += (delta.x() ** 2 + delta.y() ** 2) ** 0.5
    return total


def _point_at(points: list[QPointF], distance: float) -> tuple[QPointF, QPointF]:
    """按弧长取点，同时返回该处单位法线。"""
    for i in range(len(points) - 1):
        seg = points[i + 1] - points[i]
        length = (seg.x() ** 2 + seg.y() ** 2) ** 0.5
        if length <= 1e-6:
            continue
        if distance <= length or i == len(points) - 2:
            ratio = max(0.0, min(1.0, distance / length))
            point = points[i] + seg * ratio
            normal = QPointF(-seg.y() / length, seg.x() / length)
            return point, normal
        distance -= length
    return points[-1], QPointF(0.0, -1.0)


def _smooth_path(points: list[QPointF], closed: bool = False) -> QPainterPath:
    """Catmull-Rom → 三次贝塞尔，做河流 / 湖泊的平滑轮廓。"""
    pts = list(points)
    if closed and len(pts) >= 3:
        pts = pts + [pts[0], pts[1]]
    path = QPainterPath(pts[0])
    for i in range(len(pts) - 1):
        p0 = pts[i - 1] if i > 0 else pts[i]
        p1 = pts[i]
        p2 = pts[i + 1]
        p3 = pts[i + 2] if i + 2 < len(pts) else p2
        c1 = p1 + (p2 - p0) / 6.0
        c2 = p2 - (p3 - p1) / 6.0
        path.cubicTo(c1, c2, p2)
    return path


class TerrainNodeItem(QGraphicsEllipseItem):
    """地形路径端点：hover 显示，可拖动；拖动后回写父项点列。"""

    def __init__(self, index: int, owner: "TerrainItem") -> None:
        super().__init__(-TERRAIN_NODE_R, -TERRAIN_NODE_R,
                         TERRAIN_NODE_R * 2, TERRAIN_NODE_R * 2, owner)
        self.index = index
        self.owner = owner
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setBrush(QColor(owner.canvas.theme.get("accent") or "#7C5CE6"))
        pen = QPen(QColor(255, 255, 255, 230), 1.2)
        pen.setCosmetic(True)
        self.setPen(pen)
        self.setZValue(3)
        self.setVisible(False)

    def itemChange(self, change, value):  # noqa: N802
        if (change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged
                and not self.owner._syncing_nodes):
            self.owner.on_node_moved(self.index, self.pos())
        return super().itemChange(change, value)

    def hoverEnterEvent(self, event):  # noqa: N802
        self.owner._node_hover = True
        self.owner.set_handles_visible(True)
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):  # noqa: N802
        self.owner._node_hover = False
        self.owner.canvas.schedule_handles_hide(self.owner)
        super().hoverLeaveEvent(event)


class TerrainItem(QGraphicsItem):
    """地形标注：一串归一化点列组成的路径（山脉 / 河流 / 森林 / 湖泊 / 沙漠 / 平原）。

    画布内点坐标为场景坐标；落盘时由画布统一转成相对底图的归一化坐标。
    单点路径仍按旧版矢量图标绘制，兼容迁移过来的旧标注。
    """

    SIZE = 34.0

    def __init__(self, tid: str, kind: str, name: str, note: str,
                 color: str, top: bool, points, canvas: "MapCanvas") -> None:
        super().__init__()
        self.tid = tid
        self.kind = kind if kind in TERRAIN_TYPE_MAP else "mountain"
        self.name = name or terrain_label(self.kind)
        self.note = note
        self.color = color or terrain_color(self.kind)
        self.top = bool(top)
        self.canvas = canvas
        self.points: list[QPointF] = [
            QPointF(float(p.x()), float(p.y())) for p in points
        ] or [QPointF(0.0, 0.0)]
        self._nodes: list[TerrainNodeItem] = []
        self._syncing_nodes = False
        self._node_hover = False
        self._hovered = False

        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setAcceptHoverEvents(True)
        self.setZValue(20 if self.top else 5)
        self._refresh_tooltip()
        self._rebuild_nodes()

    # ------------------------------------------------------------------
    def _refresh_tooltip(self) -> None:
        self.setToolTip(
            f"{self.name}（{terrain_label(self.kind)}）\n{self.note}" if self.note
            else f"{self.name}（{terrain_label(self.kind)}）"
        )

    def _rebuild_nodes(self) -> None:
        for node in self._nodes:
            if node.scene() is not None:
                node.scene().removeItem(node)
        self._nodes = [TerrainNodeItem(i, self) for i in range(len(self.points))]
        self._sync_nodes()

    def _sync_nodes(self) -> None:
        self._syncing_nodes = True
        for i, node in enumerate(self._nodes):
            if i < len(self.points):
                node.setPos(self.points[i])
        self._syncing_nodes = False

    def on_node_moved(self, index: int, pos: QPointF) -> None:
        if 0 <= index < len(self.points):
            self.prepareGeometryChange()
            self.points[index] = QPointF(pos)
            self.update()
            self.canvas.on_item_moved()

    def add_point(self, pos: QPointF) -> int:
        self.prepareGeometryChange()
        self.points.append(QPointF(pos))
        self._rebuild_nodes()
        self.update()
        return len(self.points) - 1

    def remove_point(self, index: int) -> bool:
        if len(self.points) <= 1 or not (0 <= index < len(self.points)):
            return False
        self.prepareGeometryChange()
        del self.points[index]
        self._rebuild_nodes()
        self.update()
        return True

    def remove_last_point(self) -> bool:
        return self.remove_point(len(self.points) - 1)

    def scene_points(self) -> list[QPointF]:
        return [QPointF(p) for p in self.points]

    def set_handles_visible(self, visible: bool) -> None:
        for node in self._nodes:
            node.setVisible(bool(visible))

    def _band_width(self) -> float:
        length = _path_length(self.points)
        return _clamp(length * 0.012, 3.0, 10.0)

    # ------------------------------------------------------------------
    def boundingRect(self) -> QRectF:  # noqa: N802
        if len(self.points) == 1:
            p = self.points[0]
            return QRectF(p.x() - self.SIZE, p.y() - self.SIZE / 2 - 22,
                          self.SIZE * 2, self.SIZE + 44)
        rect = QRectF(self.points[0], QSize(0, 0))
        for p in self.points[1:]:
            rect = rect.united(QRectF(p, QSize(0, 0)))
        margin = _clamp(_path_length(self.points) * 0.08, 30.0, 80.0)
        return rect.adjusted(-margin, -margin - 20, margin, margin + 20)

    def shape(self) -> QPainterPath:  # noqa: N802
        path = QPainterPath()
        if len(self.points) == 1:
            path.addEllipse(self.points[0], 20.0, 20.0)
            return path
        line = QPainterPath(self.points[0])
        for p in self.points[1:]:
            line.lineTo(p)
        stroker = QPainterPathStroker()
        stroker.setWidth(max(16.0, self._band_width() * 2.2))
        path = stroker.createStroke(line)
        for p in self.points:
            path.addEllipse(p, TERRAIN_NODE_R + 2, TERRAIN_NODE_R + 2)
        return path

    def paint(self, painter, option, widget=None):  # noqa: N802
        painter.setRenderHint(QPainter.Antialiasing, True)
        if self.isSelected():
            line = QPainterPath(self.points[0])
            for p in self.points[1:]:
                line.lineTo(p)
            pen = QPen(QColor("#7C5CE6"), 2.2, Qt.PenStyle.DashLine)
            pen.setCosmetic(True)
            if len(self.points) > 1:
                painter.setPen(pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawPath(line)
            else:
                painter.setPen(pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawEllipse(self.points[0], self.SIZE / 2 + 3, self.SIZE / 2 + 3)
        if len(self.points) == 1:
            self._paint_single(painter)
        else:
            self._paint_path(painter)
        self._paint_name(painter)

    def _paint_single(self, painter: QPainter) -> None:
        painter.save()
        painter.translate(self.points[0].x(), self.points[0].y() + 2)
        draw_terrain_glyph(painter, self.kind, self.color, self.SIZE)
        painter.restore()

    def _paint_name(self, painter: QPainter) -> None:
        if not self.name:
            return
        if len(self.points) == 1:
            p = self.points[0]
            anchor = QPointF(p.x(), p.y() - self.SIZE / 2 - 12)
        else:
            mid, normal = _point_at(self.points, _path_length(self.points) / 2.0)
            offset = self._band_width() + 16.0
            anchor = mid + normal * offset
        font = QFont(painter.font())
        font.setPixelSize(12)
        font.setBold(True)
        painter.setFont(font)
        rect = QRectF(anchor.x() - 80, anchor.y() - 8, 160, 16)
        pen = QPen(QColor(0, 0, 0, 100), 2.6)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.drawText(rect, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                         self.name)
        painter.setPen(QPen(QColor(str(self.canvas.theme.get("text") or "#1F2329"))))
        painter.drawText(rect, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                         self.name)

    # ------------------------------------------------------------------
    def _paint_path(self, painter: QPainter) -> None:
        if self.kind == "mountain":
            self._paint_ridge(painter)
        elif self.kind == "river":
            self._paint_river(painter)
        elif self.kind == "forest":
            self._paint_forest(painter)
        elif self.kind in ("lake", "plain"):
            self._paint_area(painter, self.kind == "lake")
        elif self.kind == "desert":
            self._paint_desert(painter)
        else:
            self._paint_glyphs(painter)

    def _paint_glyphs(self, painter: QPainter) -> None:
        for p in self.points:
            painter.save()
            painter.translate(p.x(), p.y() + 2)
            draw_terrain_glyph(painter, self.kind, self.color, self.SIZE * 0.8)
            painter.restore()

    def _paint_ridge(self, painter: QPainter) -> None:
        """山脉：沿路径的锯齿脊线，脊高按总长自适应并夹取范围。"""
        length = _path_length(self.points)
        height = _clamp(length * 0.06, 9.0, 34.0)
        tooth = _clamp(height * 1.5, 16.0, 46.0)
        count = max(2, int(length / tooth))
        fill = QColor(self.color)
        fill.setAlpha(86)
        edge = QPen(QColor(self.color).darker(118), 1.6)
        edge.setCosmetic(True)
        painter.setPen(edge)
        painter.setBrush(fill)
        for i in range(count):
            start = i * tooth
            end = min(length, start + tooth)
            if end <= start:
                continue
            middle = (start + end) / 2.0
            p0, _n0 = _point_at(self.points, start)
            p1, _n1 = _point_at(self.points, end)
            peak, normal = _point_at(self.points, middle)
            peak = peak + normal * height
            path = QPainterPath(p0)
            path.lineTo(peak)
            path.lineTo(p1)
            path.closeSubpath()
            painter.drawPath(path)
        # 脊底：一条随路径的细线，避免锯齿悬空
        pen = QPen(QColor(self.color).darker(130), 1.4)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        path = QPainterPath(self.points[0])
        for p in self.points[1:]:
            path.lineTo(p)
        painter.drawPath(path)

    def _paint_river(self, painter: QPainter) -> None:
        """河流：平滑曲线带轻微宽度。"""
        width = self._band_width()
        path = _smooth_path(self.points)
        outer = QPen(QColor(self.color).darker(125), width + 2.2)
        outer.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(outer)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)
        inner = QPen(QColor(self.color), width)
        inner.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(inner)
        painter.drawPath(path)
        core = QPen(QColor(self.color).lighter(122), max(1.6, width * 0.42))
        core.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(core)
        painter.drawPath(path)

    def _paint_forest(self, painter: QPainter) -> None:
        """森林：沿线的树簇带。"""
        length = _path_length(self.points)
        step = 16.0
        size = _clamp(length * 0.02, 9.0, 17.0)
        fill = QColor(self.color)
        fill.setAlpha(150)
        outline = QPen(QColor(self.color).darker(125), 1.1)
        outline.setCosmetic(True)
        painter.setPen(outline)
        painter.setBrush(fill)
        distance = step / 2.0
        flip = 1
        while distance < length:
            p, normal = _point_at(self.points, distance)
            base = p + normal * (size * 0.45 * flip)
            painter.drawPolygon([
                base + QPointF(0, -size * 0.9),
                base + QPointF(-size * 0.42, size * 0.25),
                base + QPointF(size * 0.42, size * 0.25),
            ])
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawLine(base + QPointF(0, size * 0.25),
                             base + QPointF(0, size * 0.62))
            painter.setBrush(fill)
            distance += step
            flip *= -1

    def _paint_area(self, painter: QPainter, lake: bool) -> None:
        """湖泊（闭合轮廓）/ 平原（浅色区域）。"""
        if len(self.points) < 3:
            p, normal = _point_at(self.points, 0.0)
            painter.setBrush(QColor(self.color))
            painter.setPen(QPen(QColor(self.color).darker(120), 1.4))
            painter.drawEllipse(p, 26.0, 18.0)
            return
        path = _smooth_path(self.points, closed=True)
        fill = QColor(self.color)
        fill.setAlpha(96 if lake else 58)
        painter.setBrush(fill)
        painter.setPen(QPen(QColor(self.color).darker(122), 1.8))
        painter.drawPath(path)
        if not lake:
            # 平原：内部点几笔浅草线，避免像一块死色
            pen = QPen(QColor(self.color).darker(108), 1.2)
            pen.setCosmetic(True)
            painter.setPen(pen)
            for i in range(1, len(self.points) - 1):
                p = self.points[i]
                painter.drawLine(p + QPointF(-7, 4), p + QPointF(-3, -3))
                painter.drawLine(p + QPointF(6, 4), p + QPointF(2, -3))

    def _paint_desert(self, painter: QPainter) -> None:
        """沙漠：沿线波纹带。"""
        length = _path_length(self.points)
        width = _clamp(length * 0.02, 10.0, 26.0)
        base = QColor(self.color)
        base.setAlpha(44)
        band = QPen(base, width)
        band.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(band)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(_smooth_path(self.points))
        pen = QPen(QColor(self.color).darker(112), 1.6)
        pen.setCosmetic(True)
        painter.setPen(pen)
        for offset in (width * 0.32, -width * 0.32):
            wave = QPainterPath()
            first = True
            distance = 0.0
            while distance <= length:
                p, normal = _point_at(self.points, distance)
                amp = 4.2 * (1 if int(distance) % 2 == 0 else -1)
                point = p + normal * (offset + amp * 0.25) + QPointF(0, -amp)
                if first:
                    wave.moveTo(point)
                    first = False
                else:
                    wave.lineTo(point)
                distance += 14.0
            painter.drawPath(wave)

    # ------------------------------------------------------------------
    def itemChange(self, change, value):  # noqa: N802
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            selected = bool(value)
            self.set_handles_visible(selected)
            if not selected:
                self.canvas.schedule_handles_hide(self)
        return super().itemChange(change, value)

    def hoverEnterEvent(self, event):  # noqa: N802
        self._hovered = True
        self.set_handles_visible(True)
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):  # noqa: N802
        self._hovered = False
        self.canvas.schedule_handles_hide(self)
        super().hoverLeaveEvent(event)

    def set_top(self, top: bool) -> None:
        self.top = bool(top)
        self.setZValue(20 if self.top else 5)


# ======================================================================
# 场景元素：势力多边形
# ======================================================================
class LabelBitmapCache:
    """势力名文字位图缓存（普通 LRU，32 张 / 4MB 双硬闸）。

    生命周期跟随地图页：切到写作页时地图窗只是隐藏，缓存保留；
    只有地图页销毁或程序退出才整体释放。释放时只清位图，不碰视图状态。
    """

    def __init__(self, max_items: int = LABEL_BITMAP_MAX_ITEMS,
                 max_bytes: int = LABEL_BITMAP_MAX_BYTES) -> None:
        self.max_items = max_items
        self.max_bytes = max_bytes
        self._items: "OrderedDict[tuple, tuple[QPixmap, float]]" = OrderedDict()
        self.bytes = 0
        self.hits = 0
        self.misses = 0
        self.stale_hits = 0

    @staticmethod
    def _size_of(pm: QPixmap) -> int:
        return int(pm.width()) * int(pm.height()) * 4

    def get(self, key: tuple):
        entry = self._items.pop(key, None)
        if entry is None:
            self.misses += 1
            return None
        self._items[key] = entry
        self.hits += 1
        return entry

    def put(self, key: tuple, pm: QPixmap, scale: float) -> None:
        old = self._items.pop(key, None)
        if old is not None:
            self.bytes -= self._size_of(old[0])
        self._items[key] = (pm, scale)
        self.bytes += self._size_of(pm)
        while (len(self._items) > self.max_items
               or self.bytes > self.max_bytes) and self._items:
            _, value = self._items.popitem(last=False)
            self.bytes -= self._size_of(value[0])
        if self.bytes < 0:
            self.bytes = 0

    def stale_for(self, name: str, lines: tuple, color: str):
        """缩放中缺当前字号档时，拿同名旧档暂顶（最近使用优先）。"""
        for key in reversed(self._items):
            if key[0] == name and key[2] == lines and key[3] == color:
                self.stale_hits += 1
                return self._items[key]
        return None

    def clear(self) -> None:
        self._items.clear()
        self.bytes = 0
        self.hits = self.misses = self.stale_hits = 0

    def stats(self) -> dict:
        return {"items": len(self._items), "bytes": self.bytes,
                "hits": self.hits, "misses": self.misses,
                "stale_hits": self.stale_hits}


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
        self.setToolTip(name)   # 框内容不下时看完整名字

        fill = QColor(color)
        fill.setAlpha(78)
        self.setBrush(fill)

        self._label_key = None
        self._label_cache = (QFont(), [], QRectF(), 6.0)
        self._label_color_key = None
        self._label_color_val = None
        self._apply_pen()

    # ------------------------------------------------------------------
    def _apply_pen(self) -> None:
        pen = QPen(QColor(self.color).darker(115), 3.4 if self.isSelected() else 2.0)
        pen.setCosmetic(True)   # 缩放时描边粗细稳定
        self.setPen(pen)

    def _update_label(self) -> None:
        """字号 / 内边距按框的较短边换算；超长名称按框宽换行、末行省略。"""
        self._label_key = None
        self._label_color_key = None
        self.update()

    def _label_text_color(self) -> QColor:
        """按「半透明填充叠在地图底色上」的实际明度选黑/白字，保证两套主题都清晰。"""
        window = QColor(str(self.canvas.theme.get("window") or "#FFFFFF"))
        key = (self.color, window.name())
        if key == self._label_color_key and self._label_color_val is not None:
            return self._label_color_val
        fill = QColor(self.color)
        fill.setAlpha(78)          # 与势力框填充透明度保持一致
        a = fill.alphaF()
        mixed = QColor(
            int(fill.red() * a + window.red() * (1 - a)),
            int(fill.green() * a + window.green() * (1 - a)),
            int(fill.blue() * a + window.blue() * (1 - a)),
        )
        color = _text_color_for(mixed)
        self._label_color_key = key
        self._label_color_val = color
        return color

    def label_font_px(self) -> int:
        return self._label_metrics()[0].pixelSize()

    def _label_metrics(self) -> tuple[QFont, list[str], QRectF, float]:
        """按框的屏幕尺寸自动铺满：先在屏幕空间算字号/行数，再换算回场景。"""
        poly = self.polygon()
        if poly.isEmpty():
            return QFont(), [], QRectF(), 6.0
        rect = poly.boundingRect()
        scene_w, scene_h = abs(rect.width()), abs(rect.height())
        zoom_raw = max(0.01, float(getattr(self.canvas, "_zoom", 1.0) or 1.0))
        zoom_eff = zoom_raw if zoom_raw <= 1.0 else 1.0   # 缩放 >1 不再额外缩小
        bw, bh = scene_w * zoom_raw, scene_h * zoom_raw   # 框的屏幕逻辑像素
        pad_screen = max(6.0, min(bw, bh) * 0.06)
        # 可用区同时受比例与内边距约束，避免字号按 0.92 算出来却压到边框
        aw = max(1.0, min(bw * FACTION_LABEL_TEXT_W_RATIO, bw - pad_screen * 2))
        ah = max(1.0, min(bh * FACTION_LABEL_TEXT_H_RATIO, bh - pad_screen * 2))
        n_chars = max(1, len(self.name))

        # 枚举 1/2 行，取屏幕字号最大的方案（纯算术，不触发排版测量）
        best_n, best_fs = 1, 0.0
        for n in (1, 2):
            per_line = max(1, math.ceil(n_chars / n))
            fs_w = aw / per_line
            fs_h = ah / (n * FACTION_LABEL_LINE_FACTOR)
            fs = min(fs_w, fs_h)
            if fs > best_fs:
                best_fs, best_n = fs, n

        room = min(aw / 2.0, ah)          # 屏幕下限不得突破可显示空间
        fs_screen = min(best_fs, FACTION_LABEL_MAX_SCREEN_PX)
        floor_px = min(FACTION_LABEL_MIN_SCREEN_PX, room)
        fs_screen = max(min(fs_screen, room), min(floor_px, room))
        fs_screen = max(1.0, min(fs_screen, room))
        fs_scene = fs_screen / zoom_eff

        key = (self.name, int(round(bw)), int(round(bh)),
               best_n, int(round(zoom_raw * 100)))
        if key == self._label_key:
            return self._label_cache

        pad = pad_screen / zoom_raw
        font = QFont()
        font.setBold(True)
        font.setPixelSize(max(1, int(round(fs_scene))))
        fm = QFontMetricsF(font)
        text_w = max(1.0, scene_w - pad * 2)
        text_h = max(1.0, scene_h - pad * 2)

        lines = self._split_name(self.name, best_n)
        # 垂直兜底：Qt 实际行距略大于 1.15em 时逐像素收字号，避免压到边框
        while (fm.lineSpacing() * len(lines) > text_h
               and font.pixelSize() > 1):
            font.setPixelSize(font.pixelSize() - 1)
            fm = QFontMetricsF(font)

        # 水平兜底：屏幕下限仍放不下（窄条 + 长名）→ 前 1–2 字 + …，全名交给 tooltip
        if (fs_screen < FACTION_LABEL_MIN_SCREEN_PX
                or any(fm.horizontalAdvance(line) > text_w for line in lines)):
            lines = self._abbreviate(lines, fm, text_w)

        self._label_key = key
        self._label_cache = (font, lines, rect, pad)
        return self._label_cache

    @staticmethod
    def _abbreviate(lines: list[str], fm: QFontMetricsF, max_w: float) -> list[str]:
        joined = "".join(lines)
        for count in (2, 1):
            if len(joined) <= count:
                break
            candidate = joined[:count] + "…"
            if fm.horizontalAdvance(candidate) <= max_w:
                return [candidate]
        return [joined[:1]]

    @staticmethod
    def _split_name(text: str, n: int) -> list[str]:
        """按字数均分成 1–2 行；优先在空格/标点后断行，避免孤字成行。"""
        if n <= 1 or len(text) <= 2:
            return [text]
        mid = max(1, math.ceil(len(text) / n))
        for delta in (0, -1, 1):
            idx = mid + delta
            if 0 < idx < len(text) and text[idx - 1].isspace():
                return [text[:idx].strip(), text[idx:].strip()]
        while mid > 1 and text[mid] in "，。、！？：；）」』…":
            mid -= 1
        if len(text) - mid == 1:
            mid -= 1
        return [text[:mid], text[mid:]]

    def set_style(self, name: str, color: str) -> None:
        self.name = name
        self.color = color
        self.setToolTip(name)
        fill = QColor(color)
        fill.setAlpha(78)
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

    def _render_label_bitmap(self, font: QFont, lines: list[str], scale: float,
                             color: QColor, dpr: float) -> QPixmap:
        """把文字渲成位图（按 DPR 生成）：绘制时贴图，避免每帧重新栅格化。"""
        fm = QFontMetricsF(font)
        max_adv = max((fm.horizontalAdvance(text) for text in lines), default=0.0)
        line_h = fm.lineSpacing()
        bleed = 3.0 * max(1.0, dpr)          # 屏幕像素外扩，容纳描边
        width_px = max(1, int(math.ceil(max_adv * scale)) + int(bleed * 2) + 2)
        height_px = max(1, int(math.ceil(line_h * len(lines) * scale))
                        + int(bleed * 2) + 2)
        pm = QPixmap(width_px, height_px)
        pm.fill(Qt.GlobalColor.transparent)
        pix = QPainter(pm)
        pix.setRenderHint(QPainter.Antialiasing, True)
        pf = QFont(font)
        pf.setPixelSize(max(1, int(round(font.pixelSize() * scale))))
        pix.setFont(pf)
        pfm = QFontMetricsF(pf)
        outline = QPen(QColor(0, 0, 0, 100), max(1.0, 2.6 * max(1.0, dpr)))
        outline.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        y = float(bleed)
        for text in lines:
            line_rect = QRectF(bleed, y, width_px - bleed * 2, pfm.lineSpacing())
            pix.setPen(outline)
            pix.drawText(line_rect, Qt.AlignmentFlag.AlignHCenter
                         | Qt.AlignmentFlag.AlignVCenter, text)
            pix.setPen(QPen(color))
            pix.drawText(line_rect, Qt.AlignmentFlag.AlignHCenter
                         | Qt.AlignmentFlag.AlignVCenter, text)
            y += pfm.lineSpacing()
        pix.end()
        return pm

    def _label_bitmap(self, metrics=None, text_color: QColor | None = None):
        """取当前字号档的文字位图（LRU 命中 / 缩放旧图暂顶 / 现场生成）。"""
        font, lines, rect, pad = metrics or self._label_metrics()
        if not lines:
            return QPixmap(), 1.0
        zoom_raw = max(0.01, float(getattr(self.canvas, "_zoom", 1.0) or 1.0))
        try:
            dpr = max(1.0, float(self.canvas.devicePixelRatioF()))
        except Exception:
            dpr = 1.0
        bucket = int(round(zoom_raw * 100)) / 100.0
        scale = max(bucket * dpr, 0.01)
        text_color = text_color or self._label_text_color()
        cache: LabelBitmapCache = self.canvas._label_bitmaps
        key = (self.name, font.pixelSize(), tuple(lines),
               text_color.name(), int(round(bucket * 100)))
        entry = cache.get(key)
        if entry is None and self.canvas._label_zoom_timer.isActive():
            # 缩放进行中：旧字号档的图先顶着，不逐帧重建
            entry = cache.stale_for(self.name, tuple(lines), text_color.name())
        if entry is None:
            pm = self._render_label_bitmap(font, lines, scale, text_color, dpr)
            cache.put(key, pm, scale)
            entry = (pm, scale)
        return entry

    def paint(self, painter, option, widget=None):  # noqa: N802
        self._apply_pen()
        super().paint(painter, option, widget)
        if not self.name:
            return
        metrics = self._label_metrics()
        font, lines, rect, pad = metrics
        if not lines:
            return
        pm, entry_scale = self._label_bitmap(metrics, self._label_text_color())
        if pm.isNull():
            return
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, False)
        scene_w = pm.width() / entry_scale
        scene_h = pm.height() / entry_scale
        target = QRectF(rect.center().x() - scene_w / 2,
                        rect.center().y() - scene_h / 2, scene_w, scene_h)
        painter.drawPixmap(target, pm, QRectF(pm.rect()))
        painter.restore()


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
        painter.setPen(QPen(QColor(str(self.canvas.theme.get("text") or "#1F2329"))))
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
    terrain_changed = Signal()    # 地形增删 / 显隐变化（右键菜单计数刷新）
    start_draw_requested = Signal()   # 右键菜单请求进入绘制模式
    start_pin_requested = Signal()    # 右键菜单请求进入点位模式
    start_terrain_requested = Signal()  # 右键菜单请求放置地形

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
        self.setBackgroundBrush(QColor(theme["panel_alt"]))
        self._zoom = 1.0

        self._mode = "pan"
        self._hidden_types: set[str] = set()
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

        # 地形路径落点状态：连续点击 / 按住拖动 / 撤销
        self._terrain_kind = TERRAIN_TYPES[0][0]
        self._terrain_active: TerrainItem | None = None
        self._terrain_last_click = 0.0
        self._terrain_press: QPointF | None = None
        self._terrain_dragging = False
        self._terrain_drag_last: QPointF | None = None
        self._terrain_undo: list[dict] = []
        self._finish_timer = QTimer(self)
        self._finish_timer.setSingleShot(True)
        self._finish_timer.setInterval(TERRAIN_CONTINUE_MS)
        self._finish_timer.timeout.connect(self.finalize_terrain)
        self._handle_timer = QTimer(self)
        self._handle_timer.setSingleShot(True)
        self._handle_timer.setInterval(420)
        self._handle_timer.timeout.connect(self._hide_handles_if_idle)
        self._handle_owner: TerrainItem | None = None
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._mid_panning = False
        self._mid_last = QPointF()

        # 势力名文字位图缓存：跟随地图页存活，切页不清，销毁才释放
        self._label_bitmaps = LabelBitmapCache()
        self._labels_prewarmed = False
        self.last_prewarm_ms = 0.0
        self._label_zoom_timer = QTimer(self)
        self._label_zoom_timer.setSingleShot(True)
        self._label_zoom_timer.setInterval(LABEL_ZOOM_IDLE_MS)
        self._label_zoom_timer.timeout.connect(self.prewarm_labels)

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
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setFixedSize(36, 36)
            button.setIconSize(QSize(18, 18))
            button.setIcon(IconFactory(theme["text"]).icon("add", 18))
            button.setToolTip(tip)
            button.setAccessibleName(tip)
            button.hide()
            button.clicked.connect(lambda _c=False, d=direction: self.expand(d))
            self._plus_buttons[direction] = button

        self.horizontalScrollBar().valueChanged.connect(self._update_edge_buttons)
        self.verticalScrollBar().valueChanged.connect(self._update_edge_buttons)
        self.viewport().installEventFilter(self)

        # 地图：横 / 纵双向隐形快速滚动滑块（原生滚动条隐藏）
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
        for btn in getattr(self, "_plus_buttons", {}).values():
            btn.setIcon(IconFactory(theme["text"]).icon("add", 18))
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

        # 地形标注（旧地图缺键 -> 空列表，不报错）
        hidden_raw = data.get("terrain_hidden")
        self._hidden_types = {
            str(t) for t in hidden_raw if str(t) in TERRAIN_TYPE_MAP
        } if isinstance(hidden_raw, list) else set()
        self._terrain_active = None
        self._terrain_press = None
        self._terrain_dragging = False
        self._terrain_undo.clear()
        self._finish_timer.stop()

        for t in data.get("terrains", []):
            if not isinstance(t, dict):
                continue
            raw_kind = str(t.get("type") or "mountain")
            kind = raw_kind if raw_kind in TERRAIN_TYPE_MAP else "mountain"
            item = TerrainItem(
                str(t.get("id") or new_id()),
                kind,
                str(t.get("name") or terrain_label(kind)),
                str(t.get("note") or ""),
                str(t.get("color") or "") or terrain_color(kind),
                t.get("top") is True,
                self._terrain_points_from_data(t),
                self,
            )
            item.setVisible(kind not in self._hidden_types)
            self._scene.addItem(item)
        self.terrain_changed.emit()

        self.reset_view()

    # ------------------------------------------------------------------
    # 地形路径：归一化坐标 <-> 场景坐标
    # ------------------------------------------------------------------
    def _norm_rect(self) -> QRectF:
        """底图矩形（以数据里的中心 + 宽高为准）；无底图时退回场景矩形。"""
        bg = self._bg_data or {}
        try:
            width = float(bg.get("w") or 0)
            height = float(bg.get("h") or 0)
            cx = float(bg.get("x") or 0)
            cy = float(bg.get("y") or 0)
        except (TypeError, ValueError):
            width = height = 0.0
            cx = cy = 0.0
        if width > 1.0 and height > 1.0:
            return QRectF(cx - width / 2, cy - height / 2, width, height)
        return self._scene.sceneRect()

    def _norm_to_scene(self, nx: float, ny: float) -> QPointF:
        rect = self._norm_rect()
        return QPointF(rect.left() + nx * rect.width(), rect.top() + ny * rect.height())

    def _scene_to_norm(self, point: QPointF) -> list[float]:
        rect = self._norm_rect()
        if rect.width() <= 0 or rect.height() <= 0:
            return [0.0, 0.0]
        return [
            round((point.x() - rect.left()) / rect.width(), 5),
            round((point.y() - rect.top()) / rect.height(), 5),
        ]

    def _terrain_points_from_data(self, data: dict) -> list[QPointF]:
        points: list[QPointF] = []
        raw = data.get("points")
        if isinstance(raw, list):
            for pair in raw:
                if not isinstance(pair, (list, tuple)) or len(pair) < 2:
                    continue
                try:
                    points.append(self._norm_to_scene(float(pair[0]), float(pair[1])))
                except (TypeError, ValueError):
                    continue
        if not points:
            # 兜底：旧数据只有 x/y 时按单点路径载入
            try:
                points = [QPointF(float(data.get("x") or 0), float(data.get("y") or 0))]
            except (TypeError, ValueError):
                points = [QPointF(0.0, 0.0)]
        return points

    def collect(self) -> dict:
        factions: list[dict] = []
        pins: list[dict] = []
        terrains: list[dict] = []
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
            elif isinstance(item, TerrainItem):
                terrains.append(
                    {
                        "id": item.tid,
                        "type": item.kind,
                        "name": item.name,
                        "note": item.note,
                        "color": item.color,
                        "top": bool(item.top),
                        "points": [self._scene_to_norm(p) for p in item.scene_points()],
                    }
                )
        rect = self._scene.sceneRect()
        return {
            "bg": dict(self._bg_data) if self._bg_data else None,
            "factions": factions,
            "pins": pins,
            "terrains": terrains,
            "terrain_hidden": sorted(self._hidden_types),
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
        if mode not in {"pan", "draw", "pin", "terrain"}:
            mode = "pan"
        if mode == self._mode:
            return
        if self._mode == "terrain":
            self.finalize_terrain()
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
        self._label_zoom_timer.start()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if not self._labels_prewarmed:
            self._labels_prewarmed = True
            QTimer.singleShot(0, self.prewarm_labels)

    def prewarm_labels(self, limit: int = 24) -> float:
        """预生成当前可视区内的框名位图（约 10–20 张），避免首帧 miss。"""
        started = time.perf_counter()
        view_rect = self.mapToScene(self.viewport().rect()).boundingRect()
        if view_rect.isEmpty():
            view_rect = self._scene.sceneRect()
        made = 0
        for item in self._scene.items():
            if not isinstance(item, FactionItem) or not item.name:
                continue
            if not item.sceneBoundingRect().intersects(view_rect):
                continue
            item._label_bitmap()
            made += 1
            if made >= limit:
                break
        self.last_prewarm_ms = (time.perf_counter() - started) * 1000.0
        return self.last_prewarm_ms

    def clear_label_cache(self) -> None:
        """只释放文字位图缓存；缩放比例、选中项、滚动位置一律不动。"""
        self._label_bitmaps.clear()
        self.update()

    def zoom_steps(self, steps: float) -> None:
        """按滚轮格数缩放（以视口中心为锚，供 Ctrl+滚轮调用）。"""
        if not steps:
            return
        factor = 1.25 ** steps
        new_zoom = self._zoom * factor
        if ZOOM_MIN <= new_zoom <= ZOOM_MAX:
            self._zoom = new_zoom
            self.scale(factor, factor)
            # 缩放中先用旧字号档的位图顶着，停手后再补新档
            self._label_zoom_timer.start()

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

        if self._mode == "terrain" and event.button() == Qt.MouseButton.LeftButton:
            self.setFocus()
            self._terrain_press = QPointF(scene_pos)
            self._terrain_drag_last = None
            self._terrain_dragging = False
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

        if self._mode == "terrain" and self._terrain_press is not None:
            scene_pos = self.mapToScene(event.position().toPoint())
            if not self._terrain_dragging:
                delta = scene_pos - self._terrain_press
                if (delta.x() ** 2 + delta.y() ** 2) ** 0.5 >= 4.0:
                    self._terrain_dragging = True
                    self._terrain_add_point(self._terrain_press)
                    self._terrain_drag_last = QPointF(self._terrain_press)
            if self._terrain_dragging:
                last = self._terrain_drag_last or self._terrain_press
                if (scene_pos - last).manhattanLength() >= TERRAIN_MIN_STEP:
                    self._terrain_add_point(scene_pos)
                    self._terrain_drag_last = QPointF(scene_pos)
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

        if (self._mode == "terrain"
                and event.button() == Qt.MouseButton.LeftButton
                and self._terrain_press is not None):
            if not self._terrain_dragging:
                self._terrain_add_point(self.mapToScene(event.position().toPoint()))
            self._terrain_press = None
            self._terrain_dragging = False
            self._terrain_drag_last = None
            self._finish_timer.start()
            event.accept()
            return

        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if self._mode == "draw":
            self._finish_pending()
            event.accept()
            return
        item = self._top_item_at(event.position().toPoint())
        if isinstance(item, (FactionItem, PinItem, TerrainItem)):
            self._edit_item(item)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        raw_item = next(iter(self.items(event.pos())), None)
        if isinstance(raw_item, TerrainNodeItem):
            owner = raw_item.owner
            menu = QMenu(self)
            if len(owner.points) > 1:
                menu.addAction("删除该节点",
                               lambda: self.remove_terrain_point(owner, raw_item.index))
            menu.addSeparator()
            menu.addAction("删除整条地形", lambda: self._remove_terrain(owner))
            menu.exec(event.globalPos())
            return

        item = self._top_item_at(event.pos())
        if isinstance(item, (FactionItem, PinItem, TerrainItem)):
            item.setSelected(True)
            menu = QMenu(self)
            if isinstance(item, FactionItem):
                menu.addAction("编辑势力", lambda: self._edit_item(item))
                menu.addSeparator()
                menu.addAction("删除势力", lambda: self._remove_item(item))
            elif isinstance(item, TerrainItem):
                menu.addAction("编辑地形", lambda: self._edit_item(item))
                top_action = menu.addAction(
                    "取消置顶" if item.top else "图层置顶", lambda: self._toggle_terrain_top(item)
                )
                top_action.setCheckable(True)
                top_action.setChecked(bool(item.top))
                menu.addSeparator()
                menu.addAction("删除整条地形", lambda: self._remove_terrain(item))
            else:
                menu.addAction("编辑点位", lambda: self._edit_item(item))
                menu.addSeparator()
                menu.addAction("删除点位", lambda: self._remove_item(item))
            menu.exec(event.globalPos())
            return

        if self._mode == "draw":
            self._finish_pending()
            return

        self.finalize_terrain()
        menu = QMenu(self)
        menu.addAction("添加势力", self.start_draw_requested.emit)
        menu.addAction("添加点位", self.start_pin_requested.emit)
        menu.addAction("添加地形", self.start_terrain_requested.emit)
        menu.addSeparator()

        # 原图例浮层：全显 / 全隐 / 分类型显隐，挪到空白处右键菜单
        menu.addMenu(self.build_terrain_visibility_menu())

        menu.addSeparator()
        menu.addAction("复位视图", self.reset_view)
        menu.exec(event.globalPos())

    def build_terrain_visibility_menu(self) -> QMenu:
        """右键菜单「地形显示」子菜单：全显 / 全隐 / 按类型勾选（持久化到 terrain_hidden）。"""
        menu = QMenu("地形显示", self)
        show_all = menu.addAction("全部显示")
        show_all.triggered.connect(lambda _c=False: self.set_all_types_visible(True))
        hide_all = menu.addAction("全部隐藏")
        hide_all.triggered.connect(lambda _c=False: self.set_all_types_visible(False))
        menu.addSeparator()
        counts = self.terrain_counts()
        for kind, label, _color in TERRAIN_TYPES:
            action = menu.addAction(f"{label}（{counts.get(kind, 0)}）")
            action.setCheckable(True)
            action.setChecked(self.is_type_visible(kind))
            action.toggled.connect(
                lambda checked, k=kind: self.set_type_visible(k, bool(checked))
            )
        return menu

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
    # 地形标注（路径式落点）
    # ------------------------------------------------------------------
    def start_terrain(self, kind: str | None = None) -> None:
        if kind:
            self.set_terrain_kind(kind)
        self.set_mode("terrain")

    def set_terrain_kind(self, kind: str) -> None:
        if kind not in TERRAIN_TYPE_MAP:
            return
        if kind != self._terrain_kind:
            self.finalize_terrain()
            self._terrain_kind = kind

    def _terrain_add_point(self, scene_pos: QPointF) -> None:
        """单击 / 拖动落点：同一类型且时间窗内则并入当前路径，否则新开一条。"""
        now = time.monotonic()
        active = self._terrain_active
        can_continue = (
            active is not None
            and active.kind == self._terrain_kind
            and active.scene() is not None
            and now - self._terrain_last_click <= TERRAIN_CONTINUE_MS / 1000.0
        )
        if not can_continue:
            self.finalize_terrain()
            if not self.is_type_visible(self._terrain_kind):
                self.set_type_visible(self._terrain_kind, True)
            item = TerrainItem(
                new_id(),
                self._terrain_kind,
                terrain_label(self._terrain_kind),
                "",
                terrain_color(self._terrain_kind),
                False,
                [QPointF(scene_pos)],
                self,
            )
            self._scene.addItem(item)
            self._terrain_active = item
            self._push_terrain_undo({"op": "add_path", "id": item.tid})
        else:
            last = active.points[-1]
            if (scene_pos - last).manhattanLength() < 1.0:
                return
            index = active.add_point(scene_pos)
            self._push_terrain_undo(
                {"op": "add_point", "id": active.tid, "index": index}
            )
        self._terrain_last_click = now
        self.terrain_changed.emit()
        self._emit_changed()

    def finalize_terrain(self) -> None:
        """结束当前路径（3 秒无新点 / 切模式 / 右键空白）。数据已在画布中。"""
        self._finish_timer.stop()
        if self._terrain_active is not None:
            self._terrain_active = None
            self.terrain_changed.emit()

    def _push_terrain_undo(self, action: dict) -> None:
        self._terrain_undo.append(action)
        if len(self._terrain_undo) > TERRAIN_UNDO_MAX:
            del self._terrain_undo[0]

    def _terrain_by_id(self, tid: str) -> "TerrainItem | None":
        for item in self._scene.items():
            if isinstance(item, TerrainItem) and item.tid == tid:
                return item
        return None

    def undo_terrain(self) -> bool:
        """Ctrl+Z：撤销上一次落点 / 上一条路径。"""
        if not self._terrain_undo:
            return False
        action = self._terrain_undo.pop()
        item = self._terrain_by_id(str(action.get("id") or ""))
        if action.get("op") == "add_point":
            if item is not None and len(item.points) > 1:
                index = action.get("index")
                if not isinstance(index, int) or not (0 <= index < len(item.points)):
                    index = len(item.points) - 1
                item.remove_point(index)
            elif item is not None:
                self._remove_terrain(item)
        else:
            if item is not None:
                self._remove_terrain(item)
        self._emit_changed()
        self.terrain_changed.emit()
        return True

    def remove_terrain_point(self, item: "TerrainItem", index: int) -> None:
        if item.remove_point(index):
            self._emit_changed()
            self.terrain_changed.emit()
        else:
            self._remove_terrain(item)

    def _remove_terrain(self, item: "TerrainItem") -> None:
        if item is self._terrain_active:
            self.finalize_terrain()
        self._scene.removeItem(item)
        self._emit_changed()
        self.terrain_changed.emit()

    def _remove_selected_terrain(self) -> bool:
        nodes = [i for i in self._scene.selectedItems() if isinstance(i, TerrainNodeItem)]
        if nodes:
            for node in nodes:
                self.remove_terrain_point(node.owner, node.index)
            return True
        items = [i for i in self._scene.selectedItems() if isinstance(i, TerrainItem)]
        for item in items:
            self._remove_terrain(item)
        return bool(items)

    def schedule_handles_hide(self, item: "TerrainItem") -> None:
        self._handle_owner = item
        self._handle_timer.start()

    def _hide_handles_if_idle(self) -> None:
        item = self._handle_owner
        if item is None or item.scene() is None:
            return
        if item.isSelected() or item._hovered or item._node_hover:
            return
        item.set_handles_visible(False)

    def _toggle_terrain_top(self, item: "TerrainItem") -> None:
        item.set_top(not item.top)
        item.update()
        self._emit_changed()

    def set_type_visible(self, kind: str, visible: bool) -> None:
        """按类型整组显示 / 隐藏（选择状态保留）；无变化不落盘。"""
        if kind not in TERRAIN_TYPE_MAP:
            return
        hidden_now = kind in self._hidden_types
        if visible and not hidden_now:
            return
        if not visible and hidden_now:
            return
        if visible:
            self._hidden_types.discard(kind)
        else:
            self._hidden_types.add(kind)
        for item in self._scene.items():
            if isinstance(item, TerrainItem) and item.kind == kind:
                item.setVisible(visible)
        self.terrain_changed.emit()
        self.changed.emit()     # 显隐持久化（terrain_hidden）

    def set_all_types_visible(self, visible: bool) -> None:
        """全显 / 全隐：批量改完只发一次保存信号。"""
        target = set() if visible else {k for k, _l, _c in TERRAIN_TYPES}
        if target == self._hidden_types:
            return
        self._hidden_types = target
        for item in self._scene.items():
            if isinstance(item, TerrainItem):
                item.setVisible(item.kind not in self._hidden_types)
        self.terrain_changed.emit()
        self.changed.emit()

    def is_type_visible(self, kind: str) -> bool:
        return kind not in self._hidden_types

    def terrain_counts(self) -> dict:
        counts = {key: 0 for key, _l, _c in TERRAIN_TYPES}
        for item in self._scene.items():
            if isinstance(item, TerrainItem) and item.kind in counts:
                counts[item.kind] += 1
        return counts

    # ------------------------------------------------------------------
    # 元素编辑 / 删除
    # ------------------------------------------------------------------
    def _top_item_at(self, view_pos) -> QGraphicsItem | None:
        for item in self.items(view_pos):
            if isinstance(item, (FactionItem, PinItem, TerrainItem)):
                return item
        return None

    def _edit_item(self, item) -> None:
        if isinstance(item, FactionItem):
            dialog = FactionEditDialog(self, item.name, item.color)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                name, color = dialog.values()
                item.set_style(name, color)
                self._emit_changed()
        elif isinstance(item, TerrainItem):
            dialog = TerrainEditDialog(self, item.kind, item.name, item.note,
                                       item.color, item.top)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                kind, name, note, color, top = dialog.values()
                item.kind = kind if kind in TERRAIN_TYPE_MAP else item.kind
                item.name = name
                item.note = note
                item.color = color or terrain_color(item.kind)
                item.set_top(top)
                item._refresh_tooltip()
                item.setVisible(item.kind not in self._hidden_types)
                item.update()
                self._emit_changed()
                self.terrain_changed.emit()
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
        if isinstance(item, TerrainItem):
            self._remove_terrain(item)
            return
        self._scene.removeItem(item)
        self._emit_changed()

    # ------------------------------------------------------------------
    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.matches(QKeySequence.StandardKey.Undo):
            if self.undo_terrain():
                event.accept()
                return
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            if self._remove_selected_terrain():
                event.accept()
                return
        if event.key() == Qt.Key.Key_Escape:
            if self._mode == "draw":
                self._cancel_pending(keep_mode=False)
            elif self._mode == "terrain":
                self.finalize_terrain()
                self.set_mode("pan")
            else:
                self.set_mode("pan")
            event.accept()
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self._mode == "draw":
                self._finish_pending()
                event.accept()
                return
            if self._mode == "terrain":
                self.finalize_terrain()
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
        from app.ui.scroll_float import attach_flasher

        attach_flasher(self.desc_edit, Qt.Vertical, inset=6)
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


class TerrainEditDialog(QDialog):
    """地形标注编辑：类型 + 名称 + 备注 + 颜色 + 图层置顶。"""

    def __init__(self, parent=None, kind: str = "", name: str = "",
                 note: str = "", color: str = "", top: bool = False) -> None:
        super().__init__(parent)
        self.setWindowTitle("地形设置")
        self.setModal(True)
        self.resize(420, 360)
        self._kind = kind if kind in TERRAIN_TYPE_MAP else TERRAIN_TYPES[0][0]
        self._color = color or terrain_color(self._kind)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 16)
        layout.setSpacing(10)

        type_row = QHBoxLayout()
        type_row.setSpacing(8)
        type_row.addWidget(QLabel("类型", self))
        self.type_box = QComboBox(self)
        for key, label, default_color in TERRAIN_TYPES:
            self.type_box.addItem(label, key)
        self.type_box.setCurrentIndex(next(
            (i for i, (k, _l, _c) in enumerate(TERRAIN_TYPES) if k == self._kind), 0
        ))
        self.type_box.currentIndexChanged.connect(self._on_type_changed)
        type_row.addWidget(self.type_box, 1)
        layout.addLayout(type_row)

        self.name_edit = QLineEdit(name or terrain_label(self._kind), self)
        self.name_edit.setPlaceholderText("标记名称，例如：苍梧山脉 / 洛水")
        layout.addWidget(self.name_edit)

        self.note_edit = QPlainTextEdit(self)
        self.note_edit.setObjectName("Preview")
        self.note_edit.setPlaceholderText("备注（可选）：险要、典故、通行情况……")
        self.note_edit.setPlainText(note)
        self.note_edit.setFixedHeight(78)
        from app.ui.scroll_float import attach_flasher

        attach_flasher(self.note_edit, Qt.Vertical, inset=6)
        layout.addWidget(self.note_edit)

        layout.addWidget(QLabel("颜色", self))
        color_box = QHBoxLayout()
        color_box.setSpacing(8)
        # 先放「类型默认色」，再给地图通用色板便于自定义
        default_button = QPushButton(self)
        default_button.setFixedSize(26, 26)
        default_button.setCursor(Qt.CursorShape.PointingHandCursor)
        default_button.setToolTip("类型默认色")
        default_button.setStyleSheet(
            f"QPushButton{{background:{terrain_color(self._kind)};border-radius:13px;"
            "border:2px dashed #1F2329;}}"
        )
        default_button.clicked.connect(
            lambda _c=False: self._pick(terrain_color(self.type_box.currentData()))
        )
        color_box.addWidget(default_button)
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

        self.top_check = QCheckBox("图层置顶（压在势力 / 点位之上）", self)
        self.top_check.setChecked(bool(top))
        layout.addWidget(self.top_check)

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

    def _on_type_changed(self, _index: int) -> None:
        self._kind = self.type_box.currentData()
        if not self.name_edit.text().strip() or self.name_edit.text().strip() in {
            label for _k, label, _c in TERRAIN_TYPES
        }:
            self.name_edit.setText(terrain_label(self._kind))

    def _pick(self, value: str) -> None:
        self._color = value

    def values(self) -> tuple[str, str, str, str, bool]:
        return (
            str(self.type_box.currentData()),
            self.name_edit.text().strip() or terrain_label(self.type_box.currentData()),
            self.note_edit.toPlainText().strip(),
            self._color,
            self.top_check.isChecked(),
        )


# ======================================================================
# 主窗口
# ======================================================================
class MapDialog(QDialog):
    """地图主窗口：工具条 + 地形类型选择 + QGraphicsView 画布 + 边缘扩展按钮。"""

    changed = Signal()  # 数据变化（由主窗口负责持久化到 map_global.json）

    def __init__(self, data: dict, theme: dict, parent=None) -> None:
        super().__init__(parent)
        self.data = dict(data)
        self.setWindowTitle(f"地图 · {self.data.get('title') or '未命名地图'}")
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
        subtitle = QLabel("地图 · 独立保存", bar)
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
        self.terrain_button = add_button("添加地形", self._start_terrain, checkable=True)
        self.terrain_kind_box = QComboBox(bar)
        self.terrain_kind_box.setObjectName("MapTerrainKind")
        self.terrain_kind_box.setCursor(Qt.CursorShape.PointingHandCursor)
        self.terrain_kind_box.setToolTip("当前要落点的地形类型")
        self.terrain_kind_box.setFixedWidth(104)
        for key, label, color in TERRAIN_TYPES:
            self.terrain_kind_box.addItem(terrain_pixmap(key, color, 18), label, key)
        self.terrain_kind_box.currentIndexChanged.connect(
            lambda _index: self.canvas.set_terrain_kind(
                str(self.terrain_kind_box.currentData())
            )
        )
        bar_layout.addWidget(self.terrain_kind_box)
        add_button("导入底图", self._import_bg)
        add_button("清除底图", self._clear_bg)
        add_button("导出图片", self._export_png)
        self.save_button = add_button("保存", self._flush_save)

        hint = QLabel("Ctrl+滚轮缩放 · 拖动平移 · 右键编辑 · 地形显隐在右键菜单", bar)
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
        self.canvas.start_terrain_requested.connect(self._start_terrain)
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

    def _start_terrain(self) -> None:
        if self.canvas.mode() == "terrain":
            self.canvas.set_mode("pan")
        else:
            self.canvas.start_terrain(str(self.terrain_kind_box.currentData()))
        self._sync_tool_buttons(self.canvas.mode())

    def _sync_tool_buttons(self, mode: str) -> None:
        self.draw_button.setChecked(mode == "draw")
        self.pin_button.setChecked(mode == "pin")
        self.terrain_button.setChecked(mode == "terrain")

    def _import_bg(self) -> None:
        self.canvas.import_bg()

    def _clear_bg(self) -> None:
        self.canvas.clear_bg()

    def reload_meta(self, item: dict) -> None:
        """外部重命名后刷新标题显示。"""
        self.data["title"] = str(item.get("title") or "")
        self.setWindowTitle(f"地图 · {self.data.get('title') or '未命名地图'}")

    # ------------------------------------------------------------------
    def _collect_silent(self) -> None:
        self.data.update(self.canvas.collect())

    def _collect(self) -> None:
        """画布变化后同步数据并交给主窗口持久化。"""
        self._collect_silent()
        self.changed.emit()

    def _flush_save(self) -> None:
        self._collect()
        flash_saved(self.save_button, IconFactory(self._theme_text()))

    def _theme_text(self) -> str:
        return str(self.canvas.theme.get("text") or "#1F2329")

    def flush_pending(self) -> None:
        """主窗口退出前兜底保存。"""
        self._collect()

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
