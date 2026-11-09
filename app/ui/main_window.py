# -*- coding: utf-8 -*-
"""主窗口：上图下字工具栏、书架主页、三栏写作工作区、设置抽屉与全部功能串联。"""
from __future__ import annotations

import json
import os
import random
import shutil
import subprocess
import sys
import time
from pathlib import Path

from PySide6.QtCore import (
    QEasingCurve, QEvent, QPropertyAnimation, QRectF, QSize, QStandardPaths, Qt, QThread, QTimer, Signal,
)
from PySide6.QtGui import (
    QActionGroup, QColor, QCursor, QKeySequence, QPainter, QPen, QShortcut,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from app import config, exporter, startup_trace
from app.models import Book, Chapter, RelatedItem, Settings, Volume, create_book, new_id, next_chapter_title
from app.services import formatter
from app.services.achievements import (
    HIATUS_THRESHOLDS,
    WORD_MILESTONES,
    AchievementManager,
    ACHIEVEMENTS,
)
from app.services.stats import WritingSpeedTracker
from app.services.sounds import play_manual_save_sound, play_wav
from app.services.webdav_sync import (
    WebDavError,
    create_remote_dir,
    delete_old_backups,
    download_backup,
    normalize_remote_dir,
    test_connection,
    upload_backup,
)
from app.services.word_count import count_chars
from app.storage import Storage
from app.ui.bookshelf import BookshelfPage
from app.ui.dialogs import (
    AchievementToast,
    CharacterImportDialog,
    FindReplaceDialog,
    FontDialog,
    GlobalSearchDialog,
    HistoryDialog,
    LineSpacingDialog,
    MessageBox,
    NameGeneratorDialog,
    NewBookDialog,
    NoteEditorWindow,
    ParagraphDialog,
    ProofreadDialog,
    RelatedDialog,
    StatsDialog,
    WritingThemeDialog,
)
from app.ui.editor import EditorPage, SaveSpinner
from app.ui.icons import IconFactory
from app.ui.map import MapDialog
from app.ui.outline import OutlineDialog
from app.ui.settings_panel import SettingsDrawer
from app.ui.sidebar import SidebarWidget
from app.ui.volume_export import MODE_BOOK, VolumeExportDialog
from app.ui.snippets import SnippetsPanel
from app.ui.theme import base_theme, build_palette, build_qss, build_theme
from app.ui.timeline import TimelineDialog
from app.ui.widgets import BackgroundWidget, ToolbarButton


# 关窗时仍未结束的后台同步线程：脱离主窗口后在此保活，
# 避免 QThread 在运行中被主窗口连带析构
_ORPHAN_WORKERS: list[QThread] = []


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
                upload_backup(self.settings, self.base_dir,
                              getattr(self.settings, "sync_covers_mode", "thumb"))
                self.finished_ok.emit("备份已成功上传到云端。")
            elif self.mode == "prune":
                removed, kept = delete_old_backups(self.settings, 3)
                if removed:
                    self.finished_ok.emit(
                        f"已删除 {len(removed)} 份旧备份，保留最近 {len(kept)} 份。")
                else:
                    self.finished_ok.emit(
                        f"没有需要删除的旧备份（云端共 {len(kept)} 份，保留最近 3 份）。")
            else:
                report: dict = {}
                files = download_backup(self.settings, self.base_dir, report)
                text = f"已从云端恢复 {len(files)} 个数据文件。"
                used = int(report.get("thumbs_used", 0))
                if used:
                    text += (f"\n其中 {used} 张封面用云端缩略图代替（原图未随备份同步）。"
                              "如需原图，请回到原设备重新上传 full 模式备份。")
                self.finished_ok.emit(text)
        except WebDavError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"同步失败：{exc}")


class WebDavTestWorker(QThread):
    """「测试连接」后台线程：PROPFIND 目录 + 缺目录自动 MKCOL + PUT/DELETE 写探测。"""

    finished_result = Signal(object)   # dict: ok / missing / text
    failed = Signal(str)

    def __init__(self, settings: Settings, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings

    def run(self) -> None:
        try:
            self.finished_result.emit(test_connection(self.settings))
        except WebDavError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"测试连接失败：{exc}")


class WebDavCreateDirWorker(QThread):
    """「创建该目录」后台线程：逐级 MKCOL（≤3 级）后复测。"""

    finished_ok = Signal(str)
    failed = Signal(str)

    def __init__(self, settings: Settings, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings

    def run(self) -> None:
        try:
            self.finished_ok.emit(create_remote_dir(self.settings))
        except WebDavError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"创建目录失败：{exc}")


class ExitSaveOverlay(QWidget):
    """关窗时的居中保存浮层：圆环旋转 → 绿勾 → 淡出，失败则停留让用户决定。

    覆盖在主窗口上，只负责展示与按钮，保存动作全部由 MainWindow 复用原
    closeEvent 的 flush 路径完成；本类不碰数据、不起线程。
    """

    retry_requested = Signal()
    cancel_requested = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("ExitSaveOverlay")
        self.setAttribute(Qt.WA_StyledBackground, False)

        self._card = QFrame(self)
        self._card.setObjectName("MessageBox")   # 沿用现有底板 / 玻璃质感
        self._card.setFixedWidth(380)

        card_layout = QVBoxLayout(self._card)
        card_layout.setContentsMargins(24, 20, 24, 18)
        card_layout.setSpacing(10)

        row = QHBoxLayout()
        row.setSpacing(12)
        self.spinner = SaveSpinner(self._card)
        row.addWidget(self.spinner, 0, Qt.AlignVCenter)
        text_box = QVBoxLayout()
        text_box.setSpacing(3)
        self.title = QLabel("正在保存…", self._card)
        self.title.setObjectName("MsgTitle")
        self.detail = QLabel("正在把未保存的内容写入本地…", self._card)
        self.detail.setObjectName("MsgText")
        self.detail.setWordWrap(True)
        text_box.addWidget(self.title)
        text_box.addWidget(self.detail)
        row.addLayout(text_box, 1)
        card_layout.addLayout(row)

        self._buttons = QWidget(self._card)
        button_row = QHBoxLayout(self._buttons)
        button_row.setContentsMargins(0, 4, 0, 0)
        button_row.setSpacing(10)
        button_row.addStretch(1)
        self.retry_button = QPushButton("重试保存", self._buttons)
        self.retry_button.setObjectName("Primary")
        self.retry_button.setCursor(Qt.PointingHandCursor)
        self.retry_button.clicked.connect(self.retry_requested.emit)
        self.cancel_button = QPushButton("取消退出", self._buttons)
        self.cancel_button.setObjectName("GhostButton")
        self.cancel_button.setCursor(Qt.PointingHandCursor)
        self.cancel_button.clicked.connect(self.cancel_requested.emit)
        button_row.addWidget(self.cancel_button)
        button_row.addWidget(self.retry_button)
        self._buttons.hide()
        card_layout.addWidget(self._buttons)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addStretch(1)
        outer.addWidget(self._card, 0, Qt.AlignHCenter)
        outer.addStretch(1)

        self._effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._effect)
        self._fade = QPropertyAnimation(self._effect, b"opacity", self)
        self._fade.setDuration(220)
        self._fade.setEasingCurve(QEasingCurve.InOutQuad)
        self.hide()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self.parentWidget() is not None:
            self.setGeometry(self.parentWidget().rect())

    def show_saving(self) -> None:
        self._fade.stop()
        self._effect.setOpacity(1.0)
        self.spinner.start_saving()
        self.title.setText("正在保存…")
        self.detail.setText("正在把未保存的内容写入本地…")
        self._buttons.hide()
        self._show()

    def show_success(self) -> None:
        self.spinner.set_state(SaveSpinner.OK, 0)   # 常驻绿勾，由外部计时淡出
        self.title.setText("已保存")
        self.detail.setText("内容已安全写入本地，即将关闭。")
        self._buttons.hide()

    def show_error(self, reason: str) -> None:
        brief = (reason or "未知原因").splitlines()[0][:160]
        self.spinner.set_state(SaveSpinner.ERROR, 0)
        self.title.setText("保存失败")
        self.detail.setText(f"{brief}\n内容仍保留在编辑器中，可重试保存或取消退出。")
        self._buttons.show()
        self._show()

    def hide_overlay(self) -> None:
        self._fade.stop()
        self.hide()

    def fade_out(self, done) -> None:
        self._fade.stop()
        self._fade.setStartValue(self._effect.opacity())
        self._fade.setEndValue(0.0)
        self._fade.finished.connect(done)
        self._fade.start()

    def _show(self) -> None:
        if self.parentWidget() is not None:
            self.setGeometry(self.parentWidget().rect())
        self.show()
        self.raise_()


class TopbarReveal(QWidget):
    """顶栏收起后的恢复入口：贴在窗口上沿中央的细条。

    静止时只画一根很短的小握把，不占底色、不留横条；鼠标移上来才展开成
    “展开顶部栏”的胶囊按钮，保证“进得去也出得来”。
    """

    clicked = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setFixedSize(170, 18)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("点击展开顶部栏")
        self.setAccessibleName("展开顶部栏")
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self._hover = False

    def enterEvent(self, event) -> None:
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        full = QRectF(self.rect())
        pal = self.palette()
        if self._hover:
            bg = QColor(pal.window().color())
            bg.setAlpha(240)
            border = QColor(pal.mid().color())
            border.setAlpha(160)
            painter.setPen(QPen(border, 1))
            painter.setBrush(bg)
            painter.drawRoundedRect(full.adjusted(0.5, 0.5, -0.5, -0.5), 9, 9)
            fg = QColor(pal.windowText().color())
            fg.setAlpha(230)
            painter.setPen(fg)
            font = self.font()
            font.setPixelSize(11)
            painter.setFont(font)
            painter.drawText(full, Qt.AlignCenter, "展开顶部栏 ▾")
        else:
            grip = QColor(pal.windowText().color())
            grip.setAlpha(70)
            painter.setPen(Qt.NoPen)
            painter.setBrush(grip)
            painter.drawRoundedRect(QRectF((self.width() - 26) / 2, 2, 26, 3), 1.5, 1.5)


