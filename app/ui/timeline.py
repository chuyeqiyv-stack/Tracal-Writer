# -*- coding: utf-8 -*-
"""高级故事时间轴：大尺寸独立窗口。

- 主横线可向两侧无限延伸，鼠标按住空白处拖拽滑动，滚轮可直接左右快移；
- 不分年月日刻度，横线上任意位置点击即可放置事件节点；
- 节点上方「时间」、下方「事件」均有虚线茎连到横线，可附描述；
- 节点可沿横线拖动；拖到画布边界会出现「＋」按钮，点击后画布
  向该方向扩容一倍初始长度；
- 左侧固定「复位」按钮，一键回到 0 点；
- Ctrl+滚轮横向缩放时间轴（以 0 点为锚，高度与文字不缩放）；
- 数据持久化到 library.json 的时间轴条目中。
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.models import new_id
from app.ui.dialogs import MessageBox

INITIAL_WIDTH = 60000     # 初始画布宽度（可按需扩容）
CANVAS_HEIGHT = 620
TICK_GAP = 140            # 装饰性小刻度间距（无单位含义）
STEM_TOP = 92             # 时间文字距横线的高度
STEM_BOTTOM = 24          # 事件名距横线的高度
ZOOM_MIN, ZOOM_MAX = 0.5, 2.0
ZOOM_STEP_FACTOR = 1.1    # 每格滚轮的缩放系数（Ctrl+滚轮）


class TimelineCanvas(QWidget):
    """时间轴画布：横线与事件节点全部自绘。"""

    changed = Signal()          # 节点增删改后发出（触发保存）
    bounds_changed = Signal()   # 画布尺寸 / 原点变化后发出（更新边界按钮）

    def __init__(self, theme: dict, parent=None) -> None:
        super().__init__(parent)
        self.theme = theme
        self.events: list[dict] = []
        self._width = INITIAL_WIDTH
        self._origin = INITIAL_WIDTH / 2
        self._zoom = 1.0
        self._apply_view_size()
        self.setMouseTracking(False)
        self.setCursor(Qt.OpenHandCursor)

        self._dragging = False
        self._drag_moved = False
        self._press_pos = QPointF()
        self._press_scroll_x = 0.0
        self._drag_node: dict | None = None

    # ------------------------------------------------------------------
    def origin_x(self) -> float:
        return self._origin

    def set_theme(self, theme: dict) -> None:
        self.theme = theme
        self.update()

    def set_data(self, events: list[dict], width=None, origin=None) -> None:
        self.events = [dict(e) for e in events]
        # 恢复上次扩容后的画布尺寸与 0 点位置（老数据没有这两个字段时用默认值）
        try:
            if width is not None and int(width) >= self._width:
                self._width = int(width)
            if origin is not None and 0 < float(origin) <= self._width:
                self._origin = float(origin)
        except (TypeError, ValueError):
            pass
        self._apply_view_size()
        self.update()

    def canvas_bounds(self) -> tuple[int, float]:
        """当前画布宽度与 0 点位置（用于持久化）。"""
        return self._width, self._origin

    # ------------------------------------------------------------------
    # 缩放（Ctrl + 滚轮）：只横向缩放时间轴，以 0 点（原点）为锚；
    # 控件宽度 = 逻辑宽度 × 缩放，高度保持不变
    # ------------------------------------------------------------------
    def _apply_view_size(self) -> None:
        self.setFixedSize(max(1, int(self._width * self._zoom)), CANVAS_HEIGHT)

    def _to_logical(self, pos: QPointF) -> QPointF:
        z = self._zoom
        return QPointF(pos.x() / z, pos.y())

    def zoom_at(self, steps: float, device_pos: QPointF | None = None) -> None:
        """横向缩放时间轴：以 0 点（原点）为锚，原点在视口中的位置保持不动。"""
        old = self._zoom
        new = min(ZOOM_MAX, max(ZOOM_MIN, old * (ZOOM_STEP_FACTOR ** steps)))
        if abs(new - old) < 1e-9:
            return
        bar = self._scrollbar()
        if bar is not None:
            # 保持原点的视口位置不变：origin_view = origin*old - value
            origin_view = self._origin * old - bar.value()
            self._zoom = new
            self._apply_view_size()
            bar.setValue(int(self._origin * new - origin_view))
        else:
            self._zoom = new
            self._apply_view_size()
        self.update()
        self.bounds_changed.emit()

    def collect(self) -> list[dict]:
        return [dict(e) for e in self.events]

    def event_x(self, e: dict) -> float:
        """事件在画布上的横坐标（x 以 0 点为原点的像素偏移保存）。"""
        return self._origin + float(e.get("x") or 0)

    # ------------------------------------------------------------------
    # 画布扩容：向左 / 向右各扩一倍当前（初始）长度
    # ------------------------------------------------------------------
    def extend(self, direction: str) -> None:
        extra = self._width
        self._width = self._width * 2
        if direction == "left":
            self._origin += extra
            self._apply_view_size()
            bar = self._scrollbar()
            if bar is not None:
                bar.setValue(bar.value() + extra)
        else:
            self._apply_view_size()
        self.update()
        self.bounds_changed.emit()
        # 扩容后立即触发保存，避免退出后丢失新的时间线长度
        self.changed.emit()

    def ensure_origin_visible(self, viewport_width: float) -> None:
        """让滚动区域定位到 0 点居中。"""
        bar = self._scrollbar()
        if bar is not None:
            bar.setValue(int(self._origin * self._zoom - viewport_width / 2))

    # ------------------------------------------------------------------
    # 绘制
    # ------------------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        theme = self.theme

        painter.fillRect(self.rect(), QColor(theme["panel_alt"]))
        # 只横向缩放：x 坐标 × zoom，y 与文字尺寸保持原样
        zx = self._zoom
        mid_y = CANVAS_HEIGHT / 2

        def X(lx: float) -> float:
            return lx * zx

        # 装饰性小刻度（不含年月日含义，仅作视觉节奏）
        tick_pen = QPen(QColor(theme["border"]))
        start = int(-self._origin / TICK_GAP) - 1
        end = int((self._width - self._origin) / TICK_GAP) + 1
        painter.setPen(tick_pen)
        for i in range(start, end + 1):
            x = X(self._origin + i * TICK_GAP)
            painter.drawLine(QPointF(x, mid_y - 7), QPointF(x, mid_y + 7))

        # 主横线
        painter.setPen(QPen(QColor(theme["accent"]), 2))
        painter.drawLine(QPointF(X(0), mid_y), QPointF(X(self._width), mid_y))

        # 原点标记
        painter.setPen(QPen(QColor(theme["subtext"]), 1))
        painter.drawLine(QPointF(X(self._origin), mid_y - 14), QPointF(X(self._origin), mid_y + 14))

        # 事件节点：时间在上，事件在下（均有虚线茎连到横线）
        for e in self.events:
            x = X(self.event_x(e))
            color = QColor(theme["accent"])
            text_color = QColor(theme["text"])
            desc_color = QColor(theme["subtext"])

            # 节点圆
            painter.setPen(QPen(QColor(theme["panel"]), 2))
            painter.setBrush(color)
            painter.drawEllipse(QPointF(x, mid_y), 7, 7)

            # 上茎 + 时间标签
            time_text = str(e.get("time") or "")
            if time_text:
                stem_top = mid_y - STEM_TOP
                painter.setPen(QPen(color, 1.4, Qt.DashLine))
                painter.drawLine(QPointF(x, mid_y - 9), QPointF(x, stem_top + 6))

                font = QFont(self.font())
                font.setPixelSize(12)
                painter.setFont(font)
                painter.setPen(QPen(color))
                painter.drawText(
                    QRectF(x - 90, stem_top - 24, 180, 22),
                    Qt.AlignHCenter | Qt.TextWordWrap,
                    time_text[:40],
                )

            # 下茎 + 事件名（事件也和时间一样有线延展）
            title_font = QFont(self.font())
            title_font.setPixelSize(14)
            title_font.setBold(True)
            title = str(e.get("event") or "未命名事件")
            painter.setFont(title_font)
            painter.setPen(QPen(text_color))
            painter.drawText(
                QRectF(x - 90, mid_y + STEM_BOTTOM, 180, 24),
                Qt.AlignHCenter | Qt.TextWordWrap,
                title[:24],
            )
            painter.setPen(QPen(color, 1.4, Qt.DashLine))
            painter.drawLine(QPointF(x, mid_y + 9), QPointF(x, mid_y + STEM_BOTTOM - 2))

            # 描述（事件名下方，可选）
            desc = str(e.get("desc") or "")
            if desc:
                small_font = QFont(self.font())
                small_font.setPixelSize(11)
                painter.setFont(small_font)
                painter.setPen(QPen(desc_color))
                painter.drawText(
                    QRectF(x - 100, mid_y + STEM_BOTTOM + 26, 200, 88),
                    Qt.AlignHCenter | Qt.TextWordWrap,
                    desc[:90],
                )
        painter.end()

    # ------------------------------------------------------------------
    # 鼠标 / 滚轮交互
    # ------------------------------------------------------------------
    def _node_at(self, pos: QPointF) -> dict | None:
        mid_y = CANVAS_HEIGHT / 2
        for e in self.events:
            x = self.event_x(e)
            if abs(pos.x() - x) <= 12 and abs(pos.y() - mid_y) <= 14:
                return e
        return None

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.LeftButton:
            return super().mousePressEvent(event)
        raw = QPointF(event.position())
        pos = self._to_logical(raw)
        self._press_raw = raw
        self._dragging = True
        self._drag_moved = False
        self._press_pos = pos
        self._press_scroll_x = self._scroll_x()
        self._drag_node = self._node_at(pos)
        self.setCursor(Qt.ClosedHandCursor)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if not self._dragging:
            return super().mouseMoveEvent(event)
        raw = QPointF(event.position())
        pos = self._to_logical(raw)
        delta = pos.x() - self._press_pos.x()
        raw_delta = raw.x() - getattr(self, "_press_raw", raw).x()
        if abs(delta) > 3 or abs(pos.y() - self._press_pos.y()) > 3:
            self._drag_moved = True

        if self._drag_moved and self._drag_node is not None:
            # 拖动节点：只沿横线移动
            self._drag_node["x"] = float(self._drag_node.get("x") or 0) + delta
            self._press_pos = pos
            self._press_raw = raw
            self._press_scroll_x = self._scroll_x()
            self.update()
            return

        if self._drag_moved:
            # 拖动画布：滚动视口
            scroll = self._scrollbar()
            if scroll is not None:
                scroll.setValue(int(self._press_scroll_x - raw_delta))

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.LeftButton:
            return super().mouseReleaseEvent(event)
        pos = self._to_logical(QPointF(event.position()))
        was_drag_node = self._drag_node
        self._dragging = False
        self.setCursor(Qt.OpenHandCursor)
        self._drag_node = None

        if self._drag_moved:
            if was_drag_node is not None:
                self.changed.emit()
            return super().mouseReleaseEvent(event)

        # 视为点击：命中节点 -> 编辑；命中横线附近 -> 新建
        node = self._node_at(pos)
        if node is not None:
            self._edit_node(node)
        elif abs(pos.y() - CANVAS_HEIGHT / 2) <= 52:
            self._add_node(pos.x() - self._origin)
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        pos = self._to_logical(QPointF(event.position()))
        node = self._node_at(pos)
        if node is not None:
            self._edit_node(node)
        super().mouseDoubleClickEvent(event)

    def wheelEvent(self, event) -> None:  # noqa: N802
        """Ctrl+滚轮缩放时间轴；普通滚轮左右快移。"""
        if event.modifiers() & Qt.ControlModifier:
            steps = event.angleDelta().y() / 120.0
            if steps:
                self.zoom_at(steps, QPointF(event.position()))
            event.accept()
            return
        scroll = self._scrollbar()
        if scroll is not None:
            steps = event.angleDelta().y() / 120.0
            if steps == 0:
                steps = event.angleDelta().x() / 120.0
            scroll.setValue(int(scroll.value() - steps * 180))
            event.accept()
            return
        super().wheelEvent(event)

    # ------------------------------------------------------------------
    # 节点操作
    # ------------------------------------------------------------------
    def _add_node(self, x: float) -> None:
        event = {"id": new_id(), "x": x, "time": "", "event": "新事件", "desc": ""}
        self.events.append(event)
        self.update()
        self.changed.emit()
        self._edit_node(event)

    def _edit_node(self, node: dict) -> None:
        dialog = EventEditDialog(node, self.window())
        if dialog.exec() == QDialog.Accepted:
            node["time"] = dialog.time_text()
            node["event"] = dialog.event_text()
            node["desc"] = dialog.desc_text()
            self.update()
            self.changed.emit()
        elif dialog.deleted:
            self.events = [e for e in self.events if e.get("id") != node.get("id")]
            self.update()
            self.changed.emit()

    # ------------------------------------------------------------------
    def _scrollbar(self):
        parent = self.parentWidget()
        while parent is not None:
            if isinstance(parent, QScrollArea):
                return parent.horizontalScrollBar()
            parent = parent.parentWidget()
        return None

    def _scroll_x(self) -> float:
        bar = self._scrollbar()
        return float(bar.value()) if bar is not None else 0.0


class EventEditDialog(QDialog):
    """事件节点编辑小窗：时间（上）、事件（下）、描述、删除。"""

    def __init__(self, node: dict, parent=None) -> None:
        super().__init__(parent)
        self.deleted = False
        self.setWindowTitle("编辑事件")
        self.setModal(True)
        self.resize(400, 340)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 16)
        layout.setSpacing(10)

        form = QFormLayout()
        form.setSpacing(10)
        self.time_edit = QLineEdit(str(node.get("time") or ""), self)
        self.time_edit.setPlaceholderText("例如：第三年冬天 / 开局三日")
        form.addRow("时间", self.time_edit)
        self.event_edit = QLineEdit(str(node.get("event") or ""), self)
        self.event_edit.setPlaceholderText("事件名称，显示在横线下方")
        form.addRow("事件", self.event_edit)
        layout.addLayout(form)

        self.desc_edit = QPlainTextEdit(self)
        self.desc_edit.setObjectName("Preview")
        self.desc_edit.setPlaceholderText("事件描述（可选）……")
        self.desc_edit.setPlainText(str(node.get("desc") or ""))
        layout.addWidget(self.desc_edit, 1)

        buttons = QHBoxLayout()
        delete_button = QPushButton("删除事件", self)
        delete_button.setObjectName("DangerButton")
        delete_button.setCursor(Qt.PointingHandCursor)
        delete_button.clicked.connect(self._delete)
        buttons.addWidget(delete_button)
        buttons.addStretch(1)
        cancel_button = QPushButton("取消", self)
        cancel_button.clicked.connect(self.reject)
        buttons.addWidget(cancel_button)
        ok_button = QPushButton("保存", self)
        ok_button.setObjectName("Primary")
        ok_button.setCursor(Qt.PointingHandCursor)
        ok_button.clicked.connect(self.accept)
        buttons.addWidget(ok_button)
        layout.addLayout(buttons)

        self.event_edit.setFocus()

    def _delete(self) -> None:
        if MessageBox.question(self, "删除事件", "确定删除这个事件节点吗？"):
            self.deleted = True
            self.reject()

    def time_text(self) -> str:
        return self.time_edit.text().strip()

    def event_text(self) -> str:
        return self.event_edit.text().strip() or "未命名事件"

    def desc_text(self) -> str:
        return self.desc_edit.toPlainText().strip()


class TimelineDialog(QDialog):
    """时间轴主窗口：顶部工具条 + 可拖拽画布 + 边界扩容按钮 + 复位按钮。"""

    changed = Signal()  # 数据变化（由主窗口负责持久化）

    def __init__(self, item: dict, theme: dict, parent=None) -> None:
        super().__init__(parent)
        self.item = dict(item)
        self.setWindowTitle(f"故事时间轴 · {self.item.get('title') or '未命名'}")
        self.resize(1280, 780)
        self.setModal(False)
        self.setAttribute(Qt.WA_DeleteOnClose, False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 顶部工具条
        bar = QWidget(self)
        bar.setObjectName("TimelineBar")
        bar.setAttribute(Qt.WA_StyledBackground, True)
        bar.setFixedHeight(56)
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(18, 8, 18, 8)
        bar_layout.setSpacing(10)

        title = QLabel(f"{self.item.get('title') or '未命名'}", bar)
        title.setObjectName("NameGenTitle")
        bar_layout.addWidget(title)
        subtitle = QLabel("自由时间轴 · 无刻度约束", bar)
        subtitle.setObjectName("DrawerHint")
        bar_layout.addWidget(subtitle)
        bar_layout.addStretch(1)
        hint = QLabel("点击横线放置事件 · 滚轮左右快移 · 拖到边界点「＋」扩容", bar)
        hint.setObjectName("DrawerHint")
        bar_layout.addWidget(hint)
        self.save_button = QPushButton("保存", bar)
        self.save_button.setObjectName("GhostButton")
        self.save_button.setCursor(Qt.PointingHandCursor)
        self.save_button.setToolTip("立即保存时间轴（Ctrl+S）")
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

        # 画布（放在滚动区域里，可横向无限延伸）
        self.canvas = TimelineCanvas(theme, self)
        self.canvas.set_data(
            self.item.get("events") or [],
            self.item.get("canvas_width"),
            self.item.get("canvas_origin"),
        )
        self.canvas.changed.connect(self._collect)
        self.canvas.bounds_changed.connect(self._update_edge_buttons)

        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(False)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        # 横向滚动条隐藏，改用与正文一致的隐形快速滚动滑块（滚轮 / 拖动手柄）
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        from app.ui.scroll_float import ScrollFlasher

        self._h_flasher = ScrollFlasher(self.scroll, Qt.Horizontal, inset=8)
        self.scroll.setWidget(self.canvas)
        layout.addWidget(self.scroll, 1)

        # 边界「＋」按钮：拖到最左 / 最右时出现，点击扩容一倍
        self.left_plus = self._make_plus_button("left")
        self.right_plus = self._make_plus_button("right")
        self.left_plus.clicked.connect(lambda: self._extend("left"))
        self.right_plus.clicked.connect(lambda: self._extend("right"))

        # 固定在左侧的「复位」按钮：一键回到 0 点
        self.reset_button = QPushButton("复位", self.scroll.viewport())
        self.reset_button.setObjectName("FloatButton")
        self.reset_button.setCursor(Qt.PointingHandCursor)
        self.reset_button.setFixedSize(64, 34)
        self.reset_button.clicked.connect(self.reset_view)

        self.scroll.horizontalScrollBar().valueChanged.connect(self._update_edge_buttons)
        self.scroll.viewport().installEventFilter(self)

        # 初始定位到画布中部（0 点附近）
        QTimer.singleShot(0, self.reset_view)

    # ------------------------------------------------------------------
    def _make_plus_button(self, side: str) -> QToolButton:
        button = QToolButton(self.scroll.viewport())
        button.setObjectName("FloatButton")
        button.setText("＋")
        button.setCursor(Qt.PointingHandCursor)
        button.setFixedSize(36, 36)
        button.setToolTip("向%s扩长时间轴" % ("左" if side == "left" else "右"))
        button.hide()
        return button

    def _extend(self, direction: str) -> None:
        self.canvas.extend(direction)
        if direction == "right":
            bar = self.scroll.horizontalScrollBar()
            bar.setValue(bar.maximum())   # 贴住新的右边界，方便继续放置节点

    def reset_view(self) -> None:
        """复位：回到 0 点（最开头）。"""
        self.canvas.ensure_origin_visible(self.scroll.viewport().width())

    def _update_edge_buttons(self) -> None:
        bar = self.scroll.horizontalScrollBar()
        viewport = self.scroll.viewport()
        height = viewport.height()
        at_left = bar.value() <= bar.minimum()
        at_right = bar.value() >= bar.maximum()
        self.left_plus.setVisible(at_left)
        self.right_plus.setVisible(at_right)
        if at_left:
            self.left_plus.move(10, int(height / 2 - 18))
            self.left_plus.raise_()
        if at_right:
            self.right_plus.move(viewport.width() - 46, int(height / 2 - 18))
            self.right_plus.raise_()
        self.reset_button.move(10, height - 48)
        self.reset_button.raise_()

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        from PySide6.QtCore import QEvent

        if obj is self.scroll.viewport() and event.type() == QEvent.Resize:
            self._update_edge_buttons()
        return super().eventFilter(obj, event)

    def _collect(self) -> None:
        self.item["events"] = self.canvas.collect()
        # 画布扩容后的长度 / 0 点位置一并持久化（此前只存事件导致长度丢失）
        width, origin = self.canvas.canvas_bounds()
        self.item["canvas_width"] = int(width)
        self.item["canvas_origin"] = float(origin)
        self.changed.emit()

    def _flush_save(self) -> None:
        """立即保存（保存按钮 / Ctrl+S / 关闭窗口时调用）。"""
        self._collect()
        self.save_button.setText("已保存 ✓")
        QTimer.singleShot(1400, lambda: self.save_button.setText("保存"))

    def _export_json(self) -> None:
        """把时间轴导出为 JSON 文件（导出路径实时选择）。"""
        import json

        from PySide6.QtWidgets import QFileDialog

        self._collect()
        path, _f = QFileDialog.getSaveFileName(
            self, "导出时间轴", f"{self.item.get('title') or '时间轴'}.json",
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
        self._collect()  # 关闭前兜底：把画布尺寸与事件全部落盘
        super().closeEvent(event)

    def item_dict(self) -> dict:
        return dict(self.item)

    def set_theme(self, theme: dict) -> None:
        self.canvas.set_theme(theme)
