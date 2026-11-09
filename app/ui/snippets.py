"""写作页右侧「文笔仿写」片段栏。

数据：全书共享一份 <数据目录>/snippets.json（由 Storage 原子写入 + .bak）。
界面：QSplitter 右侧可拖拽宽度；列表采用虚拟化，仅实例化可视区域的卡片，
单条文本按实际高度完整展开，绝不省略。滚动沿用 ScrollFlasher 隐形滑块。
"""
from __future__ import annotations

import bisect
import math
import time
from typing import Any

from PySide6.QtCore import QEvent, QPoint, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QFontMetricsF,
    QKeySequence,
    QPainter,
    QPixmap,
    QShortcut,
    QTextCursor,
    QTextDocument,
    QTextOption,
)
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.ui.context_menu import make_menu, popup

from app.models import new_id
from app.ui import surface
from app.ui.icons import IconFactory
from app.ui.scroll_float import attach_flasher

MAX_SNIPPET_CHARS = 20000
SAVE_DEBOUNCE_MS = 800
# 长片段内部分段排版：每 3000 字一段，段间只画极淡分隔线，视觉上仍是一整段。
SEGMENT_CHARS = 3000

_FALLBACK_THEME = {
    "text": "#1F2329",
    "subtext": "#8F959E",
    "divider": "#E5E6EB",
    "hover": "#F2F3F5",
    "accent": "#7C5CE6",
}


def _clean_content(text: str) -> str:
    """统一换行，只去尾部多余空白；正文缩进 / 中文标点原样保留。"""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text.rstrip()


def _make_text_document(text: str, font: QFont, width: int) -> QTextDocument:
    """构造一个已排版好的文本 document（分段测量与可见区绘制共用）。"""
    doc = QTextDocument()
    doc.setDocumentMargin(0)
    option = QTextOption()
    option.setWrapMode(QTextOption.WrapMode.WordWrap)
    doc.setDefaultTextOption(option)
    doc.setDefaultFont(font)
    doc.setPlainText(text)
    doc.setTextWidth(max(24, width))
    return doc


def _measure_text(text: str, font: QFont, width: int) -> int:
    """按给定宽度排版，返回完整文本所需高度（不含卡片留白）。"""
    doc = _make_text_document(text, font, width)
    return max(1, int(math.ceil(doc.size().height())))


def _split_segments(text: str) -> list[str]:
    """把长文本切成 3000 字以内的段；短文本保持单段。"""
    if len(text) <= SEGMENT_CHARS:
        return [text]
    return [text[start:start + SEGMENT_CHARS] for start in range(0, len(text), SEGMENT_CHARS)]


def _estimate_text_height(text: str, font: QFont, width: int) -> int:
    """不排版、只按段落长度估算高度；进入视口后再换成精确值。"""
    fm = QFontMetricsF(font)
    paragraphs = text.count("\n") + 1
    avg_len = len(text) / paragraphs
    per_line = max(1, int(math.ceil(avg_len * fm.horizontalAdvance("中") / max(24, width))))
    return max(1, int(round(paragraphs * per_line * fm.lineSpacing())))


class _Segment:
    """一个排版段：先估算高度，进入视口后精确测量；document 仅可见时保留。"""

    __slots__ = ("text", "height", "doc", "measured")

    def __init__(self, text: str) -> None:
        self.text = text
        self.height = 0
        self.doc: QTextDocument | None = None
        self.measured = False

    def ensure_document(self, font: QFont, width: int) -> QTextDocument:
        if self.doc is None:
            self.doc = _make_text_document(self.text, font, width)
            if self.height <= 0:
                self.height = max(1, int(math.ceil(self.doc.size().height())))
        return self.doc

    def release(self) -> None:
        self.doc = None


