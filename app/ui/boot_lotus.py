# -*- coding: utf-8 -*-
"""启动小动画 · 莲花第二版：合苞带茎 → 碗状半开 → 全开露莲蓬 → 近全开圆润。

设计（按四张参考形态）：
- 纯矢量 QPainterPath，细匀单线（1.3px）+ 清晰中脉（0.8px），瓣尖收笔；
- 每片花瓣 5–12% 淡灰体积填充；瓣层交叠处有极轻接触影；
- 下方柔投影与接触影均为一次构建、全程缓存的模糊位图（无外发光）；
- 米白 / 近黑底；整体 0.6–0.9s（760ms）；点击 / 任意键可跳过；设置可关；
- 与主窗口初始化并行：BootSequence 等「动画结束」与「主窗口就绪」两个条件都满足后
  做一次短淡出 / 淡入衔接，初始化快则动画播完即进，慢则停在全开帧等待。

注意：paintEvent 内只使用预建路径 / 画笔 / 缓存位图，不构造任何绘图对象。
"""
from __future__ import annotations

import math

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QObject,
    QPointF,
    QPropertyAnimation,
    QRectF,
    Qt,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
)
from PySide6.QtWidgets import (
    QGraphicsBlurEffect,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QWidget,
)

from app.ui.motion import EASE_OUT

BOOT_DURATION_MS = 760          # 整体 0.6–0.9s 区间
_SIZE = 360                     # 启动窗固定边长（逻辑像素）

# 线稿令牌（细匀单线；瓣尖靠路径收笔，不靠粗线变化）
_LINE_WIDTH = 1.3
_VEIN_WIDTH = 0.8
_POD_WIDTH = 1.2
_STEM_WIDTH = 1.6

# 背景：浅 = 米白，深 = 近黑（参考底）
_BG_LIGHT = "#F6F3EA"
_BG_DARK = "#14161A"

# 阶段划分（占总时长比例）
_P_BUD_END = 0.20       # 0.00– 合苞（花苞微微苏醒）
_P_BOWL_END = 0.52      #  – 碗状半开：外层先开、内层仍抱
_P_OPEN_END = 0.82      #  – 全开露莲蓬
# 余下到 1.0：近全开圆润（外瓣再放、整朵轻轻落定）

# 花朵心部在各阶段的纵向位置（合苞时心部偏下、显茎；绽放后回中）
_HEART_BUD_Y = 34.0
_HEART_OPEN_Y = 0.0

# 茎
_STEM_BASE = QPointF(9.0, 150.0)
_STEM_CTRL = QPointF(-16.0, 96.0)

# 柔投影（一次性缓存模糊位图用）
_SHADOW_BLUR = 12.0
_SHADOW_MARGIN = 28


def _clamp01(value: float) -> float:
    return 0.0 if value < 0.0 else 1.0 if value > 1.0 else value


def _smooth01(value: float) -> float:
    """0–1 平滑阶跃（两端速度为 0），用于心部抬升 / 茎淡出。"""
    t = _clamp01(value)
    return t * t * (3.0 - 2.0 * t)


def _ease_out_cubic(t: float) -> float:
    return 1.0 - (1.0 - t) ** 3


def _make_petal_path(length: float, half_width: float, pointed: float = 0.92) -> QPainterPath:
    """单片莲花瓣：基窄、腹宽、瓣尖收细成尖（收笔感）。

    pointed：开始收尖的纵向位置（0–1，越大越靠尖端才收，瓣形越修长）。
    """
    path = QPainterPath()
    path.moveTo(0.0, 0.0)
    # 左缘：基部 → 腹中上部最宽（约 0.45L）→ 顺滑收细成尖（收笔）
    path.cubicTo(
        -half_width * 0.5, -length * 0.12,
        -half_width * 1.05, -length * 0.42,
        -half_width * 0.72, -length * 0.66,
    )
    path.cubicTo(
        -half_width * 0.62, -length * 0.84,
        -half_width * 0.25, -length * 0.98,
        0.0, -length,
    )
    # 右缘对称回基
    path.cubicTo(
        half_width * 0.25, -length * 0.98,
        half_width * 0.62, -length * 0.84,
        half_width * 0.72, -length * 0.66,
    )
    path.cubicTo(
        half_width * 1.05, -length * 0.42,
        half_width * 0.5, -length * 0.12,
        0.0, 0.0,
    )
    path.closeSubpath()
    return path