class MainWindow(QMainWindow):
    """应用主窗口。"""

    def __init__(self, storage: Storage) -> None:
        super().__init__()
        startup_trace.reset()
        self.storage = storage
        self.settings: Settings = storage.load_settings()
        self._sync_target = self._sync_target_tuple()   # 同步目标快照（变更即重置同步状态）
        self.book: Book | None = None
        self.current_chapter_id = ""
        self._editor_chapter_id = ""     # 编辑器里承载的章节 id（防止把旧文本存进新章节）
        self.current_volume_id = ""
        self.counts: dict[str, int] = {}
        self._other_chars = 0
        self._total_chars = 0
        self._dirty = False
        self._last_save_error = ""       # 最近一次真实落盘失败原因（圆环红叉 / 退出浮层用）
        self._exit_overlay = None
        self._close_flow_active = False
        self._close_from_flow = False
        self.speed = WritingSpeedTracker()
        self.achievements = AchievementManager(storage)
        self._words_pending = 0          # 尚未落盘的今日键盘打字字数
        self._snapshot_marks: dict[str, tuple[float, int]] = {}   # 每章上一版快照的(时间, 字数)
        self._chapter_order_undo: list[list[tuple[str, list[str]]]] = []   # 目录拖动排序撤销栈
        self._webdav_worker: WebDavWorker | None = None
        self._webdav_test_worker: WebDavTestWorker | None = None
        self._webdav_create_worker: WebDavCreateDirWorker | None = None
        self._auto_sync_failures = 0
        self._timeline_dialogs: list[TimelineDialog] = []
        self._outline_dialogs: list[OutlineDialog] = []
        self._map_dialogs: dict[str, MapDialog] = {}
        self._note_windows: dict[str, NoteEditorWindow] = {}
        self._snippets_visible = False
        self._snippets_panel: SnippetsPanel | None = None
        self._load_imported_fonts()
        self._load_backgrounds()

        self._find_dialog: FindReplaceDialog | None = None
        self._search_dialog: GlobalSearchDialog | None = None
        self._proofread_dialog: ProofreadDialog | None = None
        self._focus_mode = False
        self._focus_snapshot: dict | None = None
        self._topbar_compact: bool | None = None
        self._name_dialog: NameGeneratorDialog | None = None

        self.setWindowTitle(config.APP_NAME)
        self.resize(1360, 880)
        self.setMinimumSize(1080, 680)
        startup_trace.mark("mw_setup")

        self._build_ui()
        startup_trace.mark("mw_ui")
        self._build_timers()
        self._build_shortcuts()
        self._connect_signals()
        startup_trace.mark("mw_signals")

        self._ensure_first_run_demo()
        self.apply_theme()
        startup_trace.mark("mw_theme")
        self.show_bookshelf()
        startup_trace.mark("mw_shelf")
        # 启动自检：只在发现异常时写 logs/editor_health.log，正常启动零噪音
        QTimer.singleShot(0, lambda: self._selfcheck_editor("startup"))
        # 任务G：封面懒迁移/孤儿扫描延后到首帧之后（不压构造耗时）；有变更再刷书架
        QTimer.singleShot(0, self._housekeep_covers_deferred)
        startup_trace.dump("mainwindow")

    def _housekeep_covers_deferred(self) -> None:
        """首帧后整理 covers/（data-URI 外置 / 旧命名哈希化 / 孤儿清理）。"""
        try:
            stats = self.storage.housekeep_covers_once()
        except OSError:
            return
        if stats.get("skipped") or stats.get("error"):
            return
        if stats.get("data_uri") or stats.get("legacy_renamed") or stats.get("removed"):
            from app.ui.widgets import cover_cache_clear
            cover_cache_clear()
            if self.book is None:
                self._update_shelf()

    # ==================================================================
    # 字体导入
    # ==================================================================
    def _load_imported_fonts(self) -> None:
        """启动时重新注册用户导入过的字体（顺带把旧绝对路径迁进数据目录）。"""
        from app.services import font_store

        kept = font_store.load_all(self.settings.imported_fonts)
        if kept != list(self.settings.imported_fonts):
            self.settings.imported_fonts = kept
            self.storage.save_settings(self.settings)

    def _load_backgrounds(self) -> None:
        """启动时整理自定义背景：旧版单张绝对路径迁成 1 个框，清理缺失文件。"""
        from app.services import backgrounds

        if backgrounds.migrate(self.settings):
            self.storage.save_settings(self.settings)

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

        self.shelf = BookshelfPage(self.stack, storage=self.storage)
        self.stack.addWidget(self.shelf)
        startup_trace.mark("ui_shelf")

        self.workspace = QWidget(self.stack)
        workspace_layout = QHBoxLayout(self.workspace)
        workspace_layout.setContentsMargins(0, 0, 0, 0)
        workspace_layout.setSpacing(0)
        self.sidebar = SidebarWidget(self.workspace)
        startup_trace.mark("ui_sidebar")
        self.editor_page = EditorPage(self.settings, self.workspace)
        startup_trace.mark("ui_editor")
        # 片段栏按需创建：首次展开时才构造面板并读取 snippets.json，
        # 避免每次启动都为未展开的面板付出构造 / 解析成本。
        workspace_layout.addWidget(self.sidebar)
        # v1.4.4：目录收起细条不进布局，改为浮在写作区左缘的覆盖层。
        # 这样左栏收起时纸张可以真正贴到窗口左边缘，细条仍可点击恢复。
        self.side_toggle_bar = self._build_side_toggle()
        self.side_toggle_bar.setParent(self.workspace)
        self.side_toggle_bar.raise_()
        # 侧栏展开时给细条预留 16px；收起时归零，让纸张真正贴窗口左缘
        self.side_toggle_spacer = QWidget(self.workspace)
        self.side_toggle_spacer.setFixedWidth(16)
        workspace_layout.addWidget(self.side_toggle_spacer)
        # 正文与右侧片段栏：可拖拽分栏；片段栏收起时正文自动占满
        self.editor_split = QSplitter(Qt.Horizontal, self.workspace)
        self.editor_split.setObjectName("EditorSplit")
        self.editor_split.setChildrenCollapsible(False)
        self.editor_split.setHandleWidth(6)
        self.editor_split.addWidget(self.editor_page)
        self.editor_split.setStretchFactor(0, 1)
        workspace_layout.addWidget(self.editor_split, 1)
        self.stack.addWidget(self.workspace)
        startup_trace.mark("ui_workspace")

        # 工具栏依赖 editor_page 的撤销 / 重做方法，因此放在工作区之后创建
        self.topbar = self._build_topbar()
        self.top_stack.addWidget(self.topbar)
        root_layout.addWidget(self.top_stack)
        root_layout.addWidget(self.stack, 1)

        # 顶栏收起后的恢复细条：浮在窗口上沿中央（不进布局，绝不额外占高度）
        self.top_reveal = TopbarReveal(self.root)
        self.top_reveal.clicked.connect(lambda: self._set_topbar_collapsed(False))
        self.top_reveal.hide()

        self.drawer = SettingsDrawer(self.settings, self)
        startup_trace.mark("ui_drawer")

    @property
    def snippets_panel(self) -> SnippetsPanel:
        """片段栏按需创建：首次展开（或外部显式访问）时才构造并读盘。"""
        return self._ensure_snippets_panel()

    def _ensure_snippets_panel(self) -> SnippetsPanel:
        panel = self._snippets_panel
        if panel is not None:
            return panel
        startup_trace.mark("snippets_begin")
        panel = SnippetsPanel(self.storage, self.settings, self.workspace)
        startup_trace.mark("snippets_repo")
        panel.close_requested.connect(lambda: self.toggle_snippets_panel(False))
        panel.focus_editor_requested.connect(self.editor_page.focus_editor)
        panel.set_theme(getattr(self, "_theme", {}))
        panel.set_fast_scroll(getattr(self.settings, "fast_scroll", True))
        book_title, chapter_title = self._snippets_context()
        panel.set_context(book_title, chapter_title)
        self.editor_split.addWidget(panel)
        self.editor_split.setStretchFactor(1, 0)
        panel.hide()
        handle = self.editor_split.handle(1)
        if handle is not None:
            handle.hide()
        self._snippets_panel = panel
        return panel

    def _snippets_context(self) -> tuple[str, str]:
        if self.book is None:
            return "", ""
        _volume, chapter = self.book.find_chapter(self.current_chapter_id)
        return self.book.title, (chapter.title if chapter is not None else "")

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
        self.side_toggle_button.setFixedSize(14, 64)
        self.side_toggle_button.setIconSize(QSize(14, 14))
        self.side_toggle_button.clicked.connect(self.toggle_sidebar)
        layout.addWidget(self.side_toggle_button)
        layout.addStretch(1)
        return bar

    def _update_side_toggle_arrow(self) -> None:
        """根据目录显隐切换箭头方向与提示。"""
        factory = getattr(self, "_icon_factory", None)
        visible = not self.sidebar.isHidden()
        if factory is not None:
            self.side_toggle_button.setIcon(factory.icon("left" if visible else "right", 14))
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
        add("format", "排版", "format", "段落排版设置（右键打开排版选项）", self.show_paragraph_dialog)
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
        add("proofread", "体检", "proofread", "文本体检 Ctrl+Shift+K",
            self.show_proofread_dialog)
        focus_button = add("focus", "专注", "focus", "专注模式 F11",
                           self.toggle_focus_mode)
        focus_button.setCheckable(True)
        more = add("settings", "设置", "settings", "更多设置", self.toggle_drawer)
        more.setCheckable(True)
        # 顶栏收起：收起后纸张上沿贴住窗口上沿，窗口上沿中央留细条可恢复
        add("collapse", "收起", "top", "收起顶部栏（悬停窗口上沿可恢复）", self.toggle_topbar)
        self.buttons["toc"].setCheckable(True)
        self.buttons["speed"].setCheckable(True)

        self.buttons["undo"].setEnabled(False)
        self.buttons["redo"].setEnabled(False)
        self._apply_topbar_compact(self.width())
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

        # 片段栏拖拽宽度：停手 300ms 后写入 Settings，避免拖动过程频繁落盘
        self.snippets_width_timer = QTimer(self)
        self.snippets_width_timer.setSingleShot(True)
        self.snippets_width_timer.setInterval(300)
        self.snippets_width_timer.timeout.connect(self._persist_snippets_width)

        # 自动同步：按档位在后台静默上传，不弹窗、不阻塞主线程
        self.auto_sync_timer = QTimer(self)
        self.auto_sync_timer.timeout.connect(self._auto_sync_tick)
        self._restart_auto_sync_timer()

    def _build_shortcuts(self) -> None:
        save_shortcut = QShortcut(QKeySequence("Ctrl+S"), self)
        save_shortcut.activated.connect(self._manual_save)

        find_shortcut = QShortcut(QKeySequence("Ctrl+F"), self)
        find_shortcut.activated.connect(self.show_find_dialog)

        global_shortcut = QShortcut(QKeySequence("Ctrl+Shift+F"), self)
        global_shortcut.activated.connect(self.show_search_dialog)

        proofread_shortcut = QShortcut(QKeySequence("Ctrl+Shift+K"), self)
        proofread_shortcut.activated.connect(self.show_proofread_dialog)

        focus_shortcut = QShortcut(QKeySequence("F11"), self)
        focus_shortcut.activated.connect(self.toggle_focus_mode)

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

        # 目录拖动排序撤销：仅在目录树获得焦点时生效，不拦截正文原生 Ctrl+Z
        order_undo = QShortcut(QKeySequence("Ctrl+Z"), self.sidebar.tree)
        order_undo.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        order_undo.activated.connect(self.undo_chapter_order)

        escape_shortcut = QShortcut(QKeySequence("Escape"), self)
        escape_shortcut.activated.connect(self._handle_escape)

    def _handle_escape(self) -> None:
        # 目录拖动中按 Esc：先取消拖动，顺序保持不变
        if self.sidebar.tree.cancel_drag():
            return
        if self._focus_mode:
            self.exit_focus_mode()
            return
        self.drawer.close_drawer()

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
        self.shelf.notes_view_changed.connect(self._on_notes_view_changed)
        self.shelf.set_notes_view(self.settings.notes_view)

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

        # 角色（角色卡 + 内嵌关系图，书架页管理）
        self.shelf.new_character_requested.connect(self.new_character)
        self.shelf.character_view_changed.connect(self._on_character_view_changed)
        self.shelf.characters_changed.connect(self._update_shelf)

        # 地图（可创建多张）与导出
        self.shelf.open_map_requested.connect(self.open_map)
        self.shelf.new_map_requested.connect(self.new_map)
        self.shelf.rename_map_requested.connect(self.rename_map)
        self.shelf.delete_map_requested.connect(self.delete_map)
        self.shelf.stats_open_requested.connect(self.show_shelf_stats)
        self.shelf.refresh_requested.connect(self._update_shelf)
        self.shelf.export_book_requested.connect(self.export_book)
        self.shelf.export_book_chapters_requested.connect(self.export_book_chapters)
        self.shelf.cover_change_book_requested.connect(self.change_book_cover)

        # 导入（软件导出的同类型文件）
        self.shelf.import_book_requested.connect(self.import_book)
        self.shelf.import_note_requested.connect(self.import_note)
        self.shelf.import_timeline_requested.connect(self.import_timeline)
        self.shelf.import_outline_requested.connect(self.import_outline)
        self.shelf.import_characters_requested.connect(self.import_characters)
        self.shelf.import_map_requested.connect(self.import_map)

        # 成就：成就页右键设置展示勋章
        self.shelf.medal_showcase_requested.connect(self._set_showcase_medal)

        # 目录与编辑器
        self.sidebar.chapter_selected.connect(self.open_chapter)
        self.sidebar.chapter_move_requested.connect(self.move_chapter)
        self.sidebar.new_chapter_requested.connect(self.new_chapter)
        self.sidebar.new_volume_requested.connect(self.new_volume)
        self.sidebar.rename_requested.connect(self.rename_item)
        self.sidebar.delete_requested.connect(self.delete_item)
        self.sidebar.export_volume_requested.connect(self.export_volume)
        self.sidebar.new_related_requested.connect(self.add_related)
        self.sidebar.edit_related_requested.connect(self.edit_related)
        self.sidebar.delete_related_requested.connect(self.delete_related)
        self.sidebar.snippets_requested.connect(self.toggle_snippets_panel)

        # 右侧文笔仿写片段栏的信号在面板创建时连接（懒加载）
        self.editor_split.splitterMoved.connect(self._on_editor_split_moved)

        self.editor_page.content_edited.connect(self._on_content_edited)
        self.editor_page.title_edited.connect(self._on_title_edited)
        self.editor_page.chapter_chars_changed.connect(self._on_chapter_chars)
        self.editor_page.speed_delta.connect(self.speed.record)
        # 只有键盘输入的字才计入每日打字统计（粘贴 / 拖放不计）
        self.editor_page.typed_delta.connect(self._on_typed_delta)
        self.editor_page.set_speed_tracker(self.speed)
        self.editor_page.speed_panel_toggled.connect(
            self.buttons["speed"].setChecked
        )
        # 时速面板切换单位 / 统计开关后立即持久化
        self.editor_page.speed_panel.unit_changed.connect(self._on_speed_unit_changed)
        self.editor_page.speed_panel.tracking_changed.connect(self._on_speed_tracking_changed)
        # 正文右键：段落设置 / 智能排版 / 文本体检
        self.editor_page.paragraph_settings_requested.connect(self.show_paragraph_dialog)
        self.editor_page.smart_format_requested.connect(self.smart_format)
        self.editor_page.proofread_requested.connect(self.show_proofread_dialog)
        self.editor_page.focus_mode_requested.connect(self.toggle_focus_mode)
        self.editor_page.find_requested.connect(self.show_find_dialog)
        self.editor_page.new_chapter_requested.connect(self.new_chapter_here)

        self.editor_page.editor.undoAvailable.connect(self.buttons["undo"].setEnabled)
        self.editor_page.editor.redoAvailable.connect(self.buttons["redo"].setEnabled)

        # 设置抽屉
        self.drawer.settings_changed.connect(self._on_settings_changed)
        self.drawer.profile_changed.connect(self._on_profile_changed)
        self.drawer.webdav_upload_requested.connect(self.webdav_upload)
        self.drawer.webdav_restore_requested.connect(self.webdav_restore)
        self.drawer.webdav_test_requested.connect(self.webdav_test)
        self.drawer.webdav_create_dir_requested.connect(self.webdav_create_dir)
        self.drawer.webdav_prune_requested.connect(self.webdav_prune)
        self.drawer.auto_sync_now_requested.connect(self.webdav_upload)
        self.drawer.data_dir_change_requested.connect(self.change_data_dir)
        self.drawer.geometry_changed.connect(
            lambda: self.storage.save_settings(self.settings)
        )
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
                # 关闭设置后焦点直接还给正文：用户不需要“先点一下”才能打字
                self._refocus_editor_after_drawer(),
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
        startup_trace.mark("theme_begin")
        theme = build_theme(self.settings)
        self._theme = theme
        startup_trace.mark("theme_dict")
        # 外观风格的浮层阴影令牌同步给 motion（在构建任何浮层前生效）
        from app.ui import motion
        motion.set_elevation_tokens(theme)
        # 玻璃质感覆层的圆角跟随风格令牌
        from app.ui.widgets import set_ui_radius
        set_ui_radius(theme.get("radius", 8))
        # 表面质感引擎（液态玻璃 / 磨砂哑光）同步当前令牌与模糊开关
        from app.ui import surface
        surface.set_surface_state(theme)
        surface.set_glass_mode(str(getattr(self.settings, "glass_mode", "block")))
        surface.set_blur_enabled(bool(getattr(self.settings, "glass_blur", True)))

        app = QApplication.instance()
        if app is not None:
            app.setPalette(build_palette(theme))
            qss = build_qss(theme)
            startup_trace.mark("theme_qss_build")
            app.setStyleSheet(qss)
            startup_trace.mark("theme_qss_apply")

        self._icon_factory = IconFactory(theme["icon"], active_color=theme["accent"])
        self._refresh_icons()
        startup_trace.mark("theme_icons")
        self.root.set_theme(theme)
        self.editor_page.apply_settings(self.settings)
        self.editor_page.apply_writing_background()
        if self._snippets_panel is not None:
            self._snippets_panel.set_theme(theme)
            self._snippets_panel.set_fast_scroll(
                getattr(self.settings, "fast_scroll", True)
            )
        self.drawer.refresh_values()
        self.drawer.repaint_switches()
        self.drawer.refresh_surface()
        self._update_shelf()
        for dialog in self._map_dialogs.values():
            if dialog.isVisible():
                dialog.set_theme(theme)
        # 角色面板在数据计数未变时会跳过 _render，这里显式把主题送进去（含内嵌画布）
        panel = self.shelf.characters_view()
        if panel is not None:
            panel.set_theme(theme)
        # 主题重建后重新落位顶栏收起态（纸张圆角属性 & 恢复细条位置）
        self._apply_topbar_state()

    def _refresh_icons(self) -> None:
        factory = self._icon_factory
        theme = self._theme
        for button in self.buttons.values():
            name = str(button.property("icon_name") or "")
            if name:
                button.set_icon_spec(factory, name, 20)
            button.set_theme(theme)
        for button in getattr(self, "home_buttons", {}).values():
            name = str(button.property("icon_name") or "")
            if name:
                button.set_icon_spec(factory, name, 20)
            button.set_theme(theme)
        # 侧边栏导航（普通 QPushButton，QSS 样式；选中态换 accent 图标）
        shelf = getattr(self, "shelf", None)
        nav = getattr(shelf, "nav", None)
        if nav is not None:
            nav.refresh_icons(factory)
        # CTA 主按钮在 accent 底上，用 on_accent 色
        if shelf is not None and hasattr(shelf, "refresh_icons"):
            shelf.refresh_icons(IconFactory(theme["on_accent"],
                                             active_color=theme["on_accent"]))
        # 设置抽屉关闭钮
        if getattr(self, "drawer", None) is not None:
            self.drawer.refresh_icons(factory)
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
        if self._focus_mode:
            self.exit_focus_mode()
        self.flush()
        self.top_stack.setCurrentIndex(0)
        self.stack.setCurrentIndex(0)
        self._apply_topbar_state()
        self._update_shelf()

    def _update_shelf(self) -> None:
        # 连续多次 set_* 只重建一次页面；try/finally 保证异常后也不会卡在批量态
        self.shelf.begin_batch()
        try:
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
            self.shelf.set_characters(
                self.storage.load_characters(),
                self.storage.load_relations(),
                self._theme,
                self.settings.character_view,
            )
            # 角色导出文件名 / 文件头使用当前作品名（面板未创建时不触发创建）
            panel = self.shelf.characters_panel()
            if panel is not None:
                panel.book_name = self.book.title if self.book is not None else ""
            self.shelf.set_maps(self.storage.load_maps())
            self.shelf.set_achievements(self.achievements.unlocked)
            self.shelf.set_showcase(self.settings.showcase_medal)
            self.shelf.set_view(self.settings.shelf_view)
            self.shelf.set_user(self.settings.nickname, self.settings.avatar_path)
        finally:
            self.shelf.end_batch()

    def _on_shelf_view_changed(self, view: str) -> None:
        self.settings.shelf_view = view
        self.storage.save_settings(self.settings)

    def _on_notes_view_changed(self, view: str) -> None:
        """记住设定页用的是卡片还是列表，重开程序保持。"""
        self.settings.notes_view = view if view in {"grid", "list"} else "grid"
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
            self._apply_topbar_state()
            self.open_chapter(self.current_chapter_id)
            self.editor_page.focus_editor()
            QTimer.singleShot(0, self._position_side_toggle)
            # 上次退出时若展开着仿写区，打开书籍后还原（面板本身懒加载，不预建）
            if getattr(self.settings, "snippets_open", False) and not self._snippets_visible:
                self.toggle_snippets_panel(True)

    def _load_current_book(self, book_id: str, keep_chapter: bool = False) -> None:
        book = self.storage.load_book(book_id)
        if book is None:
            return
        self.book = book
        self._chapter_order_undo.clear()
        if not keep_chapter or not book.find_chapter(self.current_chapter_id)[1]:
            self.current_chapter_id = book.last_chapter_id
        volume = book.volume_of(self.current_chapter_id)
        self.current_volume_id = volume.id if volume is not None else ""
        self._recount_all()
        self.sidebar.set_book(book, self.counts, self.current_chapter_id)
        if self._snippets_panel is not None:
            self._snippets_panel.set_context(book.title, "")
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
        if self._snippets_panel is not None:
            self._snippets_panel.set_context(self.book.title, chapter.title)
        self.editor_page.setDisabled(False)
        self.editor_page.load_text(chapter.title, chapter.content)
        self._editor_chapter_id = chapter_id
        self.editor_page.set_total_chars(self._total_chars)
        self.sidebar.select_chapter(chapter_id)
        self.editor_page.focus_editor()

    # ==================================================================
    # 保存与快照
    # ==================================================================
    def flush(self, force_snapshot: bool = False) -> bool:
        """立即保存当前章节的未落盘修改；返回是否真实写入成功。

        每次自动保存都会记录一个历史版本；自动快照按 config.AUTO_SNAPSHOT_GAP
        的时间间隔或 config.AUTO_SNAPSHOT_CHAR_DELTA 的字数变化去重，
        手动保存（Ctrl+S）等 force_snapshot=True 的调用始终立即记录。
        """
        if self.book is None or not self.current_chapter_id:
            return True
        if self._editor_chapter_id != self.current_chapter_id:
            # 编辑器里还是别的书/章节的旧文本（如刚切换书籍但尚未载入），
            # 此时把编辑器文本写回会覆盖新章节内容，必须跳过。
            return True
        volume, chapter = self.book.find_chapter(self.current_chapter_id)
        if chapter is None:
            return True
        text = self.editor_page.current_text()
        if text == chapter.content:
            return True
        previous = chapter.content
        chapter.content = text
        chapter.updated_at = time.time()
        self.book.touch()
        self.counts[chapter.id] = count_chars(text, self.settings.exclude_punct)
        try:
            self.storage.save_book(self.book, self._total_chars)
        except Exception as exc:   # noqa: BLE001 - 任何落盘异常都要如实反馈
            # 回滚内存里的正文，下一次 flush 才会真正重试而不是误判“无改动”
            chapter.content = previous
            self._last_save_error = str(exc) or exc.__class__.__name__
            return False
        self.sidebar.set_saved_time(time.strftime("%H:%M"))

        # 历史版本：随自动保存记录，每章最多保留 history_cap() 个；
        # 自动快照按「时间间隔 或 字数变化」阈值去重，手动保存始终立即记录。
        now = time.time()
        current_chars = self.counts[chapter.id]
        last_time, last_chars = self._snapshot_marks.get(chapter.id, (0.0, -1))
        if (
            force_snapshot
            or now - last_time >= config.AUTO_SNAPSHOT_GAP
            or abs(current_chars - last_chars) >= config.AUTO_SNAPSHOT_CHAR_DELTA
        ):
            self._snapshot_marks[chapter.id] = (now, current_chars)
            try:
                self.storage.save_snapshot(
                    self.book.id,
                    chapter.id,
                    text,
                    current_chars,
                    cap=self.achievements.history_cap(),
                )
            except Exception:   # noqa: BLE001 - 快照失败不影响正文已落盘的事实
                pass
        return True

    def save_current_book(self) -> bool:
        if self._dirty:
            self.editor_page.begin_save()
            ok = self.flush()
            self._dirty = not ok
            if not ok:
                self.autosave_timer.start()   # 失败后保持脏标记，稍后自动重试
            self.editor_page.set_save_result(ok, time.strftime("%H:%M"), self._last_save_error)
            return ok
        return True

    def _manual_save(self) -> None:
        self.editor_page.begin_save()
        ok = self.flush(force_snapshot=True)
        self._dirty = not ok
        if not ok:
            self.autosave_timer.start()
        else:
            self._play_save_sound()
        self.editor_page.set_save_result(ok, time.strftime("%H:%M"), self._last_save_error)
        self.editor_page.focus_editor()

    def _play_save_sound(self) -> None:
        """手动保存成功后播放所选提示音；自动保存不经过这里，保持静默。"""
        play_manual_save_sound(str(getattr(self.settings, "manual_save_sound", "wood")))

    def _on_title_edited(self, title: str) -> None:
        """双击正文标题改名：与左侧目录重命名走同一套数据流。"""
        if self.book is None or not self.current_chapter_id:
            return
        _volume, chapter = self.book.find_chapter(self.current_chapter_id)
        if chapter is None or chapter.title == title:
            return
        chapter.title = title
        self.book.touch()
        self.storage.save_book(self.book, self._total_chars)
        self.sidebar.set_book(self.book, self.counts, self.current_chapter_id)
        self.editor_page.set_title(title)

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
        self._play_achievement_sound()
        toast = AchievementToast(key, self)
        toast.show_at()
        self.shelf.set_achievements(self.achievements.unlocked)
        self.shelf.set_showcase(self.settings.showcase_medal)
        return True

    def _play_achievement_sound(self) -> None:
        """成就真正解锁时播放一次预渲染 WAV（音量已烘焙进音频，不做实时调节）。"""
        if not bool(getattr(self.settings, "achievement_sound", True)):
            return
        play_wav(config.ASSETS_DIR / "sounds" / "achievement_unlock.wav")

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
        menu.addAction("段落设置…", self.show_paragraph_dialog)
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

    def show_proofread_dialog(self) -> None:
        if self.book is None or not self.current_chapter_id:
            MessageBox.information(self, "文本体检", "请先打开一本书并进入正文章节。")
            return
        if self._proofread_dialog is None:
            self._proofread_dialog = ProofreadDialog(self)
        self._proofread_dialog.show()
        self._proofread_dialog.raise_()
        self._proofread_dialog.rescan()

    # ==================================================================
    # 专注模式（沉浸式）
    # ==================================================================
    def toggle_focus_mode(self) -> None:
        if self._focus_mode:
            self.exit_focus_mode()
        else:
            self.enter_focus_mode()

    def enter_focus_mode(self) -> None:
        """隐藏侧栏 / 顶栏 / 底部字数行，正文铺满，光标行滚动居中。"""
        if self._focus_mode or self.stack.currentIndex() != 1:
            return
        if self.book is None or not self.current_chapter_id:
            return
        editor = self.editor_page.editor
        cursor = editor.textCursor()
        self._focus_snapshot = {
            "sidebar": not self.sidebar.isHidden(),
            "snippets": bool(self._snippets_visible),
            "speed": not self.editor_page.speed_panel.isHidden(),
            "scroll": editor.verticalScrollBar().value(),
            "anchor": cursor.anchor(),
            "position": cursor.position(),
        }
        self._focus_mode = True
        self.sidebar.hide()
        self.side_toggle_bar.hide()
        self.top_stack.hide()
        self.top_reveal.hide()
        if self._snippets_visible:
            self._snippets_visible = False
            if self._snippets_panel is not None:
                self._snippets_panel.hide()
            handle = self.editor_split.handle(1)
            if handle is not None:
                handle.hide()
        self.editor_page.speed_panel.hide()
        self.editor_page.set_focus_mode(True)
        self._apply_workspace_flush()
        self._sync_focus_button()
        self.editor_page.focus_editor()

    def exit_focus_mode(self) -> None:
        """恢复进入前的布局、光标、选区、滚动位置与撤销栈。"""
        if not self._focus_mode:
            return
        snapshot = self._focus_snapshot or {}
        self._focus_mode = False
        self.editor_page.set_focus_mode(False)

        sidebar_visible = bool(snapshot.get("sidebar", True))
        self.sidebar.setVisible(sidebar_visible)
        self.buttons["toc"].setChecked(sidebar_visible)
        self.side_toggle_bar.show()

        if snapshot.get("snippets"):
            panel = self._ensure_snippets_panel()
            self._snippets_visible = True
            self.sidebar.btn_snippets.setChecked(True)
            self._apply_snippets_width()
            panel.show()
            handle = self.editor_split.handle(1)
            if handle is not None:
                handle.show()
            panel.raise_()
        else:
            self._snippets_visible = False
            self.sidebar.btn_snippets.setChecked(False)
            if self._snippets_panel is not None:
                self._snippets_panel.hide()
            handle = self.editor_split.handle(1)
            if handle is not None:
                handle.hide()

        if snapshot.get("speed"):
            self.editor_page.speed_panel.show()

        self._apply_topbar_state()
        self._apply_workspace_flush()
        self._update_side_toggle_arrow()

        editor = self.editor_page.editor
        cursor = editor.textCursor()
        cursor.setPosition(int(snapshot.get("anchor", 0)))
        cursor.setPosition(
            int(snapshot.get("position", 0)), QTextCursor.KeepAnchor
        )
        editor.setTextCursor(cursor)
        scroll = int(snapshot.get("scroll", 0))
        bar = editor.verticalScrollBar()
        # 贴边恢复会按比例延后调整滚动，这里排在它之后落回进入前的精确位置
        QTimer.singleShot(0, lambda: bar.setValue(min(scroll, bar.maximum())))
        self._focus_snapshot = None
        self._sync_focus_button()
        self.editor_page.focus_editor()

    def _sync_focus_button(self) -> None:
        """顶栏「专注」按钮的激活态始终跟随真实专注状态（含 F11 / Esc 入口）。"""
        button = self.buttons.get("focus")
        if button is not None:
            button.setChecked(bool(self._focus_mode))

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
                # 颜色走全局 QSS（QSS 优先级高于程序字体），必须整体刷新
                self.apply_theme()
            else:
                # 字体 / 字号 / 粗斜下划线只影响正文：直接套用，避免整窗 QSS 重建
                # （整窗刷新约 0.8s，实时预览会明显卡顿）
                self.editor_page.apply_settings(self.settings)

        dialog.live_changed.connect(_live)
        if dialog.exec() == FontDialog.Accepted:
            (self.settings.font_family, self.settings.font_size, self.settings.editor_text,
             self.settings.font_bold, self.settings.font_italic, self.settings.font_underline
             ) = dialog.values()
        else:
            # 取消：还原为打开前的字体
            (self.settings.font_family, self.settings.font_size, self.settings.editor_text,
             self.settings.font_bold, self.settings.font_italic, self.settings.font_underline
             ) = old
        self.storage.save_settings(self.settings)
        if self.settings.editor_text != old[2]:
            # 正文颜色走全局 QSS，必须整体刷新（全窗重建约 0.8s）
            self.apply_theme()
        else:
            # 字体 / 字号 / 粗斜下划线只影响正文：局部套用，关窗不卡顿
            self.editor_page.apply_settings(self.settings)

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

    def show_paragraph_dialog(self) -> None:
        """段落设置：对齐 / 首行缩进 / 段前段后间距 / 行距（借鉴 Word 段落对话框）。

        字体不在此修改，段落度量基于正文当前字体，自动跟随“字体”设置。
        """
        dialog = ParagraphDialog(self, self.settings)
        if dialog.exec() != ParagraphDialog.Accepted:
            return
        (self.settings.para_align,
         self.settings.para_indent,
         self.settings.para_space_before,
         self.settings.para_space_after,
         self.settings.line_spacing) = dialog.values()
        self.storage.save_settings(self.settings)
        self.editor_page.apply_settings(self.settings)

    def show_writing_theme(self) -> None:
        """写作主题面板：圆形色块实时切换写作区背景。"""
        dialog = WritingThemeDialog(self, self.settings)
        dialog.theme_applied.connect(self._on_writing_theme)
        dialog.backgrounds_changed.connect(self._on_backgrounds_changed)
        dialog.exec()
        theme_key, image = dialog.values()
        self.settings.writing_theme = theme_key
        self.settings.writing_image = image
        self.settings.backgrounds = dialog.backgrounds()
        self.storage.save_settings(self.settings)
        self.apply_theme()

    def _on_writing_theme(self, theme_key: str, image: str) -> None:
        """面板内点击色块时实时应用。"""
        previous = self.settings.writing_theme
        self.settings.writing_theme = theme_key
        self.settings.writing_image = image
        self.storage.save_settings(self.settings)
        if theme_key == "custom" and image and previous == "custom":
            # 自定义图片之间切换：只需换纸张背景，不必重建全局 QSS
            self.editor_page.apply_writing_background()
            return
        self.apply_theme()

    def _on_backgrounds_changed(self, names: list) -> None:
        """面板里上传/删除背景后立即落盘（原子写），崩溃也不丢列表。"""
        self.settings.backgrounds = [str(name) for name in names]
        self.storage.save_settings(self.settings)

    def show_name_dialog(self) -> None:
        # 复用同一实例：候选、选中名字与各分类筛选条件跨打开保留
        if self._name_dialog is None:
            self._name_dialog = NameGeneratorDialog(self)
        self._name_dialog.exec()

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
        # 同一时间轴只保留一个窗口：重复点卡片时把旧窗口前置。
        # 否则两个窗口各持一份旧 item，后关闭的那个会把新窗口保存的事件覆盖回旧版。
        for dialog in list(self._timeline_dialogs):
            if dialog.item.get("id") == timeline_id:
                if dialog.isMinimized():
                    dialog.showNormal()
                dialog.show()
                dialog.raise_()
                dialog.activateWindow()
                return
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
        # 进入新的设定编辑窗前，先把上一篇未落盘的修改落盘
        for other in self._note_windows.values():
            if other.isVisible():
                other.flush_pending()
        doc = None
        for item in self.storage.load_library().get("notes", []):
            if item.get("id") == note_id:
                doc = item
                break
        if doc is None:
            return
        window = NoteEditorWindow(self.storage, doc, self)
        window.saved.connect(lambda w=window: self._on_note_saved(w))
        window.closed.connect(self._on_note_window_closed)
        window.show()
        self._note_windows[note_id] = window

    def _on_note_saved(self, window) -> None:
        """设定保存后只就地更新这一条卡片，不重建整页。"""
        doc = dict(getattr(window, "doc", {}) or {})
        if doc.get("id"):
            self.shelf.update_note(doc)

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
    # 角色卡 + 角色关系思维导图（视图内嵌在角色页）
    # ==================================================================
    def new_character(self) -> None:
        """新建角色卡：切到角色页并弹出表单。"""
        self.shelf.nav.set_page("characters")
        panel = self.shelf.characters_view()
        if panel is not None:
            panel.add_character()

    def _on_character_view_changed(self, view: str) -> None:
        """记住角色页上次停留的视图（列表 / 关系图）。"""
        if view not in ("list", "board"):
            return
        if self.settings.character_view == view:
            return
        self.settings.character_view = view
        self.storage.save_settings(self.settings)


    def import_characters(self) -> None:
        """导入角色 JSON：读文件 → 预检 → 冲突策略二选一 → 写盘 → 汇报。"""
        path = self._pick_open_file("导入角色", "JSON 文件 (*.json);;所有文件 (*)")
        if not path:
            return
        try:
            size = os.path.getsize(path)
        except OSError as exc:
            MessageBox.warning(self, "导入失败", f"无法读取文件：{exc}")
            return
        if size > 30 * 1024 * 1024:
            MessageBox.warning(
                self, "导入失败",
                f"文件过大（{size / 1024 / 1024:.1f} MB），角色 JSON 不应超过 30 MB。",
            )
            return
        try:
            raw = Path(path).read_bytes()
            # utf-8-sig：兼容本软件导出的带 BOM 文件与普通 UTF-8
            data = json.loads(raw.decode("utf-8-sig"))
        except (OSError, ValueError, UnicodeDecodeError) as exc:
            MessageBox.warning(self, "导入失败", f"无法读取文件：{exc}")
            return
        if not (isinstance(data, dict) and isinstance(data.get("characters"), list)):
            MessageBox.warning(self, "导入失败", "这不是本软件导出的角色文件。")
            return
        pre = self.storage.analyze_character_import(data)
        if not (pre["valid_characters"] or pre["valid_relations"]):
            MessageBox.warning(
                self, "导入失败",
                f"文件里没有可导入的有效数据。\n"
                f"坏数据：角色 {pre['skipped_characters']} 条、"
                f"关系 {pre['skipped_relations']} 条。",
            )
            return
        dialog = CharacterImportDialog(self, pre)
        if dialog.exec() != CharacterImportDialog.Accepted:
            return
        report = self.storage.import_character_board(data, dialog.strategy())
        self._update_shelf()
        panel = self.shelf.characters_panel()
        if panel is not None:
            panel.reload()
        lines = [
            f"新增角色 {report['added_characters']} 名，"
            f"更新 {report['updated_characters']} 名，"
            f"跳过冲突 {report['skipped_conflicts']} 名；",
            f"新增关系 {report['added_relations']} 条，"
            f"重复跳过 {report['duplicate_relations']} 条，"
            f"坏/悬空/自环剔除 {report['dropped_relations']} 条；",
        ]
        if report["skipped_characters"] or report["skipped_relations"]:
            lines.append(
                f"坏数据跳过：角色 {report['skipped_characters']} 条、"
                f"关系 {report['skipped_relations']} 条；"
            )
        lines.append(
            f"画布 {report['board_width']}×{report['board_height']}，"
            f"重排节点 {report['reflowed_characters']} 个。"
        )
        MessageBox.information(self, "导入完成", "\n".join(lines))

    # ==================================================================
    # 地图（可创建多张，独立保存为 map_global.json）
    # ==================================================================
    def new_map(self) -> None:
        text, ok = QInputDialog.getText(self, "新建地图", "地图名称：")
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

    def export_volume(self, volume_id: str) -> None:
        """把某一卷导出为单个 txt / docx 文件（只读：不改正文、不写盘、不响音效）。"""
        if self.book is None:
            return
        volume = self.book.find_volume(volume_id)
        if volume is None:
            return
        if not volume.chapters:
            MessageBox.information(self, "无法导出", "该卷还没有章节，请先新建章节再导出。")
            return

        dialog = VolumeExportDialog(self, volume.title)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        fmt = dialog.chosen_format()

        default_dir = str(self.settings.export_dir or "").strip()
        if not default_dir or not Path(default_dir).is_dir():
            default_dir = QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation)
        if not default_dir:
            default_dir = str(Path.home())
        default_path = str(
            Path(default_dir) / f"{exporter.sanitize_filename(volume.title)}.{fmt}"
        )
        name_filter = "Word 文档 (*.docx)" if fmt == exporter.DOCX_FORMAT else "文本文档 (*.txt)"
        path, _f = QFileDialog.getSaveFileName(self, "导出本卷", default_path, name_filter)
        if not path:
            return
        if not path.lower().endswith("." + fmt):
            path += "." + fmt

        # 用内存数据组装；当前章若正在编辑，取编辑器里的最新文本（不触发保存）
        chapters: list[tuple[str, str]] = []
        for chapter in volume.chapters:
            text = chapter.content
            if chapter.id == self.current_chapter_id and chapter.id == self._editor_chapter_id:
                text = self.editor_page.current_text()
            chapters.append((chapter.title, text))
        try:
            exporter.export_volume_file(path, fmt, chapters, self.settings)
        except Exception as exc:   # noqa: BLE001 - 写入失败要如实提示且不留残文件
            MessageBox.warning(self, "导出失败", f"无法写入文件：{exc}")
            return

        directory = str(Path(path).parent)
        if directory and directory != self.settings.export_dir:
            self.settings.export_dir = directory
            try:
                self.storage.save_settings(self.settings)
            except Exception:   # noqa: BLE001 - 记忆目录失败不影响导出结果
                pass
        MessageBox.information(self, "导出成功", f"已导出到：\n{path}")

    def _ask_export_folder_conflict(self, folder_name: str) -> str:
        """同名文件夹处理：合并 / 新建带时间戳 / 取消。"""
        box = QMessageBox(self)
        box.setWindowTitle("文件夹已存在")
        box.setText(f"「{folder_name}」已经存在，要如何处理？")
        merge_button = box.addButton("合并到已有文件夹", QMessageBox.ButtonRole.AcceptRole)
        new_button = box.addButton("新建带时间戳的文件夹", QMessageBox.ButtonRole.ActionRole)
        box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(merge_button)
        box.exec()
        clicked = box.clickedButton()
        if clicked is merge_button:
            return "merge"
        if clicked is new_button:
            return "timestamp"
        return "cancel"

    def export_book_chapters(self, book_id: str) -> None:
        """整本导出：一章一个文件、平铺在「书名」文件夹内（只读）。"""
        book = self.book if (self.book is not None and self.book.id == book_id) \
            else self.storage.load_book(book_id)
        if book is None:
            return

        chapters: list[tuple[str, str]] = []
        for volume in book.volumes:
            for chapter in volume.chapters:
                text = chapter.content
                if (self.book is not None and self.book.id == book_id
                        and chapter.id == self.current_chapter_id
                        and chapter.id == self._editor_chapter_id):
                    text = self.editor_page.current_text()
                chapters.append((chapter.title, text))
        if not chapters:
            MessageBox.information(self, "无法导出", "这本书还没有章节，请先新建章节再导出。")
            return

        dialog = VolumeExportDialog(self, book.title, mode=MODE_BOOK)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        fmt = dialog.chosen_format()

        default_dir = str(self.settings.export_dir or "").strip()
        if not default_dir or not Path(default_dir).is_dir():
            default_dir = QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation)
        if not default_dir:
            default_dir = str(Path.home())
        parent = QFileDialog.getExistingDirectory(self, "选择导出位置", default_dir)
        if not parent:
            return

        folder_name = exporter.sanitize_filename(
            book.title, max_chars=80, fallback="未命名作品")
        if (Path(parent) / folder_name).exists():
            choice = self._ask_export_folder_conflict(folder_name)
            if choice == "cancel":
                return
            if choice == "timestamp":
                folder_name = exporter.timestamped_folder_name(parent, folder_name)

        try:
            folder, files = exporter.export_book_directory(
                parent, folder_name, fmt, chapters, self.settings)
        except exporter.BookExportError as exc:
            MessageBox.warning(
                self, "导出失败",
                f"已成功导出 {exc.success_count}/{exc.total} 章，"
                f"「{exc.failed_name}」写入失败：{exc.reason}",
            )
            return
        except Exception as exc:   # noqa: BLE001 - 失败要中文提示且不留半截文件
            MessageBox.warning(self, "导出失败", f"无法导出：{exc}")
            return

        if parent != self.settings.export_dir:
            self.settings.export_dir = parent
            try:
                self.storage.save_settings(self.settings)
            except Exception:   # noqa: BLE001 - 记忆目录失败不影响导出结果
                pass
        MessageBox.information(
            self, "导出成功", f"已导出 {len(files)} 章到：\n{folder}")

    # ==================================================================
    # 导入（兼容本软件历史版本 / 当前版本导出的同类型文件）
    # ==================================================================
    def _pick_open_file(self, title: str, name_filter: str) -> str:
        path, _f = QFileDialog.getOpenFileName(self, title, "", name_filter)
        return path

    def import_book(self) -> None:
        """导入小说 TXT（本软件导出格式，普通 TXT 按章节标记尽力拆章）。"""
        from app.models import Book, Chapter, Volume
        from app.services import importer

        path = self._pick_open_file("导入小说", "文本文档 (*.txt);;所有文件 (*)")
        if not path:
            return
        try:
            text = importer.read_text_file(path)
        except OSError as exc:
            MessageBox.warning(self, "导入失败", f"无法读取文件：{exc}")
            return
        parsed = importer.parse_novel_txt(text)
        now = time.time()
        book = Book(
            id=new_id(),
            title=parsed["title"],
            author=parsed["author"],
            cover_index=random.randint(0, 5),
            created_at=now,
            updated_at=now,
        )
        for vol_data in parsed["volumes"]:
            volume = Volume(id=new_id(), title=vol_data["title"])
            for ch_data in vol_data["chapters"]:
                volume.chapters.append(
                    Chapter(id=new_id(), title=ch_data["title"], content=ch_data["content"])
                )
            book.volumes.append(volume)
        first = book.first_chapter()
        book.last_chapter_id = first.id if first is not None else ""
        self.storage.save_book(book)
        self._update_shelf()
        self._unlock_achievement("first_book")   # 卧龙出山：创立第一本书
        MessageBox.information(
            self, "导入成功", f"《{book.title}》已导入书架，共 {book.chapter_count()} 章。"
        )

    def import_note(self) -> None:
        """导入设定 TXT（首行标题、空行、正文）。"""
        from app.services import importer

        path = self._pick_open_file("导入设定", "文本文档 (*.txt);;所有文件 (*)")
        if not path:
            return
        try:
            text = importer.read_text_file(path)
        except OSError as exc:
            MessageBox.warning(self, "导入失败", f"无法读取文件：{exc}")
            return
        parsed = importer.parse_note_txt(text)
        doc = self.storage.import_note_doc(parsed["title"], parsed["content"])
        self._update_shelf()
        MessageBox.information(self, "导入成功", f"设定「{doc['title']}」已导入。")

    def import_timeline(self) -> None:
        """导入时间轴 JSON（本软件导出格式）。"""
        path = self._pick_open_file("导入时间轴", "JSON 文件 (*.json);;所有文件 (*)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError) as exc:
            MessageBox.warning(self, "导入失败", f"无法读取文件：{exc}")
            return
        if not (isinstance(data, dict) and isinstance(data.get("events"), list)):
            MessageBox.warning(self, "导入失败", "这不是本软件导出的时间轴文件。")
            return
        item = self.storage.import_timeline(data)
        self._update_shelf()
        MessageBox.information(self, "导入成功", f"时间轴「{item['title']}」已导入。")

    def import_outline(self) -> None:
        """导入大纲 JSON（本软件导出格式）。"""
        path = self._pick_open_file("导入大纲", "JSON 文件 (*.json);;所有文件 (*)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError) as exc:
            MessageBox.warning(self, "导入失败", f"无法读取文件：{exc}")
            return
        if not (isinstance(data, dict) and isinstance(data.get("nodes"), list)):
            MessageBox.warning(self, "导入失败", "这不是本软件导出的大纲文件。")
            return
        item = self.storage.import_outline(data)
        self._update_shelf()
        MessageBox.information(self, "导入成功", f"大纲「{item['title']}」已导入。")

    def import_map(self) -> None:
        """导入地图底图图片（导出的 PNG / 普通图片，作为新地图底图）。"""
        path = self._pick_open_file(
            "导入地图底图", "图片文件 (*.png *.jpg *.jpeg *.webp *.bmp);;所有文件 (*)"
        )
        if not path:
            return
        try:
            item = self.storage.import_map_image(path)
        except OSError as exc:
            MessageBox.warning(self, "导入失败", f"无法读取文件：{exc}")
            return
        self._update_shelf()
        MessageBox.information(self, "导入成功", f"地图「{item['title']}」已导入，可继续在底图上标注。")

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
        if self._snippets_panel is not None:
            self._snippets_panel.flush_pending()
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
                "words_daily.json", "achievements.json", "snippets.json",
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
        # v3：速度单位 char 字/分 / hour 字/时
        if unit in ("char", "hour"):
            self.settings.speed_unit_scale = unit
        self.storage.save_settings(self.settings)

    def _on_speed_tracking_changed(self, on: bool) -> None:
        self.settings.speed_tracking = bool(on)
        self.storage.save_settings(self.settings)

    def show_history_dialog(self) -> None:
        if self.book is None or not self.current_chapter_id:
            return
        self.flush()   # 先把编辑区未落盘的修改写入，历史列表才与当前内容一致
        _volume, chapter = self.book.find_chapter(self.current_chapter_id)
        if chapter is None:
            return
        dialog = HistoryDialog(
            self.storage,
            self.book.id,
            chapter.id,
            chapter.title,
            self.achievements.history_cap(),
            self,
        )
        if dialog.exec() == HistoryDialog.Accepted:
            content = dialog.selected_content()
            if content is None:
                return
            # 还原前先把当前内容另存一版，避免「还原即丢失」；
            # 若刚刚的 flush 已经存下同一份内容则不重复写盘。
            previous_text = self.editor_page.current_text()
            if content == previous_text:
                return
            snapshots = self.storage.list_snapshots(self.book.id, chapter.id)
            already_kept = bool(
                snapshots
                and self.storage.snapshot_content(snapshots[0]) == previous_text
            )
            if not already_kept:
                try:
                    self.storage.save_snapshot(
                        self.book.id,
                        chapter.id,
                        previous_text,
                        count_chars(previous_text, self.settings.exclude_punct),
                        cap=self.achievements.history_cap(),
                    )
                except Exception as exc:   # noqa: BLE001 - 保底失败则取消还原
                    MessageBox.warning(
                        self, "恢复已取消", f"当前内容备份失败，为避免丢失已中止恢复：\n{exc}"
                    )
                    return
            chapter.content = content
            chapter.updated_at = time.time()
            self.book.touch()
            self.storage.save_book(self.book, self._total_chars)
            self.editor_page.load_text(chapter.title, content)
            self._editor_chapter_id = self.current_chapter_id
            self.counts[chapter.id] = count_chars(content, self.settings.exclude_punct)
            self._snapshot_marks[chapter.id] = (time.time(), self.counts[chapter.id])
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
        worker = self._webdav_worker
        mode = worker.mode if worker is not None else ""
        MessageBox.information(self, "云同步", message)
        if mode == "restore":
            self._reload_after_restore()
        elif mode == "upload":
            # 手动上传成功后记下指纹，避免紧接着的自动同步重复上传
            self.settings.auto_sync_signature = self._data_signature()
            self._record_sync_result(True, message="上传成功")

    def _on_webdav_failed(self, message: str) -> None:
        self._record_sync_result(False, message=message)
        MessageBox.warning(self, "云同步失败", message)

    def _record_sync_result(self, ok: bool, message: str = "") -> None:
        """更新「上次同步 HH:MM · 成功/失败」，失败原因截断成一行写进面板。"""
        now = time.strftime("%H:%M")
        self.settings.auto_sync_last_time = now
        self.settings.auto_sync_last_ok = bool(ok)
        self.storage.save_settings(self.settings)
        if ok:
            self.drawer.set_auto_sync_status(f"上次同步 {now} · 成功")
        else:
            brief = (message or "未知原因").splitlines()[0][:120]
            self.drawer.set_auto_sync_status(f"上次同步 {now} · 失败：{brief}")

    # ------------------------------------------------------------------
    # 自动同步：后台静默上传（无弹窗、主线程不做网络 IO）
    # ------------------------------------------------------------------
    def _sync_target_tuple(self) -> tuple:
        """同步目标标识（地址 / 账号 / 应用密码 / 目录），任一变化都算换了目标。"""
        s = self.settings
        return ((s.webdav_url or "").strip(), (s.webdav_user or "").strip(),
                s.webdav_password or "", normalize_remote_dir(s.webdav_dir or ""))

    def _reset_sync_state_on_target_change(self) -> bool:
        """切换账号 / 目录 / 地址后清空同步状态：指纹清空 → 下一次同步强制上传。

        只清「上次同步时间/上次成功/内容指纹/连续失败计数」，不动本地数据，
        也不删云端任何文件（旧账号的备份仍在旧账号目录里）。
        """
        target = self._sync_target_tuple()
        if target == self._sync_target:
            return False
        self._sync_target = target
        self._auto_sync_failures = 0
        self.settings.auto_sync_signature = ""
        self.settings.auto_sync_last_time = ""
        self.settings.auto_sync_last_ok = False
        self.storage.save_settings(self.settings)
        self.drawer.set_auto_sync_status("尚未同步（已切换同步目标，首次会完整上传）")
        return True

    def _restart_auto_sync_timer(self) -> None:
        """按当前设置启停自动同步；关闭或凭据不全时保持停止。"""
        self.auto_sync_timer.stop()
        if not self.settings.auto_sync_enabled:
            return
        if not ((self.settings.webdav_url or "").strip()
                and (self.settings.webdav_user or "").strip()
                and (self.settings.webdav_password or "").strip()):
            return
        try:
            minutes = int(self.settings.auto_sync_interval or 30)
        except (TypeError, ValueError):
            minutes = 30
        minutes = max(10, min(60, minutes))
        self.auto_sync_timer.setInterval(minutes * 60 * 1000)
        self.auto_sync_timer.start()

    def _data_signature(self) -> str:
        """本地 JSON 数据指纹（内容哈希 + 大小）。

        不把 settings.json 算进去：同步状态（上次同步时间/指纹）本身就写在
        settings.json 里，若计入会自己触发自己，永远「有变化」。
        """
        import hashlib

        digest = hashlib.sha1()
        try:
            files = sorted(self.storage.base_dir.rglob("*.json"))
        except OSError:
            return ""
        for path in files:
            if path.name == "settings.json" or ".tmp" in path.name:
                continue
            try:
                digest.update(path.relative_to(self.storage.base_dir).as_posix().encode("utf-8"))
                with path.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(1 << 20), b""):
                        digest.update(chunk)
            except OSError:
                continue
        return digest.hexdigest()

    def _auto_sync_tick(self) -> None:
        """到点后：内容未变不发请求；有变化则起后台线程静默上传。"""
        if not self.settings.auto_sync_enabled:
            self.auto_sync_timer.stop()
            return
        if not ((self.settings.webdav_url or "").strip()
                and (self.settings.webdav_user or "").strip()
                and (self.settings.webdav_password or "").strip()):
            return
        if self._webdav_worker is not None and self._webdav_worker.isRunning():
            return
        self.flush()
        signature = self._data_signature()
        if signature and signature == (self.settings.auto_sync_signature or ""):
            return
        self.storage.save_settings(self.settings)
        worker = WebDavWorker("upload", self.settings, self.storage.base_dir, self)
        worker.finished_ok.connect(
            lambda _msg, sig=signature: self._on_auto_sync_done(True, "", sig)
        )
        worker.failed.connect(lambda msg: self._on_auto_sync_done(False, msg))
        worker.start()
        self._webdav_worker = worker

    def _on_auto_sync_done(self, ok: bool, message: str, signature: str = "") -> None:
        worker, self._webdav_worker = self._webdav_worker, None
        if worker is not None:
            worker.deleteLater()
        if ok:
            self._auto_sync_failures = 0
            self.settings.auto_sync_signature = signature
            self._record_sync_result(True)
            return
        self._auto_sync_failures += 1
        brief = (message or "未知原因").splitlines()[0][:120]
        if self._auto_sync_failures >= 3:
            # 连续失败 3 次：本轮不再重试，等下一周期；状态行常驻可读原因
            now = time.strftime("%H:%M")
            self.settings.auto_sync_last_time = now
            self.settings.auto_sync_last_ok = False
            self.storage.save_settings(self.settings)
            self.drawer.set_auto_sync_status(
                f"自动同步连续失败 {self._auto_sync_failures} 次，已暂停到下一周期：{brief}"
            )
            return
        self._record_sync_result(False, message)

    def webdav_prune(self) -> None:
        """清理云端旧备份：只保留最近 3 份（DELETE，404 视为已不存在）。"""
        if self._webdav_worker is not None and self._webdav_worker.isRunning():
            MessageBox.information(self, "云同步", "上一次同步还在进行中，请稍候。")
            return
        if not self._confirm(
                "云同步", "删除云端旧备份，只保留最近 3 份？此操作不可撤销。"):
            return
        self.storage.save_settings(self.settings)
        worker = WebDavWorker("prune", self.settings, self.storage.base_dir, self)
        worker.finished_ok.connect(self._on_webdav_ok)
        worker.failed.connect(self._on_webdav_failed)
        worker.start()
        self._webdav_worker = worker

    def webdav_test(self) -> None:
        """设置面板「测试连接」：后台 PROPFIND 同步目录，结果只写面板状态行。"""
        if (self._webdav_test_worker is not None
                and self._webdav_test_worker.isRunning()):
            return
        if not ((self.settings.webdav_url or "").strip()
                and (self.settings.webdav_user or "").strip()
                and (self.settings.webdav_password or "").strip()):
            self.drawer.set_auto_sync_status(
                "测试连接：请先填写 WebDAV 地址、账号与应用密码"
            )
            return
        self.storage.save_settings(self.settings)
        self.drawer.set_create_dir_available(False)
        worker = WebDavTestWorker(self.settings, self)
        worker.finished_result.connect(self._on_test_result)
        worker.failed.connect(
            lambda text: self.drawer.set_auto_sync_status("测试连接失败：" + text)
        )
        worker.start()
        self._webdav_test_worker = worker
        self.drawer.set_auto_sync_status("正在测试连接…")

    def _on_test_result(self, result: dict) -> None:
        self.drawer.set_auto_sync_status(str(result.get("text", "")))
        # 三步（读 + 建目录 + 写探测）任一失败且目录缺失时，就地出现「创建该目录」
        self.drawer.set_create_dir_available(bool(result.get("missing")))

    def webdav_create_dir(self) -> None:
        """按需创建同步目录（逐级 MKCOL ≤3 级），成功后复测一次。"""
        if (self._webdav_create_worker is not None
                and self._webdav_create_worker.isRunning()):
            return
        self.drawer.set_create_dir_available(False)
        worker = WebDavCreateDirWorker(self.settings, self)
        worker.finished_ok.connect(self.drawer.set_auto_sync_status)
        worker.failed.connect(
            lambda text: self.drawer.set_auto_sync_status("创建目录失败：" + text)
        )
        worker.start()
        self._webdav_create_worker = worker
        self.drawer.set_auto_sync_status("正在创建目录…")

    def _reload_after_restore(self) -> None:
        """恢复后重新加载全部数据。"""
        # 任务G：云端恢复带回的 covers/ 先做迁移/孤儿整理，并清空位图缓存
        self.storage.housekeep_covers_once(force=True)
        from app.ui.widgets import cover_cache_clear
        cover_cache_clear()
        self.settings = self.storage.load_settings()
        self._load_imported_fonts()
        self._load_backgrounds()
        if self._snippets_panel is not None:
            self._snippets_panel.reload()
        if self._snippets_visible:
            self._apply_snippets_width()
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
        visible = self.sidebar.isHidden()
        self.sidebar.setVisible(visible)
        self.buttons["toc"].setChecked(visible)
        self._update_side_toggle_arrow()
        self._apply_workspace_flush()

    # ------------------------------------------------------------------
    # 顶栏收起 / 展开
    # ------------------------------------------------------------------
    def toggle_topbar(self) -> None:
        self._set_topbar_collapsed(not bool(getattr(self.settings, "topbar_collapsed", False)))

    def _set_topbar_collapsed(self, collapsed: bool, save: bool = True) -> None:
        self.settings.topbar_collapsed = bool(collapsed)
        if save:
            self.storage.save_settings(self.settings)
        self._apply_topbar_state()

    def _apply_topbar_state(self) -> None:
        """按“当前页面 + 已保存状态”实时决定顶栏显隐与纸张是否贴顶。

        书架页始终保留顶栏（收起只针对写作页），回到写作页再按保存状态收起；
        状态持久化由 _set_topbar_collapsed 立即写盘，重开程序保持一致。
        """
        collapsed = (
            bool(getattr(self.settings, "topbar_collapsed", False))
            and self.stack.currentIndex() == 1
        )
        if collapsed:
            self.top_stack.setFixedHeight(0)
            self.top_stack.hide()
        else:
            self.top_stack.setFixedHeight(64)
            self.top_stack.show()
        self.editor_page.set_top_flush(collapsed)
        reveal = getattr(self, "top_reveal", None)
        if reveal is not None:
            reveal.setVisible(collapsed)
            if collapsed:
                self._position_top_reveal()
                reveal.raise_()
        self._apply_workspace_flush()

    def _position_top_reveal(self) -> None:
        reveal = getattr(self, "top_reveal", None)
        if reveal is None:
            return
        reveal.move(max(0, (self.root.width() - reveal.width()) // 2), 0)

    # ------------------------------------------------------------------
    # 左右栏收起：纸张贴边（不留底色条 / 圆角空档）
    # ------------------------------------------------------------------
    def _apply_workspace_flush(self) -> None:
        """按左右栏显隐把纸张对应一侧贴到窗口边缘。

        侧栏显隐是瞬时布局切换（无动画），因此不需要逐帧跟随；
        贴边只改外边距与圆角，正文宽度仍由编辑器视口决定，滚动位置按比例恢复。
        """
        writing = self.stack.currentIndex() == 1
        left_flush = writing and self.sidebar.isHidden()
        right_flush = writing and not self._snippets_visible

        spacer = getattr(self, "side_toggle_spacer", None)
        if spacer is not None:
            if left_flush:
                spacer.setFixedWidth(0)
                spacer.hide()
            else:
                spacer.setFixedWidth(16)
                spacer.show()

        self.editor_page.set_side_flush(left_flush, right_flush)

        bar = getattr(self, "side_toggle_bar", None)
        if bar is not None:
            button = self.side_toggle_button
            if bool(button.property("floating")) != left_flush:
                button.setProperty("floating", left_flush)
                button.style().unpolish(button)
                button.style().polish(button)
            self._position_side_toggle()

    def _position_side_toggle(self) -> None:
        bar = getattr(self, "side_toggle_bar", None)
        if bar is None:
            return
        x = 0 if self.sidebar.isHidden() else self.sidebar.width()
        bar.setGeometry(x, 0, 16, max(1, self.workspace.height()))
        bar.raise_()

    def toggle_drawer(self) -> None:
        self.drawer.toggle()

    # ==================================================================
    # 正文可编辑自检（“点不进 / 输不进”的兜底与取证）
    # ==================================================================
    def focusInEvent(self, event) -> None:  # noqa: N802
        super().focusInEvent(event)
        self._selfcheck_editor("focus_in")

    def changeEvent(self, event) -> None:  # noqa: N802
        if event.type() == QEvent.ActivationChange and self.isActiveWindow():
            self._selfcheck_editor("activate")
        elif event.type() == QEvent.WindowStateChange:
            from app.ui import motion
            if self.windowState() & Qt.WindowState.WindowMinimized:
                motion.hold_animations("minimized")
            else:
                motion.release_animations("minimized")
        super().changeEvent(event)

    def showEvent(self, event) -> None:  # noqa: N802
        """重新可见：解除动效挂起，从当前帧继续。"""
        from app.ui import motion
        motion.release_animations("hidden")
        super().showEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802
        """窗口 Hide：停掉动画定时器，避免后台空转。"""
        from app.ui import motion
        motion.hold_animations("hidden")
        super().hideEvent(event)

    def _refocus_editor_after_drawer(self) -> None:
        """设置关闭后焦点交还正文（不必再点一次才能输入）。"""
        if not self.current_chapter_id or self.stack.currentIndex() != 1:
            return
        if not self.editor_page.isVisible():
            return
        self.editor_page.unlock_editor()
        self.editor_page.focus_editor()

    def _clear_stuck_dim_overlay(self) -> list[str]:
        """设置已不可见、遮罩却还盖着主窗口：撤掉它并复位按钮勾选态。"""
        drawer = getattr(self, "drawer", None)
        overlay = getattr(drawer, "_overlay", None) if drawer is not None else None
        if overlay is None or not overlay.isVisible() or drawer.isVisible():
            return []
        drawer._is_open = False
        overlay.hide()
        for group in (getattr(self, "buttons", {}), getattr(self, "home_buttons", {})):
            btn = group.get("settings")
            if btn is not None:
                btn.setChecked(False)
        return ["dim_overlay_stale"]

    def _selfcheck_editor(self, reason: str) -> None:
        """轻量自检：正常状态下只读几个标志位，不做任何重活。"""
        started = time.perf_counter()
        fixed: list[str] = []
        page = getattr(self, "editor_page", None)
        if page is not None and self.current_chapter_id and page.isVisible():
            fixed += page.unlock_editor()
        fixed += self._clear_stuck_dim_overlay()
        self._selfcheck_cost_us = int((time.perf_counter() - started) * 1e6)
        if fixed:
            self._log_anomaly(
                f"editor_selfcheck[{reason}] fixed={fixed} cost={self._selfcheck_cost_us}us"
            )

    def _log_anomaly(self, line: str) -> None:
        """异常才写盘：数据目录 logs/editor_health.log（正常启动不产生噪音）。"""
        try:
            log_dir = Path(self.storage.base_dir) / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            with open(log_dir / "editor_health.log", "a", encoding="utf-8") as fp:
                fp.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {line}\n")
        except OSError:
            pass

    # ==================================================================
    # 文笔仿写片段栏（右侧，可拖拽宽度）
    # ==================================================================
    def toggle_snippets_panel(self, show: bool | None = None) -> None:
        target = (not self._snippets_visible) if show is None else bool(show)
        if target == self._snippets_visible:
            self.sidebar.btn_snippets.setChecked(target)
            return
        if target:
            panel = self._ensure_snippets_panel()
            self._snippets_visible = True
            self.sidebar.btn_snippets.setChecked(True)
            self._apply_snippets_width()
            panel.show()
            handle = self.editor_split.handle(1)
            if handle is not None:
                handle.show()
            panel.raise_()
            self._remember_snippets_open(True)
            self._apply_workspace_flush()
            return
        # 先记住拖拽宽度，再真正收起（收起后宽度会变 0）
        self._persist_snippets_width()
        self._snippets_visible = False
        self.sidebar.btn_snippets.setChecked(False)
        if self._snippets_panel is not None:
            self._snippets_panel.hide()
        handle = self.editor_split.handle(1)
        if handle is not None:
            handle.hide()
        self._remember_snippets_open(False)
        self._apply_workspace_flush()

    def _remember_snippets_open(self, opened: bool) -> None:
        """面板开合状态写回 Settings（与宽度字段一起，不新增重复键）。"""
        if bool(getattr(self.settings, "snippets_open", False)) == bool(opened):
            return
        self.settings.snippets_open = bool(opened)
        self.storage.save_settings(self.settings)

    def _snippets_max_width(self) -> int:
        """片段栏最大宽度：不超过窗口宽度的 45%。"""
        return max(220, int(self.width() * 0.45))

    def _apply_snippets_width(self) -> None:
        total = self.editor_split.width()
        if total <= 0:
            total = max(1, self.workspace.width() - self.sidebar.width() - 16)
        maxw = min(self._snippets_max_width(), max(220, total - 320))
        if self._snippets_panel is not None:
            self._snippets_panel.setMaximumWidth(self._snippets_max_width())
        want = int(getattr(self.settings, "snippets_width", 320) or 320)
        want = max(220, min(want, maxw))
        self.editor_split.setSizes([max(0, total - want), want])

    def _on_editor_split_moved(self, _pos: int, _index: int) -> None:
        if self._snippets_visible:
            self.snippets_width_timer.start()

    def _persist_snippets_width(self) -> None:
        self.snippets_width_timer.stop()
        if not self._snippets_visible:
            return
        if self._snippets_panel is None:
            return
        width = max(220, min(self._snippets_panel.width(), self._snippets_max_width()))
        if width != self.settings.snippets_width:
            self.settings.snippets_width = width
            self.storage.save_settings(self.settings)

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

    def new_chapter_here(self) -> None:
        """正文右键「新建章节」：建在当前章节所在卷，没有当前章就用第一卷。"""
        if self.book is None:
            return
        volume_id = ""
        if self.current_chapter_id:
            volume, _chapter = self.book.find_chapter(self.current_chapter_id)
            if volume is not None:
                volume_id = volume.id
        self.new_chapter(volume_id)

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
            self._snapshot_marks.pop(item_id, None)
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
                self._snapshot_marks.pop(chapter.id, None)
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
    # 目录拖动排序
    # ==================================================================
    ORDER_UNDO_LIMIT = 20

    def _order_snapshot(self) -> list[tuple[str, list[str]]]:
        if self.book is None:
            return []
        return [(vol.id, [ch.id for ch in vol.chapters]) for vol in self.book.volumes]

    def _sync_editor_text(self) -> None:
        """把编辑器里未落盘的正文同步进内存（不写盘），供重排一次保存。"""
        if self.book is None or not self.current_chapter_id:
            return
        if self._editor_chapter_id != self.current_chapter_id:
            return
        _volume, chapter = self.book.find_chapter(self.current_chapter_id)
        if chapter is None:
            return
        text = self.editor_page.current_text()
        if text == chapter.content:
            return
        chapter.content = text
        chapter.updated_at = time.time()
        self.counts[chapter.id] = count_chars(text, self.settings.exclude_punct)
        self._total_chars = self._other_chars + self.counts.get(self.current_chapter_id, 0)
        self.book.touch()

    def move_chapter(self, chapter_id: str, target_volume_id: str, target_index: int) -> None:
        """目录拖动落地：卷内 / 跨卷重排章节数组，立即落盘一次。"""
        if self.book is None:
            return
        src_volume, chapter = self.book.find_chapter(chapter_id)
        if chapter is None or src_volume is None:
            return
        dst_volume = self.book.find_volume(target_volume_id)
        if dst_volume is None:
            return
        target_index = max(0, min(int(target_index), len(dst_volume.chapters)))
        old_index = src_volume.chapters.index(chapter)
        if src_volume is dst_volume and target_index in (old_index, old_index + 1):
            return   # 原地不动，不产生撤销步，也不写盘
        self._sync_editor_text()
        self._chapter_order_undo.append(self._order_snapshot())
        if len(self._chapter_order_undo) > self.ORDER_UNDO_LIMIT:
            self._chapter_order_undo.pop(0)
        src_volume.chapters.pop(old_index)
        if src_volume is dst_volume and target_index > old_index:
            target_index -= 1
        dst_volume.chapters.insert(target_index, chapter)
        self.book.touch()
        self.storage.save_book(self.book, self._total_chars)
        self._refresh_after_reorder()

    def undo_chapter_order(self) -> None:
        """目录树焦点下的 Ctrl+Z：还原上一次拖动排序（一次拖动 = 一步撤销）。"""
        if self.book is None or not self._chapter_order_undo:
            return
        snapshot = self._chapter_order_undo.pop()
        volumes = {vol.id: vol for vol in self.book.volumes}
        chapters = {ch.id: ch for ch in self.book.all_chapters()}
        snapshot_ids = {cid for _vid, ids in snapshot for cid in ids}
        for volume_id, chapter_ids in snapshot:
            volume = volumes.get(volume_id)
            if volume is None:
                continue
            restored = [chapters[cid] for cid in chapter_ids if cid in chapters]
            # 快照之后新增的章节不丢，追加在卷尾；快照里属于别的卷的章节不重复保留
            extra = [ch for ch in volume.chapters if ch.id not in snapshot_ids]
            volume.chapters = restored + extra
        self.book.touch()
        self.storage.save_book(self.book, self._total_chars)
        self._refresh_after_reorder()

    def _refresh_after_reorder(self) -> None:
        """重排或撤销后刷新目录与卷归属，当前章保持打开、正文与光标不动。"""
        if self.book is None:
            return
        volume = self.book.volume_of(self.current_chapter_id)
        self.current_volume_id = volume.id if volume is not None else ""
        self.sidebar.set_book(self.book, self.counts, self.current_chapter_id)
        if self.current_chapter_id:
            self.sidebar.select_chapter(self.current_chapter_id)
        self.sidebar.set_saved_time(time.strftime("%H:%M"))

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
        # 账号 / 应用密码 / 同步目录 / WebDAV 地址变更：清空同步状态，首次强制上传
        self._reset_sync_state_on_target_change()
        # 自动同步开关 / 档位 / 凭据变化后，按新设置重启定时器
        self._restart_auto_sync_timer()
        # 深浅色 / 颜色自定义需要整体刷新；其余只需要局部应用
        self.apply_theme()

    # ==================================================================
    # 工具
    # ==================================================================
    def _confirm(self, title: str, text: str) -> bool:
        return MessageBox.question(self, title, text)

    def _apply_topbar_compact(self, width: int) -> None:
        """窄窗口下顶栏按钮收窄（58→48），避免 2 字标签挤压与按钮溢出。"""
        compact = width < 1250
        if self._topbar_compact == compact:
            return
        self._topbar_compact = compact
        for button in getattr(self, "buttons", {}).values():
            button.set_compact(compact)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._apply_topbar_compact(self.width())
        self.drawer.relayout()
        reveal = getattr(self, "top_reveal", None)
        if reveal is not None and reveal.isVisible():
            self._position_top_reveal()
        if self._snippets_visible and self._snippets_panel is not None:
            maxw = self._snippets_max_width()
            self._snippets_panel.setMaximumWidth(maxw)
            sizes = self.editor_split.sizes()
            if len(sizes) == 2 and sizes[1] > maxw:
                total = sum(sizes)
                self.editor_split.setSizes([max(0, total - maxw), maxw])
        self._position_side_toggle()

    def closeEvent(self, event) -> None:
        # 第二次（浮层流程结束后）才真正关闭
        if self._close_from_flow:
            self._settle_workers(timeout_ms=200)
            super().closeEvent(event)
            return
        # 首次关闭：拦下事件，先显示保存浮层，落盘结果确认后再关
        event.ignore()
        if self._close_flow_active:
            return
        if self._exit_overlay is not None:
            self._exit_retry()   # 失败浮层还在：再次点关闭等同于重试
            return
        self._begin_exit_flow()

    # ------------------------------------------------------------------
    # 退出保存浮层
    # ------------------------------------------------------------------
    def _begin_exit_flow(self) -> None:
        self._close_flow_active = True
        self.auto_sync_timer.stop()
        self.autosave_timer.stop()
        self.words_timer.stop()
        self._exit_started = time.monotonic()
        overlay = ExitSaveOverlay(self)
        overlay.retry_requested.connect(self._exit_retry)
        overlay.cancel_requested.connect(self._exit_cancel)
        self._exit_overlay = overlay
        overlay.show_saving()
        # 先让圆环画出来，再在下一轮事件循环做同步落盘：动画真的会动，
        # 且最终状态完全来自 flush 的返回值（不起线程、不冻结事件循环）。
        QTimer.singleShot(0, self._exit_do_flush)

    def _exit_do_flush(self) -> None:
        ok, reason = self._flush_all_before_close()
        elapsed_ms = (time.monotonic() - getattr(self, "_exit_started", time.monotonic())) * 1000
        # 最短显示 0.5 秒：保存极快时也要把旋转这段走完，避免一闪而过
        remaining = max(0, int(500 - elapsed_ms))
        if ok:
            QTimer.singleShot(remaining, self._exit_show_success)
        else:
            QTimer.singleShot(remaining, lambda r=reason: self._exit_show_error(r))

    def _flush_all_before_close(self) -> tuple[bool, str]:
        """复用原 closeEvent 的落盘顺序；返回 (是否主稿写入成功, 失败原因)。"""
        ok = True
        reason = ""
        try:
            self._last_save_error = ""
            ok = self.save_current_book()
            if not self.flush():
                ok = False
            self.words_timer.stop()
            self._flush_words()
            if self._snippets_panel is not None:
                self._snippets_panel.flush_pending()
            # 独立窗口 / 面板里可能还有防抖未触发的改动（时间轴、大纲、设定、
            # 地图、角色关系），逐个 flush，避免强关窗口时丢最后一批编辑。
            for dialog in list(self._timeline_dialogs):
                dialog.flush_pending()
            for dialog in list(self._outline_dialogs):
                dialog.flush_pending()
            for dialog in list(self._map_dialogs.values()):
                dialog.flush_pending()
            for window in list(self._note_windows.values()):
                window.flush_pending()
            panel = self.shelf.characters_view()
            if panel is not None:
                panel.flush_pending()
            self.storage.save_settings(self.settings)
        except Exception as exc:   # noqa: BLE001 - 设置/统计等次要写入失败也要让用户看到
            ok = False
            reason = reason or str(exc) or exc.__class__.__name__
        if not ok:
            reason = reason or self._last_save_error or "写入失败"
        return ok, reason

    def _exit_show_success(self) -> None:
        overlay = self._exit_overlay
        if overlay is None:
            return
        overlay.show_success()
        # 绿勾停留 0.5 秒，再淡出 220ms，才真正关窗
        QTimer.singleShot(500, self._exit_fade)

    def _exit_fade(self) -> None:
        overlay = self._exit_overlay
        if overlay is None:
            return
        overlay.fade_out(self._exit_finish)

    def _exit_finish(self) -> None:
        overlay = self._exit_overlay
        self._exit_overlay = None
        if overlay is not None:
            overlay.hide_overlay()
            overlay.deleteLater()
        self._close_flow_active = False
        self._close_from_flow = True
        self.close()

    def _exit_show_error(self, reason: str) -> None:
        overlay = self._exit_overlay
        if overlay is None:
            return
        overlay.show_error(reason)
        self._close_flow_active = False   # 等用户决定：重试 / 取消退出

    def _exit_retry(self) -> None:
        overlay = self._exit_overlay
        if overlay is None or self._close_flow_active:
            return
        self._close_flow_active = True
        self._exit_started = time.monotonic()
        overlay.show_saving()
        QTimer.singleShot(0, self._exit_do_flush)

    def _exit_cancel(self) -> None:
        overlay = self._exit_overlay
        self._exit_overlay = None
        self._close_flow_active = False
        if overlay is not None:
            overlay.hide_overlay()
            overlay.deleteLater()
        # 用户选择不退出：恢复自动保存与同步定时器，内容仍在编辑器里
        self._restart_auto_sync_timer()
        if self._dirty:
            self.autosave_timer.start()

    def _settle_workers(self, timeout_ms: int = 400) -> None:
        """关窗前给后台同步线程一个有限的收尾时间，避免无限等待卡住退出。"""
        workers = (
            self._webdav_worker,
            self._webdav_test_worker,
            self._webdav_create_worker,
        )
        for worker in workers:
            if worker is None or not worker.isRunning():
                continue
            worker.requestInterruption()
            worker.wait(timeout_ms)
            if worker.isRunning():
                # 超时仍未结束：脱离主窗口，避免窗口销毁时连带析构运行中的 QThread
                worker.setParent(None)
                _ORPHAN_WORKERS.append(worker)

