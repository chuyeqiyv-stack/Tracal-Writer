# -*- coding: utf-8 -*-
"""大纲（思维导图）：大尺寸独立窗口。

- 卡片节点可自由拖动，双击就地编辑文字，卡片大小随文字自动调整；
- 选中卡片后出现八方连接点，向任意方向拖出箭头即可引申新卡片，
  或连接到已有卡片（思维导图常规操作）；
- 连线风格逐条独立：先在上方工具条选择「直线 / 曲线」（新连线的编辑模式），
  再从连接点拖出，每条连线各自记住自己的风格并随大纲持久化，末端带三角箭头；
- 卡片支持更换颜色；右键菜单可编辑 / 换色 / 添加子节点 / 删除；
- 画布可按住空白拖动平移；拖到四向边界出现「＋」按钮扩容画布，
  左下角「复位」按钮一键回到画布中心；
- 数据持久化到 library.json 的大纲条目中（含画布尺寸）。
"""
from __future__ import annotations

import time

from PySide6.QtCore import QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QKeySequence, QPainter, QPainterPath, QPen, QShortcut
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.models import new_id
from app.ui.dialogs import MessageBox

CANVAS_WIDTH = 4800
CANVAS_HEIGHT = 3000
EXTEND_STEP = 2400         # 每次向一个方向扩容的步长
NODE_MAX_TEXT_W = 176      # 文字换行最大宽度
ZOOM_MIN, ZOOM_MAX = 0.5, 2.0
ZOOM_STEP_FACTOR = 1.1     # 每格滚轮的缩放系数（Ctrl+滚轮）
NODE_PAD_X = 16
NODE_PAD_Y = 12
NODE_MIN_W = 64
NODE_MIN_H = 40

# 卡片预设颜色（空字符串 = 跟随主题）
NODE_COLORS: list[tuple[str, str]] = [
    ("", "主题色"),
    ("#FDE3E1", "浅红"),
    ("#FCEBD3", "浅橙"),
    ("#FAF2CE", "浅黄"),
    ("#DFF2DF", "浅绿"),
    ("#DEEDF8", "浅蓝"),
    ("#EAE2F8", "浅紫"),
    ("#F9E2ED", "浅粉"),
    ("#E7E9EE", "浅灰"),
]

# 八方连接点方向（相对卡片中心的角度偏移比例）
HANDLES = [
    (0.0, -1.0), (0.75, -1.0), (1.0, 0.0), (1.0, 0.75),
    (0.0, 1.0), (-0.75, 1.0), (-1.0, 0.0), (-1.0, -0.75),
]


def _luma(color: QColor) -> float:
    return (0.299 * color.red() + 0.587 * color.green() + 0.114 * color.blue()) / 255.0