def _make_vein_path(length: float) -> QPainterPath:
    """清晰中脉：自瓣基通向瓣尖，末段几乎贴尖。"""
    vein = QPainterPath()
    vein.moveTo(0.0, -length * 0.12)
    vein.cubicTo(
        -length * 0.018, -length * 0.42,
        length * 0.018, -length * 0.74,
        0.0, -length * 0.94,
    )
    return vein


def _make_stem_path() -> QPainterPath:
    """合苞期挺直的花茎（略带弧度，自花苞基部贯穿到底）。"""
    stem = QPainterPath()
    stem.moveTo(0.0, 4.0)
    stem.cubicTo(-2.0, 48.0, 3.0, 100.0, _STEM_BASE.x(), _STEM_BASE.y())
    return stem


def _make_sepal_paths() -> list:
    """花苞基部一圈小萼片（3 枚，对称抱向基部；绽放后快速淡出）。"""
    paths = []
    for dx, bend in ((0.0, 0.0), (-1.0, -1.0), (1.0, 1.0)):
        p = QPainterPath()
        p.moveTo(dx * 4.0, 6.0)
        p.cubicTo(dx * 10.0 + bend * 6.0, 12.0, dx * 18.0 + bend * 10.0, 20.0,
                  dx * 22.0 + bend * 12.0, 30.0)
        p.cubicTo(dx * 12.0 + bend * 6.0, 24.0, dx * 6.0, 14.0, dx * 4.0, 6.0)
        p.closeSubpath()
        paths.append(p)
    return paths


# 相机俯仰角（3/4 俯视）：决定碗口椭圆短缩与花体侧视感
_CAM_PITCH = 0.62          # 弧度（约 35.5°）
_COS_PITCH = 0.816
_SIN_PITCH = 0.578


class _PetalLayer:
    """一层花瓣的静态定义、缓存路径与合苞/半开/盛开/圆润四态开合角。"""

    def __init__(
        self,
        count: int,
        length: float,
        half_width: float,
        ring: float,
        bud_deg: float,
        half_deg: float,
        full_deg: float,
        round_deg: float,
        bud_curl: float,
    ) -> None:
        import math

        self.count = count
        self.ring = ring
        self.bud = math.radians(bud_deg)
        self.half = math.radians(half_deg)
        self.full = math.radians(full_deg)
        self.round = math.radians(round_deg)
        # 方位角：θ=0 朝向观察者（屏幕下方），±π 在后（屏幕上方），左右对称
        step = 2.0 * math.pi / count
        self.thetas = [(i - (count - 1) / 2.0) * step for i in range(count)]
        # 合苞时各瓣微微的抱合错落角（弧度；顶端收尖、两侧包裹）
        curl_rad = math.radians(bud_curl)
        self.curls = [curl_rad * (1.0 if i % 2 == 0 else -0.6) for i in range(count)]
        # 背→前绘制顺序（后面的瓣先画），预排序缓存
        self.order = sorted(range(count), key=lambda i: -math.cos(self.thetas[i]))
        self.path = _make_petal_path(length, half_width)
        self.vein = _make_vein_path(length)


