# -*- coding: utf-8 -*-
"""高级故事时间轴：大尺寸独立窗口。

- 主横线可向两侧无限延伸，鼠标按住空白处拖拽滑动，滚轮可直接左右快移；
- 不分年月日刻度，横线上任意位置点击即可放置事件节点；
- 节点上方「时间」、下方「事件」均有虚线茎连到横线，可附描述；
- 节点须按住**鼠标中键**才能沿横线拖动（左键只负责选中 / 双击编辑 / 右键菜单，
  空白左键拖动画布）；Ctrl + 左键纵向拖动可逐节点调整引线长短；拖到画布边界会
  出现「＋」按钮，点击后画布向该方向扩容一倍初始长度；
- 左侧固定「复位」按钮，一键回到 0 点；
- Ctrl+滚轮横向缩放时间轴（以 0 点为锚，高度与文字不缩放）；
- 数据持久化到 library.json 的时间轴条目中。
"""
from __future__ import annotations

from PySide6.QtCore import (
    QEasingCurve, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, QVariantAnimation, Signal,
)
from PySide6.QtGui import (
    QColor, QFont, QFontMetrics, QIcon, QKeySequence, QPainter, QPen, QPixmap, QShortcut,
)
from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from app.models import new_id
from app.ui.dialogs import MessageBox
from app.ui.icons import IconFactory, flash_saved

INITIAL_WIDTH = 60000     # 初始画布宽度（可按需扩容）
CANVAS_HEIGHT = 620
TICK_GAP = 140            # 装饰性小刻度间距（无单位含义）
STEM_TOP = 92             # 时间文字距横线的默认高度（上引线默认长）
STEM_BOTTOM = 24          # 事件名距横线的默认高度（下引线默认长）
# v3：引线可调范围（默认长的 0.5～3 倍，缺键/非法回退默认）
LEAD_TOP_MIN, LEAD_TOP_MAX = 46, 276
LEAD_BOT_MIN, LEAD_BOT_MAX = 12, 72
ZOOM_MIN, ZOOM_MAX = 0.5, 2.0
ZOOM_STEP_FACTOR = 1.1    # 每格滚轮的缩放系数（Ctrl+滚轮）
# v5 小地图（右下角浮动缩略图）
MINIMAP_MIN_W, MINIMAP_MIN_H = 140, 90
MINIMAP_MAX_W = 200
MINIMAP_GAP = 12          # 距视口右下角的间距
MINIMAP_ANIM_MS = 180     # 点击外部跳转的 ease-out 时长
LABEL_W = 180             # 事件标签块宽度（时间 / 事件名）
DESC_W = 200              # 描述块宽度
# 居中文字的对齐标志组合（逐帧逐行重复引用同值对象，不在绘制循环里反复 |
# 生成新标志：与逐次组合结果等价，只是去掉每帧的临时对象分配）
TEXT_ALIGN_CENTER = Qt.AlignHCenter | Qt.AlignVCenter

NODE_HINT = "中键拖动移动 · Ctrl+左键拖动调整引线长短"
LOCKED_HINT = "事件已锁定 · 右键解锁后才能移动"

# 事件点色板（6 色，纯色块不带语义文字；首项 "" = 默认主题色）。
# 浅 / 深两套十六进制，保证在浅色 panel_alt(#F7F8F9) 与深色 panel_alt(#2C2E33)
# 上都有足够可读性。新数据只存色值（#RRGGBB），旧数据里的 key 仍按此表映射读取。
EVENT_COLORS: list[tuple[str, str, str]] = [
    ("", "#7C5CE6", "#9B7BFF"),
    ("side", "#0D8C88", "#4FD8C7"),
    ("crisis", "#D44343", "#FF7A70"),
    ("memory", "#B07C10", "#EDBE57"),
    ("foreshadow", "#376FE0", "#82B0FF"),
    ("turn", "#B239A0", "#E086E8"),
]
_EVENT_COLOR_MAP = {key: (light, dark) for key, light, dark in EVENT_COLORS}


def event_color_hex(key: str, dark: bool) -> str:
    entry = _EVENT_COLOR_MAP.get(str(key or "")) or _EVENT_COLOR_MAP[""]
    return entry[1 if dark else 0]


def _wrap_all_lines(text: str, fm: "QFontMetrics", width: float) -> list[str]:
    """按字贪心换行的完整结果（不省略），供绘制与 tooltip 判定共用。"""
    lines: list[str] = []
    # 显式换行（"名称\\n年份副标题"）必须先按段落切开，否则会被当成普通字符
    # 塞进同一行的绘制矩形里，第二行又被裁掉。
    for para in str(text).splitlines() or [""]:
        cur = ""
        for ch in para:
            if cur and fm.horizontalAdvance(cur + ch) > width:
                lines.append(cur)
                cur = ch
            else:
                cur += ch
        lines.append(cur)
    return lines


def wrap_lines(text: str, fm: "QFontMetrics", width: float, max_lines: int) -> list[str]:
    """按字贪心换行（中文无空格），保留数据里已有的显式换行；
    超出 max_lines 时末行用省略号收尾。"""
    if not text:
        return []
    lines = _wrap_all_lines(text, fm, width)
    if len(lines) <= max_lines:
        return lines
    tail = "".join(lines[max_lines - 1:])
    return lines[:max_lines - 1] + [fm.elidedText(tail, Qt.ElideRight, int(width))]


def is_elided(text: str, fm: "QFontMetrics", width: float, max_lines: int) -> bool:
    """true = 该文本绘制时一定走了省略号（应补 tooltip 显示全文）。"""
    if not text:
        return False
    return len(_wrap_all_lines(text, fm, width)) > max_lines


def _color_dot_icon(hex_color: str, size: int = 14, selected: bool = False) -> QIcon:
    """供右键菜单使用的纯色圆点图标；选中色多画一圈同色描边（无文字，仍只是颜色块）。"""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    color = QColor(hex_color)
    if selected:
        p.setPen(QPen(color, max(1.4, size / 11.0)))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QRectF(1.0, 1.0, size - 2.0, size - 2.0))
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawEllipse(QRectF(size / 3.2, size / 3.2, size - size / 1.6, size - size / 1.6))
    else:
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawEllipse(QRectF(1.5, 1.5, size - 3.0, size - 3.0))
    p.end()
    return QIcon(pm)


