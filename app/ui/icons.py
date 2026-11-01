# -*- coding: utf-8 -*-
"""矢量图标：全部用 QPainter 现场绘制，项目不依赖任何图片资源。

这样打包成 exe 时只需要代码本身，也方便跟随主题切换颜色。
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QIcon,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)

CANVAS = 24.0  # 图标设计坐标系为 24x24


class IconFactory:
    """按指定颜色生成整套线性图标。"""

    def __init__(self, color: str) -> None:
        self.color = QColor(color)
        self._cache: dict[tuple[str, int], QIcon] = {}

    def icon(self, name: str, size: int = 18) -> QIcon:
        key = (name, size)
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        pixmap = QPixmap(size * 2, size * 2)
        pixmap.setDevicePixelRatio(2.0)
        pixmap.fill(Qt.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.scale(size * 2 / CANVAS, size * 2 / CANVAS)

        pen = QPen(self.color)
        pen.setWidthF(1.6)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)

        self._draw(painter, name)
        painter.end()

        result = QIcon(pixmap)
        self._cache[key] = result
        return result

    # ------------------------------------------------------------------
    def _draw(self, p: QPainter, name: str) -> None:
        if name == "shelf":
            p.drawRoundedRect(QRectF(3.8, 6, 4.4, 14), 1, 1)
            p.drawRoundedRect(QRectF(9.8, 4, 4.4, 16), 1, 1)
            p.drawRoundedRect(QRectF(15.8, 7, 4.4, 13), 1, 1)
            p.drawLine(QPointF(3, 21), QPointF(21, 21))

        elif name == "toc":
            for y in (7, 12, 17):
                p.drawEllipse(QPointF(5.6, y), 1.0, 1.0)
                p.drawLine(QPointF(9, y), QPointF(20.5, y))

        elif name == "undo":
            self._draw_undo(p)
        elif name == "redo":
            p.save()
            p.translate(CANVAS, 0)
            p.scale(-1, 1)
            self._draw_undo(p)
            p.restore()

        elif name == "format":
            p.drawLine(QPointF(5, 19), QPointF(14.2, 9.8))
            p.drawLine(QPointF(16.8, 4), QPointF(16.8, 9.4))
            p.drawLine(QPointF(14.1, 6.7), QPointF(19.5, 6.7))
            p.drawLine(QPointF(19.8, 13), QPointF(19.8, 16.6))
            p.drawLine(QPointF(18, 14.8), QPointF(21.6, 14.8))

        elif name == "find":
            p.drawEllipse(QRectF(5, 5, 10, 10))
            p.drawLine(QPointF(13.4, 13.4), QPointF(19.4, 19.4))

        elif name == "search":
            p.drawEllipse(QRectF(4.5, 4.5, 10, 10))
            p.drawLine(QPointF(7.4, 8.2), QPointF(11.6, 8.2))
            p.drawLine(QPointF(7.4, 11), QPointF(10.6, 11))
            p.drawLine(QPointF(13.2, 13.2), QPointF(19.4, 19.4))
            p.drawLine(QPointF(17.6, 3.2), QPointF(17.6, 7.2))
            p.drawLine(QPointF(15.6, 5.2), QPointF(19.6, 5.2))

        elif name == "theme":
            p.drawEllipse(QRectF(5, 5, 14, 14))
            path = QPainterPath()
            path.moveTo(12, 5)
            path.arcTo(QRectF(5, 5, 14, 14), 90, 180)
            path.closeSubpath()
            p.setBrush(self.color)
            p.setPen(Qt.NoPen)
            p.drawPath(path)

        elif name == "font":
            font = QFont("Segoe UI")
            font.setPixelSize(16)
            font.setBold(True)
            p.setFont(font)
            p.drawText(QRectF(0, 1.5, 24, 20), Qt.AlignCenter, "A")

        elif name == "spacing":
            for y in (6.5, 11, 15.5):
                p.drawLine(QPointF(3.6, y), QPointF(15, y))
            p.drawLine(QPointF(18.6, 5), QPointF(18.6, 19))
            p.drawLine(QPointF(18.6, 5), QPointF(16.2, 7.4))
            p.drawLine(QPointF(18.6, 5), QPointF(21, 7.4))
            p.drawLine(QPointF(18.6, 19), QPointF(16.2, 16.6))
            p.drawLine(QPointF(18.6, 19), QPointF(21, 16.6))

        elif name == "history":
            p.drawEllipse(QRectF(5, 5, 14, 14))
            p.drawLine(QPointF(12, 12), QPointF(12, 8))
            p.drawLine(QPointF(12, 12), QPointF(15.2, 13.6))

        elif name == "count":
            font = QFont("Microsoft YaHei")
            font.setPixelSize(15)
            font.setBold(True)
            p.setFont(font)
            p.drawText(QRectF(0, 2, 24, 19), Qt.AlignCenter, "字")

        elif name == "speed":
            p.drawArc(QRectF(4.5, 6, 15, 15), 0, 180 * 16)
            p.drawLine(QPointF(12, 15.5), QPointF(16.3, 9.2))
            p.drawLine(QPointF(4.5, 15.5), QPointF(19.5, 15.5))

        elif name == "name":
            p.drawEllipse(QRectF(9, 4.5, 6, 6))
            p.drawArc(QRectF(5, 11.5, 14, 11), 0, 180 * 16)

        elif name == "settings":
            p.drawEllipse(QRectF(4.5, 4.5, 15, 15))
            for i in range(8):
                angle = math.radians(i * 45)
                p.drawLine(
                    QPointF(12 + 7.5 * math.cos(angle), 12 + 7.5 * math.sin(angle)),
                    QPointF(12 + 9.4 * math.cos(angle), 12 + 9.4 * math.sin(angle)),
                )
            p.setBrush(self.color)
            p.setPen(Qt.NoPen)
            p.drawEllipse(QRectF(9.6, 9.6, 4.8, 4.8))

        elif name == "top":
            p.drawLine(QPointF(12, 19.4), QPointF(12, 5.2))
            p.drawLine(QPointF(12, 5.2), QPointF(6.4, 10.8))
            p.drawLine(QPointF(12, 5.2), QPointF(17.6, 10.8))

        elif name == "bottom":
            p.drawLine(QPointF(12, 4.6), QPointF(12, 18.8))
            p.drawLine(QPointF(12, 18.8), QPointF(6.4, 13.2))
            p.drawLine(QPointF(12, 18.8), QPointF(17.6, 13.2))

        elif name == "close":
            p.drawLine(QPointF(6.5, 6.5), QPointF(17.5, 17.5))
            p.drawLine(QPointF(17.5, 6.5), QPointF(6.5, 17.5))

        elif name == "add":
            p.drawLine(QPointF(12, 5.5), QPointF(12, 18.5))
            p.drawLine(QPointF(5.5, 12), QPointF(18.5, 12))

        elif name == "edit":
            p.drawLine(QPointF(5.5, 18.5), QPointF(16.5, 7.5))
            p.drawLine(QPointF(8, 20.5), QPointF(18.5, 10))
            p.drawLine(QPointF(5.5, 18.5), QPointF(8, 20.5))
            p.drawLine(QPointF(14.6, 5.4), QPointF(18.6, 9.4))

        elif name == "trash":
            p.drawLine(QPointF(4.8, 7.8), QPointF(19.2, 7.8))
            p.drawLine(QPointF(9.2, 5), QPointF(14.8, 5))
            p.drawRoundedRect(QRectF(7, 7.8, 10, 12.4), 1.5, 1.5)
            p.drawLine(QPointF(10.5, 11), QPointF(10.5, 17))
            p.drawLine(QPointF(13.5, 11), QPointF(13.5, 17))

        elif name == "refresh":
            p.drawArc(QRectF(5, 5, 14, 14), 40, 280)
            p.drawLine(QPointF(18.4, 4.8), QPointF(18.2, 9.2))
            p.drawLine(QPointF(18.4, 4.8), QPointF(14.2, 5.4))

        elif name == "restore":
            p.drawArc(QRectF(5, 5, 14, 14), 140, 280)
            p.drawLine(QPointF(5.6, 4.8), QPointF(5.8, 9.2))
            p.drawLine(QPointF(5.6, 4.8), QPointF(9.8, 5.4))

        elif name == "copy":
            p.drawRoundedRect(QRectF(8, 4, 12, 12), 2, 2)
            p.drawRoundedRect(QRectF(4, 8, 12, 12), 2, 2)

        elif name == "book":
            p.drawRoundedRect(QRectF(4, 4.5, 16, 15), 2, 2)
            p.drawLine(QPointF(8, 4.5), QPointF(8, 19.5))
            p.drawLine(QPointF(11, 9), QPointF(17, 9))
            p.drawLine(QPointF(11, 12.5), QPointF(17, 12.5))

        elif name in ("volume", "folder"):
            path = QPainterPath()
            path.moveTo(3.5, 7)
            path.lineTo(9.5, 7)
            path.lineTo(11.5, 9.5)
            path.lineTo(20.5, 9.5)
            path.lineTo(20.5, 19)
            path.lineTo(3.5, 19)
            path.closeSubpath()
            p.drawPath(path)

        elif name == "chapter":
            p.drawRoundedRect(QRectF(5.5, 3.5, 13, 17), 1.5, 1.5)
            for y in (8, 11.5, 15):
                p.drawLine(QPointF(8.5, y), QPointF(15.5, y))

        elif name == "dots":
            p.setBrush(self.color)
            p.setPen(Qt.NoPen)
            for x in (6, 12, 18):
                p.drawEllipse(QPointF(x, 12), 1.6, 1.6)

        elif name == "home":
            p.drawLine(QPointF(4, 11), QPointF(12, 4.2))
            p.drawLine(QPointF(12, 4.2), QPointF(20, 11))
            p.drawRoundedRect(QRectF(6.2, 10.5, 11.6, 9.5), 1.2, 1.2)
            p.drawLine(QPointF(10.2, 20), QPointF(10.2, 15.2))
            p.drawLine(QPointF(13.8, 15.2), QPointF(13.8, 20))

        elif name == "discover":
            p.drawEllipse(QRectF(4.5, 4.5, 15, 15))
            path = QPainterPath()
            path.moveTo(9.2, 9.2)
            path.lineTo(14.8, 6.6)
            path.lineTo(12.2, 12.2)
            path.closeSubpath()
            path.moveTo(9.2, 9.2)
            path.lineTo(6.6, 14.8)
            path.lineTo(12.2, 12.2)
            path.closeSubpath()
            p.setBrush(self.color)
            p.setPen(Qt.NoPen)
            p.drawPath(path)

        elif name == "grid":
            for x in (5, 13):
                for y in (5, 13):
                    p.drawRoundedRect(QRectF(x, y, 6, 6), 1.2, 1.2)

        elif name == "list":
            for y in (6.5, 12, 17.5):
                p.drawRoundedRect(QRectF(4, y - 2, 4, 4), 1, 1)
                p.drawLine(QPointF(10.5, y), QPointF(20, y))

        elif name == "timeline":
            p.drawLine(QPointF(3, 13), QPointF(21, 13))
            for x, up in ((7, True), (12, False), (17, True)):
                p.drawLine(QPointF(x, 13), QPointF(x, 7 if up else 19))
                p.setBrush(self.color)
                p.setPen(Qt.NoPen)
                p.drawEllipse(QPointF(x, 13), 2.0, 2.0)
                p.setBrush(Qt.NoBrush)
                p.setPen(QPen(self.color, 1.6))

        elif name == "palette":
            p.drawEllipse(QRectF(4.5, 4.5, 15, 15))
            p.setBrush(self.color)
            for x, y in ((9, 9), (14, 8.5), (8.5, 14), (14.5, 13.5)):
                p.drawEllipse(QPointF(x, y), 1.7, 1.7)
            p.setBrush(Qt.NoBrush)

        elif name == "image":
            p.drawRoundedRect(QRectF(3.5, 5, 17, 14), 2, 2)
            p.drawEllipse(QPointF(8.5, 9.5), 1.6, 1.6)
            p.drawLine(QPointF(5, 17), QPointF(10, 12))
            p.drawLine(QPointF(10, 12), QPointF(14, 15.5))
            p.drawLine(QPointF(14, 15.5), QPointF(17, 12.5))
            p.drawLine(QPointF(17, 12.5), QPointF(19.5, 15))

        elif name == "group":
            p.drawRoundedRect(QRectF(4.2, 8, 15.6, 11), 2, 2)
            p.drawLine(QPointF(9.4, 8), QPointF(9.4, 5.2))
            p.drawLine(QPointF(9.4, 5.2), QPointF(16, 5.2))

        elif name == "user":
            p.drawEllipse(QRectF(9, 4, 6, 6))
            p.drawArc(QRectF(5.5, 12, 13, 10), 0, 180 * 16)

        elif name == "cloud":
            path = QPainterPath()
            path.moveTo(7.2, 18)
            path.lineTo(17.2, 18)
            path.cubicTo(21.6, 18, 21.6, 11.5, 17.2, 11.5)
            path.cubicTo(17.2, 6.4, 10.4, 5.4, 8.6, 10)
            path.cubicTo(4.4, 10, 3.4, 18, 7.2, 18)
            p.drawPath(path)

        elif name == "upload":
            p.drawLine(QPointF(12, 19), QPointF(12, 6))
            p.drawLine(QPointF(12, 6), QPointF(7.2, 10.8))
            p.drawLine(QPointF(12, 6), QPointF(16.8, 10.8))
            p.drawLine(QPointF(4.8, 19), QPointF(19.2, 19))

        elif name == "download":
            p.drawLine(QPointF(12, 4.5), QPointF(12, 17.5))
            p.drawLine(QPointF(12, 17.5), QPointF(7.2, 12.7))
            p.drawLine(QPointF(12, 17.5), QPointF(16.8, 12.7))
            p.drawLine(QPointF(4.8, 19.5), QPointF(19.2, 19.5))

        elif name == "mind":
            # 思维导图：中心节点 + 三个子节点连线
            p.setBrush(self.color)
            p.setPen(Qt.NoPen)
            p.drawEllipse(QPointF(12, 12), 2.6, 2.6)
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(self.color, 1.6))
            for dx, dy in ((-7.2, -5.2), (7.2, -5.2), (0, 7.2)):
                p.drawLine(QPointF(12, 12), QPointF(12 + dx, 12 + dy))
                p.drawEllipse(QPointF(12 + dx, 12 + dy), 2.1, 2.1)

        elif name == "left":
            p.drawLine(QPointF(14.5, 5), QPointF(7.5, 12))
            p.drawLine(QPointF(7.5, 12), QPointF(14.5, 19))

        elif name == "right":
            p.drawLine(QPointF(9.5, 5), QPointF(16.5, 12))
            p.drawLine(QPointF(16.5, 12), QPointF(9.5, 19))

        elif name == "reset":
            p.drawArc(QRectF(5, 5, 14, 14), 100, 250)
            p.drawLine(QPointF(6.2, 4.2), QPointF(6.2, 9.2))
            p.drawLine(QPointF(6.2, 4.2), QPointF(11.2, 4.4))


def _draw_undo(self, p: QPainter) -> None:
    """绘制“撤销”弧形箭头（供 redo 镜像复用）。"""
    path = QPainterPath()
    path.moveTo(7, 8.5)
    path.lineTo(11.5, 8.5)
    path.cubicTo(18, 8.5, 18, 17, 11.5, 17)
    path.lineTo(7.5, 17)
    p.drawPath(path)
    p.drawLine(QPointF(7, 8.5), QPointF(10.8, 4.7))
    p.drawLine(QPointF(7, 8.5), QPointF(10.8, 12.3))


# 把辅助方法挂到类上，保持 _draw 分支简洁
IconFactory._draw_undo = _draw_undo