class BootLotusWidget(QWidget):
    """黑白线稿莲花（第二版）自绘控件（无边框顶级小窗）。"""

    bloomFinished = Signal()

    def __init__(self, theme: dict, dark: bool, duration_ms: int = BOOT_DURATION_MS) -> None:
        super().__init__(
            None,
            Qt.SplashScreen | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint,
        )
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setFixedSize(_SIZE, _SIZE)
        self.setStyleSheet(f"background: {_BG_DARK if dark else _BG_LIGHT};")

        self._dark = dark
        self._progress = 0.0
        self._bloomed = False

        # 墨色与淡灰体积（填充 alpha 落在 5–12%：浅底 ~6%，深底 ~10%）
        text = QColor(theme.get("text", "#1F2329"))
        self._ink = QColor(text)
        self._fill = QColor(text)
        self._fill.setAlpha(26 if dark else 16)
        # 花瓣是不透明瓣面：底色取背景色，只混入 5–12% 淡灰做体积暗示。
        # 前瓣因此能物理遮住后瓣交叠线，花芯不会挤成黑团。
        bgc = QColor(_BG_DARK if dark else _BG_LIGHT)

        def surface(blend: float) -> QColor:
            c = QColor(
                int(bgc.red() + (text.red() - bgc.red()) * blend),
                int(bgc.green() + (text.green() - bgc.green()) * blend),
                int(bgc.blue() + (text.blue() - bgc.blue()) * blend),
                238,
            )
            return c

        self._fill_back = surface(0.12 if dark else 0.09)
        self._fill_front = surface(0.08 if dark else 0.055)
        self._shade = QColor(text)
        self._shade.setAlpha(30 if dark else 20)   # 接触影（叠加受透明度限制）

        # 三层花瓣（3/4 视角投影）：外层先张成碗，中层继之，内层最后收出圆顶。
        # 参数：瓣数 / 瓣长 / 半宽 / 着生环半径 / 合苞·半开·盛开·圆润四态开合角（度）/
        # 合苞抱合错落角（度）。
        self._layers = [
            _PetalLayer(8, 124.0, 27.0, 9.0, 7.0, 88.0, 90.0, 82.0, 2.4),
            _PetalLayer(7, 112.0, 26.0, 6.0, 4.0, 54.0, 80.0, 56.0, 1.7),
            _PetalLayer(5, 100.0, 24.0, 3.0, 3.0, 16.0, 54.0, 32.0, 1.2),
        ]

        # 莲蓬（半开后即可见）：扁圆蓬房 + 莲子 + 前方雄蕊点
        self._pod_rect = QRectF(-10.5, -8.0, 21.0, 16.0)
        self._pod_fill = QColor(_BG_DARK if dark else _BG_LIGHT)
        dots = []
        import math as _math

        for rr, nn, off in ((0.0, 1, 0.0), (4.4, 7, 0.25)):
            for i in range(nn):
                ang = _math.pi / 2.0 + i * (2.0 * _math.pi / max(nn, 1)) + off
                dots.append((rr * _math.cos(ang), -rr * 0.78 * _math.sin(ang)))
        self._pod_dots = tuple(dots)
        self._stamens = tuple((-8.4 + i * 2.8, 5.6 + (1.4 if i % 2 else 0.0))
                              for i in range(7))
        # 花托节点：遮住所有花瓣在基部的交汇墨结合苞期也干净
        self._node_rect = QRectF(-6.0, -4.0, 12.0, 9.0)

        # ---- 预建画笔（paintEvent 绝不新建）----
        def pen(width: float) -> QPen:
            p = QPen(QColor(self._ink))
            p.setWidthF(width)
            p.setCapStyle(Qt.RoundCap)
            p.setJoinStyle(Qt.RoundJoin)
            return p

        self._pen = pen(_LINE_WIDTH)
        self._vein_pen = pen(_VEIN_WIDTH)
        self._pod_pen = pen(_POD_WIDTH)
        self._stem_pen = pen(_STEM_WIDTH)
        # 兼容旧冒烟的缓存属性名
        self._pens = [self._pen]

        self._stem_path = _make_stem_path()
        self._sepal_paths_all = _make_sepal_paths()

        # ---- 一次构建、全程复用的柔投影 / 接触影位图（首帧懒构建）----
        self._ground_shadow: QPixmap | None = None
        self._contact_mid: QPixmap | None = None
        self._contact_inner: QPixmap | None = None

        self._anim = QPropertyAnimation(self, b"progress", self)
        self._anim.setDuration(duration_ms)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setEasingCurve(QEasingCurve.Linear)   # 每片花瓣各自 ease-out
        self._anim.finished.connect(self._on_anim_finished)

    # ------------------------------------------------------------------
    def _blur_pixmap(self, pm: QPixmap, radius: float) -> QPixmap:
        scene = QGraphicsScene()
        item = QGraphicsPixmapItem(pm)
        scene.addItem(item)
        effect = QGraphicsBlurEffect()
        effect.setBlurRadius(radius)
        item.setGraphicsEffect(effect)
        rect = QRectF(0.0, 0.0, pm.width() / pm.devicePixelRatioF(),
                      pm.height() / pm.devicePixelRatioF())
        scene.setSceneRect(rect)
        out = QPixmap(pm.size())
        out.setDevicePixelRatio(pm.devicePixelRatioF())
        out.fill(Qt.transparent)
        painter = QPainter(out)
        painter.setRenderHint(QPainter.Antialiasing, True)
        scene.render(painter, rect, scene.sceneRect())
        painter.end()
        return out

    def _build_shadows(self) -> None:
        """柔投影（地面椭圆）与两档接触影（瓣层凹陷），均模糊一次缓存。"""
        dpr = self.devicePixelRatioF() or 1.0

        def canvas(w: float, h: float) -> QPixmap:
            pm = QPixmap(int(w * dpr), int(h * dpr))
            pm.setDevicePixelRatio(dpr)
            pm.fill(Qt.transparent)
            return pm

        # 地面柔投影：横向扁椭圆
        gw, gh = 190.0, 46.0
        pm = canvas(gw, gh)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 70 if self._dark else 52))
        p.drawEllipse(QRectF(0.0, 0.0, gw, gh))
        p.end()
        self._ground_shadow = self._blur_pixmap(pm, _SHADOW_BLUR)

        # 接触影：径向柔斑（两档直径），画在瓣基交叠处
        def contact(diameter: float, alpha: int) -> QPixmap:
            m = 8.0
            size = diameter + m * 2.0
            c = canvas(size, size)
            painter = QPainter(c)
            painter.setRenderHint(QPainter.Antialiasing, True)
            grad = QRadialGradient(size / 2.0, size / 2.0, diameter / 2.0)
            col = QColor(self._ink)
            col.setAlpha(alpha)
            grad.setColorAt(0.0, col)
            col0 = QColor(col)
            col0.setAlpha(0)
            grad.setColorAt(1.0, col0)
            painter.fillRect(QRectF(0.0, 0.0, size, size), grad)
            painter.end()
            return c

        self._contact_mid = contact(96.0, 46 if self._dark else 30)
        self._contact_inner = contact(58.0, 40 if self._dark else 26)

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------
    @property
    def bloomed(self) -> bool:
        return self._bloomed

    def start_anim(self) -> None:
        self._center_on_screen()
        self.show()
        self.raise_()
        self._anim.start()

    def skip(self) -> None:
        if self._bloomed:
            return
        self._anim.stop()
        self.set_progress(1.0)
        self._finish()

    # ------------------------------------------------------------------
    def get_progress(self) -> float:
        return self._progress

    def set_progress(self, value: float) -> None:
        v = _clamp01(float(value))
        if v != self._progress:
            self._progress = v
            self.update()

    progress = Property(float, fget=get_progress, fset=set_progress)

    # ------------------------------------------------------------------
    def _center_on_screen(self) -> None:
        screen = self.screen()
        geo = screen.availableGeometry() if screen else None
        if geo is None:
            return
        self.move(
            geo.x() + (geo.width() - _SIZE) // 2,
            geo.y() + (geo.height() - _SIZE) // 2,
        )

    # 各层开合角关键帧（全局进度 → bud / half / full / round 四态）
    _ANGLE_KEYS = (0.0, 0.20, 0.45, 0.78, 1.0)

    def _layer_angle(self, layer, p: float) -> float:
        bud, half, full, rnd = layer.bud, layer.half, layer.full, layer.round
        # 合苞末期花苞微微苏醒（角度放大 1.3 倍）
        vals = (bud, bud * 1.3 + 0.01, half, full, rnd)
        keys = self._ANGLE_KEYS
        if p <= keys[0]:
            return vals[0]
        for k in range(len(keys) - 1):
            if p <= keys[k + 1]:
                t = _smooth01((p - keys[k]) / (keys[k + 1] - keys[k]))
                return vals[k] + (vals[k + 1] - vals[k]) * t
        return vals[-1]

    def _draw_petal(self, painter, layer, i: int, angle: float, front: bool) -> None:
        """3/4 视角投影绘制一片花瓣（路径 / 画笔全部取缓存）。

        front（朝向观察者）的瓣填充稍强、线条清晰，用于掩住花芯重叠线；
        背侧瓣更淡，只交代外包轮廓。
        """
        theta = layer.thetas[i]
        # 合苞抱合错落随打开消失
        open01 = _clamp01((angle - layer.bud) / (math.pi / 2.0 - layer.bud))
        angle = angle + layer.curls[i] * (1.0 - open01)
        sa, ca = math.sin(angle), math.cos(angle)
        st, ct = math.sin(theta), math.cos(theta)
        # 相机俯仰 φ：屏幕轴向量（dx 向右，dy 向上）
        dx = sa * st
        dy = -ca * _COS_PITCH - sa * ct * _SIN_PITCH
        axis = math.hypot(dx, dy) or 1.0
        rot_deg = math.degrees(math.atan2(dx, -dy))
        # 着生点在花托环上的投影（后瓣偏上、前瓣偏下）
        bx = layer.ring * st
        by = -layer.ring * ct * _SIN_PITCH

        fill = self._fill_front if front else self._fill_back
        line_alpha = 1.0 if front else 0.62
        painter.save()
        painter.translate(bx, by)
        painter.rotate(rot_deg)
        painter.scale(1.0, axis)
        painter.setPen(Qt.NoPen)
        painter.setBrush(fill)
        painter.setOpacity(1.0)
        painter.drawPath(layer.path)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(self._pen)
        # 合苞时背侧瓣轮廓再压淡，避免花芯挤成黑团
        painter.setOpacity(line_alpha * (0.55 + 0.45 * open01 if not front else 1.0))
        painter.drawPath(layer.path)
        painter.setPen(self._vein_pen)
        painter.setOpacity((0.10 + 0.35 * open01) * line_alpha)
        painter.drawPath(layer.vein)
        painter.restore()

    def _draw_receptacle(self, painter) -> None:
        """花托小节点：瓣基交汇点，合苞期也画。"""
        painter.save()
        painter.setPen(Qt.NoPen)
        painter.setBrush(self._fill_front)
        painter.drawEllipse(self._node_rect)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(self._vein_pen)
        painter.drawEllipse(self._node_rect)
        painter.restore()

    def _draw_pod(self, painter, p: float) -> None:
        """莲蓬：扁圆蓬房 + 莲子 + 前侧雄蕊点（碗形即显现，盛开最完整）。"""
        t = _smooth01((p - 0.28) / 0.20)
        if t <= 0.0:
            self._draw_receptacle(painter)
            return
        scale = 0.78 + 0.22 * t
        painter.save()
        painter.translate(0.0, -3.0)
        painter.scale(scale, scale * 0.82)
        painter.setOpacity(0.55 + 0.45 * t)
        painter.setPen(Qt.NoPen)
        painter.setBrush(self._pod_fill)
        painter.drawEllipse(self._pod_rect)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(self._pod_pen)
        painter.drawEllipse(self._pod_rect)
        painter.setPen(Qt.NoPen)
        painter.setBrush(self._ink)
        for dx, dy in self._pod_dots:
            painter.drawEllipse(QRectF(dx - 1.1, dy - 1.1, 2.2, 2.2))
        # 雄蕊：蓬房前弧一排小点
        for dx, dy in self._stamens:
            painter.drawEllipse(QRectF(dx - 0.8, dy - 0.8, 1.6, 1.6))
        painter.setBrush(Qt.NoBrush)
        painter.restore()

    # ------------------------------------------------------------------
    # 绘制
    # ------------------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802
        if self._ground_shadow is None:
            self._build_shadows()

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        painter.translate(_SIZE / 2.0, _SIZE / 2.0)

        prog = self._progress
        rise = _smooth01((prog - 0.10) / 0.40)
        heart_y = _HEART_BUD_Y + (_HEART_OPEN_Y - _HEART_BUD_Y) * rise
        stem_alpha = 1.0 - _smooth01((prog - 0.16) / 0.18)

        # 1) 地面柔投影（极轻，随绽放略收淡）
        gs = 1.0 - 0.10 * rise
        painter.save()
        painter.translate(-self._ground_shadow.width() / self.devicePixelRatioF() / 2.0 * gs,
                          _STEM_BASE.y() - 18.0)
        painter.scale(gs, 1.0)
        painter.setOpacity(0.26 if not self._dark else 0.40)
        painter.drawPixmap(0, 0, self._ground_shadow)
        painter.restore()

        # 2) 茎（仅合苞阶段清晰）+ 基部萼片（碗形期淡出）
        painter.save()
        painter.translate(0.0, heart_y)
        if stem_alpha > 0.01:
            painter.setOpacity(stem_alpha)
            painter.setPen(self._stem_pen)
            painter.drawPath(self._stem_path)
        sepal_alpha = 1.0 - _smooth01((prog - 0.16) / 0.26)
        if sepal_alpha > 0.01:
            painter.setOpacity(sepal_alpha)
            for sp in self._sepal_paths_all:
                painter.setPen(Qt.NoPen)
                painter.setBrush(self._fill_front)
                painter.drawPath(sp)
                painter.setBrush(Qt.NoBrush)
                painter.setPen(self._vein_pen)
                painter.drawPath(sp)
        painter.restore()

        # 3) 花朵（3/4 视角；按 背→前 深度两遍，莲蓬夹在中间）
        painter.save()
        painter.translate(0.0, heart_y)
        settle = _smooth01((prog - _P_OPEN_END) / (1.0 - _P_OPEN_END))
        bloom_scale = 0.985 + 0.015 * settle
        painter.scale(bloom_scale, bloom_scale)

        angles = [self._layer_angle(layer, prog) for layer in self._layers]

        def draw_side(front: bool) -> None:
            seq = (2, 1, 0) if front else (0, 1, 2)
            for li in seq:
                layer = self._layers[li]
                for idx in layer.order:
                    if (math.cos(layer.thetas[idx]) > 0.0) != front:
                        continue
                    self._draw_petal(painter, layer, idx, angles[li], front)

        # 背侧花瓣（外→内）
        draw_side(False)

        # 瓣层接触影（心部极轻凹陷）
        t1 = _smooth01((prog - 0.34) / 0.22)
        if t1 > 0.0 and self._contact_mid is not None:
            cw = self._contact_mid.width() / self.devicePixelRatioF()
            painter.save()
            painter.setOpacity(0.5 * t1)
            painter.translate(-cw / 2.0, -cw / 2.0 - 2.0)
            painter.drawPixmap(0, 0, self._contact_mid)
            painter.restore()

        # 前侧花瓣（内→外：最靠近观察者的前瓣最后画）
        draw_side(True)

        # 莲蓬最后画：半开即在杯中露出，全开最完整，同时遮住瓣基交汇墨结
        self._draw_pod(painter, prog)

        painter.restore()
        painter.setOpacity(1.0)
        painter.end()

    # -----------------------------------------------------------------
    def keyPressEvent(self, event) -> None:  # noqa: N802
        self.skip()
        event.accept()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self.skip()
        event.accept()

    def _on_anim_finished(self) -> None:
        self.set_progress(1.0)
        self._finish()

    def _finish(self) -> None:
        if self._bloomed:
            return
        self._bloomed = True
        self.bloomFinished.emit()