class TimelineMinimap(QWidget):
    """右下角浮动小地图：全时间轴缩略 + 视口方框 + 点击/拖动跳转（v5）。

    - 点集按画布数据版本（canvas._data_version）缓存：只有增删/换色/锁定/换主题/
      扩容才重建；视口变化只移动方框，不重建点集。
    - 只处理自己矩形内的左键；滚轮转发给画布，既有缩放 / 隐藏滚动条交互不变。
    """

    collapse_changed = Signal(bool)

    def __init__(self, canvas: "TimelineCanvas", parent=None) -> None:
        super().__init__(parent)
        self.canvas = canvas
        self._hover = False
        self._collapsed = False
        self._pv = -1                  # 点集对应的数据版本
        self._points: list[tuple[float, str, bool, QColor]] = []   # (fx, id, locked, 色)
        self._dragging = False
        self._drag_grab = 0.0          # 按下时指针相对方框左端的比例偏移
        self._anim: QVariantAnimation | None = None
        self.setMouseTracking(True)
        self.setAttribute(Qt.WA_Hover, True)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("小地图：拖动方框平移 · 点击空白跳转 · 右上角收起")

    # ---------------------------------------------------------------- 尺寸
    def apply_size(self, viewport_width: int) -> None:
        if self._collapsed:
            self.setFixedSize(96, 30)
        else:
            w = min(MINIMAP_MAX_W, max(MINIMAP_MIN_W, int(viewport_width * 0.16)))
            h = max(MINIMAP_MIN_H, min(124, int(w * 0.66)))
            self.setFixedSize(w, h)

    def set_collapsed(self, collapsed: bool, persist: bool = True) -> None:
        collapsed = bool(collapsed)
        if collapsed == self._collapsed:
            return
        self._collapsed = collapsed
        parent = self.parentWidget()
        if parent is not None:
            self.apply_size(parent.width())
        self.update()
        if persist:
            self.collapse_changed.emit(collapsed)

    def is_collapsed(self) -> bool:
        return self._collapsed

    # ---------------------------------------------------------------- 映射
    def _total_dev(self) -> float:
        return max(1.0, float(self.canvas._width) * float(self.canvas._zoom))

    def _view_frac(self) -> tuple[float, float]:
        bar = self.canvas._scrollbar()
        val = float(bar.value()) if bar is not None else 0.0
        total = self._total_dev()
        vw = float(self.parentWidget().width()) if self.parentWidget() is not None else 0.0
        return val / total, min(1.0, (val + vw) / total)

    def _plot_rect(self) -> QRectF:
        r = QRectF(self.rect())
        if self._collapsed:
            return r.adjusted(8, 8, -8, -8)
        return r.adjusted(10, 22, -10, -12)

    def _fx_to_x(self, fx: float) -> float:
        plot = self._plot_rect()
        return plot.left() + min(1.0, max(0.0, fx)) * plot.width()

    def _x_to_fx(self, x: float) -> float:
        plot = self._plot_rect()
        if plot.width() <= 0:
            return 0.0
        return min(1.0, max(0.0, (x - plot.left()) / plot.width()))

    def box_rect(self) -> QRectF:
        plot = self._plot_rect()
        a, b = self._view_frac()
        x0 = plot.left() + a * plot.width()
        x1 = plot.left() + b * plot.width()
        return QRectF(x0, plot.top() - 2, max(2.0, x1 - x0), plot.height() + 4)

    def _toggle_rect(self) -> QRectF:
        r = QRectF(self.rect())
        return QRectF(r.right() - 20, r.top() + 3, 17, 17)

    def _lane_a(self) -> float:
        plot = self._plot_rect()
        return plot.top() + plot.height() * 0.34

    def _lane_b(self) -> float:
        plot = self._plot_rect()
        return plot.top() + plot.height() * 0.78

    # ---------------------------------------------------------------- 点集缓存
    def _points_cached(self) -> list[tuple[float, str, bool]]:
        version = int(getattr(self.canvas, "_data_version", 0))
        if version != self._pv:
            width = max(1.0, float(self.canvas._width))
            pts = []
            ink = QColor(self.canvas.theme["text"])
            for e in self.canvas.events:
                fx = min(1.0, max(0.0, self.canvas.event_x(e) / width))
                locked = self.canvas._is_locked(e)
                pts.append((fx, str(e.get("id")), locked,
                            QColor(ink if locked else self.canvas._event_qcolor(e))))
            self._points = pts
            self._pv = version
        return self._points

    # ---------------------------------------------------------------- 绘制
    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        theme = self.canvas.theme
        self._glass_panel(painter)
        painter.setOpacity(1.0 if self._hover else 0.85)
        if self._collapsed:
            ink = QColor(theme["text"])
            f = QFont(self.font())
            f.setPixelSize(11)
            painter.setFont(f)
            painter.setPen(ink)
            painter.drawText(QRectF(self.rect()).adjusted(10, 0, -20, 0),
                             Qt.AlignLeft | Qt.AlignVCenter, "小地图")
            self._draw_chevron(painter, ink, expand=True)
            painter.end()
            return
        self._draw_plot(painter, theme)
        painter.end()

    def _glass_panel(self, painter: QPainter) -> bool:
        """立体玻璃块质感（侧壁 / rim / 内阴影）；非玻璃风走 panel 圆角底。"""
        from app.ui.surface import is_glass, paint_liquid_overlay

        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if is_glass():
            paint_liquid_overlay(painter, r, 12.0, 1.0 if self._hover else 0.55,
                                 sheen_center=QPointF(r.left() + r.width() * 0.28,
                                                      r.top() + r.height() * 0.18))
            return True
        painter.setPen(QPen(QColor(self.canvas.theme["border_strong"]), 1))
        painter.setBrush(QColor(self.canvas.theme["panel"]))
        painter.drawRoundedRect(r, 12, 12)
        return False

    def _draw_plot(self, painter: QPainter, theme: dict) -> None:
        plot = self._plot_rect()
        ink = QColor(theme["text"])
        accent = QColor(theme["accent"])
        muted = QColor(ink)
        muted.setAlpha(70)
        # 两条轨道基线（上：时间 / 下：事件），把点分开避免叠成一团
        painter.setPen(QPen(muted, 1))
        for y in (self._lane_a(), self._lane_b()):
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))

        a, b = self._view_frac()
        dots = self._points_cached()
        selected = str(self.canvas._selected_id or "")
        sel_color = QColor(accent)
        for fx, eid, locked, base in dots:
            x = self._fx_to_x(fx)
            inside = a <= fx <= b
            col = QColor(base)
            col.setAlpha(235 if inside else 110)
            painter.setPen(Qt.NoPen)
            painter.setBrush(col)
            painter.drawEllipse(QPointF(x, self._lane_a()), 1.7, 1.7)
            painter.drawEllipse(QPointF(x, self._lane_b()), 2.3, 2.3)
            if locked:       # 锁定点：外圈实心环，一眼分辨
                painter.setBrush(Qt.NoBrush)
                painter.setPen(QPen(col, 1.0))
                painter.drawEllipse(QPointF(x, self._lane_b()), 3.6, 3.6)
            if eid == selected and eid:      # 选中高亮
                ring = QColor(sel_color)
                ring.setAlpha(230)
                painter.setBrush(Qt.NoBrush)
                painter.setPen(QPen(ring, 1.3))
                painter.drawEllipse(QPointF(x, self._lane_b()), 5.4, 5.4)
                painter.drawEllipse(QPointF(x, self._lane_a()), 3.4, 3.4)

        # 视口方框（描边 accent + 极淡填充）
        box = self.box_rect()
        fill = QColor(accent)
        fill.setAlpha(26)
        painter.setBrush(fill)
        border = QColor(accent)
        border.setAlpha(210)
        painter.setPen(QPen(border, 1.4))
        painter.drawRoundedRect(box, 3, 3)
        self._draw_chevron(painter, ink, expand=False)

    def _draw_chevron(self, painter: QPainter, ink: QColor, expand: bool) -> None:
        r = QRectF(self.rect()) if self._collapsed else self._toggle_rect()
        cx, cy = r.right() - 10, r.top() + (r.height() / 2 if self._collapsed else 10)
        pen = QPen(ink, 1.4)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        dx = 3 if expand else -3
        painter.drawLine(QPointF(cx - dx, cy - 3), QPointF(cx + dx, cy))
        painter.drawLine(QPointF(cx + dx, cy), QPointF(cx - dx, cy + 3))

    # ---------------------------------------------------------------- 交互
    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.LeftButton:
            return super().mousePressEvent(event)
        pos = QPointF(event.position())
        if self._collapsed:
            self.set_collapsed(False)
            event.accept()
            return
        if self._toggle_rect().contains(pos):
            self.set_collapsed(True)
            event.accept()
            return
        self._stop_anim()
        if self.box_rect().contains(pos):
            self._dragging = True
            a, _b = self._view_frac()
            self._drag_grab = self._x_to_fx(pos.x()) - a
            self.setCursor(Qt.ClosedHandCursor)
        else:
            self.jump_to_frac(self._x_to_fx(pos.x()), animate=True)
        event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        pos = QPointF(event.position())
        if self._dragging:
            frac = self._x_to_fx(pos.x()) - self._drag_grab
            self.scroll_to_frac(frac)
            event.accept()
            return
        if not self._collapsed:
            inside = self.box_rect().contains(pos)
            self.setCursor(Qt.OpenHandCursor if inside else Qt.PointingHandCursor)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._dragging:
            self._dragging = False
            self.setCursor(Qt.OpenHandCursor)
            self.update()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event) -> None:  # noqa: N802
        # 交给画布：滚轮左右快移 / Ctrl 缩放语义完全沿用既有实现
        self.canvas.wheelEvent(event)

    # ---------------------------------------------------------------- 滚动
    def _bar(self):
        return self.canvas._scrollbar()

    def _max_scroll(self) -> int:
        bar = self._bar()
        return int(bar.maximum()) if bar is not None else 0

    def scroll_to_frac(self, frac: float) -> None:
        """方框左端落到 frac 处（按比例换算成滚动像素；超出自动夹到两端）。"""
        bar = self._bar()
        if bar is None:
            return
        target = frac * self._total_dev()
        target = min(float(self._max_scroll()), max(0.0, target))
        bar.setValue(int(round(target)))
        self.update()

    def jump_to_frac(self, frac: float, animate: bool = True) -> None:
        """点击外部：目标点居中（150–200ms ease-out），拖动中则直接跟手。"""
        bar = self._bar()
        if bar is None:
            return
        vw = float(self.parentWidget().width()) if self.parentWidget() is not None else 0.0
        target = frac * self._total_dev() - vw / 2
        target = min(float(self._max_scroll()), max(0.0, target))
        self._stop_anim()
        if not animate:
            bar.setValue(int(round(target)))
            self.update()
            return
        start = float(bar.value())
        anim = QVariantAnimation(self)
        anim.setStartValue(start)
        anim.setEndValue(target)
        anim.setDuration(MINIMAP_ANIM_MS)
        anim.setEasingCurve(QEasingCurve.OutCubic)

        def _on_value(v) -> None:
            bar.setValue(int(round(float(v))))
            self.update()

        anim.valueChanged.connect(_on_value)
        anim.finished.connect(self.update)
        self._anim = anim
        anim.start()

    def _stop_anim(self) -> None:
        if self._anim is not None:
            self._anim.stop()
            self._anim = None


