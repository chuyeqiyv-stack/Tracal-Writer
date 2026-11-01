# -*- coding: utf-8 -*-
"""主窗口：上图下字工具栏、书架主页、三栏写作工作区、设置抽屉与全部功能串联。"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QActionGroup, QCursor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from app import config
from app.models import Book, Chapter, RelatedItem, Settings, Volume, create_book, new_id, next_chapter_title
from app.services import formatter
from app.services.achievements import (
    HIATUS_THRESHOLDS,
    WORD_MILESTONES,
    AchievementManager,
    ACHIEVEMENTS,
)
from app.services.stats import WritingSpeedTracker
from app.services.webdav_sync import WebDavError, download_backup, upload_backup
from app.services.word_count import count_chars
from app.storage import Storage
from app.ui.bookshelf import BookshelfPage
from app.ui.dialogs import (
    AchievementToast,
    FindReplaceDialog,
    FontDialog,
    GlobalSearchDialog,
    HistoryDialog,
    LineSpacingDialog,
    MessageBox,
    NameGeneratorDialog,
    NewBookDialog,
    NoteEditorWindow,
    RelatedDialog,
    StatsDialog,
    WritingThemeDialog,
)
from app.ui.editor import EditorPage
from app.ui.icons import IconFactory
from app.ui.map import MapDialog
from app.ui.outline import OutlineDialog
from app.ui.settings_panel import SettingsDrawer
from app.ui.sidebar import SidebarWidget
from app.ui.theme import base_theme, build_palette, build_qss, build_theme
from app.ui.timeline import TimelineDialog
from app.ui.widgets import BackgroundWidget, ToolbarButton


class WebDavWorker(QThread):
    """WebDAV 手动同步后台线程：完成或失败通过信号通知主线程弹窗。"""

    finished_ok = Signal(str)   # 成功提示
    failed = Signal(str)        # 失败原因

    def __init__(self, mode: str, settings: Settings, base_dir, parent=None) -> None:
        super().__init__(parent)
        self.mode = mode  # upload / restore
        self.settings = settings
        self.base_dir = base_dir

    def run(self) -> None:
        try:
            if self.mode == "upload":
                upload_backup(self.settings, self.base_dir)
                self.finished_ok.emit("备份已成功上传到云端。")
            else:
                files = download_backup(self.settings, self.base_dir)
                self.finished_ok.emit(f"已从云端恢复 {len(files)} 个数据文件。")
        except WebDavError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"同步失败：{exc}")


class MainWindow(QMainWindow):
    """应用主窗口。"""

    def __init__(self, storage: Storage) -> None:
        super().__init__()
        self.storage = storage
        self.settings: Settings = storage.load_settings()
        self.book: Book | None = None
        self.current_chapter_id = ""
        self._editor_chapter_id = ""     # 编辑器里承载的章节 id（防止把旧文本存进新章节）
        self.current_volume_id = ""
        self.counts: dict[str, int] = {}
        self._other_chars = 0
        self._total_chars = 0
        self._dirty = False
        self._save_error_shown = False
        self.speed = WritingSpeedTracker()
        self.achievements = AchievementManager(storage)
        self._words_pending = 0          # 尚未落盘的今日键盘打字字数
        self._last_auto_snapshot = 0.0   # 上一次自动快照时间（去重用）
        self._webdav_worker: WebDavWorker | None = None
        self._timeline_dialogs: list[TimelineDialog] = []
        self._outline_dialogs: list[OutlineDialog] = []
        self._map_dialogs: dict[str, MapDialog] = {}
        self._note_windows: dict[str, NoteEditorWindow] = {}
        self._load_imported_fonts()

        self._find_dialog: FindReplaceDialog | None = None
        self._search_dialog: GlobalSearchDialog | None = None

        self.setWindowTitle(config.APP_NAME)
        self.resize(1360, 880)
        self.setMinimumSize(1080, 680)

        self._build_ui()
        self._build_timers()
        self._build_shortcuts()
        self._connect_signals()

        self._ensure_first_run_demo()
        self.apply_theme()
        self.show_bookshelf()

    # ==================================================================
    # 字体导入
    # ==================================================================
    def _load_imported_fonts(self) -> None:
        """启动时加载用户导入过的本地字体文件。"""
        from PySide6.QtGui import QFontDatabase

        for path in list(self.settings.imported_fonts):
            if path and os.path.exists(path):
                QFontDatabase.addApplicationFont(path)
            else:
                self.settings.imported_fonts.remove(path)

    # ==================================================================
    # 界面搭建
    # ==================================================================
    def _build_ui(self) -> None:
        self.root = BackgroundWidget(base_theme(self.settings.mode), self)
        self.setCentralWidget(self.root)
        root_layout = QVBoxLayout(self.root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # 顶部双栏：主页极简顶栏 / 编辑器完整工具栏，随模式切换
        self.top_stack = QStackedWidget(self.root)
        self.top_stack.setFixedHeight(64)
        self.home_bar = self._build_homebar()
        self.top_stack.addWidget(self.home_bar)

        self.stack = QStackedWidget(self.root)

        self.shelf = BookshelfPage(self.stack)
        self.stack.addWidget(self.shelf)

        self.workspace = QWidget(self.stack)
        workspace_layout = QHBoxLayout(self.workspace)
        workspace_layout.setContentsMargins(0, 0, 0, 0)
        workspace_layout.setSpacing(0)
        self.sidebar = SidebarWidget(self.workspace)
        self.editor_page = EditorPage(self.settings, self.workspace)
        workspace_layout.addWidget(self.sidebar)
        workspace_layout.addWidget(self._build_side_toggle())
        workspace_layout.addWidget(self.editor_page, 1)
        self.stack.addWidget(self.workspace)

        # 工具栏依赖 editor_page 的撤销 / 重做方法，因此放在工作区之后创建
        self.topbar = self._build_topbar()
        self.top_stack.addWidget(self.topbar)
        root_layout.addWidget(self.top_stack)
        root_layout.addWidget(self.stack, 1)

        self.drawer = SettingsDrawer(self.settings, self.root)
        self.drawer.raise_()

    def _build_side_toggle(self) -> QFrame:
        """目录侧栏旁的细条：点击箭头收起 / 展开左侧目录。"""
        bar = QFrame(self.workspace)
        bar.setObjectName("SideToggleBar")
        bar.setFixedWidth(16)
        layout = QVBoxLayout(bar)
        layout.setContentsMargins(2, 10, 2, 10)
        layout.addStretch(1)
        self.side_toggle_button = QPushButton(bar)
        self.side_toggle_button.setObjectName("SideToggle")
        self.side_toggle_button.setCursor(Qt.PointingHandCursor)
        self.side_toggle_button.setFixedSize(12, 64)
        self.side_toggle_button.setIconSize(QSize(12, 12))
        self.side_toggle_button.clicked.connect(self.toggle_sidebar)
        layout.addWidget(self.side_toggle_button)
        layout.addStretch(1)
        return bar

    def _update_side_toggle_arrow(self) -> None:
        """根据目录显隐切换箭头方向与提示。"""
        factory = getattr(self, "_icon_factory", None)
        visible = self.sidebar.isVisible()
        if factory is not None:
            self.side_toggle_button.setIcon(factory.icon("left" if visible else "right", 12))
        self.side_toggle_button.setToolTip("收起目录" if visible else "展开目录")

    def _build_homebar(self) -> QWidget:
        """主页极简顶栏：只有品牌与少量入口，保持书架干净（参考图二）。"""
        bar = QWidget(self.root)
        bar.setObjectName("TopBar")
        bar.setAttribute(Qt.WA_StyledBackground, True)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(18, 6, 14, 6)
        layout.setSpacing(4)

        brand_box = QVBoxLayout()
        brand_box.setSpacing(0)
        brand = QLabel(config.APP_NAME, bar)
        brand.setObjectName("Brand")
        brand_sub = QLabel(config.APP_NAME_EN.upper(), bar)
        brand_sub.setObjectName("BrandSub")
        brand_box.addWidget(brand)
        brand_box.addWidget(brand_sub)
        layout.addLayout(brand_box)
        layout.addStretch(1)

        theme = base_theme(self.settings.mode)
        theme_button = ToolbarButton("主题", theme, bar)
        theme_button.setToolTip("深色 / 浅色与写作主题")
        theme_button.setProperty("icon_name", "theme")
        theme_button.clicked.connect(lambda: self.show_theme_menu(theme_button))
        layout.addWidget(theme_button)

        settings_button = ToolbarButton("设置", theme, bar)
        settings_button.setToolTip("更多设置（再次点击可退出）")
        settings_button.setProperty("icon_name", "settings")
        settings_button.setCheckable(True)
        settings_button.clicked.connect(self.toggle_drawer)
        layout.addWidget(settings_button)

        self.home_buttons: dict[str, ToolbarButton] = {
            "theme": theme_button,
            "settings": settings_button,
        }
        return bar

    def _build_topbar(self) -> QWidget:
        """写作页顶栏：三段式布局（导航 / 编辑排版 / 数据设置），无分割线。"""
        bar = QWidget(self.root)
        bar.setObjectName("TopBar")
        bar.setAttribute(Qt.WA_StyledBackground, True)
        bar.setFixedHeight(64)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(16, 6, 16, 6)
        layout.setSpacing(6)

        self.buttons: dict[str, ToolbarButton] = {}
        theme = base_theme(self.settings.mode)

        def add(key: str, label: str, icon_name: str, tooltip: str, slot) -> ToolbarButton:
            button = ToolbarButton(label, theme, bar)
            button.setToolTip(tooltip)
            button.setProperty("icon_name", icon_name)
            button.clicked.connect(slot)
            layout.addWidget(button)
            self.buttons[key] = button
            return button

        # 左段：导航与查找
        add("shelf", "书架", "shelf", "返回书架主页", self.show_bookshelf)
        add("toc", "目录", "toc", "收起 / 展开左侧目录", self.toggle_sidebar)
        add("find", "查找", "find", "查找替换（Ctrl+F）", self.show_find_dialog)
        add("search", "搜索", "search", "全局搜索（Ctrl+Shift+F）", self.show_search_dialog)

        # 中段：编辑与排版（用弹性间距与两侧拉开）
        layout.addStretch(1)
        add("undo", "撤销", "undo", "撤销（Ctrl+Z）", self.editor_page.undo)
        add("redo", "重做", "redo", "重做（Ctrl+Y）", self.editor_page.redo)
        add("format", "排版", "format", "智能排版（右键打开排版设置）", self.smart_format)
        self.buttons["format"].setContextMenuPolicy(Qt.CustomContextMenu)
        self.buttons["format"].customContextMenuRequested.connect(
            lambda _pos: self._show_format_menu()
        )
        add("font", "字体", "font", "字体设置", self.show_font_dialog)
        add("spacing", "行距", "spacing", "行距设置", self.show_spacing_dialog)
        add("wt", "背板", "palette", "写作区主题面板", self.show_writing_theme)
        add("theme", "主题", "theme", "深色 / 浅色与写作主题", lambda: self.show_theme_menu(self.buttons["theme"]))
        layout.addStretch(1)

        # 右段：数据与设置
        add("history", "历史", "history", "历史版本", self.show_history_dialog)
        add("count", "统计", "count", "字数统计", self.show_stats_dialog)
        add("speed", "时速", "speed", "码字时速", self.show_speed_dialog)
        add("name", "起名", "name", "起名助手", self.show_name_dialog)
        more = add("settings", "设置", "settings", "更多设置", self.toggle_drawer)
        more.setCheckable(True)
        self.buttons["toc"].setCheckable(True)
        self.buttons["speed"].setCheckable(True)

        self.buttons["undo"].setEnabled(False)
        self.buttons["redo"].setEnabled(False)
        return bar

    # ==================================================================
    # 定时器与快捷键
    # ==================================================================
    def _build_timers(self) -> None:
        self.autosave_timer = QTimer(self)
        self.autosave_timer.setSingleShot(True)
        self.autosave_timer.setInterval(config.AUTOSAVE_DELAY_MS)
        self.autosave_timer.timeout.connect(self.save_current_book)

        # 每日打字统计：打字后 3 秒落盘一次（避免每键写盘）
        self.words_timer = QTimer(self)
        self.words_timer.setSingleShot(True)
        self.words_timer.setInterval(3000)
        self.words_timer.timeout.connect(self._flush_words)

        # 成就检查：每 30 秒轮询（挥斥方遒 = 单次写作超过 1 小时）
        self.achievement_timer = QTimer(self)
        self.achievement_timer.setInterval(30 * 1000)
        self.achievement_timer.timeout.connect(self._check_achievements)
        self.achievement_timer.start()

        self.sidebar_timer = QTimer(self)
        self.sidebar_timer.setSingleShot(True)
        self.sidebar_timer.setInterval(400)
        self.sidebar_timer.timeout.connect(self._refresh_sidebar_counts)

    def _build_shortcuts(self) -> None:
        save_shortcut = QShortcut(QKeySequence("Ctrl+S"), self)
        save_shortcut.activated.connect(self._manual_save)

        find_shortcut = QShortcut(QKeySequence("Ctrl+F"), self)
        find_shortcut.activated.connect(self.show_find_dialog)

        global_shortcut = QShortcut(QKeySequence("Ctrl+Shift+F"), self)
        global_shortcut.activated.connect(self.show_search_dialog)

        new_chapter_shortcut = QShortcut(QKeySequence("Ctrl+N"), self)
        new_chapter_shortcut.activated.connect(
            lambda: self.new_chapter(self.current_volume_id)
        )

        # 编辑器重做：Ctrl+Z 撤销原生支持；Ctrl+Y 在部分平台不触发，显式补上
        redo_shortcut = QShortcut(QKeySequence("Ctrl+Y"), self.editor_page.editor)
        redo_shortcut.setContext(Qt.WidgetShortcut)
        redo_shortcut.activated.connect(self.editor_page.editor.redo)
        redo_shortcut2 = QShortcut(QKeySequence("Ctrl+Shift+Z"), self.editor_page.editor)
        redo_shortcut2.setContext(Qt.WidgetShortcut)
        redo_shortcut2.activated.connect(self.editor_page.editor.redo)

        escape_shortcut = QShortcut(QKeySequence("Escape"), self)
        escape_shortcut.activated.connect(lambda: self.drawer.close_drawer())

    def _connect_signals(self) -> None:
        # 书架
        self.shelf.open_book_requested.connect(self.open_book)
        self.shelf.new_book_requested.connect(self.new_book)
        self.shelf.rename_book_requested.connect(self.rename_book)
        self.shelf.trash_book_requested.connect(self.trash_book)
        self.shelf.restore_book_requested.connect(self.restore_book)
        self.shelf.delete_book_requested.connect(self.delete_book)
        self.shelf.delete_forever_requested.connect(self.delete_book)
        self.shelf.empty_trash_requested.connect(self.empty_trash)
        self.shelf.new_group_requested.connect(self.new_group)
        self.shelf.delete_group_requested.connect(self.delete_group)
        self.shelf.move_group_requested.connect(self.move_to_group)
        self.shelf.profile_edit_requested.connect(self.edit_profile)
        self.shelf.avatar_import_requested.connect(self.change_avatar)
        self.shelf.view_changed.connect(self._on_shelf_view_changed)

        # 时间轴与设定（书架页管理）
        self.shelf.open_timeline_requested.connect(self.open_timeline)
        self.shelf.new_timeline_requested.connect(self.new_timeline)
        self.shelf.rename_timeline_requested.connect(self.rename_timeline)
        self.shelf.delete_timeline_requested.connect(self.delete_timeline)
        self.shelf.open_note_requested.connect(self.open_note)
        self.shelf.new_note_requested.connect(self.new_note)
        self.shelf.rename_note_requested.connect(self.rename_note)
        self.shelf.delete_note_requested.connect(self.delete_note)

        # 大纲（思维导图，书架页管理）
        self.shelf.open_outline_requested.connect(self.open_outline)
        self.shelf.new_outline_requested.connect(self.new_outline)
        self.shelf.rename_outline_requested.connect(self.rename_outline)
        self.shelf.delete_outline_requested.connect(self.delete_outline)

        # 世界地图（可创建多张）与导出
        self.shelf.open_map_requested.connect(self.open_map)
        self.shelf.new_map_requested.connect(self.new_map)
        self.shelf.rename_map_requested.connect(self.rename_map)
        self.shelf.delete_map_requested.connect(self.delete_map)
        self.shelf.stats_open_requested.connect(self.show_shelf_stats)
        self.shelf.export_book_requested.connect(self.export_book)
        self.shelf.cover_change_book_requested.connect(self.change_book_cover)

        # 成就：成就页右键设置展示勋章
        self.shelf.medal_showcase_requested.connect(self._set_showcase_medal)

        # 目录与编辑器
        self.sidebar.chapter_selected.connect(self.open_chapter)
        self.sidebar.new_chapter_requested.connect(self.new_chapter)
        self.sidebar.new_volume_requested.connect(self.new_volume)
        self.sidebar.rename_requested.connect(self.rename_item)
        self.sidebar.delete_requested.connect(self.delete_item)
        self.sidebar.new_related_requested.connect(self.add_related)
        self.sidebar.edit_related_requested.connect(self.edit_related)
        self.sidebar.delete_related_requested.connect(self.delete_related)

        self.editor_page.content_edited.connect(self._on_content_edited)
        self.editor_page.chapter_chars_changed.connect(self._on_chapter_chars)
        self.editor_page.speed_delta.connect(self.speed.record)
        # 只有键盘输入的字才计入每日打字统计（粘贴 / 拖放不计）
        self.editor_page.typed_delta.connect(self._on_typed_delta)
        self.editor_page.set_speed_tracker(self.speed)
        self.editor_page.speed_panel_toggled.connect(
            self.buttons["speed"].setChecked
        )
        # 时速面板切换单位后立即持久化
        self.editor_page.speed_panel.unit_changed.connect(self._on_speed_unit_changed)

        self.editor_page.editor.undoAvailable.connect(self.buttons["undo"].setEnabled)
        self.editor_page.editor.redoAvailable.connect(self.buttons["redo"].setEnabled)

        # 设置抽屉
        self.drawer.settings_changed.connect(self._on_settings_changed)
        self.drawer.profile_changed.connect(self._on_profile_changed)
        self.drawer.webdav_upload_requested.connect(self.webdav_upload)
        self.drawer.webdav_restore_requested.connect(self.webdav_restore)
        self.drawer.data_dir_change_requested.connect(self.change_data_dir)
        self.drawer.set_data_dir(str(self.storage.base_dir))
        self.drawer.opened.connect(
            lambda: (
                self.buttons["settings"].setChecked(True),
                self.home_buttons["settings"].setChecked(True),
            )
        )
        self.drawer.closed.connect(
            lambda: (
                self.buttons["settings"].setChecked(False),
                self.home_buttons["settings"].setChecked(False),
            )
        )

    # ==================================================================
    # 书架与主题
    # ==================================================================
    def _ensure_first_run_demo(self) -> None:
        """第一次启动时创建一本示例书，方便直接看到完整目录结构。"""
        library = self.storage.load_library()
        if library.get("initialized"):
            return
        demo = create_book("示例作品：星落长夜", "墨迹作者", demo=True)
        # save_book 会同步书目到 library.json，因此之后需要重新读取再更新标记
        self.storage.save_book(demo, 0)
        library = self.storage.load_library()
        library["initialized"] = True
        library["last_book_id"] = demo.id
        self.storage.save_library(library)

    def apply_theme(self) -> None:
        """根据设置重建调色板、QSS、图标与各处颜色。"""
        theme = build_theme(self.settings)
        self._theme = theme

        app = QApplication.instance()
        if app is not None:
            app.setPalette(build_palette(theme))
            app.setStyleSheet(build_qss(theme))

        self._icon_factory = IconFactory(theme["icon"])
        self._refresh_icons()
        self.root.set_theme(theme)
        self.editor_page.apply_settings(self.settings)
        self.editor_page.apply_writing_background()
        self.drawer.refresh_values()
        self.drawer.repaint_switches()
        self._update_shelf()
        for dialog in self._map_dialogs.values():
            if dialog.isVisible():
                dialog.set_theme(theme)

    def _refresh_icons(self) -> None:
        factory = self._icon_factory
        theme = self._theme
        for button in self.buttons.values():
            name = str(button.property("icon_name") or "")
            if name:
                button.set_icon(factory.icon(name, 20))
            button.set_theme(theme)
        for button in getattr(self, "home_buttons", {}).values():
            name = str(button.property("icon_name") or "")
            if name:
                button.set_icon(factory.icon(name, 20))
            button.set_theme(theme)
        self._update_side_toggle_arrow()
        self.editor_page.refresh_icons(factory)

    def show_theme_menu(self, anchor) -> None:
        """主题按钮菜单：深 / 浅色一键切换 + 写作主题面板。"""
        menu = QMenu(self)
        mode_group = QActionGroup(menu)
        light_action = menu.addAction("浅色模式")
        light_action.setCheckable(True)
        light_action.setChecked(self.settings.mode == "light")
        light_action.setActionGroup(mode_group)
        light_action.triggered.connect(lambda: self._set_mode("light"))

        dark_action = menu.addAction("深色模式")
        dark_action.setCheckable(True)
        dark_action.setChecked(self.settings.mode == "dark")
        dark_action.setActionGroup(mode_group)
        dark_action.triggered.connect(lambda: self._set_mode("dark"))

        menu.addSeparator()
        menu.addAction("写作主题面板…", self.show_writing_theme)
        menu.addAction("字体设置…", self.show_font_dialog)
        menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def _set_mode(self, mode: str) -> None:
        if self.settings.mode == mode:
            return
        self.settings.mode = mode
        self.storage.save_settings(self.settings)
        self.apply_theme()

    def show_bookshelf(self) -> None:
        """保存当前书并回到书架（主页使用极简顶栏）。"""
        self.flush()
        self.top_stack.setCurrentIndex(0)
        self.stack.setCurrentIndex(0)
        self._update_shelf()

    def _update_shelf(self) -> None:
        library = self.storage.load_library()
        cover_paths: dict[str, str] = {}
        for meta in library["books"]:
            path = self.storage.cover_abs(str(meta.get("cover") or ""))
            if path is not None:
                cover_paths[str(meta.get("id"))] = str(path)
        self.shelf.set_books(library["books"], library["groups"], cover_paths)
        self.shelf.set_timelines(library.get("timelines", []))
        self.shelf.set_notes(library.get("notes", []))
        self.shelf.set_outlines(library.get("outlines", []))
        self.shelf.set_maps(self.storage.load_maps())
        self.shelf.set_achievements(self.achievements.unlocked)
        self.shelf.set_showcase(self.settings.showcase_medal)
        self.shelf.set_view(self.settings.shelf_view)
        self.shelf.set_user(self.settings.nickname, self.settings.avatar_path)

    def _on_shelf_view_changed(self, view: str) -> None:
        self.settings.shelf_view = view
        self.storage.save_settings(self.settings)

    # ==================================================================
    # 书籍管理
    # ==================================================================
    def new_book(self) -> None:
        dialog = NewBookDialog(self)
        if dialog.exec() != NewBookDialog.Accepted:
            return
        title, author, tags, cover_path = dialog.values()
        book = create_book(title, author, demo=False, tags=tags)
        if cover_path:
            relative = self.storage.save_cover_image(cover_path, book.id)
            book.cover = relative
        self.storage.save_book(book, 0)
        self._update_shelf()
        self._unlock_achievement("first_book")   # 卧龙出山：创立第一本书
        # 彩蛋：书名与作者都输入 114514 → 解锁全部成就
        if title == "114514" and author == "114514":
            for key in ACHIEVEMENTS:
                self._unlock_achievement(key)
        # 创建后留在书架主页，是否进入由用户自己选择
        self.show_bookshelf()
        self.shelf.nav.set_page("home")

    def rename_book(self, book_id: str) -> None:
        book = self.storage.load_book(book_id)
        if book is None:
            return
        text, ok = QInputDialog.getText(self, "重命名", "新的书名：", text=book.title)
        if ok and text.strip():
            book.title = text.strip()
            book.touch()
            self.storage.save_book(book, None)
            if self.book is not None and self.book.id == book_id:
                self._load_current_book(book_id, keep_chapter=True)
            self._update_shelf()

    def change_book_cover(self, book_id: str) -> None:
        """更换书籍封面：选择本地图片后立即保存并刷新书架。"""
        book = self.storage.load_book(book_id)
        if book is None:
            return
        path, _filter = QFileDialog.getOpenFileName(
            self,
            "选择封面图片",
            "",
            "图片文件 (*.png *.jpg *.jpeg *.bmp *.webp);;所有文件 (*)",
        )
        if not path:
            return
        relative = self.storage.save_cover_image(path, book.id)
        if not relative:
            return
        book.cover = relative
        book.touch()
        self.storage.save_book(book, None)
        self._update_shelf()

    def trash_book(self, book_id: str) -> None:
        book = self.storage.load_book(book_id)
        title = book.title if book is not None else "该书"
        if not self._confirm(f"把「{title}」移入回收站？", "可在书架的回收站页恢复。"):
            return
        self.storage.trash_book(book_id)
        if self.book is not None and self.book.id == book_id:
            self.book = None
            self.current_chapter_id = ""
            self._editor_chapter_id = ""
            self.editor_page.clear_page()
            self.sidebar.set_book(None, {}, "")
            self.show_bookshelf()
        else:
            self._update_shelf()

    def restore_book(self, book_id: str) -> None:
        self.storage.restore_book(book_id)
        self._update_shelf()

    def empty_trash(self) -> None:
        count = len([m for m in self.storage.load_library()["books"] if m.get("trashed")])
        if count == 0:
            return
        if not self._confirm(f"清空回收站（{count} 本作品）？", "彻底删除后无法恢复。"):
            return
        self.storage.empty_trash()
        self._update_shelf()

    def delete_book(self, book_id: str) -> None:
        book = self.storage.load_book(book_id)
        title = book.title if book is not None else "该书"
        if not self._confirm(f"彻底删除「{title}」？", "删除后无法恢复（包括其历史版本）。"):
            return
        self.storage.delete_book(book_id)
        if self.book is not None and self.book.id == book_id:
            self.book = None
            self.current_chapter_id = ""
            self._editor_chapter_id = ""
            self.editor_page.clear_page()
            self.sidebar.set_book(None, {}, "")
            self.show_bookshelf()
        else:
            self._update_shelf()

    def new_group(self) -> None:
        text, ok = QInputDialog.getText(self, "新建分组", "分组名称：")
        if ok and text.strip():
            self.storage.add_group(text.strip())
            self._update_shelf()

    def move_to_group(self, book_id: str) -> None:
        library = self.storage.load_library()
        names = ["（无分组）"] + [g["name"] for g in library["groups"]]
        choice, ok = QInputDialog.getItem(self, "移动到分组", "选择分组：", names, 0, False)
        if not ok:
            return
        group_id = ""
        for g in library["groups"]:
            if g["name"] == choice:
                group_id = str(g["id"])
                break
        book = self.storage.load_book(book_id)
        if book is not None:
            book.group_id = group_id
            self.storage.save_book(book, None)
        else:
            for meta in library["books"]:
                if meta.get("id") == book_id:
                    meta["group_id"] = group_id
            self.storage.save_library(library)
        self._update_shelf()

    def delete_group(self, group_id: str) -> None:
        """右键分组按钮删除分组：组内作品保留，回到未分组状态。"""
        library = self.storage.load_library()
        name = next(
            (
                str(g.get("name") or "")
                for g in library["groups"]
                if str(g.get("id") or "") == str(group_id)
            ),
            "该分组",
        )
        if not MessageBox.question(
            self,
            "删除分组",
            f"确定删除分组「{name}」吗？\n组内作品不会被删除，将回到未分组状态。",
        ):
            return
        self.storage.delete_group(group_id)
        self.shelf.reset_group_if_deleted(str(group_id))
        self._update_shelf()

    def edit_profile(self) -> None:
        """修改昵称与头像。"""
        nickname, ok = QInputDialog.getText(self, "个人资料", "昵称：", text=self.settings.nickname)
        if not ok:
            return
        avatar = self.settings.avatar_path
        if MessageBox.question(self, "头像", "要更换头像图片吗？"):
            path, _f = QFileDialog.getOpenFileName(
                self, "选择头像", "", "图片文件 (*.png *.jpg *.jpeg *.webp *.bmp)"
            )
            if path:
                avatar = self.storage.save_avatar(path) or path
        self.settings.nickname = nickname.strip() or "写作者"
        self.settings.avatar_path = avatar
        self.storage.save_settings(self.settings)
        self.shelf.set_user(self.settings.nickname, self.settings.avatar_path)
        self.drawer.refresh_values()

    def change_avatar(self) -> None:
        """直接点击左下角头像：选择图片并立即导入。"""
        path, _f = QFileDialog.getOpenFileName(
            self, "选择头像", "", "图片文件 (*.png *.jpg *.jpeg *.webp *.bmp)"
        )
        if not path:
            return
        avatar = self.storage.save_avatar(path) or path
        if not avatar:
            MessageBox.warning(self, "导入失败", "头像图片保存失败，请重试。")
            return
        self.settings.avatar_path = avatar
        self.storage.save_settings(self.settings)
        self.shelf.set_user(self.settings.nickname, avatar)
        self.drawer.refresh_values()

    def _on_profile_changed(self, nickname: str, avatar: str) -> None:
        self.storage.save_settings(self.settings)
        self.shelf.set_user(nickname, avatar)

    # ==================================================================
    # 打开书籍 / 章节
    # ==================================================================
    def open_book(self, book_id: str) -> None:
        self.flush()
        self._load_current_book(book_id)
        if self.book is not None:
            self.top_stack.setCurrentIndex(1)
            self.stack.setCurrentIndex(1)
            self.open_chapter(self.current_chapter_id)
            self.editor_page.focus_editor()

    def _load_current_book(self, book_id: str, keep_chapter: bool = False) -> None:
        book = self.storage.load_book(book_id)
        if book is None:
            return
        self.book = book
        if not keep_chapter or not book.find_chapter(self.current_chapter_id)[1]:
            self.current_chapter_id = book.last_chapter_id
        volume = book.volume_of(self.current_chapter_id)
        self.current_volume_id = volume.id if volume is not None else ""
        self._recount_all()
        self.sidebar.set_book(book, self.counts, self.current_chapter_id)
        self.storage.set_last_book(book.id)

    def open_chapter(self, chapter_id: str) -> None:
        if self.book is None:
            return
        self.flush()
        volume, chapter = self.book.find_chapter(chapter_id)
        if chapter is None:
            return
        self.current_chapter_id = chapter_id
        self.current_volume_id = volume.id if volume is not None else ""
        self.editor_page.setDisabled(False)
        self.editor_page.load_text(chapter.title, chapter.content)
        self._editor_chapter_id = chapter_id
        self.editor_page.set_total_chars(self._total_chars)
        self.sidebar.select_chapter(chapter_id)
        self.editor_page.focus_editor()

    # ==================================================================
    # 保存与快照
    # ==================================================================
    def flush(self, force_snapshot: bool = False) -> None:
        """立即保存当前章节的未落盘修改。

        每次自动保存都会记录一个历史版本；自动快照按 config.AUTO_SNAPSHOT_GAP
        去重，手动保存（Ctrl+S）等 force_snapshot=True 的调用始终立即记录。
        """
        if self.book is None or not self.current_chapter_id:
            return
        if self._editor_chapter_id != self.current_chapter_id:
            # 编辑器里还是别的书/章节的旧文本（如刚切换书籍但尚未载入），
            # 此时把编辑器文本写回会覆盖新章节内容，必须跳过。
            return
        volume, chapter = self.book.find_chapter(self.current_chapter_id)
        if chapter is None:
            return
        text = self.editor_page.current_text()
        if text == chapter.content:
            return
        chapter.content = text
        chapter.updated_at = time.time()
        self.book.touch()
        self.counts[chapter.id] = count_chars(text, self.settings.exclude_punct)
        self.storage.save_book(self.book, self._total_chars)
        self.sidebar.set_saved_time(time.strftime("%H:%M"))

        # 历史版本：随自动保存记录，每本书最多保留 history_cap() 个
        now = time.time()
        if force_snapshot or now - self._last_auto_snapshot >= config.AUTO_SNAPSHOT_GAP:
            self._last_auto_snapshot = now
            self.storage.save_snapshot(
                self.book.id,
                chapter.id,
                text,
                self.counts[chapter.id],
                cap=self.achievements.history_cap(),
            )

    def save_current_book(self) -> None:
        if self._dirty:
            self.flush()
            self._dirty = False
            self.editor_page.set_saved_text(time.strftime("%H:%M"))

    def _manual_save(self) -> None:
        self.flush(force_snapshot=True)
        self._dirty = False
        self.editor_page.set_saved_text(time.strftime("%H:%M"))
        self.editor_page.focus_editor()

    def _on_content_edited(self) -> None:
        self._dirty = True
        self.autosave_timer.start()

    def _on_chapter_chars(self, chars: int) -> None:
        if self.book is None or not self.current_chapter_id:
            return
        self.counts[self.current_chapter_id] = chars
        self._total_chars = self._other_chars + chars
        self.editor_page.set_total_chars(self._total_chars)
        self.sidebar_timer.start()

    # ==================================================================
    # 每日打字统计与成就
    # ==================================================================
    def _on_typed_delta(self, delta: int) -> None:
        """编辑器报来一批键盘输入的字数（粘贴已在编辑器侧过滤）。"""
        if delta <= 0:
            return
        self._words_pending += delta
        self.words_timer.start()

    def _flush_words(self) -> None:
        """把累计的键盘打字字数写入 words_daily.json，并检查相关成就。"""
        pending, self._words_pending = self._words_pending, 0
        if pending <= 0:
            return
        day = time.strftime("%Y-%m-%d")
        data = self.storage.add_words(pending, day)
        self._check_persistent(data)
        if self.book is not None:
            # 单本每日有效码字：断更警示 / 英雄重生的判定依据
            self.storage.add_book_words(self.book.id, pending, day)
            self._check_rebirth()

    def _check_achievements(self) -> None:
        """定时轮询成就条件（挥斥方遒 / 字数里程碑 / 断更警示）。"""
        if not self.achievements.has("marathon") and self.speed.active_seconds > 3600:
            self._unlock_achievement("marathon")
        self._check_book_achievements()

    def _check_book_achievements(self) -> None:
        """单本成就：字数里程碑（正向）+ 断更警示（负向）。"""
        library = self.storage.load_library()
        now = time.time()
        book_words = self.storage.load_book_words()
        for meta in library.get("books", []):
            if meta.get("trashed"):
                continue
            book_id = str(meta.get("id") or "")
            if not book_id:
                continue
            # 字数里程碑：任一作品累计字数达标即解锁（全局仅一次）
            words = int(meta.get("word_count") or 0)
            for key, threshold in WORD_MILESTONES:
                if words >= threshold:
                    self._unlock_achievement(key)
            # 断更警示：最后有效码字日与最后更新时间取较晚者
            days = ((book_words.get(book_id) or {}).get("days")) or {}
            last_ts = 0.0
            for day_key in days:
                try:
                    last_ts = max(
                        last_ts, time.mktime(time.strptime(day_key, "%Y-%m-%d")) + 86400
                    )
                except ValueError:
                    continue
            try:
                last_ts = max(last_ts, float(meta.get("updated_at") or 0))
            except (TypeError, ValueError):
                pass
            if last_ts <= 0:
                continue
            idle_days = int((now - last_ts) // 86400)
            for key, threshold in HIATUS_THRESHOLDS:
                if idle_days >= threshold and self._unlock_achievement(key):
                    # 记录是哪本书、何时达成（英雄重生需要本书的文魂永寂时间）
                    self.storage.set_book_medal(
                        book_id, key, self.achievements.unlocked[key]
                    )

    def _check_rebirth(self) -> None:
        """英雄重生：本书已达成文魂永寂，之后累计新增有效码字 ≥500。"""
        if self.achievements.has("rebirth") or self.book is None:
            return
        medals = self.storage.load_book_medals().get(self.book.id) or {}
        try:
            since = float(medals.get("hiatus_365d") or 0)
        except (TypeError, ValueError):
            since = 0.0
        if since <= 0:
            return
        days = ((self.storage.load_book_words().get(self.book.id) or {}).get("days")) or {}
        total = 0
        for day_key, n in days.items():
            try:
                day_ts = time.mktime(time.strptime(day_key, "%Y-%m-%d"))
            except ValueError:
                continue
            if day_ts + 86400 > since:   # 文魂永寂解锁之后的码字日
                total += int(n or 0)
        if total >= 500:
            self._unlock_achievement("rebirth")

    def _check_persistent(self, words: dict) -> None:
        """坚持不懈：最近一个月（含今天）每天都更新 500 字以上。"""
        if self.achievements.has("persistent"):
            return
        days: dict[str, int] = words.get("days") or {}
        today = time.localtime()
        for offset in range(30):
            day_time = time.mktime(
                (today.tm_year, today.tm_mon, today.tm_mday - offset, 0, 0, 0, 0, 0, -1)
            )
            key = time.strftime("%Y-%m-%d", time.localtime(day_time))
            if int(days.get(key) or 0) < 500:
                return
        self._unlock_achievement("persistent")

    def _unlock_achievement(self, key: str) -> bool:
        """解锁成就：初次解锁时右下角弹窗庆祝；返回是否为本次初次解锁。"""
        if not self.achievements.unlock(key):
            return False
        toast = AchievementToast(key, self)
        toast.show_at()
        self.shelf.set_achievements(self.achievements.unlocked)
        self.shelf.set_showcase(self.settings.showcase_medal)
        return True

    def _set_showcase_medal(self, key: str) -> None:
        """成就页右键：设置 / 卸下首页左下角展示的勋章（仅限已解锁成就）。"""
        target = str(key or "")
        if target and not self.achievements.has(target):
            return
        if target == self.settings.showcase_medal:
            return
        self.settings.showcase_medal = target
        self.storage.save_settings(self.settings)
        self.shelf.set_showcase(target)

    def _refresh_sidebar_counts(self) -> None:
        if self.book is not None:
            self.sidebar.set_book(self.book, self.counts, self.current_chapter_id)

    def _recount_all(self) -> None:
        if self.book is None:
            self._total_chars = 0
            self._other_chars = 0
            return
        self.counts = {
            ch.id: count_chars(ch.content, self.settings.exclude_punct)
            for ch in self.book.all_chapters()
        }
        current = self.counts.get(self.current_chapter_id, 0)
        self._other_chars = sum(self.counts.values()) - current
        self._total_chars = self._other_chars + current

    # ==================================================================
    # 排版
    # ==================================================================
    def smart_format(self) -> None:
        if self.book is None or not self.current_chapter_id:
            return
        self._format_chapters([self.current_chapter_id])
        _volume, chapter = self.book.find_chapter(self.current_chapter_id)
        if chapter is not None:
            self.editor_page.load_text(chapter.title, chapter.content)
            self._editor_chapter_id = self.current_chapter_id
            self.editor_page.set_total_chars(self._total_chars)

    def _format_chapters(self, chapter_ids: list[str]) -> None:
        if self.book is None:
            return
        for chapter in self.book.all_chapters():
            if chapter.id not in chapter_ids:
                continue
            chapter.content = formatter.format_text(
                chapter.content,
                self.settings.indent,
                self.settings.blank_line,
                self.settings.clean_spaces,
                self.settings.unify_punct,
                self.settings.ellipsis_fix,
            )
            chapter.updated_at = time.time()
            self.counts[chapter.id] = count_chars(chapter.content, self.settings.exclude_punct)
        self.book.touch()
        self.storage.save_book(self.book, self._total_chars)
        self.sidebar_timer.start()

    def format_whole_book(self) -> None:
        """按当前排版设置整理全书章节（排版右键菜单入口）。"""
        if self.book is None:
            return
        ids = [ch.id for ch in self.book.all_chapters()]
        self._format_chapters(ids)
        if self.current_chapter_id:
            _volume, chapter = self.book.find_chapter(self.current_chapter_id)
            if chapter is not None:
                self.editor_page.load_text(chapter.title, chapter.content)
                self._editor_chapter_id = self.current_chapter_id
                self.editor_page.set_total_chars(self._total_chars)
        MessageBox.information(self, "排版完成", f"已按当前排版设置整理全书 {len(ids)} 个章节。")

    def _set_format_option(self, key: str, checked: bool) -> None:
        """排版右键菜单里的开关（下次执行排版时生效）。"""
        if key == "indent":
            self.settings.indent = "two" if checked else "none"
        elif key == "blank_line":
            self.settings.blank_line = "blank" if checked else "none"
        else:
            setattr(self.settings, key, bool(checked))
        self.storage.save_settings(self.settings)

    def _show_format_menu(self) -> None:
        """排版按钮右键菜单：实用排版开关（可勾选）+ 执行排版。"""
        menu = QMenu(self)
        options = [
            ("indent", "段首缩进两字符", self.settings.indent == "two"),
            ("blank_line", "段落之间空一行", self.settings.blank_line == "blank"),
            ("clean_spaces", "清除多余空格", self.settings.clean_spaces),
            ("unify_punct", "半角标点转全角", self.settings.unify_punct),
            ("ellipsis_fix", "规范省略号与破折号", self.settings.ellipsis_fix),
        ]
        for key, text, checked in options:
            action = menu.addAction(text)
            action.setCheckable(True)
            action.setChecked(checked)
            action.toggled.connect(lambda on, k=key: self._set_format_option(k, on))
        menu.addSeparator()
        menu.addAction("排版当前章节", self.smart_format)
        menu.addAction("排版整本书", self.format_whole_book)
        menu.exec(QCursor.pos())

    # ==================================================================
    # 查找 / 搜索
    # ==================================================================
    def show_find_dialog(self) -> None:
        if self._find_dialog is None:
            self._find_dialog = FindReplaceDialog(self)
        self._find_dialog.show()
        self._find_dialog.raise_()
        self._find_dialog.refresh()

    def show_search_dialog(self) -> None:
        if self._search_dialog is None:
            self._search_dialog = GlobalSearchDialog(self)
        self._search_dialog.show()
        self._search_dialog.raise_()
        self._search_dialog.refresh()

    def find_next_in_book(self, needle: str, case: bool) -> bool:
        """在当前章节之后的章节里查找，找到则打开对应章节。"""
        if self.book is None:
            return False
        chapters = self.book.all_chapters()
        from app.services.search import find_all

        current_index = -1
        for i, ch in enumerate(chapters):
            if ch.id == self.current_chapter_id:
                current_index = i
                break
        for chapter in chapters[current_index + 1:]:
            if find_all(chapter.content, needle, case):
                self.open_chapter(chapter.id)
                return True
        return False

    def replace_all(
        self, needle: str, replacement: str, case: bool, whole_book: bool
    ) -> tuple[int, int]:
        """执行替换，返回 (替换次数, 涉及章节数)。"""
        if self.book is None:
            return 0, 0
        from app.services.search import replace_all_text

        changed_ids: list[str] = []
        total = 0
        for chapter in self.book.all_chapters():
            if not whole_book and chapter.id != self.current_chapter_id:
                continue
            new_text, count = replace_all_text(chapter.content, needle, replacement, case)
            if count:
                chapter.content = new_text
                chapter.updated_at = time.time()
                changed_ids.append(chapter.id)
                total += count
        if changed_ids:
            self.book.touch()
            self.storage.save_book(self.book, self._total_chars)
            if self.current_chapter_id in changed_ids:
                _volume, chapter = self.book.find_chapter(self.current_chapter_id)
                if chapter is not None:
                    self.editor_page.load_text(chapter.title, chapter.content)
                    self._editor_chapter_id = self.current_chapter_id
            self._recount_all()
            self.editor_page.set_total_chars(self._total_chars)
            self.sidebar_timer.start()
        return total, len(changed_ids)

    def jump_to_match(self, chapter_id: str, start: int, length: int) -> None:
        if self.book is None:
            return
        if chapter_id != self.current_chapter_id:
            self.open_chapter(chapter_id)
        self.editor_page.select_range(start, length)

    # ==================================================================
    # 弹窗
    # ==================================================================
    def show_font_dialog(self) -> None:
        dialog = FontDialog(
            self,
            self.settings.font_family,
            self.settings.font_size,
            self.settings.editor_text,
            self.settings.font_bold,
            self.settings.font_italic,
            self.settings.font_underline,
            self.settings.imported_fonts,
        )
        dialog.fonts_imported.connect(self._on_fonts_imported)

        old = (
            self.settings.font_family,
            float(self.settings.font_size),
            self.settings.editor_text,
            self.settings.font_bold,
            self.settings.font_italic,
            self.settings.font_underline,
        )

        def _live(family: str, size: float, color: str,
                  bold: bool, italic: bool, underline: bool) -> None:
            # 对话框内实时预览：改动立即应用到正文（未保存，取消会还原）
            self.settings.font_family = family
            self.settings.font_size = size
            self.settings.editor_text = color
            self.settings.font_bold = bold
            self.settings.font_italic = italic
            self.settings.font_underline = underline
            if color != old[2]:
                # 文字颜色走全局 QSS（主题变量），需要整体刷新
                self.apply_theme()
            else:
                self.editor_page.apply_settings(self.settings)

        dialog.live_changed.connect(_live)
        if dialog.exec() == FontDialog.Accepted:
            (self.settings.font_family, self.settings.font_size, self.settings.editor_text,
             self.settings.font_bold, self.settings.font_italic, self.settings.font_underline
             ) = dialog.values()
            self.storage.save_settings(self.settings)
            self.apply_theme()
        else:
            # 取消：还原为打开前的字体
            (self.settings.font_family, self.settings.font_size, self.settings.editor_text,
             self.settings.font_bold, self.settings.font_italic, self.settings.font_underline
             ) = old
            self.apply_theme()

    def _on_fonts_imported(self, paths: list[str]) -> None:
        known = set(self.settings.imported_fonts)
        for path in paths:
            if path not in known:
                self.settings.imported_fonts.append(path)
                known.add(path)
        self.storage.save_settings(self.settings)

    def show_spacing_dialog(self) -> None:
        dialog = LineSpacingDialog(self, self.settings.line_spacing)
        if dialog.exec() == LineSpacingDialog.Accepted:
            self.settings.line_spacing = dialog.value()
            self.storage.save_settings(self.settings)
            self.editor_page.apply_settings(self.settings)

    def show_writing_theme(self) -> None:
        """写作主题面板：圆形色块实时切换写作区背景。"""
        dialog = WritingThemeDialog(self, self.settings)
        dialog.theme_applied.connect(self._on_writing_theme)
        dialog.exec()
        theme_key, image = dialog.values()
        self.settings.writing_theme = theme_key
        self.settings.writing_image = image
        self.storage.save_settings(self.settings)
        self.apply_theme()

    def _on_writing_theme(self, theme_key: str, image: str) -> None:
        """面板内点击色块时实时应用。"""
        self.settings.writing_theme = theme_key
        self.settings.writing_image = image
        self.storage.save_settings(self.settings)
        self.apply_theme()

    def show_name_dialog(self) -> None:
        dialog = NameGeneratorDialog(self)
        dialog.exec()

    # ==================================================================
    # 独立时间轴（可创建多个，从书架打开）
    # ==================================================================
    def new_timeline(self) -> None:
        text, ok = QInputDialog.getText(self, "新建时间轴", "时间轴名称：")
        if not (ok and text.strip()):
            return
        item = self.storage.add_timeline(text.strip())
        self._update_shelf()
        self.open_timeline(str(item["id"]))

    def open_timeline(self, timeline_id: str) -> None:
        item = self.storage.load_timeline(timeline_id)
        if item is None:
            return
        dialog = TimelineDialog(item, self._theme, self)
        dialog.changed.connect(lambda d=dialog: self._save_timeline(d))
        dialog.setWindowFlags(dialog.windowFlags() | Qt.WindowMinMaxButtonsHint)
        dialog.show()
        dialog.raise_()
        self._timeline_dialogs.append(dialog)

    def _save_timeline(self, dialog: TimelineDialog) -> None:
        item = dialog.item
        item["updated_at"] = time.time()
        self.storage.save_timeline(item)

    def rename_timeline(self, timeline_id: str) -> None:
        item = self.storage.load_timeline(timeline_id)
        if item is None:
            return
        text, ok = QInputDialog.getText(
            self, "重命名时间轴", "新的名称：", text=str(item.get("title") or "")
        )
        if ok and text.strip():
            item["title"] = text.strip()
            item["updated_at"] = time.time()
            self.storage.save_timeline(item)
            self._update_shelf()

    def delete_timeline(self, timeline_id: str) -> None:
        item = self.storage.load_timeline(timeline_id)
        title = str(item.get("title") or "该时间轴") if item else "该时间轴"
        if not self._confirm(f"删除时间轴「{title}」？", "删除后无法恢复。"):
            return
        self.storage.delete_timeline(timeline_id)
        for dialog in self._timeline_dialogs:
            if dialog.item.get("id") == timeline_id and dialog.isVisible():
                dialog.close()
        self._timeline_dialogs = [
            d for d in self._timeline_dialogs if d.item.get("id") != timeline_id
        ]
        self._update_shelf()

    # ==================================================================
    # 独立设定文档（可创建多个，从书架打开）
    # ==================================================================
    def new_note(self) -> None:
        text, ok = QInputDialog.getText(self, "新建设定", "设定名称：")
        if not (ok and text.strip()):
            return
        doc = self.storage.add_note(text.strip())
        self._update_shelf()
        self.open_note(str(doc["id"]))

    def open_note(self, note_id: str) -> None:
        existing = self._note_windows.get(note_id)
        if existing is not None and existing.isVisible():
            existing.raise_()
            existing.activateWindow()
            return
        doc = None
        for item in self.storage.load_library().get("notes", []):
            if item.get("id") == note_id:
                doc = item
                break
        if doc is None:
            return
        window = NoteEditorWindow(self.storage, doc, self)
        window.saved.connect(self._update_shelf)
        window.closed.connect(self._on_note_window_closed)
        window.show()
        self._note_windows[note_id] = window

    def _on_note_window_closed(self, note_id: str) -> None:
        self._note_windows.pop(note_id, None)

    def rename_note(self, note_id: str) -> None:
        doc = None
        for item in self.storage.load_library().get("notes", []):
            if item.get("id") == note_id:
                doc = item
                break
        if doc is None:
            return
        text, ok = QInputDialog.getText(
            self, "重命名设定", "新的名称：", text=str(doc.get("title") or "")
        )
        if ok and text.strip():
            doc["title"] = text.strip()
            doc["updated_at"] = time.time()
            self.storage.save_note(doc)
            window = self._note_windows.get(note_id)
            if window is not None and window.isVisible():
                window.reload(doc)
            self._update_shelf()

    def delete_note(self, note_id: str) -> None:
        doc = None
        for item in self.storage.load_library().get("notes", []):
            if item.get("id") == note_id:
                doc = item
                break
        title = str(doc.get("title") or "该设定") if doc else "该设定"
        if not self._confirm(f"删除设定「{title}」？", "删除后无法恢复。"):
            return
        window = self._note_windows.pop(note_id, None)
        if window is not None:
            window.close()
        self.storage.delete_note(note_id)
        self._update_shelf()

    # ==================================================================
    # 独立大纲（思维导图，可创建多个，从书架打开）
    # ==================================================================
    def new_outline(self) -> None:
        text, ok = QInputDialog.getText(self, "新建大纲", "大纲名称：")
        if not (ok and text.strip()):
            return
        item = self.storage.add_outline(text.strip())
        self._update_shelf()
        self.open_outline(str(item["id"]))

    def open_outline(self, outline_id: str) -> None:
        item = self.storage.load_outline(outline_id)
        if item is None:
            return
        dialog = OutlineDialog(item, self._theme, self)
        dialog.changed.connect(lambda d=dialog: self._save_outline(d))
        dialog.setWindowFlags(dialog.windowFlags() | Qt.WindowMinMaxButtonsHint)
        dialog.show()
        dialog.raise_()
        self._outline_dialogs.append(dialog)

    def _save_outline(self, dialog: OutlineDialog) -> None:
        item = dialog.item
        item["updated_at"] = time.time()
        self.storage.save_outline(item)

    def rename_outline(self, outline_id: str) -> None:
        item = self.storage.load_outline(outline_id)
        if item is None:
            return
        text, ok = QInputDialog.getText(
            self, "重命名大纲", "新的名称：", text=str(item.get("title") or "")
        )
        if ok and text.strip():
            item["title"] = text.strip()
            item["updated_at"] = time.time()
            self.storage.save_outline(item)
            self._update_shelf()

    def delete_outline(self, outline_id: str) -> None:
        item = self.storage.load_outline(outline_id)
        title = str(item.get("title") or "该大纲") if item else "该大纲"
        if not self._confirm(f"删除大纲「{title}」？", "删除后无法恢复。"):
            return
        self.storage.delete_outline(outline_id)
        for dialog in self._outline_dialogs:
            if dialog.item.get("id") == outline_id and dialog.isVisible():
                dialog.close()
        self._outline_dialogs = [
            d for d in self._outline_dialogs if d.item.get("id") != outline_id
        ]
        self._update_shelf()

    # ==================================================================
    # 世界地图（可创建多张，独立保存为 map_global.json）
    # ==================================================================
    def new_map(self) -> None:
        text, ok = QInputDialog.getText(self, "新建世界地图", "地图名称：")
        if not (ok and text.strip()):
            return
        item = self.storage.add_map(text.strip())
        self._update_shelf()
        self.open_map(str(item["id"]))

    def open_map(self, map_id: str) -> None:
        existing = self._map_dialogs.get(map_id)
        if existing is not None and existing.isVisible():
            existing.raise_()
            existing.activateWindow()
            return
        item = self.storage.load_map_item(map_id)
        if item is None:
            return
        dialog = MapDialog(item, self._theme, self)
        dialog.changed.connect(lambda d=dialog: self._save_map(d))
        dialog.setWindowFlags(dialog.windowFlags() | Qt.WindowMinMaxButtonsHint)
        dialog.show()
        dialog.raise_()
        self._map_dialogs[map_id] = dialog

    def show_shelf_stats(self) -> None:
        """书架主页的统计卡片：查看累计打字与每日记录。"""
        StatsDialog(self, self.storage, show_book_stats=False).exec()

    def _save_map(self, dialog: MapDialog) -> None:
        item = dialog.data_dict()
        item["updated_at"] = time.time()
        self.storage.save_map_item(item)

    def rename_map(self, map_id: str) -> None:
        item = self.storage.load_map_item(map_id)
        if item is None:
            return
        text, ok = QInputDialog.getText(
            self, "重命名地图", "新的名称：", text=str(item.get("title") or "")
        )
        if ok and text.strip():
            item["title"] = text.strip()
            item["updated_at"] = time.time()
            self.storage.save_map_item(item)
            window = self._map_dialogs.get(map_id)
            if window is not None and window.isVisible():
                window.reload_meta(item)
            self._update_shelf()

    def delete_map(self, map_id: str) -> None:
        item = self.storage.load_map_item(map_id)
        title = str(item.get("title") or "该地图") if item else "该地图"
        if not self._confirm(f"删除地图「{title}」？", "删除后无法恢复。"):
            return
        window = self._map_dialogs.pop(map_id, None)
        if window is not None:
            window.close()
        self.storage.delete_map(map_id)
        self._update_shelf()

    # ==================================================================
    # 导出（导出路径每次实时选择）
    # ==================================================================
    def export_book(self, book_id: str) -> None:
        """把整本小说导出为 TXT 文本。"""
        if self.book is not None and self.book.id == book_id:
            self.flush()   # 先落盘当前修改，保证导出内容最新
        book = self.storage.load_book(book_id)
        if book is None:
            return
        lines: list[str] = [book.title]
        if book.author.strip():
            lines.append(f"作者：{book.author.strip()}")
        lines.append("")
        for volume in book.volumes:
            lines.append(f"◆ {volume.title}")
            lines.append("")
            for chapter in volume.chapters:
                lines.append(chapter.title)
                lines.append("")
                lines.append(chapter.content.strip())
                lines.append("")
                lines.append("")
        path, _f = QFileDialog.getSaveFileName(
            self, "导出小说", f"{book.title}.txt", "文本文档 (*.txt)"
        )
        if not path:
            return
        if not path.lower().endswith(".txt"):
            path += ".txt"
        try:
            Path(path).write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        except OSError as exc:
            MessageBox.warning(self, "导出失败", f"无法写入文件：{exc}")
            return
        MessageBox.information(self, "导出成功", f"已导出到：\n{path}")

    # ==================================================================
    # 默认保存（数据）路径
    # ==================================================================
    def change_data_dir(self, new_path: str) -> None:
        """迁移数据到新目录并写入指针文件，重启后生效。"""
        target = Path(new_path).expanduser()
        current = self.storage.base_dir
        try:
            target = target.resolve()
            current_resolved = current.resolve()
        except OSError:
            return
        if target == current_resolved:
            MessageBox.information(self, "默认保存路径", "新路径与当前路径相同。")
            return

        # 保存当前未落盘的数据，确保迁移的是最新内容
        self.flush()
        self.storage.save_settings(self.settings)

        try:
            target_non_empty = target.exists() and any(target.iterdir())
        except OSError:
            target_non_empty = False
        if target_non_empty:
            if not self._confirm(
                "目标文件夹不是空的",
                f"「{target}」中已有的同名文件将被覆盖，是否继续？",
            ):
                return
        try:
            target.mkdir(parents=True, exist_ok=True)
            for name in (
                "library.json", "settings.json", "map_global.json",
                "words_daily.json", "achievements.json",
            ):
                src = current / name
                if src.exists():
                    shutil.copy2(src, target / name)
            for src in current.glob("avatar.*"):
                shutil.copy2(src, target / src.name)
            for name in ("books", "covers", "history"):
                src = current / name
                if src.is_dir():
                    shutil.copytree(src, target / name, dirs_exist_ok=True)
        except OSError as exc:
            MessageBox.warning(self, "迁移失败", f"数据迁移失败：{exc}")
            return

        config.set_custom_data_dir(target)
        self.drawer.set_data_dir(str(target))
        self.drawer.close_drawer()

        box = MessageBox(
            self,
            "默认保存路径已修改",
            f"数据已迁移到：\n{target}\n\n重启应用后，新路径正式生效。现在就重启吗？",
            ok_text="立即重启",
            cancel_text="稍后手动重启",
        )
        box.exec()
        if box.choice:
            self._restart_app()

    def _restart_app(self) -> None:
        """重启应用（兼容开发脚本与打包后的 exe）。"""
        self.flush()
        self.storage.save_settings(self.settings)
        if getattr(sys, "frozen", False):
            subprocess.Popen([sys.executable])
        else:
            subprocess.Popen([sys.executable, str(Path(sys.argv[0]).resolve())])
        QApplication.instance().quit()

    def show_stats_dialog(self) -> None:
        if self.book is None:
            return
        from app.services.word_count import count_chars as _cc

        total = sum(_cc(ch.content, False) for ch in self.book.all_chapters())
        total_no_punct = sum(_cc(ch.content, True) for ch in self.book.all_chapters())
        _volume, chapter = self.book.find_chapter(self.current_chapter_id)
        dialog = StatsDialog(
            self,
            self.storage,
            self.book.title,
            total,
            total_no_punct,
            chapter.title if chapter is not None else "",
            self.counts.get(self.current_chapter_id, 0),
            self.book.chapter_count(),
            len(self.book.volumes),
        )
        dialog.exec()

    def show_speed_dialog(self) -> None:
        """时速：点击在编辑器右侧常驻面板显示 / 隐藏。"""
        self.editor_page.toggle_speed_panel()

    def _on_speed_unit_changed(self, unit: str) -> None:
        self.settings.speed_unit = unit
        self.storage.save_settings(self.settings)

    def show_history_dialog(self) -> None:
        if self.book is None or not self.current_chapter_id:
            return
        _volume, chapter = self.book.find_chapter(self.current_chapter_id)
        if chapter is None:
            return
        dialog = HistoryDialog(
            self.storage, self.book.id, chapter.id, chapter.title, self
        )
        if dialog.exec() == HistoryDialog.Accepted:
            content = dialog.selected_content()
            if content is not None:
                chapter.content = content
                chapter.updated_at = time.time()
                self.book.touch()
                self.storage.save_book(self.book, self._total_chars)
                self.editor_page.load_text(chapter.title, content)
                self._editor_chapter_id = self.current_chapter_id
                self.counts[chapter.id] = count_chars(content, self.settings.exclude_punct)
                self.sidebar_timer.start()

    # ==================================================================
    # WebDAV 同步
    # ==================================================================
    def webdav_upload(self) -> None:
        self.flush()
        self._run_webdav("upload", "确认上传备份到云端？\n将打包本地全部 JSON 数据覆盖云端备份。")

    def webdav_restore(self) -> None:
        if not self._confirm(
            "从云端恢复数据？", "本地现有的书籍与设置将被云端备份覆盖。"
        ):
            return
        self._run_webdav("restore", "")

    def _run_webdav(self, mode: str, confirm_text: str) -> None:
        if self._webdav_worker is not None and self._webdav_worker.isRunning():
            MessageBox.information(self, "云同步", "上一次同步还在进行中，请稍候。")
            return
        if confirm_text and not self._confirm("云同步", confirm_text):
            return
        self.flush()
        self.storage.save_settings(self.settings)
        worker = WebDavWorker(mode, self.settings, self.storage.base_dir, self)
        worker.finished_ok.connect(self._on_webdav_ok)
        worker.failed.connect(self._on_webdav_failed)
        worker.start()
        self._webdav_worker = worker

    def _on_webdav_ok(self, message: str) -> None:
        MessageBox.information(self, "云同步", message)
        if self._webdav_worker is not None and self._webdav_worker.mode == "restore":
            self._reload_after_restore()

    def _on_webdav_failed(self, message: str) -> None:
        MessageBox.warning(self, "云同步失败", message)

    def _reload_after_restore(self) -> None:
        """恢复后重新加载全部数据。"""
        self.settings = self.storage.load_settings()
        self._load_imported_fonts()
        self.achievements = AchievementManager(self.storage)
        self._words_pending = 0
        self.book = None
        self.current_chapter_id = ""
        self._editor_chapter_id = ""
        self.editor_page.clear_page()
        self.sidebar.set_book(None, {}, "")
        self.apply_theme()
        self.show_bookshelf()

    # ==================================================================
    # 目录操作
    # ==================================================================
    def toggle_sidebar(self) -> None:
        visible = not self.sidebar.isVisible()
        self.sidebar.setVisible(visible)
        self.buttons["toc"].setChecked(visible)
        self._update_side_toggle_arrow()

    def toggle_drawer(self) -> None:
        self.drawer.toggle()

    def new_chapter(self, volume_id: str) -> None:
        if self.book is None:
            return
        volume = self.book.find_volume(volume_id) if volume_id else None
        if volume is None:
            volume = self.book.volumes[0]
        title = next_chapter_title(volume)
        chapter = Chapter(id=new_id(), title=title)
        volume.chapters.append(chapter)
        self.book.touch()
        self.storage.save_book(self.book, self._total_chars)
        self.sidebar.set_book(self.book, self.counts, self.current_chapter_id)
        self.open_chapter(chapter.id)

    def new_volume(self) -> None:
        if self.book is None:
            return
        text, ok = QInputDialog.getText(
            self, "新建卷", "卷名：", text=f"第{len(self.book.volumes) + 1}卷"
        )
        if not (ok and text.strip()):
            return
        volume = Volume(id=new_id(), title=text.strip())
        self.book.volumes.append(volume)
        self.book.touch()
        self.storage.save_book(self.book, self._total_chars)
        self.sidebar.set_book(self.book, self.counts, self.current_chapter_id)

    def rename_item(self, kind: str, item_id: str) -> None:
        if self.book is None:
            return
        if kind == "chapter":
            _volume, item = self.book.find_chapter(item_id)
            label = "章节名"
        else:
            item = self.book.find_volume(item_id)
            label = "卷名"
        if item is None:
            return
        text, ok = QInputDialog.getText(self, "重命名", f"新的{label}：", text=item.title)
        if ok and text.strip():
            item.title = text.strip()
            self.book.touch()
            self.storage.save_book(self.book, self._total_chars)
            self.sidebar.set_book(self.book, self.counts, self.current_chapter_id)
            if kind == "chapter" and item_id == self.current_chapter_id:
                self.editor_page.set_title(item.title)

    def delete_item(self, kind: str, item_id: str) -> None:
        if self.book is None:
            return
        if kind == "chapter":
            _volume, chapter = self.book.find_chapter(item_id)
            if chapter is None:
                return
            if not self._confirm(f"删除章节「{chapter.title}」？", "该章节的正文与历史版本将一并删除。"):
                return
            for volume in self.book.volumes:
                if chapter in volume.chapters:
                    volume.chapters.remove(chapter)
            self.storage.delete_snapshots(self.book.id, item_id)
            self.counts.pop(item_id, None)
            if item_id == self.current_chapter_id:
                self.current_chapter_id = ""
        else:
            volume = self.book.find_volume(item_id)
            if volume is None:
                return
            if not self._confirm(f"删除卷「{volume.title}」？", "卷内所有章节将一并删除。"):
                return
            for chapter in volume.chapters:
                self.storage.delete_snapshots(self.book.id, chapter.id)
                self.counts.pop(chapter.id, None)
                if chapter.id == self.current_chapter_id:
                    self.current_chapter_id = ""
            self.book.volumes.remove(volume)
        self.book.touch()
        self.storage.save_book(self.book, self._total_chars)
        self._recount_all()
        self.sidebar.set_book(self.book, self.counts, self.current_chapter_id)
        if not self.current_chapter_id:
            first = self.book.first_chapter()
            if first is not None:
                self.open_chapter(first.id)
            else:
                self._editor_chapter_id = ""
                self.editor_page.clear_page()
        else:
            self.open_chapter(self.current_chapter_id)

    # ==================================================================
    # 相关设定
    # ==================================================================
    def add_related(self) -> None:
        if self.book is None:
            return
        dialog = RelatedDialog(self)
        if dialog.exec() == RelatedDialog.Accepted:
            kind, name, note = dialog.values()
            self.book.relations.append(RelatedItem(id=new_id(), kind=kind, name=name, note=note))
            self.book.touch()
            self.storage.save_book(self.book, self._total_chars)
            self.sidebar.set_related(self.book.relations)

    def edit_related(self, item_id: str) -> None:
        if self.book is None:
            return
        item = next((r for r in self.book.relations if r.id == item_id), None)
        if item is None:
            return
        dialog = RelatedDialog(self, item.kind, item.name, item.note)
        if dialog.exec() == RelatedDialog.Accepted:
            item.kind, item.name, item.note = dialog.values()
            self.book.touch()
            self.storage.save_book(self.book, self._total_chars)
            self.sidebar.set_related(self.book.relations)

    def delete_related(self, item_id: str) -> None:
        if self.book is None:
            return
        self.book.relations = [r for r in self.book.relations if r.id != item_id]
        self.book.touch()
        self.storage.save_book(self.book, self._total_chars)
        self.sidebar.set_related(self.book.relations)

    # ==================================================================
    # 设置变化
    # ==================================================================
    def _on_settings_changed(self) -> None:
        self.storage.save_settings(self.settings)
        # 深浅色 / 颜色自定义需要整体刷新；其余只需要局部应用
        self.apply_theme()

    # ==================================================================
    # 工具
    # ==================================================================
    def _confirm(self, title: str, text: str) -> bool:
        return MessageBox.question(self, title, text)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.drawer.relayout()

    def closeEvent(self, event) -> None:  # noqa: N802
        self.words_timer.stop()
        self._flush_words()
        self.flush()
        self.storage.save_settings(self.settings)
        super().closeEvent(event)
