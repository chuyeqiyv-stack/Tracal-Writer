# -*- coding: utf-8 -*-
"""表面质感引擎：液态玻璃（liquid glass 四层结构）与磨砂哑光（frosted matte）。

液态玻璃层次（参数全部来自主题令牌 liq_*，本模块不写魔法颜色）：
  底层  缓存的背景模糊位图（先降采样 0.5 再模糊 σ/2，再放大回原尺寸，成本约 1/4；
        浮层开合 / 尺寸稳定时只抓一次，滚动拖动 120–150ms 节流，绝不逐帧模糊）；
  本体  主题面板色 55–70% 竖向渐变（SourceOver）+ 8–12% 压暗层保文字对比；
  ①    顶部渐隐高光 白 22% → 8% → 0，落在顶部 45% 高，圆角路径裁剪（Plus 加色）；
  ②    边缘镜面线：内缩 1px 圆角路径描边，左上 → 右下 白 55% → 10%（Plus）；
  ③    高光斑：椭圆径向渐变 白 18% → 0，宽 ≈ 卡宽 60%，静止偏左上，
        指针进入后 180ms ease-out 跟随（Plus）；
  ④    底部内暗边 1px 黑 8% + 左右缘极淡青 / 品红色散各 1px（4–6%，SourceOver）；
  噪点  玻璃颗粒 2–3%（深色 1.5%，SourceOver）；
  阴影  仅浮层 0 8 24 rgba(0,0,0,0.10)（motion.apply_elevation），卡片本体无阴影。

硬指标由探针 smoke_liquid.py 断言：高光层面积占比 ≤ 50%、本体不透明度区间、
深色正文对比度 ≥ 4.5:1、噪点比例、accent 全屏占比 < 5%。
磨砂（order）与 default / paper 不启用本模块任何高光效果，历史观感保持不动。
"""
from __future__ import annotations

from PySide6.QtCore import (
    QEvent,
    QObject,
    QPoint,
    QPointF,
    QRectF,
    Qt,
    QTimer,
    QVariantAnimation,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
    QRegion,
)
from PySide6.QtWidgets import QApplication, QGraphicsBlurEffect, QLabel, QWidget

from app.ui.theme import qc

# ----------------------------------------------------------------------
# 全局质感状态：apply_theme 时写入，自绘控件只读
# ----------------------------------------------------------------------
_KIND = "plain"          # plain / glass / matte
_DARK = False
_BLUR_ENABLED = True     # 设置项「玻璃背景模糊」关闭时只保留半透明本体，不抓不糊
_TOKENS: dict[str, str] = {}
_CARD_RADIUS = 12.0
_PANEL = QColor("#FAFAF8")   # 内容浮层底板色（浅色暖白；深色取主题深面板）
_BORDER = QColor("#E6EAF1")  # 底板唯一一条边框线

# 玻璃风三档子模式（v3 定调）：
#   block    立体玻璃块（新默认）：弱反光 + 侧壁挤出/倒角/内阴影的体积几何
#   liquid   液态玻璃（弱反光版）：平面半透体 + 降级后的顶光/rim/sheen
#   frosted  磨砂雾面：高不透明乳白膜、σ 16–24、只留 1px 淡亮线，无 sheen/色散
GLASS_MODES = ("block", "liquid", "frosted")
_GLASS_MODE = "block"
# 当前模式 × 深浅解析好的绘制参数（set_*_state 时一次性重建，绘制零分配）
_MP: dict = {}


def set_surface_state(theme: dict) -> None:
    """apply_theme 调用：同步当前质感种类、深浅与液态玻璃令牌。"""
    global _KIND, _DARK, _TOKENS, _CARD_RADIUS, _PANEL, _BORDER
    _KIND = str(theme.get("surface_kind", "plain"))
    _DARK = str(theme.get("window", "#FFFFFF")).upper() in {"", "#000000"} or (
        QColor(theme.get("window", "#FFFFFF")).lightness() < 128
    )
    _TOKENS = {k: v for k, v in theme.items() if k.startswith("liq_") or k.startswith("noise_")}
    # 底板：浅色暖白（比纯白低一档、略偏暖）；深色用主题深面板，杜绝发白
    _PANEL = QColor(theme.get("panel", "#292D35")) if _DARK else QColor("#FAFAF8")
    _BORDER = QColor(theme.get("border", "#3A3F49" if _DARK else "#E6EAF1"))
    try:
        _CARD_RADIUS = float(theme.get("radius_lg", 12))
    except (TypeError, ValueError):
        _CARD_RADIUS = 12.0
    _rebuild_mode_params()


def set_glass_mode(mode: str) -> None:
    """玻璃质感三档：block / liquid / frosted；旧设置缺值由 models 层回退 block。"""
    global _GLASS_MODE
    _GLASS_MODE = mode if mode in GLASS_MODES else "block"
    _rebuild_mode_params()


def glass_mode() -> str:
    return _GLASS_MODE