class TimelineCanvas(QWidget):
    """时间轴画布：横线与事件节点全部自绘。"""

    changed = Signal()          # 节点增删改后发出（触发保存）
    bounds_changed = Signal()   # 画布尺寸 / 原点变化后发出（更新边界按钮）
    hint_requested = Signal(str)  # 需要在主窗口上显示一次性轻提示
    selection_changed = Signal()  # 选中节点变化（小地图高亮用）

    def __init__(self, theme: dict, parent=None) -> None:
        super().__init__(parent)
        self.theme = theme
        self.events: list[dict] = []
        self._width = INITIAL_WIDTH
        self._origin = INITIAL_WIDTH / 2
        self._zoom = 1.0
        # v5：数据版本号——点集/缩略图按它缓存，只在增删改 / 换色 / 锁定 / 扩容 /
        # 换主题时 +1；拖拽节点坐标与视口滚动不 +1（小地图只需移动方框）
        self._data_version = 0
        self._apply_view_size()
        self.setMouseTracking(True)          # v3：悬停节点要给光标 / 气泡提示
        self.setCursor(Qt.OpenHandCursor)

        # 拖拽事务："" / "pan"（空白平移）/ "node"（中键移点）/
        # "lead"（Ctrl+左键调引线）/ "nodeleft"（左键点在节点上，仅选中不移动）
        self._drag_mode = ""
        self._drag_moved = False
        self._press_pos = QPointF()
        self._press_raw = QPointF()
        self._press_scroll_x = 0.0
        self._drag_node: dict | None = None
        self._drag_anchor_x = 0.0            # 中键拖动起点（逻辑 x），用于画原位残影
        self._lead_side = ""                 # 引线调整事务："top" / "bot"
        self._lead_anchor_len = 0.0
        self._lead_anchor_y = 0.0
        # 锁定 / 选中 / 一次性提示 / 悬停状态
        self._press_locked = False
        self._selected_id: str | None = None
        self._locked_hint_shown = False
        self._hover_id: str | None = None
        self._hover_tip = ""
        # 每帧复用的三档字体（随控件字体变化惰性重建，不在 paint 里 new QFont）
        self._font_cache_key: str | None = None
        self._font_time: QFont | None = None
        self._font_title: QFont | None = None
        self._font_desc: QFont | None = None
        # 配套的 QFontMetrics 与换行结果缓存（同样随字体变化整体失效）：
        # 文本排版只和「字体 + 文本 + 宽度 + 行数」有关，与缩放 / 滚动无关，
        # 重复绘制时直接复用，绘制结果与逐帧重新排版完全一致。
        self._fm_time: QFontMetrics | None = None
        self._fm_title: QFontMetrics | None = None
        self._fm_desc: QFontMetrics | None = None
        self._wrap_cache: dict[tuple[str, str, float, int], list[str]] = {}

    def _cached_fonts(self) -> tuple[QFont, QFont, QFont]:
        key = self.font().key()
        if key != self._font_cache_key:
            f_time = QFont(self.font()); f_time.setPixelSize(12)
            f_title = QFont(self.font()); f_title.setPixelSize(14); f_title.setBold(True)
            f_desc = QFont(self.font()); f_desc.setPixelSize(11)
            self._font_time, self._font_title, self._font_desc = f_time, f_title, f_desc
            self._fm_time = QFontMetrics(f_time)
            self._fm_title = QFontMetrics(f_title)
            self._fm_desc = QFontMetrics(f_desc)
            self._wrap_cache.clear()
            self._font_cache_key = key
        return self._font_time, self._font_title, self._font_desc

    def _cached_metrics(self) -> tuple[QFontMetrics, QFontMetrics, QFontMetrics]:
        """与 _cached_fonts 同一生命周期的三档度量（字体不变就不重建）。"""
        self._cached_fonts()
        return self._fm_time, self._fm_title, self._fm_desc

    def _wrap_cached(self, text: str, slot: str, fm: QFontMetrics,
                     width: float, max_lines: int) -> list[str]:
        """wrap_lines 的结果缓存；键含档位 / 全文 / 宽度 / 行数，字体变更时清空。"""
        key = (slot, text, width, max_lines)
        lines = self._wrap_cache.get(key)
        if lines is None:
            lines = wrap_lines(text, fm, width, max_lines)
            if len(self._wrap_cache) >= 1024:
                self._wrap_cache.clear()
            self._wrap_cache[key] = lines
        return lines

    def set_theme(self, theme: dict) -> None:
        self.theme = theme
        self._bump_data()          # 节点色 / 底色的缩略图点集需要重算
        self.update()

    def _bump_data(self) -> None:
        """数据版本 +1（小地图点集据此失效；拖拽坐标不调用）。"""
        self._data_version += 1

    def set_data(self, events: list[dict], width=None, origin=None) -> None:
        # 复制时给旧数据补缺失键：未锁定 + 默认色，任何入口进来都不报错
        normalized = []
        for e in events:
            node = dict(e)
            node["locked"] = node.get("locked") is True
            node["color"] = str(node.get("color") or "")
            normalized.append(node)
        self.events = normalized
        # 恢复上次扩容后的画布尺寸与 0 点位置（老数据没有这两个字段时用默认值）
        try:
            if width is not None and int(width) >= self._width:
                self._width = int(width)
            if origin is not None and 0 < float(origin) <= self._width:
                self._origin = float(origin)
        except (TypeError, ValueError):
            pass
        self._apply_view_size()
        self._bump_data()
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

    def zoom_at(self, steps: float, _device_pos: QPointF | None = None) -> None:
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
    # v3：每节点独立的上 / 下引线长度（缺键、非正数、越界均回退/夹取）
    # ------------------------------------------------------------------
    @staticmethod
    def _clamp_lead(value, lo: float, hi: float, default: float) -> float:
        try:
            v = float(value)
        except (TypeError, ValueError):
            return default
        if v <= 0:                 # -1 或缺键：默认
            return default
        return min(hi, max(lo, v))

    def lead_top(self, e: dict) -> float:
        return self._clamp_lead(e.get("lead_top", -1.0), LEAD_TOP_MIN, LEAD_TOP_MAX, STEM_TOP)

    def lead_bot(self, e: dict) -> float:
        return self._clamp_lead(e.get("lead_bot", -1.0), LEAD_BOT_MIN, LEAD_BOT_MAX, STEM_BOTTOM)

    # ------------------------------------------------------------------
    # 锁定 / 换色
    # ------------------------------------------------------------------
    def _is_dark(self) -> bool:
        return QColor(self.theme["panel_alt"]).lightness() < 128

    @staticmethod
    def _is_locked(e: dict) -> bool:
        return e.get("locked") is True

    def _event_qcolor(self, e: dict) -> QColor:
        """节点色：空 = 主题 accent；#RRGGBB 直接用；旧数据里的 key 按色板映射读取。"""
        raw = str(e.get("color") or "")
        if not raw:
            return QColor(self.theme["accent"])
        if raw.startswith("#"):
            c = QColor(raw)
            if c.isValid():
                return c
            return QColor(self.theme["accent"])
        return QColor(event_color_hex(raw, self._is_dark()))

    def _draw_lock_badge(self, painter: QPainter, x: float, y: float) -> None:
        """节点右上角小锁（主体色取 text 令牌，锁孔透出 panel 底色）。"""
        panel = QColor(self.theme["panel"])
        ink = QColor(self.theme["text"])
        painter.save()
        # 背板小圆：让徽标压在红莲/任何语义色上都清晰
        painter.setPen(Qt.NoPen)
        painter.setBrush(panel)
        painter.drawEllipse(QPointF(x + 9, y - 9), 8.2, 8.2)
        # 锁梁（上方半圆弧）+ 锁体
        pen = QPen(ink, 1.4)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.setBrush(ink)
        painter.drawArc(QRectF(x + 5.2, y - 13.4, 7.6, 7.6), 180 * 16, 180 * 16)
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(QRectF(x + 4.4, y - 9.6, 9.2, 7.2), 2.0, 2.0)
        painter.setBrush(panel)
        painter.drawEllipse(QPointF(x + 9.0, y - 6.4), 1.25, 1.25)
        painter.fillRect(QRectF(x + 8.6, y - 6.0, 0.9, 2.4), panel)
        painter.restore()

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
        self._bump_data()
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

        # 事件节点：时间在上，事件在下（均有虚线茎连到横线；v3 引线长度逐节点可调）
        active_lead = self._drag_node if self._drag_mode == "lead" else None
        moving_node = self._drag_node if self._drag_mode == "node" else None
        font_time, font_title, font_desc = self._cached_fonts()
        fm_time, fm_title, fm_desc = self._cached_metrics()
        # 主题文字 / 描述色整帧不变，移出逐事件循环（每帧只建两个 QColor）
        text_color = QColor(theme["text"])
        desc_color = QColor(theme["subtext"])
        for e in self.events:
            x = X(self.event_x(e))
            color = self._event_qcolor(e)
            lead_top = self.lead_top(e)
            lead_bot = self.lead_bot(e)
            top_active = active_lead is e and self._lead_side == "top"
            bot_active = active_lead is e and self._lead_side == "bot"

            # 选中态外圈（与锁定徽标可同时出现，互不遮挡）
            if self._selected_id is not None and str(e.get("id")) == self._selected_id:
                ring = QColor(theme["text"])
                ring.setAlpha(150)
                painter.setPen(QPen(ring, 1.6))
                painter.setBrush(Qt.NoBrush)
                painter.drawEllipse(QPointF(x, mid_y), 11.4, 11.4)

            # 中键拖动中的节点：在拖动起点画虚线残影圈，明确跟手位移
            if moving_node is e:
                ghost = QColor(color)
                ghost.setAlpha(120)
                gp = QPen(ghost, 1.4, Qt.DashLine)
                painter.setPen(gp)
                painter.setBrush(Qt.NoBrush)
                painter.drawEllipse(QPointF(X(self._drag_anchor_x), mid_y), 7, 7)

            # 节点圆（颜色跟随语义色板；拖动中略放大 + 提亮）
            radius = 8.2 if moving_node is e else 7.0
            painter.setPen(QPen(QColor(theme["panel"]), 2))
            painter.setBrush(color.lighter(112) if moving_node is e else color)
            painter.drawEllipse(QPointF(x, mid_y), radius, radius)

            # 上茎 + 时间标签（引线长度逐节点）
            time_text = str(e.get("time") or "")
            if time_text:
                stem_top = mid_y - lead_top
                pen = QPen(color, 2.0 if top_active else 1.4,
                           Qt.SolidLine if top_active else Qt.DashLine)
                painter.setPen(pen)
                painter.drawLine(QPointF(x, mid_y - 9), QPointF(x, stem_top + 6))
                if top_active:
                    painter.setBrush(color)
                    painter.setPen(Qt.NoPen)
                    painter.drawEllipse(QPointF(x, stem_top + 6), 3.4, 3.4)

                font = font_time
                painter.setFont(font)
                painter.setPen(QPen(color))
                # 按实际换行行数算文字块高度（含行距），文字底边固定在引线起点上方，
                # 多行也完整可见；超过 2 行才省略号收尾
                fm = fm_time
                lines = self._wrap_cached(time_text, "time", fm, LABEL_W, 2)
                line_h = fm.height()
                top = stem_top - 6 - line_h * len(lines)
                for i, ln in enumerate(lines):
                    painter.drawText(
                        QRectF(x - LABEL_W / 2, top + i * line_h, LABEL_W, line_h),
                        TEXT_ALIGN_CENTER, ln,
                    )

            # 下茎 + 事件名（文字块从引线末端向下排；多行按实际高度，不被固定 rect 裁掉）
            title_font = font_title
            title = str(e.get("event") or "未命名事件")
            painter.setFont(title_font)
            painter.setPen(QPen(text_color))
            fm_t = fm_title
            t_lines = self._wrap_cached(title, "title", fm_t, LABEL_W, 2)
            t_line_h = fm_t.height()
            t_top = mid_y + lead_bot
            for i, ln in enumerate(t_lines):
                painter.drawText(
                    QRectF(x - LABEL_W / 2, t_top + i * t_line_h, LABEL_W, t_line_h),
                    TEXT_ALIGN_CENTER, ln,
                )
            painter.setPen(QPen(color, 2.0 if bot_active else 1.4,
                                Qt.SolidLine if bot_active else Qt.DashLine))
            painter.drawLine(QPointF(x, mid_y + 9), QPointF(x, mid_y + lead_bot - 2))
            if bot_active:
                painter.setBrush(color)
                painter.setPen(Qt.NoPen)
                painter.drawEllipse(QPointF(x, mid_y + lead_bot - 2), 3.4, 3.4)

            # 描述（接在事件名实际高度下方，不压字、不越界）
            desc = str(e.get("desc") or "")
            if desc:
                font_d = font_desc
                painter.setFont(font_d)
                painter.setPen(QPen(desc_color))
                fm_d = fm_desc
                d_lines = self._wrap_cached(desc, "desc", fm_d, DESC_W, 3)
                d_line_h = fm_d.height()
                d_top = t_top + t_line_h * len(t_lines) + 6
                for i, ln in enumerate(d_lines):
                    painter.drawText(
                        QRectF(x - DESC_W / 2, d_top + i * d_line_h, DESC_W, d_line_h),
                        TEXT_ALIGN_CENTER, ln,
                    )

            # 锁定徽标最后画，压在最上层；与选中环各占一侧，可同时表达
            if self._is_locked(e):
                self._draw_lock_badge(painter, x, mid_y)
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

    # ------------------------------------------------------------------
    # v3 交互模型
    #   左键空白：拖动画布；左键节点：仅选中（双击才编辑，右键菜单不变）
    #   中键节点：沿横线移动（锁定节点任何键都不能动）
    #   Ctrl+左键节点：纵向拖动调引线长短（上半条调时间引线，下半条调事件引线）
    # ------------------------------------------------------------------
    def _begin_transaction(self, raw: QPointF, pos: QPointF) -> None:
        self._drag_moved = False
        self._press_pos = pos
        self._press_raw = raw
        self._press_scroll_x = self._scroll_x()
        self._lead_side = ""

    def _locked_press(self, hit: dict) -> None:
        """在锁定节点上按下：禁止态 + 一次性提示，坐标与引线都不改。"""
        self._press_locked = True
        self._drag_node = None
        self._drag_mode = ""
        self.setCursor(Qt.ForbiddenCursor)

    def _locked_drag_note(self) -> None:
        if not self._locked_hint_shown:
            self._locked_hint_shown = True
            self.hint_requested.emit(LOCKED_HINT)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        button = event.button()
        if button not in (Qt.LeftButton, Qt.MiddleButton):
            return super().mousePressEvent(event)
        raw = QPointF(event.position())
        pos = self._to_logical(raw)
        hit = self._node_at(pos)
        self._press_locked = False
        self._drag_node = None
        self._begin_transaction(raw, pos)
        QToolTip.hideText()

        if button == Qt.MiddleButton:
            # 中键：只有点在节点上才有意义（空白中键不触发画布自动滚动）
            if hit is None:
                self._drag_mode = ""
                event.accept()
                return
            if self._is_locked(hit):
                self._locked_press(hit)
                event.accept()
                return
            self._drag_mode = "node"
            self._drag_node = hit
            self._drag_anchor_x = self.event_x(hit)
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()
            return

        # ---- 左键 ----
        ctrl = bool(event.modifiers() & Qt.ControlModifier)
        if hit is not None and ctrl:
            # Ctrl+左键节点：调整引线（锁定节点禁止）
            if self._is_locked(hit):
                self._locked_press(hit)
                event.accept()
                return
            self._drag_mode = "lead"
            self._drag_node = hit
            self._lead_side = "top" if pos.y() <= CANVAS_HEIGHT / 2 else "bot"
            self._lead_anchor_len = (self.lead_top(hit) if self._lead_side == "top"
                                     else self.lead_bot(hit))
            self._lead_anchor_y = raw.y()
            self.setCursor(Qt.SizeVerCursor)
            event.accept()
            return

        if hit is not None:
            # 普通左键点节点：只选中，不移动（编辑走双击，锁定/换色走右键）
            self._drag_mode = "nodeleft"
            self._drag_node = hit
            self.setCursor(Qt.PointingHandCursor)
            event.accept()
            return

        # 左键空白：平移画布
        self._drag_mode = "pan"
        self.setCursor(Qt.ClosedHandCursor)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        raw = QPointF(event.position())
        pos = self._to_logical(raw)
        mode = self._drag_mode

        # 无按下事务：悬停反馈（光标 + 气泡提示）
        if not mode and not (event.buttons() & (Qt.LeftButton | Qt.MiddleButton)):
            self._update_hover(pos, event.modifiers() & Qt.ControlModifier, raw)
            return super().mouseMoveEvent(event)

        if abs(pos.x() - self._press_pos.x()) > 3 or abs(pos.y() - self._press_pos.y()) > 3:
            self._drag_moved = True

        if self._press_locked:
            if self._drag_moved:
                self._locked_drag_note()
            return

        if mode == "node":
            # 中键拖动节点：只沿横线跟手移动；吸附 / 落点规则与旧版完全一致
            delta = pos.x() - self._press_pos.x()
            if abs(delta) > 0:
                self._drag_node["x"] = float(self._drag_node.get("x") or 0) + delta
                self._press_pos = pos
                self.update()
            return

        if mode == "lead":
            # Ctrl+左键纵向拖：上引线向上增长，下引线向下增长；节点 x 不动
            node = self._drag_node
            # 拖动超界直接夹到最小/最大（缺键回默认是读取时 lead_top/lead_bot 的语义）
            if self._lead_side == "top":
                length = self._lead_anchor_len - (raw.y() - self._lead_anchor_y)
                length = min(LEAD_TOP_MAX, max(LEAD_TOP_MIN, length))
                node["lead_top"] = round(length, 1)
            else:
                length = self._lead_anchor_len + (raw.y() - self._lead_anchor_y)
                length = min(LEAD_BOT_MAX, max(LEAD_BOT_MIN, length))
                node["lead_bot"] = round(length, 1)
            self._drag_moved = True
            self.update()
            return

        if mode == "nodeleft":
            # 左键在节点上的拖动不再移动节点（也不平移画布）
            return

        if mode == "pan":
            raw_delta = raw.x() - self._press_raw.x()
            scroll = self._scrollbar()
            if scroll is not None:
                scroll.setValue(int(self._press_scroll_x - raw_delta))

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        button = event.button()
        if button not in (Qt.LeftButton, Qt.MiddleButton):
            return super().mouseReleaseEvent(event)
        raw = QPointF(event.position())
        pos = self._to_logical(raw)
        mode = self._drag_mode
        node = self._drag_node
        was_moved = self._drag_moved
        self._drag_mode = ""
        self._drag_node = None
        self._lead_side = ""
        self._press_locked = False
        self._apply_hover_cursor(pos, event.modifiers() & Qt.ControlModifier)

        if button == Qt.MiddleButton:
            # 中键松手（含空白处按下）：只有在节点上发生位移才落盘，绝不触发新增
            if mode == "node" and was_moved and node is not None:
                self.changed.emit()
            self.update()
            event.accept()
            return

        if mode == "lead":
            if was_moved:
                self.changed.emit()
            self.update()
            return

        if mode == "nodeleft":
            # 左键在节点上松手即选中（左键拖动节点不移动，抖动也不应丢选中）；双击才进编辑
            if node is not None:
                self._selected_id = str(node.get("id"))
                self.selection_changed.emit()
            self.update()
            return

        if mode == "pan":
            if was_moved:
                return super().mouseReleaseEvent(event)
            # 空白点击：命中横线附近 -> 新建节点（原交互保留）
            if abs(pos.y() - CANVAS_HEIGHT / 2) <= 52:
                self._add_node(pos.x() - self._origin)
            super().mouseReleaseEvent(event)

    # ------------------------------------------------------------------
    # 悬停：光标区分可移动 / 调引线 / 锁定，气泡提示操作方式
    # 名字/时间被省略号截断时，同一个气泡里补全文（含原始换行），不叠加第二个 tooltip
    # ------------------------------------------------------------------
    def _elided_fields(self, e: dict) -> list[str]:
        """返回该事件所有被省略的文本（含字段名前缀，原样保留 \\n 换行）。"""
        ft, fh, fd = self._cached_fonts()
        out: list[str] = []
        for label, raw, font, width, max_lines in (
            ("时间", e.get("time"), ft, LABEL_W, 2),
            ("事件", e.get("event"), fh, LABEL_W, 2),
            ("描述", e.get("desc"), fd, DESC_W, 3),
        ):
            text = str(raw or "")
            if is_elided(text, QFontMetrics(font), width, max_lines):
                out.append(f"{label}：{text}")
        return out

    def _label_hit(self, pos: QPointF) -> tuple[dict | None, str]:
        """命中时间/事件文字块 -> (事件, 省略全文)；未省略时全文为空。"""
        ft, fh, _ = self._cached_fonts()
        mid = CANVAS_HEIGHT / 2
        for e in self.events:
            x = self.event_x(e)
            if abs(pos.x() - x) > LABEL_W / 2 + 16:
                continue
            time_text = str(e.get("time") or "")
            if time_text:
                fm = QFontMetrics(ft)
                lines = wrap_lines(time_text, fm, LABEL_W, 2)
                h = fm.height() * len(lines)
                top = mid - self.lead_top(e) - 6 - h
                if QRectF(x - LABEL_W / 2, top, LABEL_W, h).contains(pos):
                    tip = ("时间：" + time_text
                           if is_elided(time_text, fm, LABEL_W, 2) else "")
                    return e, tip
            title = str(e.get("event") or "未命名事件")
            fm_t = QFontMetrics(fh)
            lines_t = wrap_lines(title, fm_t, LABEL_W, 2)
            top_t = mid + self.lead_bot(e)
            if QRectF(x - LABEL_W / 2, top_t,
                      LABEL_W, fm_t.height() * len(lines_t)).contains(pos):
                tip = ("事件：" + title
                       if is_elided(title, fm_t, LABEL_W, 2) else "")
                return e, tip
        return None, ""

    def _update_hover(self, pos: QPointF, ctrl: bool, raw: QPointF | None = None) -> None:
        hit = self._node_at(pos)
        label_ev, label_tip = (None, "")
        if hit is None:
            label_ev, label_tip = self._label_hit(pos)
        target = hit if hit is not None else label_ev
        hid = str(target.get("id")) if target is not None else None
        if hit is not None:
            tip = "\n".join(
                [LOCKED_HINT if self._is_locked(hit) else NODE_HINT]
                + self._elided_fields(hit)
            )
        else:
            tip = label_tip
        if hid != self._hover_id or tip != self._hover_tip:
            self._hover_id = hid
            self._hover_tip = tip
            if not tip:
                QToolTip.hideText()
            elif hit is not None:
                rect = QRect(int(self.event_x(hit) - 60),
                             int(CANVAS_HEIGHT / 2 - 40), 120, 80)
                QToolTip.showText(self.mapToGlobal(rect.center()), tip, self, rect)
            else:
                anchor = QPoint(int(pos.x() * self._zoom) + 24, int(pos.y()) + 18)
                QToolTip.showText(self.mapToGlobal(anchor), tip, self)
        self._apply_hover_cursor(pos, ctrl)

    def _apply_hover_cursor(self, pos: QPointF, ctrl: bool) -> None:
        if self._drag_mode:
            return
        hit = self._node_at(pos)
        if hit is None:
            self.setCursor(Qt.OpenHandCursor)
        elif self._is_locked(hit):
            self.setCursor(Qt.ArrowCursor)
        elif ctrl:
            self.setCursor(Qt.SizeVerCursor)
        else:
            self.setCursor(Qt.SplitHCursor)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover_id = None
        QToolTip.hideText()
        if not self._drag_mode:
            self.setCursor(Qt.OpenHandCursor)
        super().leaveEvent(event)

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        """右键节点：锁定 / 解锁 + 换色 + 编辑 / 删除。空白处不改变原交互。"""
        pos = self._to_logical(QPointF(event.pos()))
        node = self._node_at(pos)
        if node is None:
            return super().contextMenuEvent(event)
        self._selected_id = str(node.get("id"))
        self.selection_changed.emit()
        self.update()
        menu = self._build_context_menu(node)
        menu.exec(event.globalPos())
        menu.deleteLater()

    def _build_context_menu(self, node: dict) -> QMenu:
        """节点右键菜单（拆出来便于冒烟/截图复用同一份 UI）。"""
        menu = QMenu(self)
        locked = self._is_locked(node)
        menu.addAction("解锁事件" if locked else "锁定事件", lambda: self._toggle_lock(node))

        color_menu = menu.addMenu("更换事件颜色")
        color_menu.setToolTipsVisible(True)
        current = self._event_qcolor(node).name()
        dark = self._is_dark()
        for key, light, dark_hex in EVENT_COLORS:
            hex_color = str(self.theme["accent"]) if not key else (dark_hex if dark else light)
            hex_color = QColor(hex_color).name()
            checked = hex_color == current
            # 选中色多一圈描边（菜单项不再有文字名，仅颜色 + 描边表达选中）
            action = color_menu.addAction(_color_dot_icon(hex_color, 18, selected=checked), " ")
            action.setCheckable(True)
            action.setChecked(checked)
            action.setToolTip(hex_color.upper())      # 仅色值，不带语义名
            action.triggered.connect(
                lambda _checked=False, h=hex_color: self._set_event_color(node, h)
            )

        menu.addSeparator()
        menu.addAction("编辑事件", lambda: self._edit_node(node))
        menu.addAction("删除事件", lambda: self._delete_node(node))
        return menu

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
        event = {
            "id": new_id(), "x": x, "time": "", "event": "新事件", "desc": "",
            "locked": False, "color": "",
        }
        self.events.append(event)
        self._selected_id = str(event["id"])
        self._bump_data()
        self.update()
        self.selection_changed.emit()
        self.changed.emit()
        self._edit_node(event)

    def _toggle_lock(self, node: dict) -> None:
        node["locked"] = not self._is_locked(node)
        self._bump_data()
        self.update()
        self.changed.emit()

    def _set_event_color(self, node: dict, hex_value: str) -> None:
        """只存色值（#RRGGBB）；旧数据里的语义 key 读取时按色板映射，不丢颜色。"""
        node["color"] = str(hex_value or "")
        self._bump_data()
        self.update()
        self.changed.emit()

    def _delete_node(self, node: dict) -> None:
        if not MessageBox.question(self, "删除事件", "确定删除这个事件节点吗？"):
            return
        node_id = str(node.get("id"))
        self.events = [e for e in self.events if str(e.get("id")) != node_id]
        if self._selected_id == node_id:
            self._selected_id = None
            self.selection_changed.emit()
        self._bump_data()
        self.update()
        self.changed.emit()

    def _edit_node(self, node: dict) -> None:
        dialog = EventEditDialog(
            node,
            self.window(),
            color_hex=self._event_qcolor(node).name(),
        )
        if dialog.exec() == QDialog.Accepted:
            node["time"] = dialog.time_text()
            node["event"] = dialog.event_text()
            node["desc"] = dialog.desc_text()
            node["locked"] = dialog.locked_state
            self._bump_data()
            self.update()
            self.changed.emit()
        elif dialog.deleted:
            self.events = [e for e in self.events if e.get("id") != node.get("id")]
            if self._selected_id == str(node.get("id")):
                self._selected_id = None
                self.selection_changed.emit()
            self._bump_data()
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

    def __init__(self, node: dict, parent=None, color_hex: str = "") -> None:
        super().__init__(parent)
        self.deleted = False
        self.locked_state = node.get("locked") is True
        self.setWindowTitle("编辑事件")
        self.setModal(True)
        self.resize(400, 360)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 16)
        layout.setSpacing(10)

        # 详情条：只留色点（悬停 tooltip 显示色值），不再出现语义色名
        info_row = QHBoxLayout()
        info_row.setSpacing(8)
        dot = QLabel(self)
        dot.setPixmap(_color_dot_icon(color_hex or "#7C5CE6", 16).pixmap(16, 16))
        dot.setToolTip((color_hex or "#7C5CE6").upper())
        info_row.addWidget(dot)
        info_row.addStretch(1)
        self.lock_button = QPushButton(self)
        self.lock_button.setObjectName("GhostButton")
        self.lock_button.setCursor(Qt.PointingHandCursor)
        self.lock_button.setCheckable(True)
        self.lock_button.setChecked(self.locked_state)
        self.lock_button.setText("已锁定 · 点击解锁" if self.locked_state else "锁定事件")
        self.lock_button.toggled.connect(self._on_lock_toggled)
        info_row.addWidget(self.lock_button)
        layout.addLayout(info_row)

        form = QFormLayout()
        form.setSpacing(10)
        self.time_edit = QLineEdit(str(node.get("time") or ""), self)
        self.time_edit.setPlaceholderText("例如：第三年冬天 / 开局三日")
        form.addRow("时间", self.time_edit)
        self.event_edit = QLineEdit(str(node.get("event") or ""), self)
        self.event_edit.setPlaceholderText("事件名称，显示在横线下方")
        form.addRow("事件", self.event_edit)
        # 画布上被省略号截断的名字，编辑窗里必须能看全：行内可横滚 + tooltip 全文
        for edit in (self.time_edit, self.event_edit):
            if edit.text():
                edit.setToolTip(edit.text())
        layout.addLayout(form)

        self.desc_edit = QPlainTextEdit(self)
        self.desc_edit.setObjectName("Preview")
        self.desc_edit.setPlaceholderText("事件描述（可选）……")
        self.desc_edit.setPlainText(str(node.get("desc") or ""))
        from app.ui.scroll_float import attach_flasher

        attach_flasher(self.desc_edit, Qt.Vertical, inset=6)
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

    def _on_lock_toggled(self, checked: bool) -> None:
        self.locked_state = checked
        self.lock_button.setText("已锁定 · 点击解锁" if checked else "锁定事件")

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
        hint = QLabel("中键拖动事件点移动 · Ctrl+左键拖引线长短 · 双击编辑 · 右键锁定/换色 · 点击横线放置", bar)
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
        self._theme = theme
        self.canvas = TimelineCanvas(theme, self)
        self.canvas.set_data(
            self.item.get("events") or [],
            self.item.get("canvas_width"),
            self.item.get("canvas_origin"),
        )
        self.canvas.changed.connect(self._collect)
        self.canvas.bounds_changed.connect(self._update_edge_buttons)
        self.canvas.hint_requested.connect(self._show_hint)

        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(False)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        # 横向滚动条隐藏，改用与正文一致的隐形快速滚动滑块（滚轮 / 拖动手柄）
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setWidget(self.canvas)
        # 隐形快速滚动滑块：画布自己处理滚轮，必须显式补挂画布监听
        from app.ui.scroll_float import ScrollFlasher

        self._h_flasher = ScrollFlasher(self.scroll, Qt.Horizontal, inset=8)
        self._h_flasher.watch(self.canvas)
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

        # 右下角小地图：浮在画布之上、不挤压布局；折叠状态随 item 持久化
        self.minimap = TimelineMinimap(self.canvas, self.scroll.viewport())
        self.minimap.set_collapsed(bool(self.item.get("minimap_collapsed")), persist=False)
        self.minimap.collapse_changed.connect(self._on_minimap_collapsed)
        self.canvas.changed.connect(self.minimap.update)
        self.canvas.selection_changed.connect(self.minimap.update)
        self.canvas.bounds_changed.connect(self.minimap.update)
        self.scroll.horizontalScrollBar().valueChanged.connect(self.minimap.update)

        # 一次性轻提示（如：锁定节点被尝试拖动）
        self._toast = QLabel("", self)
        self._toast.setStyleSheet(
            f"QLabel {{ background: {theme['panel']}; color: {theme['text']};"
            f" border: 1px solid {theme['border_strong']}; border-radius: 10px;"
            " padding: 8px 18px; font-size: 13px; }"
        )
        self._toast.setAlignment(Qt.AlignCenter)
        self._toast.hide()
        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(self._toast.hide)

        # 初始定位到画布中部（0 点附近）
        QTimer.singleShot(0, self.reset_view)

    def _show_hint(self, text: str) -> None:
        self._toast.setText(text)
        self._toast.adjustSize()
        self._toast.move(
            int((self.width() - self._toast.width()) / 2),
            70,
        )
        self._toast.show()
        self._toast.raise_()
        self._toast_timer.start(1800)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if getattr(self, "_toast", None) is not None and self._toast.isVisible():
            self._toast.move(int((self.width() - self._toast.width()) / 2), 70)
        self._place_minimap()

    def _place_minimap(self) -> None:
        """小地图贴视口右下角；折叠态缩成小胶囊，不遮挡右侧按钮与滚动条。"""
        mm = getattr(self, "minimap", None)
        if mm is None:
            return
        viewport = self.scroll.viewport()
        mm.apply_size(viewport.width())
        mm.move(viewport.width() - mm.width() - MINIMAP_GAP,
                viewport.height() - mm.height() - MINIMAP_GAP)
        mm.raise_()

    def _on_minimap_collapsed(self, collapsed: bool) -> None:
        self.item["minimap_collapsed"] = bool(collapsed)
        self._place_minimap()
        self.changed.emit()      # 走既有保存链路，随 item 一起落盘

    # ------------------------------------------------------------------
    def _make_plus_button(self, side: str) -> QToolButton:
        button = QToolButton(self.scroll.viewport())
        button.setObjectName("FloatButton")
        button.setCursor(Qt.PointingHandCursor)
        button.setFixedSize(36, 36)
        button.setIconSize(QSize(18, 18))
        button.setIcon(IconFactory(self._theme["text"]).icon("add", 18))
        button.setToolTip("向%s扩长时间轴" % ("左" if side == "left" else "右"))
        button.setAccessibleName(button.toolTip())
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
        self._place_minimap()

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

    def flush_pending(self) -> None:
        """主窗口退出前兜底：把画布当前状态写回 item 并持久化。"""
        self._collect()

    def _flush_save(self) -> None:
        """立即保存（保存按钮 / Ctrl+S / 关闭窗口时调用）。"""
        self._collect()
        flash_saved(self.save_button, IconFactory(self._theme["text"]))

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
        self._theme = theme
        for btn in (getattr(self, "left_plus", None), getattr(self, "right_plus", None)):
            if btn is not None:
                btn.setIcon(IconFactory(theme["text"]).icon("add", 18))
        if getattr(self, "_toast", None) is not None:
            self._toast.setStyleSheet(
                f"QLabel {{ background: {theme['panel']}; color: {theme['text']};"
                f" border: 1px solid {theme['border_strong']}; border-radius: 10px;"
                " padding: 8px 18px; font-size: 13px; }"
            )