class _SnippetCanvas(QWidget):
    """片段列表画布：只承载当前可视卡片，并绘制拖动排序插入线。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.line_y = -1
        self._accent = QColor(_FALLBACK_THEME["accent"])

    def set_theme(self, theme: dict) -> None:
        self._accent = QColor(str(theme.get("accent") or _FALLBACK_THEME["accent"]))
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        if self.line_y < 0:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._accent)
        painter.drawRoundedRect(QRectF(8, float(self.line_y) - 1.0, self.width() - 16, 3), 1.5, 1.5)
        painter.end()


class _SnippetCard(QWidget):
    """单条片段卡片：完整绘制正文（无省略号）+ 来源 + 复制/编辑/删除。"""

    copy_clicked = Signal(str)
    edit_clicked = Signal(str)
    delete_clicked = Signal(str)
    drag_started = Signal(str, int)
    drag_moved = Signal(int)
    drag_finished = Signal()

    PAD_X = 14
    PAD_TOP = 12
    PAD_BOTTOM = 9
    FOOTER_H = 20
    RADIUS = 8

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("SnippetCard")
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self._item: dict[str, Any] = {}
        self._font = QFont(QApplication.font())
        self._theme = dict(_FALLBACK_THEME)
        self._segments: list[_Segment] = []
        self._seg_ys: list[int] = []
        self._text_h = 1
        self._width = 0
        self._height = 1
        self._chrome_cache: QPixmap | None = None
        self._chrome_key: tuple | None = None
        self._hover = False
        self._press_pos = QPoint()
        self._drag_ready = False
        self._dragging = False
        self._buttons: list[QPushButton] = []
        self._btn_copy = self._make_button("复制")
        self._btn_edit = self._make_button("编辑")
        self._btn_delete = self._make_button("删除")
        self._btn_copy.clicked.connect(lambda: self.copy_clicked.emit(self.item_id()))
        self._btn_edit.clicked.connect(lambda: self.edit_clicked.emit(self.item_id()))
        self._btn_delete.clicked.connect(lambda: self.delete_clicked.emit(self.item_id()))

    def _make_button(self, text: str) -> QPushButton:
        button = QPushButton(text, self)
        button.setObjectName("SnippetAction")
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._buttons.append(button)
        return button

    def item_id(self) -> str:
        return str(self._item.get("id") or "")

    def set_theme(self, theme: dict) -> None:
        self._theme = {**_FALLBACK_THEME, **theme}
        self._chrome_key = None
        self.update()

    def set_item(self, item: dict, font: QFont, width: int, segments: list[_Segment]) -> None:
        changed = str(item.get("id")) != self.item_id()
        if changed:
            self.release_layouts()
        self._item = item
        self._font = font
        self._width = max(120, int(width))
        self._segments = segments
        ys: list[int] = []
        top = 0
        for segment in segments:
            ys.append(top)
            top += max(1, int(segment.height))
        self._seg_ys = ys
        self._text_h = max(1, top)
        height = self._text_h + self.PAD_TOP + self.PAD_BOTTOM + self.FOOTER_H
        self._height = height
        self.setFixedSize(self._width, height)
        self._place_actions()
        self._chrome_key = None
        if changed:
            self.update()

    def release_layouts(self) -> None:
        """离开视口即释放文本 document，只保留已测高度。"""
        for segment in self._segments:
            segment.release()

    def release_outside(self, top: int, bottom: int) -> None:
        """只保留纵向区间 [top, bottom) 内的段落 document。"""
        local_top = top - self.PAD_TOP
        local_bottom = bottom - self.PAD_TOP
        for index, segment in enumerate(self._segments):
            seg_top = self._seg_ys[index]
            if seg_top + segment.height <= local_top or seg_top >= local_bottom:
                segment.release()

    def _place_actions(self) -> None:
        y = self._height - self.PAD_BOTTOM - self.FOOTER_H
        x = self._width - self.PAD_X
        for button in (self._btn_delete, self._btn_edit, self._btn_copy):
            hint = button.sizeHint()
            w = max(30, hint.width())
            h = min(self.FOOTER_H, max(18, hint.height()))
            x -= w
            button.setGeometry(x, y + (self.FOOTER_H - h) // 2, w, h)
            x -= 2

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._place_actions()

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        popup(make_menu(self, [
            ("编辑片段", lambda: self.edit_clicked.emit(self.item_id())),
            ("复制片段", lambda: self.copy_clicked.emit(self.item_id())),
            None,
            ("删除片段", lambda: self.delete_clicked.emit(self.item_id())),
        ]), event.globalPos())

    def _paint_chrome(self, painter: QPainter, damaged) -> None:
        """卡片底板（悬停底色 / 圆角）优先用缓存位图；超长卡片按可视区裁剪绘制。"""
        w, h = self.width(), self.height()
        if not self._hover:
            self._chrome_cache = None
            return
        key = (w, h)
        if key != self._chrome_key:
            self._chrome_key = key
            self._chrome_cache = None
            if h <= 2048:
                pix = QPixmap(w, h)
                pix.fill(Qt.GlobalColor.transparent)
                pp = QPainter(pix)
                pp.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                pp.setPen(Qt.PenStyle.NoPen)
                pp.setBrush(QColor(str(self._theme.get("hover") or "#F2F3F5")))
                pp.drawRoundedRect(QRectF(0, 0, w, h), self.RADIUS, self.RADIUS)
                pp.end()
                self._chrome_cache = pix
        if self._chrome_cache is not None:
            painter.drawPixmap(0, 0, self._chrome_cache)
            return
        painter.save()
        painter.setClipRect(damaged)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(str(self._theme.get("hover") or "#F2F3F5")))
        painter.drawRoundedRect(QRectF(0, 0, w, h), self.RADIUS, self.RADIUS)
        painter.restore()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w, h = self.width(), self.height()
        damaged = event.rect()
        self._paint_chrome(painter, damaged)

        text_color = QColor(str(self._theme.get("text") or "#1F2329"))
        divider_color = QColor(str(self._theme.get("divider") or "#E5E6EB"))
        painter.setPen(text_color)
        clip_top = damaged.top() - self.PAD_TOP
        clip_bottom = damaged.bottom() - self.PAD_TOP
        content_w = w - self.PAD_X * 2
        # 只为当前 damaged 区域内的段落建立 document 并绘制，滚出视口即释放。
        for index, segment in enumerate(self._segments):
            top = self._seg_ys[index]
            bottom = top + segment.height
            if bottom <= clip_top or top >= clip_bottom:
                continue
            doc = segment.ensure_document(self._font, content_w)
            painter.save()
            painter.translate(self.PAD_X, self.PAD_TOP + top)
            painter.setClipRect(QRectF(0, 0, content_w, segment.height))
            doc.drawContents(painter, QRectF(0, 0, content_w, segment.height))
            painter.restore()
            if index:
                # 段间极淡分隔线：不加留白，视觉上仍是一整段。
                line = QColor(divider_color)
                line.setAlpha(70)
                painter.setPen(line)
                painter.drawLine(
                    self.PAD_X + 24,
                    self.PAD_TOP + top,
                    w - self.PAD_X - 24,
                    self.PAD_TOP + top,
                )
                painter.setPen(text_color)

        # 底部细分隔线（单一卡片底板的一部分，不额外框套框）
        painter.setPen(divider_color)
        painter.drawLine(self.PAD_X, h - 1, w - self.PAD_X, h - 1)

        painter.setFont(self._font)
        fm = QFontMetrics(self._font)
        first_btn_x = self._btn_delete.x()
        source_w = max(0, first_btn_x - self.PAD_X - 8)
        if source_w > 12:
            painter.setPen(QColor(str(self._theme.get("subtext") or "#8F959E")))
            source = str(self._item.get("source") or "").strip() or "未标注来源"
            painter.drawText(
                QRectF(
                    self.PAD_X,
                    self._height - self.PAD_BOTTOM - self.FOOTER_H,
                    source_w,
                    self.FOOTER_H,
                ),
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                fm.elidedText(source, Qt.TextElideMode.ElideRight, source_w),
            )
        painter.end()

    # ------------------------------------------------------------------
    # 拖动排序
    # ------------------------------------------------------------------
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.position().toPoint()
            self._drag_ready = True
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_ready:
            delta = event.position().toPoint() - self._press_pos
            if abs(delta.x()) + abs(delta.y()) >= 7:
                self._drag_ready = False
                self._dragging = True
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
                self.drag_started.emit(
                    self.item_id(), event.globalPosition().toPoint().y()
                )
        if self._dragging:
            self.drag_moved.emit(event.globalPosition().toPoint().y())
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._dragging:
            self._dragging = False
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            self.drag_finished.emit()
        self._drag_ready = False
        super().mouseReleaseEvent(event)


class _SnippetListView(QScrollArea):
    """虚拟化片段列表：只为可视区域创建卡片，滚动时复用。"""

    copy_requested = Signal(str)
    edit_requested = Signal(str)
    delete_requested = Signal(str)
    reorder_requested = Signal(str, int)
    menu_requested = Signal(QPoint)   # 空白处右键 → 面板统一给菜单

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setWidgetResizable(False)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._canvas = _SnippetCanvas(self)
        self.setWidget(self._canvas)
        self._items: list[dict] = []
        self._heights: list[int] = []
        self._tops: list[int] = []
        self._total = 0
        self._cards: list[_SnippetCard] = []
        self._segment_cache: dict[str, tuple[str, int, list[_Segment]]] = {}
        self._card_state: dict[int, tuple[str, int]] = {}
        self._font = QFont(QApplication.font())
        self._font.setPointSizeF(10.5)
        self._theme = dict(_FALLBACK_THEME)
        self._drag_id = ""
        self._drag_target = -1
        self._in_update = False

        self._reflow_timer = QTimer(self)
        self._reflow_timer.setSingleShot(True)
        self._reflow_timer.setInterval(150)
        self._reflow_timer.timeout.connect(self._reflow)

        self.verticalScrollBar().valueChanged.connect(lambda _v: self._update_visible())
        self._flasher = attach_flasher(self, Qt.Orientation.Vertical, inset=4, edge_margin=6)
        self._flasher.watch(self._canvas)

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        """列表空白处右键：交给面板出「新增 / 搜索 / 收起」菜单。"""
        self.menu_requested.emit(event.globalPos())
        event.accept()

    # ------------------------------------------------------------------
    def set_theme(self, theme: dict) -> None:
        self._theme = {**_FALLBACK_THEME, **theme}
        self._canvas.set_theme(self._theme)
        for card in self._cards:
            card.set_theme(self._theme)

    def set_items(self, items: list[dict]) -> None:
        self._items = list(items)
        self._segment_cache.clear()
        self._card_state.clear()
        self._reflow_timer.stop()
        self._reflow()
        self.verticalScrollBar().setValue(0)

    def visible_card_count(self) -> int:
        return sum(1 for card in self._cards if card.isVisible())

    def _content_width(self) -> int:
        return max(40, self.viewport().width() - _SnippetCard.PAD_X * 2)

    def _segments_for(self, item: dict) -> list[_Segment]:
        """取（必要时重建）条目的 3000 字分段；高度在此一次性测量。"""
        key = str(item.get("id") or "")
        content = str(item.get("content") or "")
        width = self.viewport().width()
        cached = self._segment_cache.get(key)
        if cached is not None and cached[0] == content and cached[1] == width:
            return cached[2]
        segments = [_Segment(part) for part in _split_segments(content)]
        for segment in segments:
            segment.height = _estimate_text_height(
                segment.text, self._font, self._content_width()
            )
        self._segment_cache[key] = (content, width, segments)
        return segments

    def _card_height(self, item: dict) -> int:
        text_h = sum(max(1, int(seg.height)) for seg in self._segments_for(item))
        return text_h + _SnippetCard.PAD_TOP + _SnippetCard.PAD_BOTTOM + _SnippetCard.FOOTER_H

    def _refresh_geometry(self, anchor: int = 0, keep_value: bool = False) -> None:
        """按当前分段高度重算 tops；锚点以上高度变化时同步滚动值防跳动。"""
        heights = [self._card_height(item) for item in self._items]
        tops: list[int] = []
        acc = 0
        for height in heights:
            tops.append(acc)
            acc += height
        old_anchor_top = self._tops[anchor] if 0 <= anchor < len(self._tops) else 0
        new_anchor_top = tops[anchor] if 0 <= anchor < len(tops) else 0
        bar = self.verticalScrollBar()
        value = bar.value() if keep_value else 0
        self._heights = heights
        self._tops = tops
        self._total = acc
        self._canvas.setFixedHeight(max(1, acc))
        if keep_value:
            bar.setValue(value + new_anchor_top - old_anchor_top)
        else:
            bar.setValue(0)

    def _reflow(self) -> None:
        width = max(120, self.viewport().width())
        if self._canvas.width() != width:
            self._canvas.setFixedWidth(width)
        self._refresh_geometry()
        self._update_visible(force=True)

    def _ensure_cards(self, count: int) -> None:
        while len(self._cards) < count:
            card = _SnippetCard(self._canvas)
            card.copy_clicked.connect(self.copy_requested.emit)
            card.edit_clicked.connect(self.edit_requested.emit)
            card.delete_clicked.connect(self.delete_requested.emit)
            card.drag_started.connect(self._on_drag_started)
            card.drag_moved.connect(self._on_drag_moved)
            card.drag_finished.connect(self._on_drag_finished)
            card.set_theme(self._theme)
            card.hide()
            self._cards.append(card)

    def _visible_range(self) -> tuple[int, int]:
        if not self._items:
            return 0, 0
        value = self.verticalScrollBar().value()
        view_h = self.viewport().height()
        start = max(0, bisect.bisect_right(self._tops, value) - 1)
        end = start
        limit = value + max(1, view_h)
        while end < len(self._items) and self._tops[end] < limit:
            end += 1
        end = min(len(self._items), end + 1)       # 下方缓冲一条
        start = max(0, start - 1)                  # 上方缓冲一条
        return start, end

    def _measure_visible_for(self, index: int, top: int) -> bool:
        """只为当前视口内的分段做精确排版；其余分段保持估算高度。"""
        segments = self._segments_for(self._items[index])
        view_top = self.verticalScrollBar().value() - top - _SnippetCard.PAD_TOP
        view_bottom = view_top + self.viewport().height()
        y = 0
        changed = False
        for segment in segments:
            if (
                not segment.measured
                and y + segment.height > view_top
                and y < view_bottom
            ):
                segment.height = _measure_text(
                    segment.text, self._font, self._content_width()
                )
                segment.measured = True
                changed = True
            y += max(1, int(segment.height))
        return changed

    def _update_visible(self, force: bool = False) -> None:
        if not self._items or self._total <= 0:
            for card in self._cards:
                card.hide()
            return
        if self._in_update:
            return
        self._in_update = True
        try:
            start, end = self._visible_range()
            count = end - start
            self._ensure_cards(count)
            changed = False
            for offset in range(count):
                index = start + offset
                if self._measure_visible_for(index, self._tops[index]):
                    changed = True
            if changed:
                self._refresh_geometry(anchor=start, keep_value=True)
                force = True
            width = self._canvas.width()
            for offset in range(count):
                index = start + offset
                card = self._cards[offset]
                item = self._items[index]
                was_hidden = not card.isVisible()
                state = (str(item.get("id") or ""), width)
                if force or self._card_state.get(offset) != state:
                    card.set_item(item, self._font, width, self._segments_for(item))
                    self._card_state[offset] = state
                card.move(0, self._tops[index])
                if was_hidden:
                    card.show()
                    card.raise_()      # 仅在卡片重新进入视口时抬升，滚动热路径不再 raise
                view_top = self.verticalScrollBar().value()
                card.release_outside(
                    view_top - self._tops[index],
                    view_top + self.viewport().height() - self._tops[index],
                )
            for offset in range(count, len(self._cards)):
                card = self._cards[offset]
                if card.isVisible():
                    card.hide()
                card.release_layouts()
                self._card_state.pop(offset, None)
            # 滚动只搬移视口，不整屏 update；仅在拖动排序时需要重画画布
        finally:
            self._in_update = False

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self.viewport().width() > 0:
            self._reflow_timer.start()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._reflow_timer.start()

    # ------------------------------------------------------------------
    # 拖动排序
    # ------------------------------------------------------------------
    def _event_content_y(self, global_y: int) -> int:
        local = self.viewport().mapFromGlobal(QPoint(0, global_y))
        return max(0, local.y() + self.verticalScrollBar().value())

    def _on_drag_started(self, snippet_id: str, global_y: int) -> None:
        self._drag_id = snippet_id
        self._drag_target = -1
        self._on_drag_moved(global_y)

    def _on_drag_moved(self, global_y: int) -> None:
        if not self._drag_id:
            return
        local = self.viewport().mapFromGlobal(QPoint(0, global_y))
        bar = self.verticalScrollBar()
        if local.y() < 24:
            bar.setValue(max(0, bar.value() - 24))
        elif local.y() > self.viewport().height() - 24:
            bar.setValue(min(bar.maximum(), bar.value() + 24))
        y = self._event_content_y(global_y)

        others: list[tuple[dict, int]] = []
        for item, height in zip(self._items, self._heights):
            if str(item.get("id")) != self._drag_id:
                others.append((item, height))
        target = len(others)
        line_y = sum(height for _item, height in others)
        acc = 0
        for index, (_item, height) in enumerate(others):
            if y < acc + height // 2:
                target = index
                line_y = acc
                break
            acc += height
        self._drag_target = target
        self._canvas.line_y = line_y
        self._canvas.update()

    def _on_drag_finished(self) -> None:
        snippet_id = self._drag_id
        target = self._drag_target
        self._drag_id = ""
        self._drag_target = -1
        self._canvas.line_y = -1
        self._canvas.update()
        if snippet_id and target >= 0:
            self.reorder_requested.emit(snippet_id, target)


class _SearchLineEdit(QLineEdit):
    """优先吞掉 Esc：搜索框有焦点时清空搜索，不触发主窗口「关闭抽屉」。"""

    escape_pressed = Signal()

    def event(self, event) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.ShortcutOverride and event.key() == Qt.Key.Key_Escape:
            event.accept()
            return True
        if event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
            self.escape_pressed.emit()
            return True
        return super().event(event)


class SnippetEditDialog(QDialog):
    """新增 / 编辑片段：粘贴即入，正文完整保留。"""

    def __init__(
        self,
        parent,
        title: str,
        content: str = "",
        source: str = "",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.resize(640, 480)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 16)
        layout.setSpacing(10)

        self.source_edit = QLineEdit(self)
        self.source_edit.setPlaceholderText("来源（书名 / 章节，可空）")
        self.source_edit.setText(source)
        layout.addWidget(self.source_edit)

        self.editor = QTextEdit(self)
        self.editor.setObjectName("Preview")
        self.editor.setAcceptRichText(False)
        self.editor.setPlaceholderText("粘贴或输入要收藏的片段…")
        self.editor.setPlainText(content)
        layout.addWidget(self.editor, 1)

        foot = QHBoxLayout()
        foot.setSpacing(10)
        self.counter = QLabel("0 / 20000", self)
        self.counter.setObjectName("SnippetMeta")
        hint = QLabel("中文标点与换行原样保留，只去掉尾部多余空白。", self)
        hint.setObjectName("SnippetHint")
        foot.addWidget(self.counter)
        foot.addWidget(hint, 1)
        layout.addLayout(foot)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("取消", self)
        cancel.setObjectName("GhostButton")
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.clicked.connect(self.reject)
        ok = QPushButton("保存", self)
        ok.setObjectName("Primary")
        ok.setCursor(Qt.CursorShape.PointingHandCursor)
        ok.clicked.connect(self._on_ok)
        buttons.addWidget(cancel)
        buttons.addWidget(ok)
        layout.addLayout(buttons)

        self.editor.textChanged.connect(self._update_counter)
        self._update_counter()
        self.editor.setFocus()
        cursor = self.editor.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.editor.setTextCursor(cursor)

    def _update_counter(self) -> None:
        count = len(_clean_content(self.editor.toPlainText()))
        self.counter.setText(f"{count} / {MAX_SNIPPET_CHARS}")
        color = "#C2413B" if count > MAX_SNIPPET_CHARS else ""
        self.counter.setStyleSheet(f"color: {color};" if color else "")

    def content(self) -> str:
        return _clean_content(self.editor.toPlainText())

    def source(self) -> str:
        return self.source_edit.text().strip()

    def _on_ok(self) -> None:
        from app.ui.dialogs import MessageBox

        text = self.content()
        if not text:
            MessageBox.warning(self, "提示", "片段内容不能为空。")
            self.editor.setFocus()
            return
        if len(text) > MAX_SNIPPET_CHARS:
            MessageBox.warning(
                self,
                "片段过长",
                f"单条片段最多 {MAX_SNIPPET_CHARS} 字（当前 {len(text)} 字），"
                "请删减后再保存。",
            )
            self.editor.setFocus()
            return
        self.accept()


class SnippetsPanel(QWidget):
    """右侧片段栏：搜索 / 新增 / 编辑 / 删除 / 拖动排序 / 一键复制。"""

    close_requested = Signal()
    focus_editor_requested = Signal()

    def __init__(self, storage, settings, parent=None) -> None:
        super().__init__(parent)
        self.storage = storage
        self.settings = settings
        self.setObjectName("SnippetPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMinimumWidth(220)
        self._items: list[dict] = []
        self._query = ""
        self._theme = dict(_FALLBACK_THEME)
        self._context_source = ""
        self._dirty = False

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(SAVE_DEBOUNCE_MS)
        self._save_timer.timeout.connect(self._save_now)

        self._tip_timer = QTimer(self)
        self._tip_timer.setSingleShot(True)
        self._tip_timer.setInterval(1600)
        self._tip_timer.timeout.connect(lambda: self.status_label.setText(""))

        self._build_ui()
        # 焦点落在面板任意子控件（列表 / 卡片 / 搜索框）时，Esc 收起面板并归还焦点；
        # WidgetWithChildrenShortcut 确保正文编辑器里的 Esc 完全不受影响。
        self._esc_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        self._esc_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._esc_shortcut.activated.connect(self._collapse_to_editor)
        for widget in (self.search_edit, self.list_view, self.list_view.viewport()):
            widget.installEventFilter(self)
        self.reload()

    # ------------------------------------------------------------------
    # 界面
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 12)
        layout.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(8)
        title = QLabel("文笔仿写", self)
        title.setObjectName("SnippetTitle")
        self.count_label = QLabel("0 条", self)
        self.count_label.setObjectName("SnippetMeta")
        head.addWidget(title)
        head.addWidget(self.count_label)
        head.addStretch(1)
        # 收起按钮：向右 chevron（与侧栏「收起」语义一致），24px 图标、32px 点击区
        self.collapse_button = QPushButton(self)
        self.collapse_button.setObjectName("DrawerClose")
        self.collapse_button.setFixedSize(32, 32)
        self.collapse_button.setIconSize(QSize(24, 24))
        self.collapse_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.collapse_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.collapse_button.setToolTip("收起仿写区")
        self.collapse_button.setAccessibleName("收起仿写区")
        self.collapse_button.clicked.connect(self.close_requested.emit)
        self._icon_factory = IconFactory(
            str(self._theme.get("subtext") or "#8F959E"),
            str(self._theme.get("text") or "#1F2329"),
        )
        self.collapse_button.setIcon(self._icon_factory.icon("right", 24))
        head.addWidget(self.collapse_button)
        layout.addLayout(head)

        self.search_edit = _SearchLineEdit(self)
        self.search_edit.setPlaceholderText("搜索片段（内容 / 来源）")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._on_search_changed)
        # 搜索框内的 Esc 优先清空关键词并回到正文，不触发全局「关闭抽屉」
        self.search_edit.escape_pressed.connect(self._clear_search_and_focus_editor)
        layout.addWidget(self.search_edit)

        add_button = QPushButton("＋ 新增片段", self)
        add_button.setObjectName("GhostButton")
        add_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        add_button.setCursor(Qt.CursorShape.PointingHandCursor)
        add_button.setToolTip("粘贴一段文字收藏为仿写素材")
        add_button.clicked.connect(self.open_add_dialog)
        layout.addWidget(add_button)

        self.stack = QStackedWidget(self)
        self.list_view = _SnippetListView(self)
        self.list_view.copy_requested.connect(self.copy_snippet)
        self.list_view.edit_requested.connect(self.edit_snippet)
        self.list_view.delete_requested.connect(self.delete_snippet)
        self.list_view.reorder_requested.connect(self.reorder_snippet)
        self.list_view.menu_requested.connect(self._show_blank_menu)
        self.empty_label = QLabel("", self)
        self.empty_label.setObjectName("SnippetHint")
        self.empty_label.setWordWrap(True)
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.stack.addWidget(self.list_view)
        self.stack.addWidget(self.empty_label)
        layout.addWidget(self.stack, 1)

        self.status_label = QLabel("", self)
        self.status_label.setObjectName("SnippetMeta")
        layout.addWidget(self.status_label)

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        """面板空白处右键：只列片段栏真能做到的事。"""
        self._show_blank_menu(event.globalPos())

    def _show_blank_menu(self, global_pos) -> None:
        popup(make_menu(self, [
            ("新增片段", self.open_add_dialog),
            ("搜索片段", self._focus_search),
            None,
            ("收起片段栏", self.close_requested.emit),
        ]), global_pos)

    def _focus_search(self) -> None:
        self.search_edit.setFocus(Qt.FocusReason.OtherFocusReason)
        self.search_edit.selectAll()

    def set_theme(self, theme: dict) -> None:
        self._theme = {**_FALLBACK_THEME, **theme}
        self.list_view.set_theme(self._theme)
        self._icon_factory = IconFactory(
            str(self._theme.get("subtext") or "#8F959E"),
            str(self._theme.get("text") or "#1F2329"),
        )
        self.collapse_button.setIcon(self._icon_factory.icon("right", 24))
        self.update()

    def set_fast_scroll(self, enabled: bool) -> None:
        """跟随设置项「快速滚动滑块」；关闭时列表也不浮现隐形滑块。"""
        self.list_view._flasher.set_enabled(bool(enabled))

    def paintEvent(self, event) -> None:  # noqa: N802
        if surface.is_glass():
            painter = QPainter(self)
            surface.paint_liquid_sheet(
                painter,
                QRectF(self.rect()),
                max(8.0, surface.card_radius()),
                None,
                None,
                True,
            )
            painter.end()
            return
        super().paintEvent(event)

    def _collapse_to_editor(self) -> None:
        self.close_requested.emit()
        self.focus_editor_requested.emit()

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        # 面板内子控件按 Esc：先清搜索词（若来自搜索框），再收起面板、焦点回正文。
        # 只监听面板自己的控件，编辑器里的 Esc 行为完全不受影响。
        if (event.type() == QEvent.Type.KeyPress
                and event.key() == Qt.Key.Key_Escape
                and obj is not None and obj in (self.search_edit, self.list_view,
                                                self.list_view.viewport())):
            if obj is self.search_edit:
                self.search_edit.clear()
            self._collapse_to_editor()
            event.accept()
            return True
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        # 兜底：未被快捷键拦截的 Esc（如焦点在未处理 Esc 的自定义控件上）
        if event.key() == Qt.Key.Key_Escape:
            self._collapse_to_editor()
            event.accept()
            return
        super().keyPressEvent(event)

    def _clear_search_and_focus_editor(self) -> None:
        self.search_edit.clear()
        self.search_edit.clearFocus()
        self._collapse_to_editor()

    # ------------------------------------------------------------------
    # 数据
    # ------------------------------------------------------------------
    def reload(self) -> None:
        self._items = self.storage.load_snippets()
        self.refresh()

    def set_context(self, book_title: str = "", chapter_title: str = "") -> None:
        parts = [part.strip() for part in (book_title, chapter_title) if part.strip()]
        self._context_source = " · ".join(parts)

    def _filtered(self) -> list[dict]:
        query = self._query
        if not query:
            return list(self._items)
        return [
            item
            for item in self._items
            if query in str(item.get("content") or "").lower()
            or query in str(item.get("source") or "").lower()
        ]

    def refresh(self) -> None:
        filtered = self._filtered()
        self.list_view.set_items(filtered)
        if self._query:
            self.count_label.setText(f"{len(filtered)} / {len(self._items)} 条")
        else:
            self.count_label.setText(f"{len(self._items)} 条")
        if filtered:
            self.stack.setCurrentWidget(self.list_view)
            self.empty_label.setText("")
        else:
            if self._items:
                self.empty_label.setText("没有匹配的片段。试试换个关键词。")
            else:
                self.empty_label.setText(
                    "还没有片段。\n粘贴一段喜欢的文字，点「新增片段」存起来。"
                )
            self.stack.setCurrentWidget(self.empty_label)

    def _schedule_save(self) -> None:
        self._dirty = True
        self._save_timer.start()

    def _save_now(self) -> None:
        self._save_timer.stop()
        if not self._dirty:
            return
        try:
            self.storage.save_snippets(self._items)
        except OSError as exc:
            self.status_label.setText(f"保存失败：{exc}")
            return
        self._dirty = False

    def flush_pending(self) -> None:
        self._save_timer.stop()
        if self._dirty:
            self._save_now()

    def hideEvent(self, event) -> None:  # noqa: N802
        self.flush_pending()
        super().hideEvent(event)

    # ------------------------------------------------------------------
    # 增删改查
    # ------------------------------------------------------------------
    def open_add_dialog(self) -> None:
        dialog = SnippetEditDialog(
            self, "新增片段", "", self._context_source
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.add_content(dialog.content(), dialog.source())

    def add_content(self, content: str, source: str = "") -> bool:
        text = _clean_content(content)
        if not text:
            self._tip("片段内容不能为空")
            return False
        if len(text) > MAX_SNIPPET_CHARS:
            self._tip(f"单条片段最多 {MAX_SNIPPET_CHARS} 字（当前 {len(text)} 字），未保存")
            return False
        now = time.time()
        self._items.append(
            {
                "id": new_id(),
                "content": text,
                "source": source.strip(),
                "created_at": now,
                "updated_at": now,
                "order": len(self._items),
            }
        )
        self.refresh()
        self._schedule_save()
        return True

    def edit_snippet(self, snippet_id: str) -> None:
        item = self._find(snippet_id)
        if item is None:
            return
        dialog = SnippetEditDialog(
            self, "编辑片段", item.get("content", ""), item.get("source", "")
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.update_content(snippet_id, dialog.content(), dialog.source())

    def update_content(self, snippet_id: str, content: str, source: str = "") -> bool:
        item = self._find(snippet_id)
        if item is None:
            return False
        text = _clean_content(content)
        if not text:
            self._tip("片段内容不能为空")
            return False
        if len(text) > MAX_SNIPPET_CHARS:
            self._tip(f"单条片段最多 {MAX_SNIPPET_CHARS} 字（当前 {len(text)} 字），未保存")
            return False
        item["content"] = text
        item["source"] = source.strip()
        item["updated_at"] = time.time()
        self.refresh()
        self._schedule_save()
        return True

    def delete_snippet(self, snippet_id: str) -> None:
        from app.ui.dialogs import MessageBox

        item = self._find(snippet_id)
        if item is None:
            return
        preview = str(item.get("content") or "").strip().replace("\n", " ")
        if len(preview) > 24:
            preview = preview[:24] + "…"
        if not MessageBox.question(
            self,
            "删除片段",
            f"确定删除这条片段吗？\n「{preview}」\n此操作不可撤销。",
        ):
            return
        self._items = [entry for entry in self._items if str(entry.get("id")) != snippet_id]
        self._renumber()
        self.refresh()
        self._schedule_save()

    def copy_snippet(self, snippet_id: str) -> None:
        item = self._find(snippet_id)
        if item is None:
            return
        QApplication.clipboard().setText(str(item.get("content") or ""))
        self._tip("已复制到剪贴板")

    def _find(self, snippet_id: str) -> dict | None:
        for item in self._items:
            if str(item.get("id")) == str(snippet_id):
                return item
        return None

    def _renumber(self) -> None:
        for index, item in enumerate(self._items):
            item["order"] = index

    def reorder_snippet(self, snippet_id: str, target: int) -> None:
        """在“当前搜索结果”的可见顺序中把片段移动到 target 位置。"""
        visible_ids = [str(item.get("id")) for item in self._filtered()]
        if snippet_id not in visible_ids:
            return
        visible_ids = [sid for sid in visible_ids if sid != snippet_id]
        target = max(0, min(int(target), len(visible_ids)))
        visible_ids.insert(target, snippet_id)

        slots = [
            index
            for index, item in enumerate(self._items)
            if str(item.get("id")) in set(visible_ids)
        ]
        by_id = {str(item.get("id")): item for item in self._items}
        for slot, sid in zip(slots, visible_ids):
            by_id[sid]["order"] = slot
        self._items.sort(key=lambda item: int(item.get("order", 0)))
        self._renumber()
        self.refresh()
        self._schedule_save()

    # ------------------------------------------------------------------
    def _on_search_changed(self, text: str) -> None:
        self._query = text.strip().lower()
        filtered = self._filtered()
        self.list_view.set_items(filtered)
        if self._query:
            self.count_label.setText(f"{len(filtered)} / {len(self._items)} 条")
        else:
            self.count_label.setText(f"{len(self._items)} 条")
        if filtered:
            self.stack.setCurrentWidget(self.list_view)
        else:
            self.empty_label.setText(
                "没有匹配的片段。试试换个关键词。"
                if self._items
                else "还没有片段。\n粘贴一段喜欢的文字，点「新增片段」存起来。"
            )
            self.stack.setCurrentWidget(self.empty_label)

    def _tip(self, message: str) -> None:
        self.status_label.setText(message)
        self._tip_timer.start()