def blur_sigma() -> int:
    """当前模式的背景模糊 σ（1x 计；内部再降采样 0.5）。"""
    return int(_MP.get("blur", 16)) if is_glass() else int(token_value("liq_blur", "16") or 16)


def _hsl_adjust(c: QColor, sat_mul: float, light_add: float) -> QColor:
    h, s, l, a = c.getHslF()
    return QColor.fromHslF(h, max(0.0, min(1.0, s * sat_mul)),
                           max(0.0, min(1.0, l + light_add)), a)


def _rebuild_mode_params() -> None:
    """按当前模式 × 深浅把四层 / 体积 / 噪点参数解析成 _MP。"""
    global _MP
    if not is_glass():
        _MP = {}
        return
    dark = _DARK
    mode = _GLASS_MODE
    body_hi = QColor(token("liq_body_hi"))
    body_lo = QColor(token("liq_body_lo"))
    shade = QColor(token("liq_shade"))
    p: dict = {"mode": mode, "shade": shade, "dispersion": 0.04}

    if mode == "frosted":
        # 乳白膜 72–82%：-12% 降饱和、亮度 +5%；σ 16–24；只留 1px 淡亮线
        hi = _hsl_adjust(body_hi, 0.88, 0.05)
        lo = _hsl_adjust(body_lo, 0.88, 0.05)
        hi.setAlphaF(0.82)
        lo.setAlphaF(0.74)
        p.update(
            blur=20, body_hi=hi, body_lo=lo, shade=QColor(0, 0, 0, 0),
            glow0=QColor(255, 255, 255, 0), glow1=QColor(255, 255, 255, 0),
            glow2=QColor(255, 255, 255, 0), glow_h=0.32,
            rim_hi=QColor(255, 255, 255, 31 if not dark else 26),   # 白 12%/10%
            rim_lo=QColor(255, 255, 255, 31 if not dark else 26),
            sheen=0.0, sheen_w=0.48,
            inner_dark=QColor(0, 0, 0, 20),                        # 1px 黑 8% 收边
            dispersion=0.0, noise=0.018 if not dark else 0.012,
            volume=False,
        )
    else:
        # block / liquid 共用弱反光参数；深色比浅色再低 3–4 个百分点
        if not dark:
            g0, g1 = 0.12, 0.05
            rim_h, rim_l = 0.28, 0.06
            sheen_a = 0.09
            noise_v = 0.025
        else:
            # 深色额外压一档：比浅色名义值再低 3–4 个百分点（覆盖率口径实测低 ≥3pp）
            g0, g1 = 0.08, 0.03
            rim_h, rim_l = 0.23, 0.05
            sheen_a = 0.06
            noise_v = 0.015
        p.update(
            blur=16, body_hi=QColor(body_hi), body_lo=QColor(body_lo),
            glow0=QColor(255, 255, 255, int(255 * g0)),
            glow1=QColor(255, 255, 255, int(255 * g1)),
            glow2=QColor(255, 255, 255, 0), glow_h=0.32,
            rim_hi=QColor(255, 255, 255, int(255 * rim_h)),
            rim_lo=QColor(255, 255, 255, int(255 * rim_l)),
            sheen=sheen_a, sheen_w=0.48,
            inner_dark=QColor(token("liq_inner_dark")),
            noise=noise_v,
            # 体积几何仅立体玻璃块
            volume=(mode == "block"),
            side_bright=0.08, side_dark=0.10,     # 左上提亮 8% / 右下压暗 10%
            chamfer=0.12,                        # 内缩 2px 倒角线 白 12%
            inner_shadow=0.10,                   # 内底 3px 黑 10%→0
        )
    p["cyan"] = QColor(110, 205, 255, int(255 * p["dispersion"]))
    p["magenta"] = QColor(255, 112, 220, int(255 * p["dispersion"]))
    _MP = p


def set_blur_enabled(enabled: bool) -> None:
    """设置项切换：False 时液态玻璃不再抓取 / 模糊背后内容（半透明本体仍在）。"""
    global _BLUR_ENABLED
    _BLUR_ENABLED = bool(enabled)


def blur_enabled() -> bool:
    return _BLUR_ENABLED


def kind() -> str:
    return _KIND


def is_glass() -> bool:
    return _KIND == "glass"


def is_matte() -> bool:
    return _KIND == "matte"


def card_radius() -> float:
    return _CARD_RADIUS


def token(name: str, default: str = "rgba(0,0,0,0)") -> QColor:
    return qc(str(_TOKENS.get(name, default)))


def tokenf(name: str, default: float) -> float:
    try:
        return float(str(_TOKENS.get(name, str(default))))
    except (TypeError, ValueError):
        return default


def token_value(name: str, default: str = "") -> str:
    """取令牌原始字符串值（如 liq_blur 的 "16"）。"""
    return str(_TOKENS.get(name, default))