class OutlineCanvas(QWidget):
    """思维导图画布：卡片与连线全部自绘。"""

    changed = Signal()          # 节点 / 连线变化后发出（触发保存）
    bounds_changed = Signal()   # 画布尺寸变化后发出（更新边界按钮）

    def __init__(self, theme: dict, parent=None) -> None:
        super().__init__(parent)
        self.theme = theme
        self.nodes: list[dict] = []
        self.edges: list[dict] = []
        self.edge_style = "ortho"     # 新连线的默认风格（编辑模式）：ortho / curve
        self._width = CANVAS_WIDTH
        self._height = CANVAS_HEIGHT
        self._zoom = 1.0
        self._apply_view_size()
        self.setCursor(Qt.OpenHandCursor)
        self.setAttribute(Qt.WA_NoSystemBackground, True)

        self._selected_id = ""
        self._drag_node: dict | None = None
        self._press_pos = QPointF()
        self._press_node_pos = QPointF()
        self._moved = False

        self._panning = False
        self._press_scroll = None     # (h_value, v_value, pos)

        self._connecting = False      # 正在从连接点拖出箭头
        self._connect_from: dict | None = None
        self._connect_pos = QPointF()

        self._hover_node: dict | None = None
        self._editor: QLineEdit | None = None
        self._editing_id = ""
        self.setMouseTracking(True)

    # ------------------------------------------------------------------
    # 数据
    # ------------------------------------------------------------------
    def set_theme(self, theme: dict) -> None:
        self.theme = theme
        self.update()

    def set_data(self, item: dict) -> None:
        self.nodes = [dict(n) for n in (item.get("nodes") or [])]
        self.edges = [dict(e) for e in (item.get("edges") or [])]
        style = str(item.get("edge_style") or "ortho")
        self.edge_style = style if style in {"ortho", "curve"} else "ortho"
        # 恢复上次扩容后的画布尺寸（老数据没有这两个字段时用默认值）
        try:
            self._width = max(CANVAS_WIDTH, int(item.get("canvas_width") or 0))
            self._height = max(CANVAS_HEIGHT, int(item.get("canvas_height") or 0))
        except (TypeError, ValueError):
            self._width, self._height = CANVAS_WIDTH, CANVAS_HEIGHT
        self._apply_view_size()
        self._selected_id = ""
        self._stop_edit(commit=False)
        self.update()

    def collect(self) -> tuple[list[dict], list[dict]]:
        return [dict(n) for n in self.nodes], [dict(e) for e in self.edges]

    def set_edge_style(self, style: str) -> None:
        """设定「新连线」的默认风格（已有连线各自保留自己的风格）。"""
        if style not in {"ortho", "curve"} or style == self.edge_style:
            return
        self.edge_style = style
        self.update()
        self.changed.emit()

    def canvas_bounds(self) -> tuple[int, int]:
        """当前画布尺寸（用于持久化）。"""
        return self._width, self._height

    # ------------------------------------------------------------------
    # 缩放（Ctrl + 滚轮）：控件尺寸 = 逻辑尺寸 × 缩放，绘制整体放大
    # ------------------------------------------------------------------
    def _apply_view_size(self) -> None:
        self.setFixedSize(
            max(1, int(self._width * self._zoom)),
            max(1, int(self._height * self._zoom)),
        )

    def _to_logical(self, pos: QPointF) -> QPointF:
        z = self._zoom
        return QPointF(pos.x() / z, pos.y() / z)

    def zoom_at(self, steps: float, device_pos: QPointF) -> None:
        """以视口中心为锚缩放（内容整体向中心收放，不跟随光标）。"""
        old = self._zoom
        new = min(ZOOM_MAX, max(ZOOM_MIN, old * (ZOOM_STEP_FACTOR ** steps)))
        if abs(new - old) < 1e-9:
            return
        h_bar, v_bar = self._scrollbars()
        viewport = self._viewport()
        vw = viewport.width() if viewport is not None else self.width()
        vh = viewport.height() if viewport is not None else self.height()
        # 视口中心对应的逻辑坐标（缩放前后保持该点位于视口中心）
        cx = (h_bar.value() + vw / 2) / old if h_bar is not None else vw / 2 / old
        cy = (v_bar.value() + vh / 2) / old if v_bar is not None else vh / 2 / old
        self._zoom = new
        self._apply_view_size()
        self._place_editor()
        if h_bar is not None:
            h_bar.setValue(int(cx * new - vw / 2))
        if v_bar is not None:
            v_bar.setValue(int(cy * new - vh / 2))
        self.update()
        self.bounds_changed.emit()

    def wheelEvent(self, event) -> None:  # noqa: N802
        if event.modifiers() & Qt.ControlModifier:
            steps = event.angleDelta().y() / 120.0
            if steps:
                self.zoom_at(steps, QPointF(event.position()))
            event.accept()
            return
        super().wheelEvent(event)

    # ------------------------------------------------------------------
    # 画布扩容：向左 / 上扩容时平移全部节点，保证现有内容在视口中不动
    # ------------------------------------------------------------------
    def extend(self, direction: str) -> None:
        self._stop_edit(commit=True)
        shift_x = EXTEND_STEP if direction == "left" else 0
        shift_y = EXTEND_STEP if direction == "top" else 0
        if shift_x or shift_y:
            for node in self.nodes:
                node["x"] = float(node.get("x") or 0) + shift_x
                node["y"] = float(node.get("y") or 0) + shift_y
            self._selected_id = ""
        if direction in ("left", "right"):
            self._width += EXTEND_STEP
        else:
            self._height += EXTEND_STEP
        self._apply_view_size()
        self.update()
        self.bounds_changed.emit()
        if shift_x or shift_y:
            # 平移补偿滚动条（等布局刷新后执行，避免被钳制）
            h_bar, v_bar = self._scrollbars()

            def _compensate(dx: int, dy: int, h, v) -> None:
                if dx and h is not None:
                    h.setValue(h.value() + dx)
                if dy and v is not None:
                    v.setValue(v.value() + dy)

            QTimer.singleShot(0, lambda: _compensate(shift_x, shift_y, h_bar, v_bar))
        self.changed.emit()

    # ------------------------------------------------------------------
    # 几何辅助
    # ------------------------------------------------------------------
    def node_rect(self, node: dict) -> QRectF:
        """根据文字自动计算卡片大小（随文字自动调整）。"""
        text = str(node.get("text") or "").strip() or "双击编辑"
        font = self._node_font()
        metrics = QFontMetrics(font)
        rect = metrics.boundingRect(
            QRect(0, 0, NODE_MAX_TEXT_W, 4000), Qt.TextWordWrap, text
        )
        width = max(NODE_MIN_W, rect.width() + NODE_PAD_X * 2)
        height = max(NODE_MIN_H, rect.height() + NODE_PAD_Y * 2)
        return QRectF(float(node.get("x") or 0), float(node.get("y") or 0), width, height)

    def _node_font(self) -> QFont:
        font = QFont(self.font())
        font.setPixelSize(14)
        return font

    def _node_by_id(self, node_id: str) -> dict | None:
        return next((n for n in self.nodes if n.get("id") == node_id), None)

    def _handle_pos(self, rect: QRectF, index: int) -> QPointF:
        fx, fy = HANDLES[index]
        return QPointF(
            rect.center().x() + fx * rect.width() / 2,
            rect.center().y() + fy * rect.height() / 2,
        )

    def _hit_test(self, pos: QPointF) -> tuple[dict | None, int]:
        """返回 (节点, 连接点序号)；连接点序号 -1 表示命中卡片主体。"""
        for node in reversed(self.nodes):
            rect = self.node_rect(node)
            if node.get("id") == self._selected_id:
                for i in range(len(HANDLES)):
                    if (self._handle_pos(rect, i) - pos).manhattanLength() <= 10:
                        return node, i
            if rect.contains(pos):
                return node, -1
        return None, -1

    def _edge_route(self, ra: QRectF, rb: QRectF) -> list[QPointF]:
        """两张卡片之间的正交折线路径：横平竖直，中段一处直角拐弯。"""
        ca, cb = ra.center(), rb.center()
        dx, dy = cb.x() - ca.x(), cb.y() - ca.y()
        if abs(dx) >= abs(dy):
            # 横向为主：从 a 的左/右边缘水平出，中间竖直拐一段，水平进 b
            exit_x = ra.right() if dx > 0 else ra.left()
            enter_x = rb.left() if dx > 0 else rb.right()
            mid_x = (exit_x + enter_x) / 2
            return [
                QPointF(exit_x, ca.y()),
                QPointF(mid_x, ca.y()),
                QPointF(mid_x, cb.y()),
                QPointF(enter_x, cb.y()),
            ]
        # 纵向为主：从 a 的上/下边缘垂直出，中间水平拐一段，垂直进 b
        exit_y = ra.bottom() if dy > 0 else ra.top()
        enter_y = rb.bottom() if dy > 0 else rb.top()
        mid_y = (exit_y + enter_y) / 2
        return [
            QPointF(ca.x(), exit_y),
            QPointF(ca.x(), mid_y),
            QPointF(cb.x(), mid_y),
            QPointF(cb.x(), enter_y),
        ]

    def _edge_style_of(self, edge: dict) -> str:
        """单条连线的风格：优先用连线自己的记录，老数据跟随当前默认。"""
        style = str(edge.get("style") or "")
        return style if style in {"ortho", "curve"} else self.edge_style

    def _edge_path(
        self, ra: QRectF, rb: QRectF, style: str
    ) -> tuple[QPainterPath, QPointF, QPointF]:
        """两张卡片之间的连线路径。

        返回 (路径, 末端点, 末端方向向量)；风格由 style 决定：
        ortho 横平竖直折线 / curve 平滑贝塞尔曲线。
        """
        path = QPainterPath()
        ca, cb = ra.center(), rb.center()
        dx, dy = cb.x() - ca.x(), cb.y() - ca.y()
        if style == "curve":
            if abs(dx) >= abs(dy):
                # 横向为主：左右边缘水平出、水平进，中段平滑弯曲
                start = QPointF(ra.right() if dx > 0 else ra.left(), ca.y())
                end = QPointF(rb.left() if dx > 0 else rb.right(), cb.y())
                span = max(24.0, abs(end.x() - start.x()) * 0.5)
                c1 = QPointF(start.x() + (span if dx > 0 else -span), start.y())
                c2 = QPointF(end.x() - (span if dx > 0 else -span), end.y())
            else:
                # 纵向为主：上下边缘垂直出、垂直进
                start = QPointF(ca.x(), ra.bottom() if dy > 0 else ra.top())
                end = QPointF(cb.x(), rb.bottom() if dy > 0 else rb.top())
                span = max(24.0, abs(end.y() - start.y()) * 0.5)
                c1 = QPointF(start.x(), start.y() + (span if dy > 0 else -span))
                c2 = QPointF(end.x(), end.y() - (span if dy > 0 else -span))
            path.moveTo(start)
            path.cubicTo(c1, c2, end)
            # 曲线末端方向手动计算（angleAtPercent 的角度约定不可靠）
            return path, end, end - c2
        points = self._edge_route(ra, rb)
        path.moveTo(points[0])
        for p in points[1:]:
            path.lineTo(p)
        return path, points[-1], points[-1] - points[-2]

    # ------------------------------------------------------------------
    # 绘制
    # ------------------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        theme = self.theme

        painter.fillRect(self.rect(), QColor(theme["panel_alt"]))

        # 逻辑坐标绘制（整体按缩放放大）
        painter.scale(self._zoom, self._zoom)

        # 桌面点阵（浅色网格点，只画当前可视区域，避免大画布卡顿）
        dot = QColor(theme["border"])
        dot.setAlpha(150)
        painter.setPen(Qt.NoPen)
        painter.setBrush(dot)
        step = 44
        dev = event.rect()
        z = self._zoom
        clip = QRectF(dev.x() / z, dev.y() / z, dev.width() / z, dev.height() / z)
        x0 = max(0, int(clip.left()) // step) * step + step // 2
        y0 = max(0, int(clip.top()) // step) * step + step // 2
        y = y0
        while y <= clip.bottom():
            x = x0
            while x <= clip.right():
                painter.drawEllipse(QPointF(x, y), 1.2, 1.2)
                x += step
            y += step

        # 连线（每条独立风格：直线折线 / 平滑曲线 + 箭头）
        for edge in self.edges:
            a = self._node_by_id(str(edge.get("from") or ""))
            b = self._node_by_id(str(edge.get("to") or ""))
            if a is None or b is None:
                continue
            style = self._edge_style_of(edge)
            path, tip, d = self._edge_path(self.node_rect(a), self.node_rect(b), style)
            highlight = self._selected_id in (edge.get("from"), edge.get("to"))
            edge_color = QColor(theme["accent"] if highlight else theme["subtext"])
            pen = QPen(edge_color, 1.8 if highlight else 1.4)
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(path)

            # 三角箭头：沿末端方向指向目标卡片边缘
            dlen = (d.x() * d.x() + d.y() * d.y()) ** 0.5 or 1.0
            ux, uy = d.x() / dlen, d.y() / dlen
            left = QPointF(tip.x() - uy * 5 - ux * 8, tip.y() + ux * 5 - uy * 8)
            right = QPointF(tip.x() + uy * 5 - ux * 8, tip.y() - ux * 5 - uy * 8)
            painter.setBrush(edge_color)
            painter.setPen(Qt.NoPen)
            painter.drawPolygon([tip, left, right])

        # 卡片节点
        for node in self.nodes:
            rect = self.node_rect(node)
            self._paint_node(painter, node, rect)

        # 八方连接点（选中的卡片）
        selected = self._node_by_id(self._selected_id)
        if selected is not None:
            rect = self.node_rect(selected)
            for i in range(len(HANDLES)):
                center = self._handle_pos(rect, i)
                painter.setPen(QPen(QColor(theme["panel"]), 2))
                painter.setBrush(QColor(theme["accent"]))
                painter.drawEllipse(center, 5, 5)

        # 拖出的箭头橡皮筋（跟随当前连线风格）
        if self._connecting and self._connect_from is not None:
            start = self.node_rect(self._connect_from).center()
            d = self._connect_pos - start
            painter.setPen(QPen(QColor(theme["accent"]), 1.6, Qt.DashLine))
            painter.setBrush(Qt.NoBrush)
            if self.edge_style == "curve":
                path = QPainterPath(start)
                path.cubicTo(
                    QPointF(start.x() + d.x() * 0.5, start.y()),
                    QPointF(self._connect_pos.x() - d.x() * 0.5, self._connect_pos.y()),
                    self._connect_pos,
                )
                painter.drawPath(path)
            else:
                corner = (
                    QPointF(self._connect_pos.x(), start.y())
                    if abs(d.x()) >= abs(d.y())
                    else QPointF(start.x(), self._connect_pos.y())
                )
                painter.drawPolyline([start, corner, self._connect_pos])
            painter.setBrush(QColor(theme["accent"]))
            painter.drawEllipse(self._connect_pos, 4, 4)
        painter.end()

    def _paint_node(self, painter: QPainter, node: dict, rect: QRectF) -> None:
        theme = self.theme
        color = str(node.get("color") or "")
        selected = node.get("id") == self._selected_id
        hovered = node is self._hover_node

        if color and color.startswith("#"):
            bg = QColor(color)
            text_color = QColor("#333333") if _luma(bg) > 0.55 else QColor("#F5F6F8")
            border = bg.darker(112)
        else:
            bg = QColor(theme["panel"])
            text_color = QColor(theme["text"])
            border = QColor(theme["border_strong"])

        if selected:
            border = QColor(theme["accent"])
        elif hovered:
            border = QColor(theme["accent"]).lighter(130)

        # 柔和投影
        painter.setPen(Qt.NoPen)
        shadow = QColor(0, 0, 0, 26 if theme["window"] == "#F7F8FA" else 90)
        shadow_rect = rect.adjusted(2.5, 3.5, 2.5, 3.5)
        painter.setBrush(shadow)
        painter.drawRoundedRect(shadow_rect, 12, 12)

        painter.setPen(QPen(border, 2 if selected else 1))
        painter.setBrush(bg)
        painter.drawRoundedRect(rect, 12, 12)

        painter.setPen(QPen(text_color))
        painter.setFont(self._node_font())
        text = str(node.get("text") or "").strip() or "双击编辑"
        if not str(node.get("text") or "").strip():
            painter.setPen(QPen(QColor(theme["subtext"])))
        painter.drawText(rect.adjusted(NODE_PAD_X - 6, NODE_PAD_Y - 6, -(NODE_PAD_X - 6), -(NODE_PAD_Y - 6)),
                         Qt.AlignCenter | Qt.TextWordWrap, text)

    # ------------------------------------------------------------------
    # 鼠标交互
    # ------------------------------------------------------------------
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.LeftButton:
            return super().mousePressEvent(event)
        raw = QPointF(event.position())
        pos = self._to_logical(raw)
        self._press_raw = raw
        self._stop_edit(commit=True)
        node, handle = self._hit_test(pos)

        if node is not None and handle >= 0:
            # 从八方连接点开始拖出箭头
            self._connecting = True
            self._connect_from = node
            self._connect_pos = pos
            self.setCursor(Qt.CrossCursor)
            self.update()
            return

        if node is not None:
            self._selected_id = str(node.get("id"))
            self._drag_node = node
            self._press_pos = pos
            self._press_node_pos = QPointF(float(node.get("x") or 0), float(node.get("y") or 0))
            self._moved = False
            self.setCursor(Qt.ClosedHandCursor)
            self.update()
            return

        # 空白处：平移画布
        self._panning = True
        self._press_scroll = self._scroll_values()
        self._press_pos = raw
        self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        raw = QPointF(event.position())
        pos = self._to_logical(raw)

        if self._connecting and self._connect_from is not None:
            self._connect_pos = pos
            self.update()
            return

        if self._drag_node is not None:
            delta = pos - self._press_pos
            if not self._moved and delta.manhattanLength() > 3:
                self._moved = True
            if self._moved:
                self._drag_node["x"] = self._press_node_pos.x() + delta.x()
                self._drag_node["y"] = self._press_node_pos.y() + delta.y()
                self.update()
            return

        if self._panning and self._press_scroll is not None:
            h_bar, v_bar = self._scrollbars()
            delta = raw - getattr(self, "_press_raw", raw)
            if h_bar is not None:
                h_bar.setValue(int(self._press_scroll[0] - delta.x()))
            if v_bar is not None:
                v_bar.setValue(int(self._press_scroll[1] - delta.y()))
            return

        # 悬停高亮
        node, _handle = self._hit_test(pos)
        if node is not self._hover_node:
            self._hover_node = node
            self.setCursor(Qt.PointingHandCursor if node is not None else Qt.OpenHandCursor)
            self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.LeftButton:
            return super().mouseReleaseEvent(event)
        pos = self._to_logical(QPointF(event.position()))

        if self._connecting:
            self._finish_connect(pos)
            self.setCursor(Qt.OpenHandCursor)
            return

        if self._drag_node is not None:
            node, self._drag_node = self._drag_node, None
            self.setCursor(Qt.OpenHandCursor)
            if self._moved:
                self.changed.emit()
            else:
                self.update()   # 单击：仅选中
            return

        if self._panning:
            self._panning = False
            self._press_scroll = None
            self.setCursor(Qt.OpenHandCursor)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        pos = self._to_logical(QPointF(event.position()))
        node, _handle = self._hit_test(pos)
        if node is not None:
            self._selected_id = str(node.get("id"))
            self._start_edit(node)
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        pos = self._to_logical(QPointF(event.pos()))
        node, _handle = self._hit_test(pos)
        if node is None:
            menu = QMenu(self)
            menu.addAction("新建卡片", lambda: self._add_node(pos - QPointF(40, 18), ""))
            menu.exec(event.globalPos())
            return

        self._selected_id = str(node.get("id"))
        self.update()
        menu = QMenu(self)
        menu.addAction("编辑文字", lambda: self._start_edit(node))
        menu.addAction("添加子卡片", lambda: self._add_child(node))
        color_menu = menu.addMenu("卡片颜色")
        for value, name in NODE_COLORS:
            action = color_menu.addAction(("● " if value == "" else "■ ") + name)
            action.triggered.connect(
                lambda _c=False, v=value: self._set_color(node, v)
            )
        menu.addSeparator()
        menu.addAction("删除卡片", lambda: self._delete_node(node))
        menu.exec(event.globalPos())

    # ------------------------------------------------------------------
    # 节点操作
    # ------------------------------------------------------------------
    def _finish_connect(self, pos: QPointF) -> None:
        source = self._connect_from
        self._connecting = False
        self._connect_from = None
        if source is None:
            self.update()
            return
        target, _handle = self._hit_test(pos)
        if target is not None and target is not source:
            self._add_edge(str(source.get("id")), str(target.get("id")))
        else:
            # 拖到空白处：引申一张新卡片并自动连线
            new_node = self._create_node_at(pos)
            self._add_edge(str(source.get("id")), str(new_node["id"]))
        self.update()

    def _create_node_at(self, center: QPointF) -> dict:
        node = {
            "id": new_id(),
            "x": center.x() - NODE_MIN_W / 2,
            "y": center.y() - NODE_MIN_H / 2,
            "text": "新卡片",
            "color": "",
        }
        self.nodes.append(node)
        self._selected_id = str(node["id"])
        self.changed.emit()
        return node

    def _add_node(self, top_left: QPointF, color: str) -> None:
        node = {
            "id": new_id(),
            "x": top_left.x(),
            "y": top_left.y(),
            "text": "新卡片",
            "color": color,
        }
        self.nodes.append(node)
        self._selected_id = str(node["id"])
        self.update()
        self.changed.emit()
        self._start_edit(node)

    def _add_child(self, node: dict) -> None:
        rect = self.node_rect(node)
        child_pos = QPointF(rect.right() + 110, rect.center().y() - NODE_MIN_H / 2)
        new_node = {
            "id": new_id(),
            "x": child_pos.x(),
            "y": child_pos.y(),
            "text": "新卡片",
            "color": str(node.get("color") or ""),
        }
        self.nodes.append(new_node)
        self._add_edge(str(node.get("id")), str(new_node["id"]))
        self._selected_id = str(new_node["id"])
        self.update()
        self.changed.emit()
        self._start_edit(new_node)

    def _add_edge(self, from_id: str, to_id: str) -> None:
        if from_id == to_id:
            return
        for edge in self.edges:
            pair = {str(edge.get("from")), str(edge.get("to"))}
            if pair == {from_id, to_id}:
                return
        self.edges.append({"from": from_id, "to": to_id, "style": self.edge_style})
        self.update()
        self.changed.emit()

    def _set_color(self, node: dict, color: str) -> None:
        node["color"] = color
        self.update()
        self.changed.emit()

    def _delete_node(self, node: dict) -> None:
        node_id = str(node.get("id"))
        self.nodes = [n for n in self.nodes if n.get("id") != node_id]
        self.edges = [
            e for e in self.edges
            if e.get("from") != node_id and e.get("to") != node_id
        ]
        if self._selected_id == node_id:
            self._selected_id = ""
        self.update()
        self.changed.emit()

    # ------------------------------------------------------------------
    # 就地编辑
    # ------------------------------------------------------------------
    def _start_edit(self, node: dict) -> None:
        self._stop_edit(commit=True)
        self._editing_rect = self.node_rect(node)
        editor = QLineEdit(self)
        editor.setText(str(node.get("text") or ""))
        editor.selectAll()
        editor.editingFinished.connect(lambda: self._stop_edit(commit=True))
        editor.setStyleSheet(
            "QLineEdit{border:2px solid #21A575;border-radius:8px;padding:8px 10px;background:#FFFFFF;color:#333333;}"
        )
        editor.show()
        editor.setFocus()
        self._editor = editor
        self._editing_id = str(node.get("id"))
        self._place_editor()

    def _place_editor(self) -> None:
        """把就地编辑框按当前缩放定位到目标卡片上。"""
        editor = self._editor
        rect = getattr(self, "_editing_rect", None)
        if editor is None or rect is None:
            return
        z = self._zoom
        editor.setFixedWidth(max(160, int(rect.width() * z)))
        font = editor.font()
        font.setPixelSize(max(11, int(14 * z)))
        editor.setFont(font)
        editor.move(
            int(rect.x() * z),
            int((rect.y() + rect.height() / 2) * z - editor.height() / 2),
        )

    def _stop_edit(self, commit: bool) -> None:
        editor = self._editor
        if editor is None:
            return
        self._editor = None
        text = editor.text().strip() if commit else None
        node = self._node_by_id(self._editing_id)
        self._editing_id = ""
        editor.deleteLater()
        if text is not None and node is not None and text != str(node.get("text") or ""):
            node["text"] = text
            self.changed.emit()
        self.update()

    # ------------------------------------------------------------------
    # 滚动辅助
    # ------------------------------------------------------------------
    def _scrollbars(self):
        parent = self.parentWidget()
        while parent is not None:
            if isinstance(parent, QScrollArea):
                return parent.horizontalScrollBar(), parent.verticalScrollBar()
            parent = parent.parentWidget()
        return None, None

    def _viewport(self):
        """所在 QScrollArea 的视口（用于以视口中心为锚缩放）。"""
        parent = self.parentWidget()
        while parent is not None:
            if isinstance(parent, QScrollArea):
                return parent.viewport()
            parent = parent.parentWidget()
        return None

    def _scroll_values(self) -> tuple[float, float]:
        h_bar, v_bar = self._scrollbars()
        return (
            float(h_bar.value()) if h_bar is not None else 0.0,
            float(v_bar.value()) if v_bar is not None else 0.0,
        )


class OutlineDialog(QDialog):
    """大纲主窗口：顶部工具条 + 思维导图画布。"""

    changed = Signal()  # 数据变化（由主窗口负责持久化）

    def __init__(self, item: dict, theme: dict, parent=None) -> None:
        super().__init__(parent)
        self.item = dict(item)
        self.setWindowTitle(f"大纲思维导图 · {self.item.get('title') or '未命名'}")
        self.resize(1280, 800)
        self.setModal(False)
        self.setAttribute(Qt.WA_DeleteOnClose, False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        bar = QWidget(self)
        bar.setObjectName("OutlineBar")
        bar.setAttribute(Qt.WA_StyledBackground, True)
        bar.setFixedHeight(56)
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(18, 8, 18, 8)
        bar_layout.setSpacing(10)

        title = QLabel(str(self.item.get("title") or "未命名"), bar)
        title.setObjectName("NameGenTitle")
        bar_layout.addWidget(title)
        self.count_label = QLabel("", bar)
        self.count_label.setObjectName("DrawerHint")
        bar_layout.addWidget(self.count_label)
        # 连线风格分段切换：直线 ↔ 曲线（随该大纲持久化）
        self.edge_box = QFrame(bar)
        self.edge_box.setObjectName("SegBox")
        edge_layout = QHBoxLayout(self.edge_box)
        edge_layout.setContentsMargins(3, 3, 3, 3)
        edge_layout.setSpacing(2)
        self.edge_ortho_button = QToolButton(self.edge_box)
        self.edge_ortho_button.setObjectName("SegButton")
        self.edge_ortho_button.setText("直线")
        self.edge_ortho_button.setCheckable(True)
        self.edge_ortho_button.setCursor(Qt.PointingHandCursor)
        self.edge_ortho_button.setToolTip("之后拖出的新连线使用直角折线")
        self.edge_ortho_button.clicked.connect(lambda: self._set_edge_style("ortho"))
        edge_layout.addWidget(self.edge_ortho_button)
        self.edge_curve_button = QToolButton(self.edge_box)
        self.edge_curve_button.setObjectName("SegButton")
        self.edge_curve_button.setText("曲线")
        self.edge_curve_button.setCheckable(True)
        self.edge_curve_button.setCursor(Qt.PointingHandCursor)
        self.edge_curve_button.setToolTip("之后拖出的新连线使用平滑曲线")
        self.edge_curve_button.clicked.connect(lambda: self._set_edge_style("curve"))
        edge_layout.addWidget(self.edge_curve_button)
        bar_layout.addWidget(self.edge_box)
        bar_layout.addStretch(1)
        hint = QLabel("双击卡片编辑 · Ctrl+滚轮缩放 · 先选直线/曲线再从连接点拖出（每条连线样式独立） · 拖到边界点「＋」扩展画布", bar)
        hint.setObjectName("DrawerHint")
        bar_layout.addWidget(hint)
        self.save_button = QPushButton("保存", bar)
        self.save_button.setObjectName("GhostButton")
        self.save_button.setCursor(Qt.PointingHandCursor)
        self.save_button.setToolTip("立即保存大纲（Ctrl+S）")
        self.save_button.clicked.connect(self._flush_save)
        bar_layout.addWidget(self.save_button)
        self.export_button = QPushButton("导出", bar)
        self.export_button.setObjectName("GhostButton")
        self.export_button.setCursor(Qt.PointingHandCursor)
        self.export_button.setToolTip("导出为 JSON 文件（路径实时选择）")
        self.export_button.clicked.connect(self._export_json)
        bar_layout.addWidget(self.export_button)
        layout.addWidget(bar)

        # Ctrl+S 立即保存；关闭窗口前也会自动补一次保存
        save_sc = QShortcut(QKeySequence("Ctrl+S"), self)
        save_sc.activated.connect(self._flush_save)

        self.canvas = OutlineCanvas(theme, self)
        self.canvas.set_data(self.item)
        self.canvas.changed.connect(self._collect)
        self._sync_edge_buttons()

        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(False)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # 画布：横 / 纵双向隐形快速滚动滑块
        from app.ui.scroll_float import ScrollFlasher

        self._h_flasher = ScrollFlasher(self.scroll, Qt.Horizontal, inset=8)
        self._v_flasher = ScrollFlasher(self.scroll, Qt.Vertical, inset=8)
        self.scroll.setWidget(self.canvas)
        layout.addWidget(self.scroll, 1)

        # 四向边界「＋」按钮：视口贴到某条边界时出现，点击扩容画布
        self.left_plus = self._make_plus_button("left")
        self.right_plus = self._make_plus_button("right")
        self.top_plus = self._make_plus_button("top")
        self.bottom_plus = self._make_plus_button("bottom")
        self.left_plus.clicked.connect(lambda: self._extend("left"))
        self.right_plus.clicked.connect(lambda: self._extend("right"))
        self.top_plus.clicked.connect(lambda: self._extend("top"))
        self.bottom_plus.clicked.connect(lambda: self._extend("bottom"))

        # 固定在左下角的「复位」按钮：一键回到画布中心
        self.reset_button = QPushButton("复位", self.scroll.viewport())
        self.reset_button.setObjectName("FloatButton")
        self.reset_button.setCursor(Qt.PointingHandCursor)
        self.reset_button.setFixedSize(64, 34)
        self.reset_button.clicked.connect(self.reset_view)

        self.canvas.bounds_changed.connect(self._update_edge_buttons)
        self.scroll.horizontalScrollBar().valueChanged.connect(self._update_edge_buttons)
        self.scroll.verticalScrollBar().valueChanged.connect(self._update_edge_buttons)
        self.scroll.viewport().installEventFilter(self)

        QTimer.singleShot(0, self._center_on_content)

    # ------------------------------------------------------------------
    # 边界扩容按钮与复位
    # ------------------------------------------------------------------
    def _make_plus_button(self, side: str) -> QToolButton:
        button = QToolButton(self.scroll.viewport())
        button.setObjectName("FloatButton")
        button.setText("＋")
        button.setCursor(Qt.PointingHandCursor)
        button.setFixedSize(36, 36)
        names = {"left": "左", "right": "右", "top": "上", "bottom": "下"}
        button.setToolTip("向%s扩展画布" % names.get(side, side))
        button.hide()
        return button

    def _extend(self, direction: str) -> None:
        self.canvas.extend(direction)
        if direction in ("right", "bottom"):
            bar = (
                self.scroll.horizontalScrollBar()
                if direction == "right"
                else self.scroll.verticalScrollBar()
            )
            bar.setValue(bar.maximum())   # 贴住新的边界，方便继续绘制
        self._update_edge_buttons()

    def reset_view(self) -> None:
        """复位：视口回到画布中心。"""
        viewport = self.scroll.viewport()
        self.scroll.horizontalScrollBar().setValue(
            int((self.canvas.width() - viewport.width()) / 2)
        )
        self.scroll.verticalScrollBar().setValue(
            int((self.canvas.height() - viewport.height()) / 2)
        )

    def _update_edge_buttons(self) -> None:
        h_bar = self.scroll.horizontalScrollBar()
        v_bar = self.scroll.verticalScrollBar()
        viewport = self.scroll.viewport()
        width, height = viewport.width(), viewport.height()
        at_left = h_bar.value() <= h_bar.minimum()
        at_right = h_bar.value() >= h_bar.maximum()
        at_top = v_bar.value() <= v_bar.minimum()
        at_bottom = v_bar.value() >= v_bar.maximum()
        self.left_plus.setVisible(at_left)
        self.right_plus.setVisible(at_right)
        self.top_plus.setVisible(at_top)
        self.bottom_plus.setVisible(at_bottom)
        if at_left:
            self.left_plus.move(10, int(height / 2 - 18))
            self.left_plus.raise_()
        if at_right:
            self.right_plus.move(width - 46, int(height / 2 - 18))
            self.right_plus.raise_()
        if at_top:
            self.top_plus.move(int(width / 2 - 18), 10)
            self.top_plus.raise_()
        if at_bottom:
            self.bottom_plus.move(int(width / 2 - 18), height - 46)
            self.bottom_plus.raise_()
        self.reset_button.move(10, height - 48)
        self.reset_button.raise_()

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        from PySide6.QtCore import QEvent

        if obj is self.scroll.viewport() and event.type() == QEvent.Resize:
            self._update_edge_buttons()
        return super().eventFilter(obj, event)

    def _center_on_content(self) -> None:
        """打开时把视口定位到中心卡片（横纵都居中）。"""
        nodes, _edges = self.canvas.collect()
        if nodes:
            rect = self.canvas.node_rect(nodes[0])
            cx, cy = rect.center().x(), rect.center().y()
        else:
            cx, cy = self.canvas.width() / 2, self.canvas.height() / 2
        viewport = self.scroll.viewport()
        self.scroll.horizontalScrollBar().setValue(int(cx - viewport.width() / 2))
        self.scroll.verticalScrollBar().setValue(int(cy - viewport.height() / 2))
        self._update_edge_buttons()

    def _collect(self) -> None:
        self.item["nodes"], self.item["edges"] = self.canvas.collect()
        # 连线风格与画布扩容后的尺寸一并持久化
        self.item["edge_style"] = self.canvas.edge_style
        width, height = self.canvas.canvas_bounds()
        self.item["canvas_width"] = int(width)
        self.item["canvas_height"] = int(height)
        self.item["updated_at"] = time.time()
        self.count_label.setText(f"{len(self.item['nodes'])} 张卡片 · 已自动保存")
        self.changed.emit()

    def _set_edge_style(self, style: str) -> None:
        """切换新连线的默认风格并同步分段按钮状态。"""
        self.canvas.set_edge_style(style)
        self._sync_edge_buttons()

    def _sync_edge_buttons(self) -> None:
        """按钮 = 当前编辑模式（新连线用什么风格），不代表已有连线的样式。"""
        ortho = self.canvas.edge_style == "ortho"
        self.edge_ortho_button.setChecked(ortho)
        self.edge_curve_button.setChecked(not ortho)

    def _flush_save(self) -> None:
        """立即保存（保存按钮 / Ctrl+S / 关闭窗口时调用）。"""
        self._collect()
        self.save_button.setText("已保存 ✓")
        QTimer.singleShot(1400, lambda: self.save_button.setText("保存"))

    def _export_json(self) -> None:
        """把大纲导出为 JSON 文件（导出路径实时选择）。"""
        import json

        from PySide6.QtWidgets import QFileDialog

        self._collect()
        path, _f = QFileDialog.getSaveFileName(
            self, "导出大纲", f"{self.item.get('title') or '大纲'}.json",
            "JSON 文件 (*.json)",
        )
        if not path:
            return
        if not path.lower().endswith(".json"):
            path += ".json"
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.item, f, ensure_ascii=False, indent=2)
        except OSError as exc:
            MessageBox.warning(self, "导出失败", f"无法写入文件：{exc}")
            return
        MessageBox.information(self, "导出成功", f"已导出到：\n{path}")

    def closeEvent(self, event) -> None:  # noqa: N802
        self._collect()  # 关闭前兜底保存
        super().closeEvent(event)

    def item_dict(self) -> dict:
        return dict(self.item)

    def set_theme(self, theme: dict) -> None:
        self.canvas.set_theme(theme)
