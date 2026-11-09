# -*- coding: utf-8 -*-
"""左侧边栏：目录树（卷 / 章节）与“相关”设定页签。"""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPalette, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.models import Book, RelatedItem
from app.services.word_count import format_word_count


class _CountDelegate(QStyledItemDelegate):
    """章节行字数列：右对齐文本提前 20px 收尾，给六点把手让位。"""

    def paint(self, painter, option, index) -> None:
        if index.column() != 1 or not index.parent().isValid():
            super().paint(painter, option, index)
            return
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        widget = opt.widget
        style = widget.style() if widget is not None else QApplication.style()
        style.drawPrimitive(
            QStyle.PrimitiveElement.PE_PanelItemViewItem, opt, painter, widget
        )
        painter.save()
        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        role = QPalette.ColorRole.HighlightedText if selected else QPalette.ColorRole.Text
        painter.setPen(opt.palette.color(role))
        painter.setFont(opt.font)
        painter.drawText(
            opt.rect.adjusted(2, 0, -20, 0),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            opt.text,
        )
        painter.restore()


class TocTree(QTreeWidget):
    """目录树：章节右侧六点把手拖动排序（卷内 / 跨卷）。"""

    move_requested = Signal(str, str, int)   # 章节 ID, 目标卷 ID, 目标序号

    HANDLE_W = 14
    HANDLE_H = 28
    HANDLE_RIGHT = 3
    DRAG_THRESHOLD = 3
    EDGE = 24
    SCROLL_STEP = 14

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setItemDelegate(_CountDelegate(self))
        self.setMouseTracking(True)
        self._hover_id = ""
        self._press_pos = QPoint()
        self._pending = None
        self._drag_id = ""
        self._drag_pixmap: QPixmap | None = None
        self._drag_pos = QPoint()
        self._drop_target: tuple[str, int] | None = None
        self._drop_line_y = -1
        self._scroll_dir = 0
        self._auto_timer = QTimer(self)
        self._auto_timer.setInterval(50)
        self._auto_timer.timeout.connect(self._auto_scroll_step)

    # ------------------------------------------------------------------
    @staticmethod
    def _item_key(item: QTreeWidgetItem | None) -> tuple[str, str] | None:
        if item is None:
            return None
        data = item.data(0, Qt.UserRole)
        if isinstance(data, tuple) and len(data) == 2:
            return str(data[0]), str(data[1])
        return None

    def _chapter_parent(self, item: QTreeWidgetItem) -> QTreeWidgetItem | None:
        if self._item_key(item) and item.parent() is not None:
            return item.parent()
        return None

    def _handle_rect(self, item: QTreeWidgetItem) -> QRect:
        rect = self.visualItemRect(item)
        w = self.viewport().width()
        y = rect.center().y() - self.HANDLE_H // 2
        return QRect(w - self.HANDLE_RIGHT - self.HANDLE_W, y,
                     self.HANDLE_W, self.HANDLE_H)

    def _handle_item_at(self, pos: QPoint) -> QTreeWidgetItem | None:
        item = self.itemAt(pos)
        if item is None or self._chapter_parent(item) is None:
            return None
        if self._handle_rect(item).contains(pos):
            return item
        return None

    def _chapter_rows(self) -> list[tuple[str, int, QTreeWidgetItem, QRect]]:
        rows: list[tuple[str, int, QTreeWidgetItem, QRect]] = []
        for i in range(self.topLevelItemCount()):
            vol = self.topLevelItem(i)
            key = self._item_key(vol)
            if key is None or key[0] != "volume":
                continue
            for j in range(vol.childCount()):
                ch = vol.child(j)
                rect = self.visualItemRect(ch)
                if rect.height() > 0:
                    rows.append((key[1], j, ch, rect))
        return rows

    # ------------------------------------------------------------------
    def _update_drop_target(self, pos: QPoint) -> None:
        vh = self.viewport().height()
        if pos.y() < self.EDGE:
            self._scroll_dir = -1
        elif pos.y() > vh - self.EDGE:
            self._scroll_dir = 1
        else:
            self._scroll_dir = 0

        # 悬停在卷标题上：落到该卷开头（也兼容折叠卷）
        hovered = self.itemAt(pos)
        key = self._item_key(hovered)
        if key is not None and key[0] == "volume" and hovered.childCount() > 0:
            rect = self.visualItemRect(hovered)
            self._drop_target = (key[1], 0)
            self._drop_line_y = max(0, min(rect.bottom(), vh - 1))
            return

        rows = self._chapter_rows()
        if not rows:
            self._drop_target = None
            self._drop_line_y = -1
            return
        gaps = []
        for vid, idx, _ch, rect in rows:
            gaps.append((rect.top(), vid, idx))
            gaps.append((rect.bottom() + 1, vid, idx + 1))
        y = max(0, min(pos.y(), vh - 1))
        best = min(gaps, key=lambda g: (abs(g[0] - y), g[0]))
        self._drop_target = (best[1], best[2])
        self._drop_line_y = max(0, min(best[0], vh - 1))

    def _auto_scroll_step(self) -> None:
        if not self._drag_id or self._scroll_dir == 0:
            return
        bar = self.verticalScrollBar()
        before = bar.value()
        bar.setValue(before + self._scroll_dir * self.SCROLL_STEP)
        if bar.value() != before:
            self._update_drop_target(self._drag_pos)
            self.viewport().update()

    def _start_drag(self, pos: QPoint) -> None:
        item = self._pending
        if item is None:
            return
        key = self._item_key(item)
        if key is None:
            self._reset_drag()
            return
        rect = self.visualItemRect(item)
        if rect.height() > 0:
            self._drag_pixmap = self.viewport().grab(
                QRect(0, rect.top(), self.viewport().width(), rect.height())
            )
        self._drag_id = key[1]
        self._drag_pos = pos
        self.setCursor(Qt.CursorShape.ClosedHandCursor)
        self._auto_timer.start()
        self._update_drop_target(pos)

    def _reset_drag(self) -> None:
        self._pending = None
        self._drag_id = ""
        self._drag_pixmap = None
        self._drop_target = None
        self._drop_line_y = -1
        self._scroll_dir = 0
        self._auto_timer.stop()
        self.unsetCursor()
        self.viewport().update()

    def _inside_viewport(self, pos: QPoint) -> bool:
        return 0 <= pos.x() < self.viewport().width() and 0 <= pos.y() < self.viewport().height()

    def cancel_drag(self) -> bool:
        """Esc 兜底：主窗口 Esc 快捷键会先于树 keyPress 触发，这里统一取消。"""
        if self._drag_id or self._pending is not None:
            self._reset_drag()
            return True
        return False

    # ------------------------------------------------------------------
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position().toPoint()
            item = self._handle_item_at(pos)
            if item is not None:
                self.setFocus()
                self._pending = item
                self._press_pos = pos
                self.viewport().update()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        pos = event.position().toPoint()
        key = self._item_key(self.itemAt(pos))
        hover_id = key[1] if key is not None and key[0] == "chapter" else ""
        if hover_id != self._hover_id:
            self._hover_id = hover_id
            self.viewport().update()

        if self._pending is not None:
            if not self._drag_id:
                delta = pos - self._press_pos
                if abs(delta.x()) >= self.DRAG_THRESHOLD or abs(delta.y()) >= self.DRAG_THRESHOLD:
                    self._start_drag(pos)
            else:
                self._drag_pos = pos
                self._update_drop_target(pos)
                self.viewport().update()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and (
            self._pending is not None or self._drag_id
        ):
            pos = event.position().toPoint()
            if self._drag_id:
                target = self._drop_target
                chapter_id, volume_id, index = self._drag_id, "", -1
                if target is not None and self._inside_viewport(pos):
                    volume_id, index = target
                self._reset_drag()
                if index >= 0:
                    self.move_requested.emit(chapter_id, volume_id, index)
            else:
                self._reset_drag()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if self._drag_id and event.key() == Qt.Key.Key_Escape:
            self._reset_drag()
            event.accept()
            return
        super().keyPressEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        if not self._drag_id and self._hover_id:
            self._hover_id = ""
            self.viewport().update()
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        palette = self.palette()
        text_color = palette.color(QPalette.ColorRole.Text)
        mid_color = palette.color(QPalette.ColorRole.Mid)
        accent = palette.color(QPalette.ColorRole.Highlight)

        for _vid, _idx, item, rect in self._chapter_rows():
            if rect.bottom() < 0 or rect.top() > self.viewport().height():
                continue
            key = self._item_key(item)
            hovered = key is not None and key[1] == self._hover_id
            if item.isSelected():
                color = QColor(accent)
            elif hovered:
                color = QColor(text_color)
            else:
                color = QColor(mid_color)
            color.setAlpha(255 if item.isSelected() else (210 if hovered else 110))
            self._paint_handle(painter, self._handle_rect(item), color)
            if self._drag_id and key is not None and key[1] == self._drag_id:
                pen = painter.pen()
                pen.setColor(accent)
                pen.setWidth(1)
                painter.setPen(pen)
                painter.drawRect(rect.adjusted(1, 0, -1, 0))

        if self._drag_id and self._drag_pixmap is not None and not self._drag_pixmap.isNull():
            painter.setOpacity(0.75)
            y = self._drag_pos.y() - self._drag_pixmap.height() // 2
            painter.drawPixmap(0, y, self._drag_pixmap)
            painter.setOpacity(1.0)

        if self._drag_id and self._drop_line_y >= 0:
            pen = painter.pen()
            pen.setColor(accent)
            pen.setWidth(2)
            painter.setPen(pen)
            painter.drawLine(4, self._drop_line_y, self.viewport().width() - 4,
                             self._drop_line_y)
        painter.end()

    @staticmethod
    def _paint_handle(painter: QPainter, rect: QRect, color: QColor) -> None:
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(color))
        for row in range(3):
            for col in range(2):
                painter.drawRoundedRect(
                    QRect(rect.left() + 3 + col * 5, rect.top() + 5 + row * 7, 3, 3),
                    1.5, 1.5,
                )
        painter.restore()