# ----------------------------------------------------------------------
# 磨砂噪点：预生成 PNG，全局缓存一次
# ----------------------------------------------------------------------
_NOISE_CACHE: dict[str, QPixmap] = {}


def noise_pixmap() -> QPixmap | None:
    """当前主题对应的磨砂颗粒位图（缓存；缺失时返回 None）。

    玻璃主题不显式给 noise_name：按深浅回退到与磨砂同款的颗粒 PNG。
    """
    name = str(_TOKENS.get("noise_name", ""))
    if not name:
        name = "noise_dark.png" if _DARK else "noise_light.png"
    pix = _NOISE_CACHE.get(name)
    if pix is not None:
        return pix
    from app.config import ASSETS_DIR

    path = ASSETS_DIR / name
    loaded = QPixmap(str(path)) if path.exists() else QPixmap()
    if loaded.isNull():
        return None
    _NOISE_CACHE[name] = loaded
    return loaded


def paint_matte(painter: QPainter, rect) -> None:
    """在已裁剪区域内平铺极淡噪点（哑光、无高光）。"""
    pix = noise_pixmap()
    if pix is None or pix.isNull():
        return
    painter.save()
    painter.setOpacity(0.9)
    r = QRectF(rect)
    x = r.left()
    while x < r.right():
        y = r.top()
        while y < r.bottom():
            painter.drawPixmap(int(x), int(y), pix)
            y += pix.height()
        x += pix.width()
    painter.restore()


def rounded_clip(painter: QPainter, rect, radius: float) -> QPainterPath:
    path = QPainterPath()
    path.addRoundedRect(QRectF(rect), radius, radius)
    painter.setClipPath(path, Qt.ClipOperation.IntersectClip)
    return path


def rounded_path(rect, radius: float) -> QPainterPath:
    path = QPainterPath()
    path.addRoundedRect(QRectF(rect), radius, radius)
    return path


# ----------------------------------------------------------------------
# 液态玻璃：缓存模糊位图（降采样 0.5 → σ/2 模糊 → 放大，成本约 1/4）
# ----------------------------------------------------------------------
def grab_behind(widget: QWidget, source: QWidget, size=None, origin=None) -> QPixmap | None:
    """把 source 上与 widget 重合的区域渲染成位图（调用方须保证 widget 自身未入画，
    例如抽屉 show() 之前、或 source 是不含浮层的主窗口）。"""
    if source is None:
        return None
    w, h = (size.width(), size.height()) if size is not None else (widget.width(), widget.height())
    if w <= 0 or h <= 0:
        return None
    if origin is None:
        origin = source.mapFromGlobal(widget.mapToGlobal(QPoint(0, 0)))
    pm = QPixmap(w, h)
    pm.fill(Qt.GlobalColor.transparent)
    source.render(pm, QPoint(0, 0), QRegion(origin.x(), origin.y(), w, h))
    return pm


