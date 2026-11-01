# -*- coding: utf-8 -*-
"""勋章美术：QPainter 程序绘制的水墨国风勋章（透明底、无外部背景）。

统一美术规范：
- 仅绘制勋章主体（金属外环 + 盘面 + 水墨纹样 + 铭牌），不带外部照片背景；
- 正向字数勋章（墨积成卷 → 鸿篇铸就）由浅金逐步升级为厚重暗金；
- 断更警示勋章（太监断更 → 文魂永寂）整体冷灰、暗沉，色调逐级加深；
- 救赎勋章（英雄重生）灰底搭配鎏金星火，对比强烈；
- 旧三枚（卧龙出山 / 挥斥方遒 / 坚持不懈）统一为水墨金铜风。

所有勋章按 key 缓存，medal_pixmap(key, size) 直接返回 QPixmap。
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
)

_CACHE: dict[tuple[str, int], QPixmap] = {}


def _qc(color: str, alpha: int) -> QColor:
    """带透明度的颜色（QColor 不支持 (str, alpha) 两参构造）。"""
    c = QColor(color)
    c.setAlpha(int(alpha))
    return c

# ---------------------------------------------------------------------------
# 美术规范表：shape(盘形) rim(外环金属) face(盘面) ink(水墨) motif(纹样)
#             plaque(铭牌) text(铭牌文字)
# ---------------------------------------------------------------------------
SPECS: dict[str, dict] = {
    # ---- 正向字数里程碑：浅金 → 厚重暗金 ----
    "words_30k": {
        "shape": "circle", "rim": ("#EED9A0", "#C0A054"), "face": ("#FCF6E4", "#F0E2B9"),
        "ink": "#2B2A26", "motif": "scroll", "plaque": "#8A6A28", "text": "#FBF0CE",
    },
    "words_100k": {
        "shape": "circle", "rim": ("#E0C078", "#A57C2E"), "face": ("#F7ECD0", "#E4D0A0"),
        "ink": "#2B2A26", "motif": "brush_scroll", "plaque": "#7A5D1F", "text": "#F9ECBE",
    },
    "words_300k": {
        "shape": "circle", "rim": ("#CBA64E", "#85641B"), "face": ("#EDDFB6", "#D5BE84"),
        "ink": "#26251F", "motif": "waves", "plaque": "#6A4F16", "text": "#F7E7AF",
    },
    "words_1m": {
        "shape": "hex", "rim": ("#B18C36", "#5F430E"), "face": ("#C9AC6C", "#8D6D28"),
        "ink": "#211F19", "motif": "book_mountain", "plaque": "#4E380C", "text": "#FFE49A",
    },
    # ---- 断更警示：冷灰暗沉，逐级加深 ----
    "hiatus_15d": {
        "shape": "circle", "rim": ("#C6CBD0", "#8C9299"), "face": ("#E4E7EA", "#C7CCD1"),
        "ink": "#3A3E43", "motif": "hat", "plaque": "#585D63", "text": "#E9EBED",
    },
    "hiatus_30d": {
        "shape": "circle", "rim": ("#B0B6BC", "#777D85"), "face": ("#CFD4D8", "#ABB1B8"),
        "ink": "#33373C", "motif": "stopped_scroll", "plaque": "#4C5157", "text": "#E4E6E9",
    },
    "hiatus_180d": {
        "shape": "shield", "rim": ("#989EA5", "#61676E"), "face": ("#B3B9BF", "#8F959C"),
        "ink": "#2C3035", "motif": "grave", "plaque": "#41464C", "text": "#DFE1E4",
    },
    "hiatus_365d": {
        "shape": "octa", "rim": ("#7F868D", "#484E55"), "face": ("#959BA2", "#71777E"),
        "ink": "#24272B", "motif": "tomb_star", "plaque": "#373B41", "text": "#D9DBDE",
    },
    # ---- 救赎：灰底鎏金星火 ----
    "rebirth": {
        "shape": "circle", "rim": ("#94805A", "#453E2C"), "face": ("#5C6065", "#3B3F44"),
        "ink": "#1E2023", "motif": "spark_star", "plaque": "#2C2F33", "text": "#F4D98E",
    },
    # ---- 旧三枚：水墨金铜风 ----
    "first_book": {
        "shape": "circle", "rim": ("#C9BFA8", "#8A8062"), "face": ("#F3EFE3", "#DCD5C0"),
        "ink": "#2E2C26", "motif": "sun_mountain", "plaque": "#6E6647", "text": "#F7F2DE",
    },
    "marathon": {
        "shape": "circle", "rim": ("#D2C08C", "#937A3C"), "face": ("#F6EFDA", "#E1D3AC"),
        "ink": "#2B2A24", "motif": "brush_sweep", "plaque": "#716022", "text": "#F8EFC9",
    },
    "persistent": {
        "shape": "circle", "rim": ("#C2C8BC", "#7E867B"), "face": ("#EFF1E7", "#D7DCCB"),
        "ink": "#2D302B", "motif": "moon_ring", "plaque": "#5C655A", "text": "#EFF2E7",
    },
}


# ---------------------------------------------------------------------------
# 盘形路径
# ---------------------------------------------------------------------------
def _shape_path(shape: str, rect: QRectF) -> QPainterPath:
    cx, cy = rect.center().x(), rect.center().y()
    w, h = rect.width(), rect.height()
    path = QPainterPath()
    if shape == "shield":
        r = w * 0.10
        path.moveTo(rect.left(), rect.top() + r)
        path.quadTo(rect.left(), rect.top(), rect.left() + r, rect.top())
        path.lineTo(rect.right() - r, rect.top())
        path.quadTo(rect.right(), rect.top(), rect.right(), rect.top() + r)
        path.lineTo(rect.right(), rect.top() + h * 0.62)
        path.quadTo(rect.right(), rect.bottom() - h * 0.10, cx, rect.bottom())
        path.quadTo(rect.left(), rect.bottom() - h * 0.10, rect.left(), rect.top() + h * 0.62)
        path.closeSubpath()
    elif shape in {"hex", "octa"}:
        import math

        n = 6 if shape == "hex" else 8
        offset = 0.0 if shape == "hex" else math.pi / n
        points = []
        for i in range(n):
            ang = -math.pi / 2 + i * 2 * math.pi / n + offset
            points.append(QPointF(cx + w / 2 * math.cos(ang), cy + h / 2 * math.sin(ang)))
        path.moveTo(points[0])
        for pt in points[1:]:
            path.lineTo(pt)
        path.closeSubpath()
    else:
        path.addEllipse(rect)
    return path


# ---------------------------------------------------------------------------
# 纹样基元
# ---------------------------------------------------------------------------
def _ink_blob(p: QPainter, cx: float, cy: float, r: float, color: str, alpha: int) -> None:
    """水墨晕染：低透明径向渐变圆斑。"""
    grad = QRadialGradient(cx, cy, r)
    c = QColor(color)
    c.setAlpha(alpha)
    grad.setColorAt(0.0, c)
    c2 = QColor(color)
    c2.setAlpha(0)
    grad.setColorAt(1.0, c2)
    p.save()
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(grad))
    p.drawEllipse(QPointF(cx, cy), r, r)
    p.restore()


def _brush_pen(p: QPainter, x: float, y: float, length: float, angle: float,
               shaft: str, tip: str) -> None:
    """毛笔：笔杆 + 笔锋（angle 为笔锋指向的角度，单位度，0 = 向下）。"""
    import math

    p.save()
    p.translate(x, y)
    p.rotate(angle)
    # 笔锋（水滴形）
    tip_h = length * 0.30
    shaft_len = length - tip_h
    path = QPainterPath()
    path.moveTo(-length * 0.055, -shaft_len)
    path.quadTo(-length * 0.075, -tip_h * 0.4, 0, 0)
    path.quadTo(length * 0.075, -tip_h * 0.4, length * 0.055, -shaft_len)
    path.closeSubpath()
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(tip))
    p.drawPath(path)
    # 笔杆
    rod = QRectF(-length * 0.038, -length, length * 0.076, shaft_len + length * 0.06)
    rod_grad = QLinearGradient(rod.topLeft(), rod.topRight())
    rod_grad.setColorAt(0.0, QColor(shaft))
    rod_grad.setColorAt(0.5, QColor("#E8D9B8"))
    rod_grad.setColorAt(1.0, QColor(shaft))
    p.setBrush(QBrush(rod_grad))
    p.drawRoundedRect(rod, length * 0.03, length * 0.03)
    p.restore()


def _scroll_roll(p: QPainter, rect: QRectF, paper: str, wood: str) -> None:
    """横置卷轴：纸卷 + 两端木轴。"""
    p.save()
    p.setPen(QPen(QColor(0, 0, 0, 70), 1))
    p.setBrush(QColor(paper))
    p.drawRoundedRect(rect, rect.height() * 0.18, rect.height() * 0.18)
    end_w = rect.height() * 0.42
    for rx in (rect.left() - end_w * 0.4, rect.right() - end_w * 0.6):
        rod = QRectF(rx, rect.top() - rect.height() * 0.10, end_w, rect.height() * 1.2)
        grad = QLinearGradient(rod.topLeft(), rod.bottomLeft())
        grad.setColorAt(0.0, QColor(wood).lighter(125))
        grad.setColorAt(0.5, QColor(wood))
        grad.setColorAt(1.0, QColor(wood).darker(135))
        p.setPen(QPen(QColor(0, 0, 0, 80), 1))
        p.setBrush(QBrush(grad))
        p.drawRoundedRect(rod, end_w / 2, end_w / 2)
    p.restore()


def _star_path(cx: float, cy: float, r_out: float, r_in: float) -> QPainterPath:
    import math

    path = QPainterPath()
    for i in range(10):
        r = r_out if i % 2 == 0 else r_in
        ang = -math.pi / 2 + i * math.pi / 5
        pt = QPointF(cx + r * math.cos(ang), cy + r * math.sin(ang))
        if i == 0:
            path.moveTo(pt)
        else:
            path.lineTo(pt)
    path.closeSubpath()
    return path


# ---------------------------------------------------------------------------
# 各勋章纹样（坐标均基于盘面 rect）
# ---------------------------------------------------------------------------
def _m_scroll(p: QPainter, r: QRectF, spec: dict) -> None:
    """墨积成卷：卷轴成卷 + 落墨。"""
    ink = spec["ink"]
    _ink_blob(p, r.center().x(), r.center().y() + r.height() * 0.30, r.width() * 0.30, ink, 26)
    _scroll_roll(p, QRectF(r.left() + r.width() * 0.16, r.top() + r.height() * 0.24,
                           r.width() * 0.68, r.height() * 0.20), "#FBF3DC", "#B99653")
    _scroll_roll(p, QRectF(r.left() + r.width() * 0.26, r.top() + r.height() * 0.47,
                           r.width() * 0.56, r.height() * 0.17), "#F6ECCB", "#A8873F")
    p.setPen(QPen(_qc(ink, 200), r.height() * 0.035, Qt.SolidLine, Qt.RoundCap))
    p.drawLine(QPointF(r.center().x() - r.width() * 0.16, r.top() + r.height() * 0.74),
               QPointF(r.center().x() + r.width() * 0.05, r.top() + r.height() * 0.74))


def _m_brush_scroll(p: QPainter, r: QRectF, spec: dict) -> None:
    """笔筑千章：摊开书卷 + 直立毛笔。"""
    ink = spec["ink"]
    _ink_blob(p, r.center().x(), r.center().y() + r.height() * 0.28, r.width() * 0.32, ink, 24)
    _scroll_roll(p, QRectF(r.left() + r.width() * 0.14, r.top() + r.height() * 0.52,
                           r.width() * 0.72, r.height() * 0.22), "#FBF3DC", "#A8873F")
    _brush_pen(p, r.center().x() + r.width() * 0.06, r.center().y() + r.height() * 0.02,
               r.height() * 0.62, 12, "#6E4F28", ink)


def _m_waves(p: QPainter, r: QRectF, spec: dict) -> None:
    """瀚墨连篇：墨浪翻卷 + 群卷浮沉。"""
    ink = spec["ink"]
    _ink_blob(p, r.center().x(), r.center().y() + r.height() * 0.24, r.width() * 0.34, ink, 30)
    pen = QPen(_qc(ink, 215), r.height() * 0.045)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    for i, (dy, sc) in enumerate(((0.10, 1.0), (0.34, 0.8), (0.56, 0.62))):
        base_y = r.top() + r.height() * (0.52 + dy * 0.6)
        w = r.width() * 0.62 * sc
        path = QPainterPath()
        path.moveTo(r.center().x() - w / 2, base_y)
        path.cubicTo(r.center().x() - w / 4, base_y - r.height() * 0.16 * sc,
                     r.center().x() - w / 8, base_y + r.height() * 0.10 * sc,
                     r.center().x(), base_y - r.height() * 0.05 * sc)
        path.cubicTo(r.center().x() + w / 8, base_y - r.height() * 0.18 * sc,
                     r.center().x() + w / 4, base_y + r.height() * 0.08 * sc,
                     r.center().x() + w / 2, base_y - r.height() * 0.06 * sc)
        p.drawPath(path)
    _scroll_roll(p, QRectF(r.left() + r.width() * 0.18, r.top() + r.height() * 0.22,
                           r.width() * 0.34, r.height() * 0.13), "#FBF3DC", "#8F6E2A")


def _m_book_mountain(p: QPainter, r: QRectF, spec: dict) -> None:
    """鸿篇铸就：书册堆山 + 鎏金星光。"""
    ink = spec["ink"]
    _ink_blob(p, r.center().x(), r.bottom() - r.height() * 0.20, r.width() * 0.34, ink, 34)
    tiers = (0.66, 0.50, 0.34)
    colors = ("#8A6420", "#A67C2A", "#C29A3E")
    for i, frac in enumerate(tiers):
        w = r.width() * frac
        bh = r.height() * 0.115
        bx = r.center().x() - w / 2
        by = r.bottom() - r.height() * 0.24 - i * (bh + r.height() * 0.015)
        grad = QLinearGradient(QPointF(bx, by), QPointF(bx, by + bh))
        grad.setColorAt(0.0, QColor(colors[i]).lighter(130))
        grad.setColorAt(1.0, QColor(colors[i]).darker(125))
        p.setPen(QPen(QColor(0, 0, 0, 90), 1))
        p.setBrush(QBrush(grad))
        p.drawRoundedRect(QRectF(bx, by, w, bh), 3, 3)
    p.setPen(QPen(QColor("#FFE9A8"), r.height() * 0.03, Qt.SolidLine, Qt.RoundCap))
    for sx, sy, sr in ((0.30, 0.30, 0.05), (0.68, 0.24, 0.065), (0.52, 0.14, 0.045)):
        cx, cy = r.left() + r.width() * sx, r.top() + r.height() * sy
        p.drawLine(QPointF(cx - sr * r.width(), cy), QPointF(cx + sr * r.width(), cy))
        p.drawLine(QPointF(cx, cy - sr * r.width()), QPointF(cx, cy + sr * r.width()))


def _m_hat(p: QPainter, r: QRectF, spec: dict) -> None:
    """太监断更：乌纱官帽 + 暂停符。"""
    ink = spec["ink"]
    _ink_blob(p, r.center().x(), r.bottom() - r.height() * 0.22, r.width() * 0.30, ink, 26)
    cx = r.center().x()
    dome_b = r.top() + r.height() * 0.58
    path = QPainterPath()
    path.moveTo(cx - r.width() * 0.26, dome_b)
    path.cubicTo(cx - r.width() * 0.30, r.top() + r.height() * 0.16,
                 cx + r.width() * 0.30, r.top() + r.height() * 0.16,
                 cx + r.width() * 0.26, dome_b)
    path.closeSubpath()
    grad = QLinearGradient(QPointF(cx - r.width() * 0.26, r.top()), QPointF(cx + r.width() * 0.26, dome_b))
    grad.setColorAt(0.0, QColor("#4A4F55"))
    grad.setColorAt(0.5, QColor("#6E747B"))
    grad.setColorAt(1.0, QColor("#3F444A"))
    p.setPen(QPen(QColor(0, 0, 0, 80), 1))
    p.setBrush(QBrush(grad))
    p.drawPath(path)
    # 两端帽翅
    wing_w, wing_h = r.width() * 0.16, r.height() * 0.085
    wing_y = r.top() + r.height() * 0.30
    for sx in (-1, 1):
        wing = QRectF(cx + sx * r.width() * 0.30 - (wing_w if sx < 0 else 0),
                      wing_y, wing_w, wing_h)
        p.setBrush(QColor("#565C63"))
        p.drawRoundedRect(wing, wing_h / 2, wing_h / 2)
    # 暂停符（金色）
    bar_w = r.width() * 0.045
    bar_h = r.height() * 0.14
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#D8C684"))
    p.drawRoundedRect(QRectF(cx - bar_w * 1.6, r.top() + r.height() * 0.30, bar_w, bar_h), 2, 2)
    p.drawRoundedRect(QRectF(cx + bar_w * 0.6, r.top() + r.height() * 0.30, bar_w, bar_h), 2, 2)


def _m_stopped_scroll(p: QPainter, r: QRectF, spec: dict) -> None:
    """久搁弃章：搁置卷轴 + 朱印。"""
    ink = spec["ink"]
    _ink_blob(p, r.center().x() + r.width() * 0.10, r.bottom() - r.height() * 0.22, r.width() * 0.26, ink, 40)
    _scroll_roll(p, QRectF(r.left() + r.width() * 0.16, r.top() + r.height() * 0.38,
                           r.width() * 0.68, r.height() * 0.22), "#EDE9DE", "#8E8E8E")
    # 朱红停印
    seal = QRectF(r.center().x() - r.width() * 0.09, r.top() + r.height() * 0.42,
                  r.width() * 0.18, r.width() * 0.18)
    p.setPen(QPen(QColor("#B24A3A"), r.width() * 0.02))
    p.setBrush(Qt.NoBrush)
    p.drawEllipse(seal)
    p.setPen(QPen(QColor("#B24A3A"), r.width() * 0.022, Qt.SolidLine, Qt.RoundCap))
    cy = seal.center().y()
    p.drawLine(QPointF(seal.left() + seal.width() * 0.28, cy), QPointF(seal.right() - seal.width() * 0.28, cy))


def _m_grave(p: QPainter, r: QRectF, spec: dict) -> None:
    """旧稿荒坟：荒冢 + 残卷 + 冷月。"""
    ink = spec["ink"]
    # 月
    p.save()
    p.setPen(Qt.NoPen)
    moon = QColor("#E8EAE6")
    moon.setAlpha(150)
    p.setBrush(moon)
    p.drawEllipse(QPointF(r.right() - r.width() * 0.26, r.top() + r.height() * 0.22),
                  r.width() * 0.11, r.width() * 0.11)
    p.setBrush(QColor(spec["face"][0]))
    p.drawEllipse(QPointF(r.right() - r.width() * 0.30, r.top() + r.height() * 0.19),
                  r.width() * 0.10, r.width() * 0.10)
    p.restore()
    _ink_blob(p, r.center().x(), r.bottom() - r.height() * 0.18, r.width() * 0.36, ink, 32)
    # 冢
    mound = QPainterPath()
    mound.moveTo(r.left() + r.width() * 0.16, r.bottom() - r.height() * 0.24)
    mound.quadTo(r.center().x(), r.top() + r.height() * 0.34,
                 r.right() - r.width() * 0.16, r.bottom() - r.height() * 0.24)
    p.setPen(QPen(QColor(0, 0, 0, 70), 1))
    p.setBrush(QColor("#7C8288"))
    p.drawPath(mound)
    # 残稿
    p.save()
    p.translate(r.center().x() - r.width() * 0.02, r.bottom() - r.height() * 0.28)
    p.rotate(-14)
    p.setPen(QPen(QColor(0, 0, 0, 60), 1))
    p.setBrush(QColor("#E6E2D4"))
    p.drawRect(QRectF(-r.width() * 0.17, -r.height() * 0.10, r.width() * 0.34, r.height() * 0.19))
    p.restore()


def _m_tomb_star(p: QPainter, r: QRectF, spec: dict) -> None:
    """文魂永寂：裂碑 + 熄灭文星。"""
    ink = spec["ink"]
    _ink_blob(p, r.center().x(), r.bottom() - r.height() * 0.20, r.width() * 0.34, ink, 36)
    # 碑
    stone = QRectF(r.center().x() - r.width() * 0.17, r.top() + r.height() * 0.26,
                   r.width() * 0.34, r.height() * 0.50)
    path = QPainterPath()
    path.moveTo(stone.left(), stone.bottom())
    path.lineTo(stone.left(), stone.top() + stone.height() * 0.28)
    path.quadTo(stone.center().x(), stone.top() - stone.height() * 0.06,
                stone.right(), stone.top() + stone.height() * 0.28)
    path.lineTo(stone.right(), stone.bottom())
    path.closeSubpath()
    grad = QLinearGradient(stone.topLeft(), stone.bottomRight())
    grad.setColorAt(0.0, QColor("#585D63"))
    grad.setColorAt(1.0, QColor("#3A3E44"))
    p.setPen(QPen(QColor(0, 0, 0, 90), 1))
    p.setBrush(QBrush(grad))
    p.drawPath(path)
    # 裂纹
    p.setPen(QPen(QColor("#22252A"), r.height() * 0.014))
    p.drawLine(QPointF(stone.center().x(), stone.top() + stone.height() * 0.30),
               QPointF(stone.center().x() - stone.width() * 0.14, stone.center().y()))
    p.drawLine(QPointF(stone.center().x() - stone.width() * 0.14, stone.center().y()),
               QPointF(stone.center().x() + stone.width() * 0.06, stone.bottom() - stone.height() * 0.12))
    # 将熄的星（暗金微光）
    _ink_blob(p, r.center().x(), r.top() + r.height() * 0.17, r.width() * 0.10, "#D8C684", 70)
    p.setPen(QPen(QColor("#C9B878"), r.height() * 0.022, Qt.SolidLine, Qt.RoundCap))
    cx, cy = r.center().x(), r.top() + r.height() * 0.17
    sr = r.width() * 0.045
    p.drawLine(QPointF(cx - sr, cy), QPointF(cx + sr, cy))
    p.drawLine(QPointF(cx, cy - sr), QPointF(cx, cy + sr))


def _m_spark_star(p: QPainter, r: QRectF, spec: dict) -> None:
    """英雄重生：灰岩裂星 + 鎏金星火（灰底与金光强对比）。"""
    cx, cy = r.center().x(), r.center().y() - r.height() * 0.06
    r_out = r.width() * 0.30
    # 星火辉光
    glow = QRadialGradient(cx, cy, r_out * 1.5)
    glow.setColorAt(0.0, QColor(255, 216, 130, 235))
    glow.setColorAt(0.45, QColor(214, 160, 60, 130))
    glow.setColorAt(1.0, QColor(214, 160, 60, 0))
    p.save()
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(glow))
    p.drawEllipse(QPointF(cx, cy), r_out * 1.5, r_out * 1.5)
    p.restore()
    # 灰岩星体（裂开）：左右两半错位
    star = _star_path(cx, cy, r_out, r_out * 0.46)
    p.save()
    p.setClipPath(star)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#6E747B"))
    p.drawRect(r)
    p.setBrush(QColor("#4E545A"))
    p.drawPolygon([QPointF(cx, cy - r_out * 1.2), QPointF(cx - r_out * 1.2, cy + r_out * 1.2),
                   QPointF(cx + r_out * 0.2, cy + r_out * 0.2)])
    p.restore()
    p.setPen(QPen(QColor("#33373C"), r.width() * 0.012))
    p.setBrush(Qt.NoBrush)
    p.drawPath(star)
    # 裂缝中透出的金光
    p.setPen(QPen(QColor("#FFD882"), r.width() * 0.02, Qt.SolidLine, Qt.RoundCap))
    p.drawLine(QPointF(cx - r_out * 0.1, cy - r_out * 0.5), QPointF(cx + r_out * 0.05, cy))
    p.drawLine(QPointF(cx + r_out * 0.05, cy), QPointF(cx - r_out * 0.08, cy + r_out * 0.55))
    # 火星四溅
    p.setBrush(QColor("#FFDf9A"))
    p.setPen(Qt.NoPen)
    for sx, sy, sr in ((-0.42, -0.30, 0.020), (0.44, -0.22, 0.016), (-0.34, 0.34, 0.014),
                       (0.38, 0.40, 0.022), (0.05, -0.46, 0.013)):
        p.drawEllipse(QPointF(cx + r.width() * sx, cy + r.height() * sy),
                      r.width() * sr, r.width() * sr)


def _m_sun_mountain(p: QPainter, r: QRectF, spec: dict) -> None:
    """卧龙出山：远山出日 + 云脚。"""
    ink = spec["ink"]
    _ink_blob(p, r.center().x(), r.bottom() - r.height() * 0.20, r.width() * 0.30, ink, 24)
    # 初日
    p.save()
    p.setPen(Qt.NoPen)
    sun = _qc("#C9994C", 200)
    p.setBrush(sun)
    p.drawEllipse(QPointF(r.center().x(), r.top() + r.height() * 0.30),
                  r.width() * 0.13, r.width() * 0.13)
    p.restore()
    # 两重山
    for i, (frac, hfrac, alpha) in enumerate(((0.86, 0.26, 90), (0.62, 0.20, 150))):
        path = QPainterPath()
        left = r.center().x() - r.width() * frac / 2
        right = r.center().x() + r.width() * frac / 2
        peak = r.top() + r.height() * (0.42 + i * 0.12)
        base = r.bottom() - r.height() * 0.22
        path.moveTo(left, base)
        path.quadTo(r.center().x() - r.width() * frac * 0.18, peak,
                    r.center().x(), base - r.height() * (0.26 - i * 0.08))
        path.quadTo(r.center().x() + r.width() * frac * 0.18, peak,
                    right, base)
        path.closeSubpath()
        c = QColor(ink)
        c.setAlpha(alpha)
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        p.drawPath(path)


def _m_brush_sweep(p: QPainter, r: QRectF, spec: dict) -> None:
    """挥斥方遒：大写意横扫一笔。"""
    ink = spec["ink"]
    _ink_blob(p, r.center().x() - r.width() * 0.12, r.center().y() + r.height() * 0.10,
              r.width() * 0.30, ink, 40)
    # 一笔横扫：由粗到细的弯月形
    path = QPainterPath()
    y = r.center().y() + r.height() * 0.12
    path.moveTo(r.left() + r.width() * 0.16, y - r.height() * 0.10)
    path.quadTo(r.center().x(), y - r.height() * 0.02,
                r.right() - r.width() * 0.16, y - r.height() * 0.22)
    path.quadTo(r.center().x(), y + r.height() * 0.10,
                r.left() + r.width() * 0.16, y - r.height() * 0.10)
    path.closeSubpath()
    grad = QLinearGradient(QPointF(r.left(), y), QPointF(r.right(), y))
    gc = QColor(ink)
    grad.setColorAt(0.0, gc)
    gc2 = QColor(ink)
    gc2.setAlpha(60)
    grad.setColorAt(1.0, gc2)
    p.save()
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(grad))
    p.drawPath(path)
    p.restore()
    _brush_pen(p, r.right() - r.width() * 0.22, r.center().y() - r.height() * 0.10,
               r.height() * 0.56, -38, "#6E4F28", ink)


def _m_moon_ring(p: QPainter, r: QRectF, spec: dict) -> None:
    """坚持不懈：环列月相（日日不辍）。"""
    import math

    ink = spec["ink"]
    cx, cy = r.center().x(), r.center().y() - r.height() * 0.02
    ring_r = r.width() * 0.26
    _ink_blob(p, cx, cy + r.height() * 0.34, r.width() * 0.28, ink, 20)
    for i in range(8):
        ang = -math.pi / 2 + i * math.pi / 4
        mx, my = cx + ring_r * math.cos(ang), cy + ring_r * math.sin(ang)
        full = i % 2 == 0
        mr = r.width() * (0.055 if full else 0.040)
        p.setPen(QPen(_qc(ink, 170), r.width() * 0.012))
        if full:
            p.setBrush(_qc(ink, 190))
        else:
            p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(mx, my), mr, mr)
    # 环心：一枚小笔锋
    p.setPen(QPen(_qc(ink, 220), r.width() * 0.030, Qt.SolidLine, Qt.RoundCap))
    p.drawLine(QPointF(cx, cy - r.height() * 0.10), QPointF(cx, cy + r.height() * 0.10))


MOTIFS = {
    "scroll": _m_scroll,
    "brush_scroll": _m_brush_scroll,
    "waves": _m_waves,
    "book_mountain": _m_book_mountain,
    "hat": _m_hat,
    "stopped_scroll": _m_stopped_scroll,
    "grave": _m_grave,
    "tomb_star": _m_tomb_star,
    "spark_star": _m_spark_star,
    "sun_mountain": _m_sun_mountain,
    "brush_sweep": _m_brush_sweep,
    "moon_ring": _m_moon_ring,
}


# ---------------------------------------------------------------------------
# 总绘制入口
# ---------------------------------------------------------------------------
def medal_pixmap(key: str, size: int = 96) -> QPixmap:
    """返回勋章（透明底，仅主体），按 (key, size) 缓存。

    优先加载 assets/medals/<key>.png 图片资源（AI 生成的水墨勋章图，已去背
    景）；资源缺失时回退到 QPainter 程序绘制。
    """
    cache_key = (str(key), int(size))
    if cache_key in _CACHE:
        return _CACHE[cache_key]
    pix = QPixmap(int(size), int(size))
    pix.fill(Qt.transparent)

    # 1) 图片资源优先
    from app import config

    asset = config.ASSETS_DIR / "medals" / f"{key}.png"
    if asset.exists():
        source = QPixmap(str(asset))
        if not source.isNull():
            pix = source.scaled(
                int(size), int(size), Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            _CACHE[cache_key] = pix
            return pix

    # 2) 程序绘制回退
    spec = SPECS.get(str(key))
    if spec is None:
        _CACHE[cache_key] = pix
        return pix

    size = int(size)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing, True)
    margin = size * 0.045
    rect = QRectF(margin, margin, size - 2 * margin, size - 2 * margin)
    shape = str(spec.get("shape") or "circle")

    # 外环（金属渐变）
    rim = QLinearGradient(rect.topLeft(), rect.bottomRight())
    rim.setColorAt(0.0, QColor(spec["rim"][0]))
    rim.setColorAt(1.0, QColor(spec["rim"][1]))
    p.setPen(QPen(QColor(0, 0, 0, 60), size * 0.008))
    p.setBrush(QBrush(rim))
    p.drawPath(_shape_path(shape, rect))

    # 内盘面
    inset = rect.width() * 0.075
    face_rect = rect.adjusted(inset, inset, -inset, -inset)
    face = QLinearGradient(face_rect.topLeft(), face_rect.bottomLeft())
    face.setColorAt(0.0, QColor(spec["face"][0]))
    face.setColorAt(1.0, QColor(spec["face"][1]))
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(face))
    p.drawPath(_shape_path(shape, face_rect))

    # 水墨底晕（左右两团，营造宣纸洇墨感）
    ink = str(spec.get("ink") or "#2B2A26")
    _ink_blob(p, face_rect.left() + face_rect.width() * 0.22,
              face_rect.top() + face_rect.height() * 0.30, face_rect.width() * 0.26, ink, 16)
    _ink_blob(p, face_rect.right() - face_rect.width() * 0.24,
              face_rect.bottom() - face_rect.height() * 0.34, face_rect.width() * 0.24, ink, 12)

    # 纹样（裁剪进盘面）
    painter_motif = MOTIFS.get(str(spec.get("motif") or ""))
    if painter_motif is not None:
        p.save()
        p.setClipPath(_shape_path(shape, face_rect))
        painter_motif(p, face_rect, spec)
        p.restore()

    # 内缘细线
    p.setPen(QPen(QColor(0, 0, 0, 42), max(1.0, size * 0.007)))
    p.setBrush(Qt.NoBrush)
    p.drawPath(_shape_path(shape, face_rect))

    # 铭牌（底部缎带形）+ 成就名
    from app.services.achievements import ACHIEVEMENTS

    name = str((ACHIEVEMENTS.get(str(key)) or {}).get("name") or "")
    if name:
        pw, ph = size * 0.70, size * 0.185
        plaque = QRectF((size - pw) / 2, size * 0.735, pw, ph)
        p.setPen(QPen(QColor(0, 0, 0, 90), size * 0.006))
        p.setBrush(QColor(spec["plaque"]))
        p.drawRoundedRect(plaque, ph * 0.32, ph * 0.32)
        p.setPen(QPen(QColor(spec["text"]), size * 0.004))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(plaque.adjusted(ph * 0.10, ph * 0.12, -ph * 0.10, -ph * 0.12),
                          ph * 0.24, ph * 0.24)
        font = QFont()
        font.setPixelSize(max(9, int(ph * 0.52)))
        font.setBold(True)
        p.setFont(font)
        p.setPen(QPen(QColor(spec["text"])))
        p.drawText(plaque, Qt.AlignCenter, name)
    p.end()

    _CACHE[cache_key] = pix
    return pix