class BootSequence(QObject):
    """启动动画与主窗口初始化的并行编排。

    用法：
        seq = BootSequence(build_theme(settings))
        seq.start()                      # 动画立即开画
        QTimer.singleShot(0, 构造主窗口并调用 seq.mark_ready(window))
        seq.entered.connect(app.quit)    # 衔接动画结束（窗口已淡入）
    """

    entered = Signal()

    _FADE_IN_MS = 220
    _FADE_OUT_MS = 170

    def __init__(self, theme: dict, dark: bool, duration_ms: int = BOOT_DURATION_MS) -> None:
        super().__init__()
        self._widget = BootLotusWidget(theme, dark, duration_ms)
        self._widget.bloomFinished.connect(self._try_enter)
        self._window = None
        self._ready = False
        self._entered = False
        self.widget_closed = False
        self._fade_in: QPropertyAnimation | None = None
        self._fade_out: QPropertyAnimation | None = None

    @property
    def widget(self) -> BootLotusWidget:
        return self._widget

    def start(self) -> None:
        self._widget.start_anim()

    def mark_ready(self, window) -> None:
        """主窗口构造完成：以透明态先显示（不白屏），等动画结束再淡入。"""
        self._window = window
        window.setWindowOpacity(0.0)
        window.show()
        window.raise_()
        self._ready = True
        self._try_enter()

    def _try_enter(self) -> None:
        if self._entered or not self._ready or not self._widget.bloomed:
            return
        self._entered = True
        window = self._window

        self._fade_in = QPropertyAnimation(window, b"windowOpacity", self)
        self._fade_in.setDuration(self._FADE_IN_MS)
        self._fade_in.setStartValue(0.0)
        self._fade_in.setEndValue(1.0)
        self._fade_in.setEasingCurve(EASE_OUT)

        self._fade_out = QPropertyAnimation(self._widget, b"windowOpacity", self)
        self._fade_out.setDuration(self._FADE_OUT_MS)
        self._fade_out.setStartValue(1.0)
        self._fade_out.setEndValue(0.0)
        self._fade_out.setEasingCurve(EASE_OUT)
        self._fade_out.finished.connect(self._finish_entered)

        window.activateWindow()
        window.raise_()
        self._fade_in.start()
        self._fade_out.start()

    def _finish_entered(self) -> None:
        self._widget.close()
        self._widget.deleteLater()
        self.widget_closed = True
        if self._window is not None:
            self._window.setWindowOpacity(1.0)
        self.entered.emit()