def blur_pixmap(src: QPixmap | None, radius: int) -> QPixmap | None:
    """背景模糊：先降采样一半，在小图上以 σ/2 半径模糊，再平滑放大回原尺寸。

    QGraphicsBlurEffect 必须挂在离屏 widget 上渲染一次；调用方负责缓存结果，
    切勿每帧调用。radius<=0 或关闭模糊时原图返回（调用方按设置决定是否传入）。
    """
    if src is None or src.isNull():
        return src
    if radius <= 0:
        return src
    w, h = src.width(), src.height()
    sw, sh = max(1, w // 2), max(1, h // 2)
    small = src.scaled(sw, sh, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)

    holder = QWidget()
    holder.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    holder.resize(sw, sh)
    label = QLabel(holder)
    label.setPixmap(small)
    label.resize(sw, sh)
    effect = QGraphicsBlurEffect()
    effect.setBlurRadius(float(radius) * 0.5)   # 半尺寸图上 σ/2，放大后等效 σ
    label.setGraphicsEffect(effect)
    holder.show()
    label.show()
    label.repaint()
    blurred_small = label.grab()
    holder.close()
    effect.deleteLater()

    out = blurred_small.scaled(w, h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    return out


# ----------------------------------------------------------------------
# 液态玻璃四层
# ----------------------------------------------------------------------
def _default_sheen(r: QRectF) -> QPointF:
    return QPointF(r.width() * tokenf("liq_sheen_x", 0.30),
                   r.height() * tokenf("liq_sheen_y", 0.16))


def _mp(key: str, default):
    return _MP.get(key, default)


def _paint_body(painter: QPainter, r: QRectF, strength: float) -> None:
    """本体：面板色竖向渐变（block/liquid 55–70%，frosted 72–82%）+ 压暗层（SourceOver）。"""
    hi = QColor(_mp("body_hi", token("liq_body_hi")))
    lo = QColor(_mp("body_lo", token("liq_body_lo")))
    hi.setAlphaF(hi.alphaF() * strength)
    lo.setAlphaF(lo.alphaF() * strength)
    grad = QLinearGradient(0.0, r.top(), 0.0, r.bottom())
    grad.setColorAt(0.0, hi)
    grad.setColorAt(1.0, lo)
    painter.fillRect(r, QBrush(grad))
    shade = QColor(_mp("shade", token("liq_shade")))
    if shade.alpha() > 0:
        shade.setAlphaF(shade.alphaF() * strength)
        painter.fillRect(r, QBrush(shade))


def _paint_top_glow(painter: QPainter, r: QRectF, strength: float) -> None:
    """① 顶部渐隐高光：白 12% → 5% → 0，顶部 32% 高（frosted 关闭）。"""
    c0, c1, c2 = QColor(_mp("glow0", QColor(0, 0, 0, 0))), QColor(_mp("glow1", QColor(0, 0, 0, 0))), QColor(255, 255, 255, 0)
    if c0.alpha() == 0:
        return
    glow_h = r.height() * float(_mp("glow_h", 0.32))
    for c in (c0, c1, c2):
        c.setAlphaF(c.alphaF() * strength)
    grad = QLinearGradient(0.0, r.top(), 0.0, r.top() + glow_h)
    # 名义 12%→5%→0；尾部在 82% 处已归零（82–100% 段为 0 缓冲），
    # 让「有效可见（≥3% 不透明）」的反光面积受控，而非整张渐变盒都计入覆盖
    grad.setColorAt(0.0, c0)
    grad.setColorAt(0.50, c1)
    grad.setColorAt(0.62, c2)
    grad.setColorAt(1.0, c2)
    painter.fillRect(QRectF(r.left(), r.top(), r.width(), glow_h + 1.0), QBrush(grad))


def _paint_rim(painter: QPainter, r: QRectF, radius: float, strength: float) -> None:
    """② 边缘淡亮线：内缩 1px 圆角路径描边（block/liquid 左上28%→右下6%；frosted 平 12%）。"""
    inset = 1.0
    rr = r.adjusted(inset, inset, -inset, -inset)
    path = QPainterPath()
    rr_rad = max(0.5, radius - inset)
    path.addRoundedRect(rr, rr_rad, rr_rad)
    hi, lo = QColor(_mp("rim_hi", QColor(255, 255, 255, 0))), QColor(_mp("rim_lo", QColor(255, 255, 255, 0)))
    hi.setAlphaF(hi.alphaF() * strength)
    lo.setAlphaF(lo.alphaF() * strength)
    grad = QLinearGradient(r.topLeft(), r.bottomRight())
    grad.setColorAt(0.0, hi)
    grad.setColorAt(1.0, lo)
    pen = QPen(QBrush(grad), 1.0)
    pen.setCapStyle(Qt.RoundCap)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    painter.drawPath(path)


def _paint_sheen(painter: QPainter, r: QRectF, center: QPointF, strength: float) -> None:
    """③ 高光斑：椭圆径向渐变（中心白 9% → 边缘 0，宽 48%）；frosted 关闭。"""
    amount = float(_mp("sheen", 0.0))
    if amount <= 0.0:
        return
    rx = r.width() * float(_mp("sheen_w", 0.48)) * 0.5
    ry = r.height() * 0.64 * 0.5
    # 中心点限制在卡内，避免整瓣移出卡片变成白雾贴边
    cx = min(max(center.x(), rx * 0.35), r.width() - rx * 0.35)
    cy = min(max(center.y(), -ry * 0.5), r.height() - ry * 0.2)
    core = QColor(255, 255, 255, int(255 * amount * strength))
    edge = QColor(core)
    edge.setAlphaF(0.0)
    painter.save()
    painter.translate(cx, cy)
    painter.scale(max(1.0, rx), max(1.0, ry))
    rad = QRadialGradient(0.0, 0.0, 1.0)
    # 中心 9% 保持；中段提前衰减，使高光斑是「一团聚光」而非白雾圆盘（覆盖率受控）
    mid = QColor(core)
    mid.setAlphaF(core.alphaF() * 0.45)
    rad.setColorAt(0.0, core)
    rad.setColorAt(0.55, mid)
    rad.setColorAt(1.0, edge)
    painter.setBrush(QBrush(rad))
    painter.setPen(Qt.NoPen)
    painter.drawEllipse(QRectF(-1.0, -1.0, 2.0, 2.0))
    painter.restore()


def _paint_side_walls(painter: QPainter, r: QRectF, radius: float, strength: float) -> None:
    """立体玻璃块·侧壁挤出：沿圆角轮廓 2px 渐变带，左上亮一档 / 右下暗一档（SourceOver）。

    画在轮廓内缩 1.5px 处（2px 笔刷占 0.5–2.5px），保证完全落在控件边界内，
    卡片无外边距也不会被裁掉；浮层有 gutter 时观感等同外挤出。
    """
    if not _mp("volume", False):
        return
    inset = 1.5
    rr = r.adjusted(inset, inset, -inset, -inset)
    path = QPainterPath()
    rad = max(0.5, radius - inset)
    path.addRoundedRect(rr, rad, rad)
    # 光源左上：取面板顶色提亮 8%；背光右下：取底色压暗 10%
    base_hi = QColor(_mp("body_hi", token("liq_body_hi"))).toRgb()
    base_lo = QColor(_mp("body_lo", token("liq_body_lo"))).toRgb()
    bh, bs, bl, ba = base_hi.getHslF()
    lh, ls, ll, la = base_lo.getHslF()
    bright = QColor.fromHslF(bh, bs, min(1.0, bl + float(_mp("side_bright", 0.08))), ba)
    dark = QColor.fromHslF(lh, ls, max(0.0, ll - float(_mp("side_dark", 0.10))), la)
    bright.setAlphaF(bright.alphaF() * strength)
    dark.setAlphaF(dark.alphaF() * strength)
    grad = QLinearGradient(r.topLeft(), r.bottomRight())
    grad.setColorAt(0.0, bright)
    grad.setColorAt(1.0, dark)
    pen = QPen(QBrush(grad), 2.0)
    pen.setCapStyle(Qt.RoundCap)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    painter.drawPath(path)


def _paint_chamfer(painter: QPainter, r: QRectF, radius: float, strength: float) -> None:
    """立体玻璃块·顶面/侧面转折：内缩 2px 路径，左上 2/3 弧段白 12% 倒角线。"""
    if not _mp("volume", False):
        return
    inset = 2.0
    rr = r.adjusted(inset, inset, -inset, -inset)
    path = QPainterPath()
    rad = max(0.5, radius - inset)
    path.addRoundedRect(rr, rad, rad)
    white = QColor(255, 255, 255, int(255 * float(_mp("chamfer", 0.12)) * strength))
    clear = QColor(255, 255, 255, 0)
    # 左上 → 右下渐变在 2/3 处归零：亮部只覆盖顶、左及部分右/底弧
    grad = QLinearGradient(r.topLeft(), r.bottomRight())
    grad.setColorAt(0.0, white)
    grad.setColorAt(0.66, QColor(255, 255, 255, int(white.alpha() * 0.35)))
    grad.setColorAt(1.0, clear)
    pen = QPen(QBrush(grad), 1.0)
    pen.setCapStyle(Qt.RoundCap)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    painter.drawPath(path)


def _paint_inner_shadow(painter: QPainter, r: QRectF, strength: float) -> None:
    """立体玻璃块·底部内阴影：内底 3px 黑 10%→0，让块体「坐下去」。"""
    if not _mp("volume", False):
        return
    h = 3.0
    band = QRectF(r.left(), r.bottom() - h, r.width(), h)
    a = float(_mp("inner_shadow", 0.10))
    grad = QLinearGradient(0.0, band.top(), 0.0, band.bottom())
    grad.setColorAt(0.0, QColor(0, 0, 0, 0))
    grad.setColorAt(1.0, QColor(0, 0, 0, int(255 * a * strength)))
    painter.fillRect(band, QBrush(grad))


def _paint_bottom_edges(painter: QPainter, r: QRectF, radius: float) -> None:
    """④ 底部内暗边 1px 黑 8% + 左右缘极淡青 / 品红色散各 1px（frosted 无色散）。"""
    y = r.bottom() - 0.5
    pen = QPen(QColor(_mp("inner_dark", token("liq_inner_dark"))))
    pen.setWidthF(1.0)
    painter.setPen(pen)
    painter.drawLine(QPointF(r.left() + radius, y), QPointF(r.right() - radius, y))
    if float(_MP.get("dispersion", 0.0)) <= 0.0:
        return
    # 色散：左缘青、右缘品红，只走两侧平直段（避开上下圆角）
    top = r.top() + radius
    bottom = r.bottom() - radius
    pen_c = QPen(QColor(_MP["cyan"]))
    pen_c.setWidthF(1.0)
    painter.setPen(pen_c)
    painter.drawLine(QPointF(r.left() + 0.5, top), QPointF(r.left() + 0.5, bottom))
    pen_m = QPen(QColor(_MP["magenta"]))
    pen_m.setWidthF(1.0)
    painter.setPen(pen_m)
    painter.drawLine(QPointF(r.right() - 0.5, top), QPointF(r.right() - 0.5, bottom))


# 玻璃噪点：程序化稀疏柔光点（2.5% 像素 / 深色 1.5%），固定种子保证各帧一致。
# 不用「满铺纹理 × 2.5% 透明度」——8 位通道下会被量化成 0 而完全不可见。
_GLASS_NOISE_CACHE: dict[tuple, QPixmap] = {}


def _glass_noise_tile() -> QPixmap:
    import random

    density = round(float(_MP.get("noise", 0.025 if not _DARK else 0.015)), 4)
    key = (density,)
    pix = _GLASS_NOISE_CACHE.get(key)
    if pix is not None:
        return pix
    from PySide6.QtGui import QImage

    size = 128
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(QColor(0, 0, 0, 0))
    rnd = random.Random(0x51A5 ^ int(density * 100000))
    total = size * size
    for _ in range(int(total * density)):
        x, y = rnd.randrange(size), rnd.randrange(size)
        # 亮 / 暗颗粒各半：亮的稍强，模拟胶片颗粒在高光与暗部的双向跳动
        if rnd.random() < 0.55:
            img.setPixelColor(x, y, QColor(255, 255, 255, rnd.randrange(26, 46)))
        else:
            img.setPixelColor(x, y, QColor(15, 20, 30, rnd.randrange(18, 34)))
    pix = QPixmap.fromImage(img)
    _GLASS_NOISE_CACHE[key] = pix
    return pix


def _paint_glass_noise(painter: QPainter, r: QRectF) -> None:
    """玻璃噪点：稀疏柔光点平铺（block/liquid 2.5%/1.5%，frosted 1.8%/1.2%）。"""
    if float(_MP.get("noise", 0.025)) <= 0:
        return
    pix = _glass_noise_tile()
    if pix.isNull():
        return
    painter.save()
    x = r.left()
    while x < r.right():
        y = r.top()
        while y < r.bottom():
            painter.drawPixmap(int(x), int(y), pix)
            y += pix.height()
        x += pix.width()
    painter.restore()


def paint_liquid_layers(painter: QPainter, rect, radius: float,
                        body_strength: float, glow_strength: float,
                        sheen_center: QPointF | None = None,
                        sheen_strength: float | None = None,
                        noise: bool = True) -> None:
    """在「已铺好模糊底图、已做圆角裁剪」的 painter 上画玻璃四层 + 噪点。

    抽取为独立函数，便于探针在透明离屏图上单画高光层做覆盖率断言。
    文字密集浮层的底板由 paint_liquid_sheet 在调用本函数之前铺好，玻璃层
    始终原样压在底板上，不再往玻璃内部叠任何内容板。
    """
    r = QRectF(rect)
    # 本体 + 压暗（SourceOver）
    _paint_body(painter, r, body_strength)
    # 立体玻璃块体积层（SourceOver）：侧壁挤出 → 倒角转折 → 底部内阴影
    if _MP.get("volume", False):
        _paint_side_walls(painter, r, radius, body_strength)
        _paint_chamfer(painter, r, radius, glow_strength)
        _paint_inner_shadow(painter, r, glow_strength)
    # 高光层一律 Plus 加色（白叠白天然发光）
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
    _paint_top_glow(painter, r, glow_strength)
    _paint_sheen(painter, r, sheen_center or _default_sheen(r),
                 glow_strength if sheen_strength is None else sheen_strength)
    _paint_rim(painter, r, radius, glow_strength)
    # 色散与噪点用 SourceOver
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
    _paint_bottom_edges(painter, r, radius)
    if noise:
        _paint_glass_noise(painter, r)


# 底板：覆盖到外框内缩仅 2px、圆角 = 外框 − 2，只有一条边框线；玻璃层原样压在上面
BASE_PLATE_INSET = 2.0
BASE_PLATE_RADIUS_DELTA = 2.0


def _base_plate_path(r: QRectF, radius: float) -> tuple[QPainterPath, QRectF]:
    plate = r.adjusted(BASE_PLATE_INSET, BASE_PLATE_INSET,
                       -BASE_PLATE_INSET, -BASE_PLATE_INSET)
    inner_radius = max(1.0, float(radius) - BASE_PLATE_RADIUS_DELTA)
    return rounded_path(plate, inner_radius), plate


def paint_base_plate(painter: QPainter, rect, radius: float) -> None:
    """内容浮层底板（画在玻璃层之下）：不透明面板色 + 唯一一条 1px 边框线。

    浅色为暖白 #FAFAF8（比纯白低一档），深色取主题深面板色；底板覆盖到外框
    内缩 2px，四周仍留 2px 玻璃带透出背后模糊与折射。
    """
    path, plate = _base_plate_path(QRectF(rect), radius)
    if plate.width() <= 4 or plate.height() <= 4:
        return
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
    painter.fillPath(path, QBrush(_PANEL))
    # 唯一一条边框线（居中压在内缩 2px 的轮廓上），底板内不再叠第二层描边
    pen = QPen(QColor(_BORDER), 1.0)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(rounded_path(plate.adjusted(0.5, 0.5, -0.5, -0.5),
                                  max(1.0, float(radius) - BASE_PLATE_RADIUS_DELTA - 0.5)))
    painter.restore()


def paint_liquid_sheet(painter: QPainter, rect, radius: float,
                       backdrop: QPixmap | None,
                       sheen_center: QPointF | None = None,
                       readable: bool = False) -> None:
    """抽屉 / 对话框等"浮在内容上"的完整液态玻璃面：缓存模糊位图 →
    底板（仅文字密集浮层）→ 本体 → 四层光学。调用方应已确保背景透明。

    readable=True 用于字体选择 / 起名助手这类文字密集弹层：先铺不透明底板
    （内缩 2px、圆角 −2、单边框线），再把玻璃层原样压在上面；不透明区域只
    损失内区背景透过，外圈 2px 玻璃带与三档光学完全保持。
    """
    r = QRectF(rect)
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    rounded_clip(painter, r, radius)
    if backdrop is not None and not backdrop.isNull():
        painter.drawPixmap(0, 0, backdrop)
    if readable:
        paint_base_plate(painter, r, radius)
    paint_liquid_layers(painter, r, radius,
                        body_strength=1.0, glow_strength=1.0,
                        sheen_center=sheen_center)
    painter.restore()


def paint_liquid_overlay(painter: QPainter, rect, radius: float, t: float,
                         sheen_center: QPointF | None = None) -> None:
    """卡片玻璃覆层：rest 轻、hover 拉满；选中态由调用方短路（不画玻璃光）。

    t=0(rest) → 1(hover)。卡片不铺模糊底图（其下是稳定页面底色），无外阴影。
    """
    r = QRectF(rect)
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    rounded_clip(painter, r, radius)
    tt = max(0.0, min(1.0, t))
    body_strength = 0.35 + 0.65 * tt
    glow_strength = 0.45 + 0.55 * tt
    sheen_strength = 0.25 + 0.75 * tt
    paint_liquid_layers(painter, r, radius,
                        body_strength=body_strength, glow_strength=glow_strength,
                        sheen_center=sheen_center, sheen_strength=sheen_strength)
    painter.restore()


def paint_reflection_only(painter: QPainter, rect, radius: float,
                          sheen_center: QPointF | None = None,
                          glow_strength: float = 1.0) -> None:
    """探针专用：只画反光层（① 顶部高光 + ③ 高光斑 + ② rim，全部 Plus）。"""
    r = QRectF(rect)
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    rounded_clip(painter, r, radius)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
    _paint_top_glow(painter, r, glow_strength)
    _paint_sheen(painter, r, sheen_center or _default_sheen(r), glow_strength)
    _paint_rim(painter, r, radius, glow_strength)
    painter.restore()


# ----------------------------------------------------------------------
# 高光斑指针跟随：180ms ease-out（一个 app 级单例跟踪所有液态表面）
# ----------------------------------------------------------------------
_SHEEN_MS = 180


class _SheenTracker(QObject):
    def __init__(self) -> None:
        super().__init__()
        # paint_target: 实际自绘并需要 update 的控件；hit 为命中判定根
        self._targets: dict[QWidget, QWidget] = {}
        self._inside: set[QWidget] = set()
        self._anims: dict[QWidget, QVariantAnimation] = {}
        self._installed = False

    def attach(self, paint_target: QWidget, hit_widget: QWidget | None = None) -> None:
        hit = hit_widget or paint_target
        if paint_target in self._targets:
            return
        self._targets[paint_target] = hit

        def _cleanup(*_args) -> None:
            self._targets.pop(paint_target, None)
            self._inside.discard(paint_target)
            self._anims.pop(paint_target, None)

        # widget 关闭销毁后必须立刻摘出，否则 C++ 事件回调命中悬空对象会原生崩溃
        paint_target.destroyed.connect(_cleanup)
        if hit is not paint_target:
            hit.destroyed.connect(_cleanup)
        try:
            # 只在命中根上开 WA_Hover：未开 hover 的子控件会把 HoverMove
            # 让给父级；避免递归 findChildren 给每个卡片的全部子控件置属性
            hit.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
            paint_target.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
            if not hasattr(paint_target, "_sheen_pos") or paint_target._sheen_pos is None:
                paint_target._sheen_pos = _default_sheen(QRectF(paint_target.rect()))
        except RuntimeError:
            self._targets.pop(paint_target, None)
            return
        if not self._installed:
            app = QApplication.instance()
            if app is not None:
                app.installEventFilter(self)
                self._installed = True

    def _animate(self, target: QWidget, dest: QPointF) -> None:
        anim = self._anims.get(target)
        if anim is None:
            anim = QVariantAnimation(target)
            anim.setDuration(_SHEEN_MS)
            from PySide6.QtCore import QEasingCurve

            anim.setEasingCurve(QEasingCurve.OutCubic)

            def _on_change(v) -> None:
                target._sheen_pos = QPointF(v)
                target.update()

            anim.valueChanged.connect(_on_change)
            self._anims[target] = anim
        cur = QPointF(getattr(target, "_sheen_pos", dest))
        anim.stop()
        anim.setStartValue(cur)
        anim.setEndValue(QPointF(dest))
        anim.start()

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        # 非玻璃风格（default/paper/order）：零成本早退，不遍历任何目标
        if not is_glass() or not self._targets:
            return False
        # C++ 虚函数回调：任何已销毁控件 / 事件异常都不得冒泡成原生崩溃
        try:
            self._dispatch(obj, event)
        except Exception:
            pass
        return False

    def _dispatch(self, obj, event) -> None:
        if not isinstance(obj, QWidget):
            return
        etype = event.type()
        if etype in (QEvent.Type.HoverMove, QEvent.Type.MouseMove):
            try:
                gpos = event.globalPosition().toPoint()
            except AttributeError:
                gpos = event.globalPos()
            for target, hit in list(self._targets.items()):
                try:
                    if not target.isVisible():
                        continue
                    inside = (obj is hit or hit.isAncestorOf(obj))
                    if inside:
                        local = hit.mapFromGlobal(gpos)
                        if hit.rect().contains(local):
                            self._inside.add(target)
                            # hit 与 paint_target 同尺寸（浮层 / overlay 都铺满）
                            self._animate(target, QPointF(local))
                        elif target in self._inside:
                            self._inside.discard(target)
                            self._animate(target, _default_sheen(QRectF(target.rect())))
                except RuntimeError:
                    self._targets.pop(target, None)
        elif etype in (QEvent.Type.HoverLeave, QEvent.Type.Leave):
            for target, hit in list(self._targets.items()):
                try:
                    if (obj is hit or hit.isAncestorOf(obj)) and target in self._inside:
                        self._inside.discard(target)
                        self._animate(target, _default_sheen(QRectF(target.rect())))
                except RuntimeError:
                    self._targets.pop(target, None)


_SHEEN_TRACKER = _SheenTracker()


def attach_sheen(paint_target: QWidget, hit_widget: QWidget | None = None) -> None:
    """给液态表面挂高光斑指针跟随（180ms ease-out，静止回偏左上）。

    始终低价挂接（仅 2 个 WA_Hover 属性 + 模块级跟踪），保证先在非玻璃风
    构造、后切到玻璃风的卡片也能跟随；事件过滤器在非玻璃风直接零成本早退。
    """
    _SHEEN_TRACKER.attach(paint_target, hit_widget)


# ----------------------------------------------------------------------
# 浮层卡片：show 时抓一次背后主窗口 → 降采样模糊缓存 → 自绘
# ----------------------------------------------------------------------
class LiquidCard(QWidget):
    """对话框 / 浮层卡片（液态玻璃）：show 时抓一次背后控件并模糊一次。"""

    def __init__(self, radius: float = 14.0, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("MessageBox")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._radius = radius
        self._backdrop: QPixmap | None = None
        self._grabbed = False
        self._sheen_pos: QPointF | None = None
        attach_sheen(self)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if not self._grabbed:
            self._grabbed = True
            self.rebuild_backdrop()

    def rebuild_backdrop(self) -> None:
        if not is_glass() or not blur_enabled():
            self._backdrop = None
            return
        dialog = self.window()
        source = dialog.parentWidget() if dialog is not None else None
        if source is None and dialog is not None:
            source = dialog
        if source is None:
            return
        origin = source.mapFromGlobal(self.mapToGlobal(QPoint(0, 0)))
        raw = grab_behind(self, source, origin=origin)
        self._backdrop = blur_pixmap(raw, blur_sigma())
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        if is_glass():
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            paint_liquid_sheet(painter, QRectF(self.rect()), self._radius,
                               self._backdrop, getattr(self, "_sheen_pos", None),
                               readable=getattr(self, "_readable", False))
            painter.end()
        else:
            super().paintEvent(event)


class ReadableCard(LiquidCard):
    """文字密集浮层专用（字体选择 / 起名助手 / 角色卡等）。

    结构：底板在下、玻璃在上。先铺不透明底板（内缩仅 2px、圆角 −2、
    暖白/深面板色、唯一一条边框线），再把当前三档玻璃原样压在上面
    （本体膜 0.55–0.72、2px 侧壁、rim、sheen、噪点、色散不变）。
    内区不再透正文；外圈 2px 玻璃带仍透背景，且没有第二条内框。
    """

    def __init__(self, radius: float = 14.0, parent=None) -> None:
        super().__init__(radius, parent)
        self._readable = True


def attach_backdrop_refresh(widget: QWidget, get_source, msec: int = 135) -> QTimer:
    """给自绘液态表面挂一个防抖失效器：位置 / 尺寸稳定（120–150ms）后重抓背景。

    get_source() → (source_widget, origin) 或 None；widget 需实现
    set_backdrop(pm) 方法。返回保活定时器（已挂为 widget 属性）。
    """
    timer = QTimer(widget)
    timer.setSingleShot(True)
    timer.setInterval(msec)

    def _rebuild() -> None:
        if not widget.isVisible() or not is_glass():
            return
        if not blur_enabled():
            widget.set_backdrop(None)
            widget.update()
            return
        info = get_source()
        if not info:
            return
        source, origin = info
        raw = grab_behind(widget, source, origin=origin)
        widget.set_backdrop(blur_pixmap(raw, blur_sigma()))
        widget.update()

    timer.timeout.connect(_rebuild)
    widget._backdrop_timer = timer
    return timer
