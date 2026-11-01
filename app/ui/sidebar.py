# -*- coding: utf-8 -*-
"""左侧边栏：目录树（卷 / 章节）与“相关”设定页签。"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QBrush, QFont, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView,
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
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.models import Book, RelatedItem
from app.services.word_count import format_word_count


class SidebarWidget(QWidget):
    """目录 + 相关设定。"""

    chapter_selected = Signal(str)
    new_chapter_requested = Signal(str)   # 参数为卷 ID，空字符串表示默认卷
    new_volume_requested = Signal()
    rename_requested = Signal(str, str)   # ("chapter"/"volume", id)
    delete_requested = Signal(str, str)
    new_related_requested = Signal()
    edit_related_requested = Signal(str)
    delete_related_requested = Signal(str)

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

        self.tree = QTreeWidget(toc_page)
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

        self.btn_new_related = QPushButton("添加设定", related_page)
        self.btn_new_related.setObjectName("GhostButton")
        self.btn_new_related.setCursor(Qt.PointingHandCursor)
        self.btn_new_related.clicked.connect(self.new_related_requested.emit)
        related_layout.addWidget(self.btn_new_related)

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

    def show_toc(self) -> None:
        """切换到“目录”页签（新建章节后使用）。"""
        self.set_page_index(0)

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
            self.set_summary(book.chapter_count(), sum(int(v) for v in counts.values()))
            self.set_related(book.relations)
        finally:
            self._building = False

    @staticmethod
    def _volume_label(title: str, expanded: bool) -> str:
        return f"{'▾' if expanded else '▸'} {title}"

    def update_chapter_count(self, chapter_id: str, count: int) -> None:
        item = self._chapter_items.get(chapter_id)
        if item is None:
            return
        item.setText(1, f"{count}字")
        item.setData(1, Qt.UserRole + 1, int(count))
        parent = item.parent()
        if parent is not None:
            total = sum(
                int(parent.child(i).data(1, Qt.UserRole + 1) or 0)
                for i in range(parent.childCount())
            )
            parent.setText(1, f"{total}字")

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
        else:
            menu.addAction("新建卷", self.new_volume_requested.emit)
            menu.addAction("新建章节", self._emit_new_chapter)

        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _show_related_menu(self, pos) -> None:
        item = self.related_list.itemAt(pos)
        if item is None or not item.data(Qt.UserRole):
            return
        related_id = str(item.data(Qt.UserRole))
        menu = QMenu(self)
        menu.addAction("编辑", lambda: self.edit_related_requested.emit(related_id))
        menu.addAction("删除", lambda: self.delete_related_requested.emit(related_id))
        menu.exec(self.related_list.viewport().mapToGlobal(pos))

    def _on_related_double_clicked(self, item: QListWidgetItem) -> None:
        related_id = item.data(Qt.UserRole)
        if related_id:
            self.edit_related_requested.emit(str(related_id))