class SidebarWidget(QWidget):
    """目录 + 相关设定。"""

    chapter_selected = Signal(str)
    chapter_move_requested = Signal(str, str, int)   # 章节 ID, 目标卷 ID, 目标序号
    new_chapter_requested = Signal(str)   # 参数为卷 ID，空字符串表示默认卷
    new_volume_requested = Signal()
    rename_requested = Signal(str, str)   # ("chapter"/"volume", id)
    delete_requested = Signal(str, str)
    export_volume_requested = Signal(str)   # 卷 ID
    new_related_requested = Signal()
    edit_related_requested = Signal(str)
    delete_related_requested = Signal(str)
    snippets_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("Sidebar")
        # 让 QSS 中的 #Sidebar 背景生效（原生 QWidget 默认不绘制样式背景）
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedWidth(252)
        self._book: Book | None = None
        self._chapter_items: dict[str, QTreeWidgetItem] = {}
        self._volume_items: dict[str, QTreeWidgetItem] = {}
        self._collapsed: set[str] = set()
        self._building = False
        self._build_ui()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 14, 10, 10)
        outer.setSpacing(8)

        self.book_title = QLabel("未打开书籍", self)
        self.book_title.setObjectName("BookTitle")
        self.book_meta = QLabel("", self)
        self.book_meta.setObjectName("BookMeta")
        outer.addWidget(self.book_title)
        outer.addWidget(self.book_meta)

        # 两个页签：目录 / 相关
        tabs = QHBoxLayout()
        tabs.setContentsMargins(0, 4, 0, 2)
        tabs.setSpacing(4)
        self.tab_toc = QPushButton("目录", self)
        self.tab_related = QPushButton("相关", self)
        for button in (self.tab_toc, self.tab_related):
            button.setObjectName("TabButton")
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            tabs.addWidget(button, 1)
        self.tab_group = QButtonGroup(self)
        self.tab_group.setExclusive(True)
        self.tab_group.addButton(self.tab_toc)
        self.tab_group.addButton(self.tab_related)
        self.tab_toc.setChecked(True)
        self.tab_toc.clicked.connect(lambda: self.set_page_index(0))
        self.tab_related.clicked.connect(lambda: self.set_page_index(1))
        outer.addLayout(tabs)

        self.pages = QStackedWidget(self)

        # ---------------- 目录页 ----------------
        toc_page = QWidget(self)
        toc_layout = QVBoxLayout(toc_page)
        toc_layout.setContentsMargins(0, 0, 0, 0)
        toc_layout.setSpacing(6)

        self.tree = TocTree(toc_page)
        self.tree.setHeaderHidden(True)
        self.tree.setColumnCount(2)
        self.tree.setIndentation(14)
        self.tree.setRootIsDecorated(False)
        self.tree.setAnimated(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setAllColumnsShowFocus(True)
        self.tree.setExpandsOnDoubleClick(False)
        self.tree.setFrameShape(QFrame.NoFrame)
        self.tree.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.tree.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.tree.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.tree.setColumnWidth(1, 64)
        self.tree.move_requested.connect(self.chapter_move_requested.emit)
        self.tree.itemClicked.connect(self._on_item_clicked)
        self.tree.itemActivated.connect(self._on_item_activated)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._show_tree_menu)
        toc_layout.addWidget(self.tree, 1)

        buttons_row = QHBoxLayout()
        buttons_row.setContentsMargins(0, 0, 0, 0)
        buttons_row.setSpacing(6)
        self.btn_new_volume = QPushButton("新建卷", toc_page)
        self.btn_new_chapter = QPushButton("新建章节", toc_page)
        for button in (self.btn_new_volume, self.btn_new_chapter):
            button.setObjectName("GhostButton")
            button.setCursor(Qt.PointingHandCursor)
            buttons_row.addWidget(button)
        self.btn_new_volume.clicked.connect(self.new_volume_requested.emit)
        self.btn_new_chapter.clicked.connect(self._emit_new_chapter)
        toc_layout.addLayout(buttons_row)

        # ---------------- 相关页 ----------------
        related_page = QWidget(self)
        related_layout = QVBoxLayout(related_page)
        related_layout.setContentsMargins(0, 0, 0, 0)
        related_layout.setSpacing(6)

        self.related_list = QListWidget(related_page)
        self.related_list.setFrameShape(QFrame.NoFrame)
        self.related_list.setWordWrap(True)
        self.related_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.related_list.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.related_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.related_list.customContextMenuRequested.connect(self._show_related_menu)
        self.related_list.itemDoubleClicked.connect(self._on_related_double_clicked)
        related_layout.addWidget(self.related_list, 1)

        related_buttons = QHBoxLayout()
        related_buttons.setSpacing(6)
        self.btn_new_related = QPushButton("添加设定", related_page)
        self.btn_new_related.setObjectName("GhostButton")
        self.btn_new_related.setCursor(Qt.PointingHandCursor)
        self.btn_new_related.clicked.connect(self.new_related_requested.emit)
        related_buttons.addWidget(self.btn_new_related, 1)
        self.btn_snippets = QPushButton("文笔仿写", related_page)
        self.btn_snippets.setObjectName("GhostButton")
        self.btn_snippets.setCursor(Qt.PointingHandCursor)
        self.btn_snippets.setCheckable(True)
        self.btn_snippets.setToolTip("在右侧打开 / 收起文笔仿写片段栏")
        self.btn_snippets.clicked.connect(self.snippets_requested.emit)
        related_buttons.addWidget(self.btn_snippets, 1)
        related_layout.addLayout(related_buttons)

        self.pages.addWidget(toc_page)
        self.pages.addWidget(related_page)
        outer.addWidget(self.pages, 1)

        # 目录树与相关设定列表：同正文一样的隐形快速滚动滑块
        from app.ui.scroll_float import ScrollFlasher

        self._tree_flasher = ScrollFlasher(self.tree, Qt.Vertical, inset=6, edge_margin=10)
        self._related_flasher = ScrollFlasher(
            self.related_list, Qt.Vertical, inset=6, edge_margin=10
        )

        footer = QHBoxLayout()
        footer.setContentsMargins(2, 0, 2, 0)
        self.footer_label = QLabel("共 0 章 · 0 字", self)
        self.footer_label.setObjectName("SidebarFoot")
        self.saved_label = QLabel("", self)
        self.saved_label.setObjectName("SidebarFoot")
        footer.addWidget(self.footer_label)
        footer.addStretch(1)
        footer.addWidget(self.saved_label)
        outer.addLayout(footer)

    def set_page_index(self, index: int) -> None:
        self.pages.setCurrentIndex(index)
        if index == 0:
            self.tab_toc.setChecked(True)
        else:
            self.tab_related.setChecked(True)

    def _emit_new_chapter(self) -> None:
        if not self._book:
            return
        item = self.tree.currentItem()
        volume_id = ""
        if item is not None:
            data = item.data(0, Qt.UserRole)
            if isinstance(data, tuple):
                if data[0] == "volume":
                    volume_id = data[1]
                elif data[0] == "chapter" and item.parent() is not None:
                    parent = item.parent().data(0, Qt.UserRole)
                    if isinstance(parent, tuple):
                        volume_id = parent[1]
        if not volume_id and self._book.volumes:
            volume_id = self._book.volumes[-1].id
        self.new_chapter_requested.emit(volume_id)

    # ------------------------------------------------------------------
    # 目录数据
    # ------------------------------------------------------------------
    def set_book(self, book: Book | None, counts: dict[str, int], current_chapter_id: str = "") -> None:
        # 同一本书重建（字数刷新 / 拖动重排）时保留目录滚动位置，避免视口跳回顶部
        same_book = book is not None and self._book is not None and book.id == self._book.id
        scroll_value = self.tree.verticalScrollBar().value() if same_book else 0
        self._book = book
        self._building = True
        try:
            self.tree.clear()
            self._chapter_items.clear()
            self._volume_items.clear()

            if book is None:
                self.book_title.setText("未打开书籍")
                self.book_meta.setText("")
                self.footer_label.setText("共 0 章 · 0 字")
                self.saved_label.setText("")
                self.related_list.clear()
                return

            self.book_title.setText(book.title)
            self.book_title.setToolTip(book.title)
            self.book_meta.setText(f"{book.author or '佚名'} · 共 {book.chapter_count()} 章")

            bold = QFont(self.font())
            bold.setBold(True)
            small = QFont(self.font())
            if small.pointSize() > 0:
                small.setPointSize(max(7, small.pointSize() - 1))
            caption = QBrush(self.palette().color(QPalette.Mid))

            for vol in book.volumes:
                total = sum(int(counts.get(ch.id, 0)) for ch in vol.chapters)
                volume_item = QTreeWidgetItem([self._volume_label(vol.title, True), f"{total}字"])
                volume_item.setData(0, Qt.UserRole, ("volume", vol.id))
                volume_item.setFont(0, bold)
                volume_item.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
                volume_item.setForeground(1, caption)
                self.tree.addTopLevelItem(volume_item)
                self._volume_items[vol.id] = volume_item

                for ch in vol.chapters:
                    count = int(counts.get(ch.id, 0))
                    child = QTreeWidgetItem([ch.title, f"{count}字"])
                    child.setData(0, Qt.UserRole, ("chapter", ch.id))
                    child.setData(1, Qt.UserRole + 1, count)
                    child.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
                    child.setForeground(1, caption)
                    child.setToolTip(0, ch.title)
                    child.setFont(1, small)
                    volume_item.addChild(child)
                    self._chapter_items[ch.id] = child

                volume_item.setExpanded(vol.id not in self._collapsed)
                volume_item.setText(0, self._volume_label(vol.title, volume_item.isExpanded()))

            if current_chapter_id:
                self.tree.setCurrentItem(self._chapter_items.get(current_chapter_id))
            if same_book and scroll_value > 0:
                self.tree.verticalScrollBar().setValue(scroll_value)
            self.set_summary(book.chapter_count(), sum(int(v) for v in counts.values()))
            self.set_related(book.relations)
        finally:
            self._building = False

    @staticmethod
    def _volume_label(title: str, expanded: bool) -> str:
        return f"{'▾' if expanded else '▸'} {title}"

    def select_chapter(self, chapter_id: str) -> None:
        item = self._chapter_items.get(chapter_id)
        if item is None:
            return
        self._building = True
        self.tree.setCurrentItem(item)
        self.tree.scrollToItem(item, QAbstractItemView.PositionAtCenter)
        self._building = False

    def set_summary(self, chapter_count: int, chars: int) -> None:
        self.footer_label.setText(f"共 {chapter_count} 章 · {format_word_count(chars)} 字")

    def set_saved_time(self, minute: str) -> None:
        self.saved_label.setText(f"已保存 {minute}" if minute else "")

    def set_related(self, items: list[RelatedItem]) -> None:
        self.related_list.clear()
        for related in items:
            text = f"【{related.kind}】{related.name}"
            if related.note:
                text += f"  {related.note}"
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, related.id)
            item.setToolTip(f"{related.kind} · {related.name}\n{related.note}")
            self.related_list.addItem(item)
        if not items:
            hint = QListWidgetItem("还没有相关设定，点击下方按钮添加角色、地点等。")
            hint.setFlags(Qt.NoItemFlags)
            hint.setForeground(QBrush(self.palette().color(QPalette.Mid)))
            self.related_list.addItem(hint)

    # ------------------------------------------------------------------
    # 事件
    # ------------------------------------------------------------------
    def _on_item_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        if self._building:
            return
        self._handle_item(item)

    def _on_item_activated(self, item: QTreeWidgetItem, column: int) -> None:
        if self._building:
            return
        self._handle_item(item)

    def _handle_item(self, item: QTreeWidgetItem) -> None:
        data = item.data(0, Qt.UserRole)
        if not isinstance(data, tuple):
            return
        kind, item_id = data
        if kind == "volume":
            expanded = not item.isExpanded()
            item.setExpanded(expanded)
            title = item.text(0).lstrip("▾▸ ").strip()
            item.setText(0, self._volume_label(title, expanded))
            if expanded:
                self._collapsed.discard(item_id)
            else:
                self._collapsed.add(item_id)
        elif kind == "chapter":
            self.chapter_selected.emit(item_id)

    def _show_tree_menu(self, pos) -> None:
        if not self._book:
            return
        item = self.tree.itemAt(pos)
        menu = QMenu(self)
        data = item.data(0, Qt.UserRole) if item is not None else None

        if isinstance(data, tuple) and data[0] == "chapter":
            chapter_id = data[1]
            vol_id = ""
            if item.parent() is not None:
                parent_data = item.parent().data(0, Qt.UserRole)
                if isinstance(parent_data, tuple):
                    vol_id = parent_data[1]
            menu.addAction("新建章节", lambda: self.new_chapter_requested.emit(vol_id))
            menu.addAction("重命名章节", lambda: self.rename_requested.emit("chapter", chapter_id))
            menu.addAction("删除章节", lambda: self.delete_requested.emit("chapter", chapter_id))
        elif isinstance(data, tuple) and data[0] == "volume":
            volume_id = data[1]
            menu.addAction("新建章节", lambda: self.new_chapter_requested.emit(volume_id))
            menu.addSeparator()
            menu.addAction("重命名卷", lambda: self.rename_requested.emit("volume", volume_id))
            menu.addAction("删除卷", lambda: self.delete_requested.emit("volume", volume_id))
            volume = self._book.find_volume(volume_id) if self._book is not None else None
            export_action = menu.addAction(
                "导出本卷…", lambda: self.export_volume_requested.emit(volume_id)
            )
            export_action.setEnabled(volume is not None and bool(volume.chapters))
            if export_action.isEnabled():
                export_action.setToolTip("一卷一个文件，可选 txt / docx")
            else:
                export_action.setToolTip("该卷还没有章节，暂无可导出内容")
        else:
            menu.addAction("新建卷", self.new_volume_requested.emit)
            menu.addAction("新建章节", self._emit_new_chapter)

        menu.exec(self.tree.viewport().mapToGlobal(pos))
        menu.deleteLater()

    def _show_related_menu(self, pos) -> None:
        item = self.related_list.itemAt(pos)
        if item is None or not item.data(Qt.UserRole):
            return
        related_id = str(item.data(Qt.UserRole))
        menu = QMenu(self)
        menu.addAction("编辑关联设定", lambda: self.edit_related_requested.emit(related_id))
        menu.addAction("删除关联设定", lambda: self.delete_related_requested.emit(related_id))
        menu.exec(self.related_list.viewport().mapToGlobal(pos))
        menu.deleteLater()

    def _on_related_double_clicked(self, item: QListWidgetItem) -> None:
        related_id = item.data(Qt.UserRole)
        if related_id:
            self.edit_related_requested.emit(str(related_id))
