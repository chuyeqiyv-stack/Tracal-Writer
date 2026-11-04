# -*- coding: utf-8 -*-
"""全站统一矢量图标系统（液态玻璃同期重制版）。

规范（全仓一套，不混用风格）：
- 24px 设计画布，主体落 18px 安全框（四周 3px 光学边距）；
- 统一 1.5px 线宽（14/16px 档降为 1.25px），圆头端点、圆角连接，内折圆角 2px，
  斜线只走 45°/30° 族，视觉重心光学居中；
- 几何基本形 + 极简曲线，无渐变 / 无多色 / 无阴影；单色跟随文字令牌，
  选中态用 accent，禁用态 45% 透明度；
- 14/16/18/20/24/32 六档，按 DPR 1 / 1.25 / 1.5 / 2 预渲染，非预设 DPR 按矢量即时
  精确渲染并缓存——运行时永远不做位图拉伸；
- 缓存键 (color, active_color, name, size, dpr, state)，切换主题只换颜色键。
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QIcon,
    QIconEngine,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)

CANVAS = 24.0
SAFE = 3.0                 # 安全边距：主体落在 3..21
STANDARD_SIZES = (14, 16, 18, 20, 24, 32)
PRESET_DPRS = (1.0, 1.25, 1.5, 2.0)
DISABLED_ALPHA = 0.45

# 全部已注册图标名（gallery / 外部校验用）
ICON_NAMES = [
    "shelf", "toc", "undo", "redo", "format", "find", "search", "theme", "font",
    "spacing", "history", "count", "speed", "name", "settings",
    "top", "bottom", "close", "add", "edit", "trash",
    "refresh", "restore", "reset", "copy", "book", "folder", "chapter",
    "dots", "home", "discover", "grid", "list", "timeline", "palette",
    "image", "group", "user", "cloud", "upload", "download", "mind",
    "left", "right", "map", "medal", "check", "help",
]

# 模块级位图缓存：argb 颜色 + 名字 + 逻辑尺寸 + DPR + 状态 -> QPixmap
_PIX_CACHE: dict[tuple, QPixmap] = {}
# 懒引擎全部模块级保活：QIcon 被按值拷贝 / 临时工厂销毁后，Qt 侧仍持有引擎
# C++ 对象并回调虚函数；若 Python 包装被 GC，Shiboken 会重建一个绕过 __init__
# 的裸包装（缺属性，真实平台直接 0xC0000409 崩溃）。引擎体极小，按调用次数累积
# （千次级弹窗约几百 KB），用后不释放可接受。
_LIVE_ENGINES: set = set()


def flash_saved(button, factory: "IconFactory", ms: int = 1400, size: int = 14) -> None:
    """保存按钮短暂显示 check 图标 +「已保存」，1.4s 后还原（替代旧 ✓ 字形）。"""
    button.setIconSize(QSize(size, size))
    button.setIcon(factory.icon("check", size))
    button.setText("已保存")

    def restore():
        try:
            button.setText("保存")
            button.setIcon(QIcon())
        except RuntimeError:
            pass   # 按钮已随窗口销毁

    QTimer.singleShot(ms, restore)


# ======================================================================
# 渲染
# ======================================================================
def _state_color(base: QColor, active: QColor | None, state: QIcon.State,
                 disabled: bool) -> QColor:
    c = QColor(active if (state == QIcon.On and active is not None) else base)
    if disabled:
        c.setAlphaF(c.alphaF() * DISABLED_ALPHA)
    return c


def render_pixmap(
    name: str,
    size: int,
    dpr: float,
    color: str,
    active_color: str | None = None,
    state: QIcon.State = QIcon.Off,
    disabled: bool = False,
    marked: bool = False,
) -> QPixmap:
    """按 (name, size, dpr, 颜色, 状态) 精确渲染一张位图（全程矢量，无拉伸）。

    marked=True 且 name=="book" 时，在书脊处叠加 2px 品牌色圆点（人物短章已填写）。
    """
    base = QColor(color)
    active = QColor(active_color) if active_color else None
    ink = _state_color(base, active, state, disabled)
    key = (ink.rgba(), name, int(size), round(float(dpr), 3), state == QIcon.On,
           disabled, bool(marked))
    cached = _PIX_CACHE.get(key)
    if cached is not None:
        return cached

    px = int(round(size * dpr))
    pixmap = QPixmap(px, px)
    pixmap.setDevicePixelRatio(dpr)
    pixmap.fill(Qt.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.scale(size / CANVAS, size / CANVAS)

    stroke = 1.25 if size <= 16 else 1.5
    pen = QPen(ink)
    pen.setWidthF(stroke)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)

    _draw(painter, ink, name)
    if marked and name == "book":
        # 书脊处 2px 品牌色圆点（16px 档 ≈ 2 物理像素直径）
        badge = QColor(active_color) if active is not None else QColor("#7C5CFF")
        painter.setPen(Qt.NoPen)
        painter.setBrush(badge)
        painter.drawEllipse(QPointF(6.1, 17.0), 1.15, 1.15)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(pen)
    painter.end()
    _PIX_CACHE[key] = pixmap
    return pixmap


# ======================================================================
# 懒渲染 QIcon 引擎：Qt 真正要画哪一帧（尺寸 / DPR / 态）才光栅化哪一帧，
# 避免启动时把 4 DPR × 3 态全部预渲染（~180 次绘制）拖慢主窗口构造。
# ======================================================================
class _LazyIconEngine(QIconEngine):
    def __init__(self, factory: "IconFactory", name: str, size: int,
                 marked: bool = False) -> None:
        super().__init__()
        self._f = factory
        self._n = name
        self._s = int(size)
        self._marked = bool(marked)
        _LIVE_ENGINES.add(self)

    def pixmap(self, size, mode, state):  # noqa: N802 (Qt 命名)
        if size is None or size.width() <= 0 or size.height() <= 0:
            return QPixmap()
        spec = getattr(self, "_s", None)
        if spec is None:
            return QPixmap()  # 极端情况下 C++ 拷贝绕过 Python 初始化：宁空不崩
        dpr = max(1.0, round(size.width() / spec, 3))
        on = state == QIcon.On or mode == QIcon.Selected
        disabled = mode == QIcon.Disabled
        return self._f.pixmap(self._n, spec, state=QIcon.On if on else QIcon.Off,
                              disabled=disabled, dpr=dpr,
                              marked=getattr(self, "_marked", False))

    def clone(self):  # noqa: N802
        return _LazyIconEngine(self._f, self._n, self._s,
                               getattr(self, "_marked", False))

    def availableSizes(self, mode=QIcon.Normal, state=QIcon.Off):  # noqa: N802
        spec = getattr(self, "_s", None)
        return [QSize(spec, spec)] if spec else []


# ======================================================================
# 图标工厂：对外主入口（API 与旧版兼容）
# ======================================================================
class IconFactory:
    """按主题文字色生成整套线性图标；可携带选中态 accent 色。"""

    def __init__(self, color: str, active_color: str | None = None) -> None:
        self.color = QColor(color).name()
        self.active_color = QColor(active_color).name() if active_color else None
        self._icon_cache: dict[tuple[str, int], QIcon] = {}

    # ------------------------------------------------------------------
    def icon(self, name: str, size: int = 18, marked: bool = False) -> QIcon:
        key = (name, int(size), bool(marked))
        cached = self._icon_cache.get(key)
        if cached is not None:
            return cached
        engine = _LazyIconEngine(self, name, int(size), marked)
        qicon = QIcon(engine)
        # 引擎 Python 包装对象必须与 QIcon 同寿，否则按钮重绘回调到已被 GC
        # 的引擎会在真实平台硬崩溃（0xC0000409）；临时工厂（如弹窗里当场
        # new 的 IconFactory）同样靠这条引用链保活。
        qicon._lazy_engine = engine  # type: ignore[attr-defined]
        self._icon_cache[key] = qicon
        return qicon

    def pixmap(self, name: str, size: int = 18, *, state: QIcon.State = QIcon.Off,
               disabled: bool = False, dpr: float | None = None,
               marked: bool = False) -> QPixmap:
        """给自绘控件用：按屏幕真实 DPR 取精确帧（不经 QIcon 缩放）。"""
        if dpr is None:
            from PySide6.QtWidgets import QApplication

            screen = QApplication.primaryScreen()
            dpr = screen.devicePixelRatio() if screen is not None else 1.0
        dpr = float(dpr)
        if dpr not in PRESET_DPRS:
            # 非预设 DPR：矢量精确渲染一次并缓存，绝不拉伸位图
            pass
        return render_pixmap(name, size, dpr, self.color, self.active_color,
                             state=state, disabled=disabled, marked=marked)


# ======================================================================
# 图元绘制（全部在 24x24 设计坐标系内）
# ======================================================================
def _line(p, x1, y1, x2, y2) -> None:
    p.drawLine(QPointF(x1, y1), QPointF(x2, y2))


def _poly(p, points) -> None:
    path = QPainterPath()
    path.moveTo(points[0][0], points[0][1])
    for x, y in points[1:]:
        path.lineTo(x, y)
    p.drawPath(path)


def _fill(p, ink, fn) -> None:
    """用主色填充执行 fn（结束后恢复描边态）。"""
    p.save()
    p.setPen(Qt.NoPen)
    p.setBrush(ink)
    fn(p)
    p.restore()


def _draw(p: QPainter, ink: QColor, name: str) -> None:  # noqa: C901 - 纯分派
    # -------------------------------------------------------------- 基础
    if name == "add":
        _line(p, 12, 5.5, 12, 18.5)
        _line(p, 5.5, 12, 18.5, 12)

    elif name == "close":
        _line(p, 6.5, 6.5, 17.5, 17.5)
        _line(p, 17.5, 6.5, 6.5, 17.5)

    elif name == "check":
        _poly(p, [(5.5, 12.4), (10.2, 17.1), (18.6, 7.4)])

    elif name == "left":
        _poly(p, [(14.6, 5.5), (8.1, 12), (14.6, 18.5)])

    elif name == "right":
        _poly(p, [(9.4, 5.5), (15.9, 12), (9.4, 18.5)])

    elif name == "top":
        _line(p, 12, 19.2, 12, 6.2)
        _poly(p, [(7.4, 10.8), (12, 6), (16.6, 10.8)])

    elif name == "bottom":
        _line(p, 12, 4.8, 12, 17.8)
        _poly(p, [(7.4, 13.2), (12, 18), (16.6, 13.2)])

    elif name == "dots":
        def dots(q):
            for x in (6, 12, 18):
                q.drawEllipse(QPointF(x, 12), 1.5, 1.5)
        _fill(p, ink, dots)

    # -------------------------------------------------------------- 房屋/人
    elif name == "home":
        _poly(p, [(4, 11), (12, 4.4), (20, 11)])
        p.drawRoundedRect(QRectF(6.4, 10.6, 11.2, 9.4), 2, 2)
        _line(p, 10.4, 20, 10.4, 15.2)
        _line(p, 13.6, 20, 13.6, 15.2)

    elif name == "user":
        p.drawEllipse(QRectF(9, 5, 6, 6))
        p.drawArc(QRectF(5, 12.2, 14, 11), 180 * 16, 182 * 16)

    elif name in ("volume", "folder"):
        path = QPainterPath()
        path.moveTo(3.5, 7.2)
        path.lineTo(9.2, 7.2)
        path.lineTo(11.4, 9.6)
        path.lineTo(20.5, 9.6)
        path.lineTo(20.5, 19)
        path.lineTo(3.5, 19)
        path.closeSubpath()
        p.drawPath(path)

    elif name == "group":
        p.drawRoundedRect(QRectF(4, 8.2, 16, 10), 2, 2)
        _line(p, 9.5, 8.2, 9.5, 5.6)
        _line(p, 9.5, 5.6, 16.2, 5.6)

    # -------------------------------------------------------------- 文档
    elif name == "shelf":
        p.drawRoundedRect(QRectF(4, 7, 4, 13), 1, 1)
        p.drawRoundedRect(QRectF(10, 4.5, 4, 15.5), 1, 1)
        p.drawRoundedRect(QRectF(16, 8.5, 4, 11.5), 1, 1)
        _line(p, 3.2, 20.5, 20.8, 20.5)

    elif name == "toc":
        for y in (7, 12, 17):
            def dot(q, yy=y):
                q.drawEllipse(QPointF(5.4, yy), 1.25, 1.25)
            _fill(p, ink, dot)
            _line(p, 9, y, 20.2, y)

    elif name == "list":
        for y in (7, 12, 17):
            p.drawRoundedRect(QRectF(4, y - 2, 4, 4), 1, 1)
            _line(p, 10.5, y, 20, y)

    elif name == "chapter":
        p.drawRoundedRect(QRectF(5.5, 3.5, 13, 17), 2, 2)
        _poly(p, [(14.5, 3.5), (14.5, 7.4), (18.4, 7.4)])
        for y in (10.2, 13.2, 16.2):
            _line(p, 8.6, y, 15.4, y)

    elif name == "book":
        p.drawRoundedRect(QRectF(4, 4.5, 16, 15), 2, 2)
        _line(p, 8.2, 4.5, 8.2, 19.5)
        _line(p, 11, 9.2, 17, 9.2)
        _line(p, 11, 12.6, 17, 12.6)
        _line(p, 11, 16, 14.8, 16)

    elif name == "format":
        for y, x2 in ((5.8, 20), (10.4, 14.2), (15, 20), (19.6, 12.4)):
            _line(p, 4, y, x2, y)

    elif name == "image":
        p.drawRoundedRect(QRectF(3.5, 5, 17, 14), 2, 2)
        def sun(q):
            q.drawEllipse(QPointF(8.6, 9.8), 1.4, 1.4)
        _fill(p, ink, sun)
        _poly(p, [(5, 16.6), (9.8, 11.8), (13, 14.8), (15.4, 12.4), (19, 16.2)])

    # -------------------------------------------------------------- 查找/编辑
    elif name == "find":
        p.drawEllipse(QRectF(4.5, 4.5, 11, 11))
        _line(p, 13, 13, 19.4, 19.4)

    elif name == "search":
        p.drawEllipse(QRectF(4.2, 4.2, 10.4, 10.4))
        _line(p, 12.4, 12.4, 19.2, 19.2)
        _line(p, 7.6, 8.4, 11.6, 8.4)
        _line(p, 7.6, 10.8, 10.4, 10.8)
        _line(p, 17.8, 2.8, 17.8, 6.6)
        _line(p, 15.9, 4.7, 19.7, 4.7)

    elif name == "edit":
        _line(p, 4.8, 19.2, 15.6, 8.4)
        _line(p, 8, 20.6, 19.2, 9.4)
        _line(p, 14.2, 5.6, 18.4, 9.8)

    elif name == "copy":
        p.drawRoundedRect(QRectF(8, 4, 12, 12), 2, 2)
        p.drawRoundedRect(QRectF(4, 8, 12, 12), 2, 2)

    elif name == "trash":
        _line(p, 4.8, 7.5, 19.2, 7.5)
        _line(p, 9.2, 7.5, 9.2, 5.4)
        _line(p, 9.2, 5.4, 14.8, 5.4)
        _line(p, 14.8, 5.4, 14.8, 7.5)
        p.drawRoundedRect(QRectF(7, 7.5, 10, 12.5), 2, 2)
        _line(p, 10.5, 10.8, 10.5, 16.8)
        _line(p, 13.5, 10.8, 13.5, 16.8)

    # -------------------------------------------------------------- 撤销/刷新类
    elif name == "undo":
        _arc_arrow(p, ink, clockwise=False)

    elif name == "redo":
        p.save()
        p.translate(CANVAS, 0)
        p.scale(-1, 1)
        _arc_arrow(p, ink, clockwise=False)
        p.restore()

    elif name == "refresh":
        _arc_arrow(p, ink, clockwise=False, start=40, sweep=280)

    elif name == "restore":
        _arc_arrow(p, ink, clockwise=False, start=140, sweep=280, reverse=True)

    elif name == "reset":
        _arc_arrow(p, ink, clockwise=False, start=100, sweep=250, reverse=True)

    # -------------------------------------------------------------- 云/箭头
    elif name == "cloud":
        path = QPainterPath()
        path.moveTo(7.2, 18)
        path.lineTo(17.2, 18)
        path.cubicTo(21.6, 18, 21.6, 11.5, 17.2, 11.5)
        path.cubicTo(17.2, 6.4, 10.4, 5.4, 8.6, 10)
        path.cubicTo(4.4, 10, 3.4, 18, 7.2, 18)
        p.drawPath(path)

    elif name == "upload":
        _line(p, 12, 19.2, 12, 6)
        _poly(p, [(7.4, 10.6), (12, 6), (16.6, 10.6)])
        _line(p, 4.8, 19.2, 19.2, 19.2)

    elif name == "download":
        _line(p, 12, 4.8, 12, 18)
        _poly(p, [(7.4, 13.4), (12, 18), (16.6, 13.4)])
        _line(p, 4.8, 19.4, 19.2, 19.4)

    # -------------------------------------------------------------- 时间/统计
    elif name == "history":
        p.drawEllipse(QRectF(4.5, 4.5, 15, 15))
        _line(p, 12, 12, 12, 8)
        _line(p, 12, 12, 15.2, 13.6)

    elif name == "speed":
        p.drawArc(QRectF(4.5, 5.5, 15, 15), 205 * 16, 250 * 16)
        _line(p, 12, 13.6, 16.2, 9.2)
        _line(p, 5.2, 16.8, 18.8, 16.8)

    elif name == "count":
        # 统计：三根高低柱 + 基线
        for x, h in ((6, 6), (10.5, 10), (15, 13.5)):
            p.drawRoundedRect(QRectF(x, 20 - h, 3, h), 1, 1)
        _line(p, 4, 20.2, 20, 20.2)

    elif name == "timeline":
        _line(p, 3, 13, 21, 13)
        for x, up in ((7, True), (12, False), (17, True)):
            _line(p, x, 13, x, 7.4 if up else 18.6)

            def node(q, xx=x):
                q.drawEllipse(QPointF(xx, 13), 1.9, 1.9)
            _fill(p, ink, node)

    elif name == "mind":
        def hub(q):
            q.drawEllipse(QPointF(12, 12), 2.5, 2.5)
        _fill(p, ink, hub)
        for dx, dy in ((-7, -5), (7, -5), (0, 7)):
            _line(p, 12, 12, 12 + dx, 12 + dy)

            def leaf(q, cx=12 + dx, cy=12 + dy):
                q.drawEllipse(QPointF(cx, cy), 2, 2)
            _fill(p, ink, leaf)

    # -------------------------------------------------------------- 外观/排版
    elif name == "theme":
        p.drawEllipse(QRectF(4.5, 4.5, 15, 15))
        path = QPainterPath()
        path.moveTo(12, 5)
        path.arcTo(QRectF(5, 5, 14, 14), 90, 180)
        path.closeSubpath()
        _fill(p, ink, lambda q: q.drawPath(path))

    elif name == "font":
        # 几何化字母 A（不依赖字体）
        _poly(p, [(8.4, 18), (12, 6.4), (15.6, 18)])
        _line(p, 9.7, 13.8, 14.3, 13.8)

    elif name == "spacing":
        for y in (7, 12, 17):
            _line(p, 3.6, y, 14.2, y)

        def arrows(q):
            top = QPainterPath()
            top.moveTo(19.4, 4)
            top.lineTo(16.8, 8.2)
            top.lineTo(22, 8.2)
            top.closeSubpath()
            bottom = QPainterPath()
            bottom.moveTo(19.4, 20)
            bottom.lineTo(16.8, 15.8)
            bottom.lineTo(22, 15.8)
            bottom.closeSubpath()
            q.drawPath(top)
            q.drawPath(bottom)
        _fill(p, ink, arrows)
        _line(p, 19.4, 8.6, 19.4, 15.4)

    elif name == "palette":
        p.drawEllipse(QRectF(4.5, 4.5, 15, 15))
        for x, y in ((9, 9), (14, 8.4), (8.4, 14), (14.6, 13.6)):
            def dot(q, cx=x, cy=y):
                q.drawEllipse(QPointF(cx, cy), 1.5, 1.5)
            _fill(p, ink, dot)

    elif name == "discover":
        p.drawEllipse(QRectF(4.5, 4.5, 15, 15))
        path = QPainterPath()
        path.moveTo(9.4, 9.4)
        path.lineTo(14.8, 6.8)
        path.lineTo(12.4, 12.4)
        path.lineTo(7, 14.6)
        path.closeSubpath()
        _fill(p, ink, lambda q: q.drawPath(path))

    elif name == "grid":
        for x in (5, 13):
            for y in (5, 13):
                p.drawRoundedRect(QRectF(x, y, 6, 6), 1.4, 1.4)

    # -------------------------------------------------------------- 起名助手
    elif name == "name":
        # 四角星火（主星 + 两颗小星），寓意“灵光取名”
        _fill(p, ink, lambda q: q.drawPath(_spark_path(11, 11, 5.2)))
        _fill(p, ink, lambda q: q.drawPath(_spark_path(18.6, 5.8, 2.1)))
        _fill(p, ink, lambda q: q.drawPath(_spark_path(5.6, 18.6, 2.1)))

    # -------------------------------------------------------------- 齿轮
    elif name == "settings":
        for i in range(8):
            angle = math.radians(i * 45 + 22.5)
            _line(p, 12 + 6.9 * math.cos(angle), 12 + 6.9 * math.sin(angle),
                  12 + 8.9 * math.cos(angle), 12 + 8.9 * math.sin(angle))
        p.drawEllipse(QRectF(4.6, 4.6, 14.8, 14.8))
        def hub(q):
            q.drawEllipse(QPointF(12, 12), 2.3, 2.3)
        _fill(p, ink, hub)

    # -------------------------------------------------------------- 地图（新增）
    elif name == "map":
        path = QPainterPath()
        path.moveTo(3.5, 7.2)
        path.lineTo(9, 4.6)
        path.lineTo(15, 7.2)
        path.lineTo(20.5, 4.6)
        path.lineTo(20.5, 16.8)
        path.lineTo(15, 19.4)
        path.lineTo(9, 16.8)
        path.lineTo(3.5, 19.4)
        path.closeSubpath()
        p.drawPath(path)
        _line(p, 9, 4.6, 9, 16.8)
        _line(p, 15, 7.2, 15, 19.4)

    # -------------------------------------------------------------- 勋章（新增）
    elif name == "medal":
        # 绶带（先描，圆盘会压住上端）
        _poly(p, [(7.6, 13.2), (6.4, 20), (9.8, 17.6), (11.6, 14.6)])
        _poly(p, [(16.4, 13.2), (17.6, 20), (14.2, 17.6), (12.4, 14.6)])
        p.drawEllipse(QRectF(4.8, 4.2, 14.4, 14.4))
        _fill(p, ink, lambda q: q.drawPath(_star_path(12, 11.4, 3.2)))

    # -------------------------------------------------------------- 帮助（新增）
    elif name == "help":
        p.drawEllipse(QRectF(4.5, 4.5, 15, 15))
        # 问号：上弯弧 + 收腰 + 竖钩 + 点
        qpath = QPainterPath()
        qpath.moveTo(9.4, 10.2)
        qpath.cubicTo(9.2, 7.4, 14.8, 7.2, 14.8, 10.0)
        qpath.cubicTo(14.8, 12.4, 12.3, 12.6, 12.1, 14.8)
        p.drawPath(qpath)
        def dot(q):
            q.drawEllipse(QPointF(12.1, 16.6), 0.95, 0.95)
        _fill(p, ink, dot)

    else:
        # 未知名：画一个虚线安全框，绝不静默空白
        pen = QPen(ink)
        pen.setStyle(Qt.DashLine)
        p.setPen(pen)
        p.drawRect(QRectF(SAFE, SAFE, CANVAS - SAFE * 2, CANVAS - SAFE * 2))


def _star_path(cx: float, cy: float, r: float) -> QPainterPath:
    """实心五角星路径。"""
    path = QPainterPath()
    for i in range(10):
        rr = r if i % 2 == 0 else r * 0.45
        a = math.radians(-90 + i * 36)
        x, y = cx + rr * math.cos(a), cy + rr * math.sin(a)
        if i == 0:
            path.moveTo(x, y)
        else:
            path.lineTo(x, y)
    path.closeSubpath()
    return path


def _spark_path(cx: float, cy: float, r: float) -> QPainterPath:
    """四角星火（直线凹菱形，30° 族以外不用曲线，小尺寸不糊）。"""
    inner = r * 0.3
    pts = [(0, -r), (inner, -inner), (r, 0), (inner, inner),
           (0, r), (-inner, inner), (-r, 0), (-inner, -inner)]
    path = QPainterPath()
    path.moveTo(cx + pts[0][0], cy + pts[0][1])
    for x, y in pts[1:]:
        path.lineTo(cx + x, cy + y)
    path.closeSubpath()
    return path


def _arc_arrow(p: QPainter, ink: QColor, *, clockwise: bool,
               start: int = 30, sweep: int = 285, reverse: bool = False) -> None:
    """圆弧 + 端部实心箭头（撤销 / 重做 / 刷新类共用）。

    矩形按图标给：undo 略下沉；其余统一 5,5,14,14。reverse 只翻 y 向切向量
    （restore / reset 的箭头在弧起点朝弧外上方）。
    """
    if start in (40, 140, 100):
        rect = QRectF(5, 5, 14, 14)
        cx, cy, r = 12.0, 12.0, 7.0
    else:
        rect = QRectF(5.5, 6.5, 13, 13)
        cx, cy, r = 12.0, 13.0, 6.5
    p.drawArc(rect, start * 16, (-sweep if clockwise else sweep) * 16)
    a = math.radians(start)
    px, py = cx + r * math.cos(a), cy - r * math.sin(a)
    tx, ty = -math.sin(a), math.cos(a) if reverse else -math.cos(a)
    nx, ny = -ty, tx
    head = QPainterPath()
    head.moveTo(QPointF(px + 3.2 * tx, py + 3.2 * ty))
    head.lineTo(QPointF(px + 2.2 * nx, py + 2.2 * ny))
    head.lineTo(QPointF(px - 2.2 * nx, py - 2.2 * ny))
    head.closeSubpath()
    _fill(p, ink, lambda q: q.drawPath(head))
