# -*- coding: utf-8 -*-
"""正文编辑区：章节标题、编辑器、状态栏、悬浮快捷控件。"""
from __future__ import annotations

from PySide6.QtCore import QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QPalette,
    QPixmap,
    QTextBlockFormat,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
)
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.models import DEFAULT_FONT_FAMILY, Settings
from app.services.search import find_all
from app.services.word_count import count_chars
from app.ui.icons import IconFactory
from app.ui.widgets import ToggleSwitch, apply_shadow


class PaperFrame(QFrame):
    """编辑区“纸张”：支持纯色（QSS）或自定义背景图片。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._pixmap: QPixmap | None = None

    def set_bg_image(self, path: str) -> None:
        if path:
            pix = QPixmap(path)
            self._pixmap = None if pix.isNull() else pix
        else:
            self._pixmap = None
        self.setAttribute(Qt.WA_StyledBackground, self._pixmap is None)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        if self._pixmap is None:
            super().paintEvent(event)
            return
        from PySide6.QtGui import QPainter

        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        scaled = self._pixmap.scaled(
            self.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation
        )
        x = (scaled.width() - self.width()) // 2
        y = (scaled.height() - self.height()) // 2
        painter.drawPixmap(self.rect(), scaled, QRect(x, y, self.width(), self.height()))
        painter.end()


class NovelEditor(QTextEdit):
    """纯文本编辑器：关闭富文本，支持成对标点自动补全。"""

    # 左符号 -> 右符号（成对标点）
    PAIRS = {
        "“": "”",
        "‘": "’",
        "（": "）",
        "《": "》",
        "「": "」",
        "『": "』",
        "【": "】",
        "(": ")",
        "[": "]",
        "{": "}",
    }

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("NovelEditor")
        self.auto_pair = True
        self._paste_insert = False   # 正在执行粘贴/拖放插入（这些字不计入打字统计）
        self.setAcceptRichText(False)
        self.setLineWrapMode(QTextEdit.WidgetWidth)
        self.setFrameShape(QFrame.NoFrame)
        # 写作页用鼠标滚轮上下滑动；不显示滚动条滑块
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setCursorWidth(2)
        self.setTabChangesFocus(False)
        self.setPlaceholderText("从这里开始你的故事……")
        self.document().setDocumentMargin(6)

    def insertFromMimeData(self, source) -> None:  # noqa: N802
        """粘贴 / 拖放插入：标记后不计入“键盘打字”统计。"""
        self._paste_insert = True
        try:
            super().insertFromMimeData(source)
        finally:
            self._paste_insert = False

    # ------------------------------------------------------------------
    def _char_after(self, cursor: QTextCursor) -> str:
        probe = QTextCursor(cursor)
        probe.movePosition(QTextCursor.NextCharacter, QTextCursor.KeepAnchor)
        return probe.selectedText()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        # 注意：Qt6 的修饰键是枚举对象，不能直接和整数 0 比较
        blocked = event.modifiers() & (Qt.ControlModifier | Qt.AltModifier)
        if self.auto_pair and blocked == Qt.NoModifier:
            text = event.text()
            if len(text) == 1:
                cursor = self.textCursor()

                # 1) 选中文字时输入左引号 / 左括号 -> 自动包裹
                if cursor.hasSelection() and text in self.PAIRS:
                    selected = cursor.selectedText().replace("\u2029", "\n")
                    cursor.insertText(text + selected + self.PAIRS[text])
                    self.setTextCursor(cursor)
                    event.accept()
                    return

                # 2) 已经自动补全了右半边时，再输入右半边直接跳过
                if not cursor.hasSelection() and text in self.PAIRS.values():
                    if self._char_after(cursor) == text:
                        cursor.movePosition(QTextCursor.NextCharacter)
                        self.setTextCursor(cursor)
                        event.accept()
                        return

                # 3) 输入左半边时自动补上右半边，光标停在中间
                if not cursor.hasSelection() and text in self.PAIRS:
                    cursor.insertText(text + self.PAIRS[text])
                    cursor.movePosition(QTextCursor.PreviousCharacter)
                    self.setTextCursor(cursor)
                    event.accept()
                    return

        # 4) 退格时，如果删除的是成对符号，则一起删掉
        if self.auto_pair and event.key() == Qt.Key_Backspace:
            cursor = self.textCursor()
            if not cursor.hasSelection() and cursor.position() > 0:
                pos = cursor.position()
                prev = QTextCursor(cursor)
                prev.movePosition(QTextCursor.PreviousCharacter, QTextCursor.KeepAnchor)
                prev_ch = prev.selectedText()
                if prev_ch in self.PAIRS and self._char_after(cursor) == self.PAIRS[prev_ch]:
                    sel = QTextCursor(cursor)
                    sel.setPosition(pos - 1)
                    sel.setPosition(pos + 1, QTextCursor.KeepAnchor)
                    sel.removeSelectedText()
                    self.setTextCursor(sel)
                    event.accept()
                    return

        super().keyPressEvent(event)


class SpeedPanel(QFrame):
    """右侧常驻的码字时速面板（可隐藏，样式参考用户提供的图二）。

    数值右对齐、深灰色；单位可在 字/分 与 字/时 之间切换并持久化。
    """

    hide_requested = Signal()
    unit_changed = Signal(str)  # "per_min" / "per_hour"

    UNIT_LABELS = {"per_min": "字/分", "per_hour": "字/时"}

    def __init__(self, settings: Settings, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("SidePanel")
        self.setFixedWidth(212)
        self._settings = settings
        self._tracker = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(8)

        # 标题行：标题 + 单位切换 + 刷新 + 隐藏
        header = QHBoxLayout()
        header.setSpacing(4)
        title = QLabel("码字时速", self)
        title.setObjectName("SidePanelTitle")
        header.addWidget(title)
        header.addStretch(1)
        # 单位拨动开关：胶囊底槽 + 滑动圆钮，字/分 ↔ 字/时（带平滑滑动动画）
        unit_widget = QWidget(self)
        unit_row = QHBoxLayout(unit_widget)
        unit_row.setContentsMargins(0, 0, 0, 0)
        unit_row.setSpacing(5)
        self.unit_min_label = QToolButton(unit_widget)
        self.unit_min_label.setObjectName("UnitLabel")
        self.unit_min_label.setText("字/分")
        self.unit_min_label.setCursor(Qt.PointingHandCursor)
        self.unit_min_label.setToolTip("按每分钟多少字显示码字速度")
        self.unit_min_label.clicked.connect(lambda: self._set_unit("per_min"))
        unit_row.addWidget(self.unit_min_label)
        # checked = 字/时，unchecked = 字/分
        self.unit_toggle = ToggleSwitch(self._speed_unit == "per_hour", unit_widget)
        self.unit_toggle.setToolTip("拨动切换单位：字/分 ↔ 字/时")
        self.unit_toggle.toggled.connect(
            lambda checked: self._set_unit("per_hour" if checked else "per_min")
        )
        unit_row.addWidget(self.unit_toggle)
        self.unit_hour_label = QToolButton(unit_widget)
        self.unit_hour_label.setObjectName("UnitLabel")
        self.unit_hour_label.setText("字/时")
        self.unit_hour_label.setCursor(Qt.PointingHandCursor)
        self.unit_hour_label.setToolTip("按每小时多少字显示码字速度")
        self.unit_hour_label.clicked.connect(lambda: self._set_unit("per_hour"))
        unit_row.addWidget(self.unit_hour_label)
        header.addWidget(unit_widget)
        refresh_button = QToolButton(self)
        refresh_button.setObjectName("PanelToolButton")
        refresh_button.setText("⟳")
        refresh_button.setCursor(Qt.PointingHandCursor)
        refresh_button.setToolTip("立即刷新")
        refresh_button.clicked.connect(self.refresh)
        header.addWidget(refresh_button)
        hide_button = QToolButton(self)
        hide_button.setObjectName("DrawerClose")
        hide_button.setText("»")
        hide_button.setCursor(Qt.PointingHandCursor)
        hide_button.setToolTip("隐藏时速面板")
        hide_button.clicked.connect(self.hide_requested.emit)
        header.addWidget(hide_button)
        layout.addLayout(header)

        # 数据块整体垂直居中：上下各留弹性间距
        layout.addStretch(2)

        # 状态行（未开始 / 计时中 / 已暂停）
        self.status_label = QLabel("未开始", self)
        self.status_label.setObjectName("SidePanelStatus")
        self.status_label.setAlignment(Qt.AlignHCenter)
        layout.addWidget(self.status_label)
        layout.addSpacing(6)

        # 四个数据行：左标签右数值
        self.session_value = self._add_row(layout, "本次字数")
        self.speed_value = self._add_row(layout, "码字速度")
        self.time_value = self._add_row(layout, "码字时长")
        self.idle_value = self._add_row(layout, "空闲时长")

        layout.addStretch(3)

        hint = QLabel("停写 30 秒后开始记录空闲；打字即停，不清零。", self)
        hint.setObjectName("SidePanelKey")
        hint.setWordWrap(True)
        hint.setAlignment(Qt.AlignHCenter)
        layout.addWidget(hint)

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self.refresh)

        # 打字时立即刷新（轻防抖），不必等 1 秒定时器
        self._activity_timer = QTimer(self)
        self._activity_timer.setSingleShot(True)
        self._activity_timer.setInterval(200)
        self._activity_timer.timeout.connect(self.refresh)

        # 速度数值平滑过渡动画（定时器步进插值，避免数值突兀跳变）
        self._shown_speed = 0.0            # 当前显示的时速（字/时）
        self._anim_start = 0.0
        self._anim_target = 0.0
        self._anim_tick = 0
        self._anim_ticks = 0
        self._speed_anim = QTimer(self)
        self._speed_anim.setInterval(33)   # ~30fps
        self._speed_anim.timeout.connect(self._step_speed_anim)

        self._sync_unit_button()
        self.refresh()

    # ------------------------------------------------------------------
    def _add_row(self, layout: QVBoxLayout, key: str) -> QLabel:
        row = QFrame(self)
        row.setObjectName("SidePanelRow")
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(2, 0, 2, 0)
        label = QLabel(key, row)
        label.setObjectName("SidePanelKey")
        value = QLabel("—", row)
        value.setObjectName("SidePanelValue")
        value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row_layout.addWidget(label)
        row_layout.addStretch(1)
        row_layout.addWidget(value)
        layout.addWidget(row)
        return value

    def _sync_unit_button(self) -> None:
        per_hour = self._speed_unit == "per_hour"
        # checked = 字/时；两侧文字标签高亮当前单位
        self.unit_toggle.setChecked(per_hour)
        self.unit_min_label.setProperty("active", not per_hour)
        self.unit_hour_label.setProperty("active", per_hour)
        for w in (self.unit_min_label, self.unit_hour_label):
            w.style().unpolish(w)
            w.style().polish(w)

    @property
    def _speed_unit(self) -> str:
        unit = getattr(self._settings, "speed_unit", "per_min")
        return unit if unit in self.UNIT_LABELS else "per_min"

    def _set_unit(self, new_unit: str) -> None:
        if new_unit not in self.UNIT_LABELS or new_unit == self._speed_unit:
            self._sync_unit_button()   # 保持选中态与数据一致
            return
        self._settings.speed_unit = new_unit
        self._sync_unit_button()
        self.refresh()
        self.unit_changed.emit(new_unit)

    def _speed_text(self, per_hour: float) -> str:
        if per_hour <= 0:
            return "0 " + self.UNIT_LABELS[self._speed_unit]
        if self._speed_unit == "per_min":
            return f"{int(per_hour / 60)} 字/分"
        return f"{int(per_hour)} 字/时"

    # ------------------------------------------------------------------
    def notify_activity(self) -> None:
        """编辑器有输入时尽快刷新（轻防抖）。"""
        self._activity_timer.start()

    def _animate_speed_to(self, target: float) -> None:
        """速度数值平滑衔接：变化小则一点点慢慢加，变化大则依次经过中间数。"""
        target = max(0.0, float(target))
        if not self.isVisible() or abs(target - self._shown_speed) < 0.51:
            self._speed_anim.stop()
            self._shown_speed = target
            self.speed_value.setText(self._speed_text(target))
            return
        self._anim_start = self._shown_speed
        self._anim_target = target
        self._anim_tick = 0
        self._anim_ticks = 20          # ~660ms 缓出过渡
        if not self._speed_anim.isActive():
            self._speed_anim.start()

    def _step_speed_anim(self) -> None:
        self._anim_tick += 1
        t = self._anim_tick / self._anim_ticks
        if t >= 1.0:
            self._speed_anim.stop()
            self._shown_speed = self._anim_target
            self.speed_value.setText(self._speed_text(self._anim_target))
            return
        eased = 1.0 - (1.0 - t) ** 2   # 缓出：先快后慢，观感更自然
        self._shown_speed = self._anim_start + (self._anim_target - self._anim_start) * eased
        self.speed_value.setText(self._speed_text(self._shown_speed))

    # ------------------------------------------------------------------
    def set_tracker(self, tracker) -> None:
        self._tracker = tracker
        self.refresh()

    def refresh(self) -> None:
        if self._tracker is None:
            self.status_label.setText("未开始")
            for label in (self.session_value, self.time_value, self.idle_value):
                label.setText("—")
            self._animate_speed_to(0.0)
            return
        tracker = self._tracker
        self.status_label.setText(getattr(tracker, "status", "未开始"))
        self.session_value.setText(f"{tracker.total_chars} 字")
        self.time_value.setText(tracker.elapsed_text)
        self._animate_speed_to(float(tracker.speed_per_hour))
        self.idle_value.setText(getattr(tracker, "idle_text", "00:00:00"))

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._timer.start()
        self.refresh()

    def hideEvent(self, event) -> None:  # noqa: N802
        # 隐藏时停掉所有定时器，避免不可见时的无效刷新
        self._timer.stop()
        self._activity_timer.stop()
        self._speed_anim.stop()
        super().hideEvent(event)


class EditorPage(QWidget):
    """完整的正文页面。"""

    content_edited = Signal()
    chapter_chars_changed = Signal(int)
    speed_delta = Signal(int)
    typed_delta = Signal(int)          # 仅键盘输入的新增字数（粘贴不计）
    speed_panel_toggled = Signal(bool)  # 右侧时速面板显隐变化

    def __init__(self, settings: Settings, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self._loading = False
        self._last_count = 0
        self._total_chars = 0
        self._applied_font: tuple[str, str, float] | None = None
        self._applied_spacing: float | None = None

        self._build_ui()
        self._connect()
        self.apply_settings(settings)

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        body = QWidget(self)
        body.setObjectName("EditorBody")
        root.addWidget(body, 1)
        outer = QVBoxLayout(body)
        # 与窗口边界 / 目录栏 / 顶部工具栏的间隔约 3 毫米
        # （96dpi 下 1px≈0.26mm；左侧正文到边界 = 6 + 0 + 文档边距 6 = 12px ≈ 3.1mm）
        outer.setContentsMargins(6, 10, 6, 8)
        outer.setSpacing(6)

        # 中间是“悬浮白纸”：章节标题融合在纸张内部顶部，铺满写作区（仅留 3mm 边距）
        paper = PaperFrame(self)
        paper.setObjectName("EditorPaper")
        paper.setMinimumWidth(320)
        paper.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        apply_shadow(paper, blur=36, alpha=36, dy=8)
        paper_layout = QVBoxLayout(paper)
        # 内部左右不再留白（外层 6px + 文档边距 6px ≈ 3mm 已够），上下 30px；
        # 标题与正文之间留 30px 间距
        paper_layout.setContentsMargins(0, 30, 0, 30)
        paper_layout.setSpacing(30)

        self.title_label = QLabel("未选择章节", self)
        self.title_label.setObjectName("EditorTitle")
        self.title_label.setAlignment(Qt.AlignCenter)
        self.title_label.setMinimumHeight(34)
        paper_layout.addWidget(self.title_label)

        self.editor = NovelEditor(paper)
        paper_layout.addWidget(self.editor, 1)

        outer.addWidget(paper, 1)
        self.paper = paper

        # 状态栏：字数统计在最左下角，其后是光标位置，保存提示靠右
        status_row = QHBoxLayout()
        status_row.setContentsMargins(10, 2, 10, 0)
        status_row.setSpacing(10)
        self.count_label = QLabel("本章 0 字 · 全书 0 字", self)
        self.count_label.setObjectName("EditorStatus")
        self.cursor_label = QLabel("第 1 行 · 第 1 列", self)
        self.cursor_label.setObjectName("EditorStatus")
        self.save_label = QLabel("", self)
        self.save_label.setObjectName("SaveHint")
        status_row.addWidget(self.count_label)
        status_row.addWidget(self.cursor_label)
        status_row.addStretch(1)
        status_row.addWidget(self.save_label)
        outer.addLayout(status_row)

        # 右侧常驻工具栏（码字时速），默认隐藏，点击顶部“时速”按钮呼出
        self.speed_panel = SpeedPanel(self.settings, self)
        self.speed_panel.hide_requested.connect(lambda: self.toggle_speed_panel(False))
        self.speed_panel.hide()
        root.addWidget(self.speed_panel)

        # 悬浮的“置顶 / 置底”按钮
        self.jump_top = QToolButton(self)
        self.jump_top.setObjectName("FloatButton")
        self.jump_top.setFixedSize(36, 36)
        self.jump_top.setIconSize(QSize(16, 16))
        self.jump_top.setToolTip("回到开头")
        self.jump_top.setCursor(Qt.PointingHandCursor)
        self.jump_top.clicked.connect(self.jump_to_top)

        self.jump_bottom = QToolButton(self)
        self.jump_bottom.setObjectName("FloatButton")
        self.jump_bottom.setFixedSize(36, 36)
        self.jump_bottom.setIconSize(QSize(16, 16))
        self.jump_bottom.setToolTip("跳到末尾")
        self.jump_bottom.setCursor(Qt.PointingHandCursor)
        self.jump_bottom.clicked.connect(self.jump_to_bottom)

        # 隐形快速滚动滑块（“=”形手柄）：平时隐藏，滚动滚轮时显示 3 秒，拖动可快速滚动
        from app.ui.scroll_float import ScrollFlasher

        self._scroll_flasher = ScrollFlasher(self.editor, Qt.Vertical, inset=12)
        self.scroll_handle = self._scroll_flasher.handle

    def _connect(self) -> None:
        self.editor.textChanged.connect(self._on_text_changed)
        self.editor.cursorPositionChanged.connect(self._update_cursor_label)
        # 滚轮监听由 ScrollFlasher 自行挂在编辑器视口上

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------
    def load_text(self, title: str, text: str) -> None:
        """载入章节内容（会清空撤销栈）。"""
        self._loading = True
        try:
            self.title_label.setText(title)
            self.editor.setExtraSelections([])
            self.editor.setPlainText(text)
            self._applied_font = None
            self._applied_spacing = None
            self._apply_typography()

            cursor = self.editor.textCursor()
            if self.settings.open_at_end:
                cursor.movePosition(QTextCursor.End)
            else:
                cursor.movePosition(QTextCursor.Start)
            self.editor.setTextCursor(cursor)
            self.editor.ensureCursorVisible()
            self.editor.document().setModified(False)
        finally:
            self._loading = False

        self._last_count = count_chars(text, self.settings.exclude_punct)
        self._update_status()
        self.chapter_chars_changed.emit(self._last_count)

    def clear_page(self) -> None:
        """没有打开任何书籍时显示的空状态。"""
        self._loading = True
        self.title_label.setText("未选择章节")
        self.editor.setPlainText("")
        self._loading = False
        self._last_count = 0
        self._total_chars = 0
        self._update_status()
        self.editor.setDisabled(True)
        self.jump_top.setVisible(False)
        self.jump_bottom.setVisible(False)
        self._scroll_flasher.reset()

    def current_text(self) -> str:
        return self.editor.toPlainText()

    def set_title(self, title: str) -> None:
        self.title_label.setText(title)

    def set_total_chars(self, total: int) -> None:
        self._total_chars = max(0, total)
        self._update_status()

    def set_saved_text(self, minute: str = "") -> None:
        self.save_label.setText(f"已自动保存 {minute}" if minute else "")

    def focus_editor(self) -> None:
        self.editor.setFocus()

    def set_speed_tracker(self, tracker) -> None:
        """注入码字时速统计器（右侧常驻面板使用）。"""
        self.speed_panel.set_tracker(tracker)

    def toggle_speed_panel(self, visible: bool | None = None) -> None:
        """显示 / 隐藏右侧常驻的时速工具栏。"""
        if visible is None:
            visible = self.speed_panel.isHidden()
        self.speed_panel.setVisible(visible)
        self._position_floating()
        self.speed_panel_toggled.emit(bool(visible))

    def speed_panel_visible(self) -> bool:
        return self.speed_panel.isVisible()

    def current_chapter_chars(self) -> int:
        return self._last_count

    # ------------------------------------------------------------------
    # 设置
    # ------------------------------------------------------------------
    def apply_settings(self, settings: Settings) -> None:
        self.settings = settings
        self.editor.auto_pair = settings.auto_pair
        self.jump_top.setVisible(settings.float_jump)
        self.jump_bottom.setVisible(settings.float_jump)

        self._apply_typography()

        # 切换“不计标点”后重新计算，避免把差值算成新增字数
        text = self.editor.toPlainText()
        new_count = count_chars(text, settings.exclude_punct) if text else 0
        self._last_count = new_count
        self._update_status()
        self.chapter_chars_changed.emit(new_count)

        self._position_floating()

    def _apply_typography(self) -> None:
        family = self.settings.font_family or DEFAULT_FONT_FAMILY
        font_key = (
            family,
            self.settings.font_latin,
            round(float(self.settings.font_size), 2),
            bool(self.settings.font_bold),
            bool(self.settings.font_italic),
            bool(self.settings.font_underline),
        )
        if font_key != self._applied_font:
            font = QFont()
            font.setFamilies([self.settings.font_latin, family])
            font.setPointSizeF(float(self.settings.font_size))
            font.setStyleHint(QFont.SansSerif)
            font.setBold(bool(self.settings.font_bold))
            font.setItalic(bool(self.settings.font_italic))
            font.setUnderline(bool(self.settings.font_underline))
            self.editor.setFont(font)
            self.editor.document().setDefaultFont(font)
            # setPlainText 会把控件当时的字体“烘焙”成显式字符格式，覆盖用户设置；
            # 这里把用户字体合并到全文，保证排版/载入后字体始终跟随设置
            cursor = QTextCursor(self.editor.document())
            cursor.select(QTextCursor.Document)
            char_format = QTextCharFormat()
            char_format.setFont(font)
            cursor.mergeCharFormat(char_format)
            self.editor.mergeCurrentCharFormat(char_format)
            self._applied_font = font_key
            # 立即重绘视口，字体变化马上可见
            self.editor.viewport().update()

        spacing_key = round(float(self.settings.line_spacing), 2)
        if spacing_key != self._applied_spacing:
            self._apply_line_spacing()
            self._applied_spacing = spacing_key

    def apply_writing_background(self) -> None:
        """根据设置应用写作区背景：预设色块走 QSS，自定义图片走自绘。"""
        image = ""
        if self.settings.writing_theme == "custom" and self.settings.writing_image:
            image = self.settings.writing_image
        self.paper.set_bg_image(image)

    def _apply_line_spacing(self) -> None:
        """给全文所有段落设置行高（按倍数）。"""
        document = self.editor.document()
        bar = self.editor.verticalScrollBar()
        old_value = bar.value()

        was_loading = self._loading
        self._loading = True
        try:
            # 临时关闭撤销记录，避免“改行距”占用用户的一次撤销
            document.setUndoRedoEnabled(False)
            block_format = QTextBlockFormat()
            block_format.setLineHeight(
                float(self.settings.line_spacing) * 100.0,
                QTextBlockFormat.LineHeightTypes.ProportionalHeight.value,
            )
            cursor = QTextCursor(document)
            cursor.select(QTextCursor.Document)
            cursor.mergeBlockFormat(block_format)
            document.setUndoRedoEnabled(True)
        finally:
            self._loading = was_loading

        bar.setValue(min(old_value, bar.maximum()))

    # ------------------------------------------------------------------
    # 内部事件
    # ------------------------------------------------------------------
    def _on_text_changed(self) -> None:
        if self._loading:
            return
        # 文本变化后旧的查找高亮已经失效，直接清除
        if self.editor.extraSelections():
            self.editor.setExtraSelections([])

        text = self.editor.toPlainText()
        count = count_chars(text, self.settings.exclude_punct)
        delta = count - self._last_count
        self._last_count = count

        if delta > 0:
            # 只有键盘敲出来的字才计入码字速度与每日打字统计；粘贴 / 拖放不计
            if not getattr(self.editor, "_paste_insert", False):
                self.speed_delta.emit(delta)
                self.typed_delta.emit(delta)
                # 打字后尽快刷新时速面板（面板可见时），不必等 1 秒定时器
                if self.speed_panel.isVisible():
                    self.speed_panel.notify_activity()

        self.chapter_chars_changed.emit(count)
        self._update_status()
        self.content_edited.emit()

    def _update_cursor_label(self) -> None:
        cursor = self.editor.textCursor()
        line = cursor.blockNumber() + 1
        column = cursor.positionInBlock() + 1
        self.cursor_label.setText(f"第 {line} 行 · 第 {column} 列")

    def _update_status(self) -> None:
        self.count_label.setText(f"本章 {self._last_count} 字 · 全书 {self._total_chars} 字")
        self._update_cursor_label()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._position_floating()

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        return super().eventFilter(obj, event)

    # ------------------------------------------------------------------
    # 隐形快速滚动滑块（实现见 app/ui/scroll_float.py 的 ScrollFlasher）
    # ------------------------------------------------------------------
    def flash_scroll_handle(self) -> None:
        """滚轮滚动时显示滑块并同步到当前滚动位置，3 秒后自动隐藏。"""
        self._scroll_flasher.flash()

    def _position_floating(self) -> None:
        width = self.width()
        height = self.height()
        # 右侧时速面板可见时，悬浮控件向左避让
        offset = self.speed_panel.width() + 14 if self.speed_panel.isVisible() else 0
        self.jump_bottom.move(width - offset - 58, height - 72)
        self.jump_top.move(width - offset - 58, height - 116)
        self.jump_bottom.raise_()
        self.jump_top.raise_()
        if self.scroll_handle.isVisible():
            self.flash_scroll_handle()   # 窗口尺寸变化时保持滑块在正确位置

    # ------------------------------------------------------------------
    # 编辑操作
    # ------------------------------------------------------------------
    def undo(self) -> None:
        self.editor.undo()

    def redo(self) -> None:
        self.editor.redo()

    def jump_to_top(self) -> None:
        cursor = self.editor.textCursor()
        cursor.movePosition(QTextCursor.Start)
        self.editor.setTextCursor(cursor)
        self.editor.ensureCursorVisible()
        self.focus_editor()

    def jump_to_bottom(self) -> None:
        cursor = self.editor.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.editor.setTextCursor(cursor)
        self.editor.ensureCursorVisible()
        self.focus_editor()

    def select_range(self, start: int, length: int) -> None:
        document = self.editor.document()
        max_pos = max(0, document.characterCount() - 1)
        begin = max(0, min(start, max_pos))
        end = max(begin, min(begin + max(1, length), max_pos))
        cursor = QTextCursor(document)
        cursor.setPosition(begin)
        cursor.setPosition(end, QTextCursor.KeepAnchor)
        self.editor.setTextCursor(cursor)
        self.editor.ensureCursorVisible()
        self.focus_editor()

    def find_next(self, needle: str, case_sensitive: bool, forward: bool = True) -> bool:
        if not needle:
            return False
        flags = QTextDocument.FindFlags()
        if case_sensitive:
            flags |= QTextDocument.FindCaseSensitively
        if not forward:
            flags |= QTextDocument.FindBackward
        found = self.editor.find(needle, flags)
        if not found:
            cursor = QTextCursor(self.editor.document())
            cursor.movePosition(QTextCursor.Start if forward else QTextCursor.End)
            self.editor.setTextCursor(cursor)
            found = self.editor.find(needle, flags)
        return found

    def replace_selection(self, replacement: str) -> bool:
        cursor = self.editor.textCursor()
        if not cursor.hasSelection():
            return False
        cursor.insertText(replacement)
        self.editor.setTextCursor(cursor)
        return True

    def highlight_all(self, needle: str, case_sensitive: bool) -> int:
        """高亮当前章节中所有匹配项，返回匹配总数。"""
        self.editor.setExtraSelections([])
        if not needle:
            return 0
        text = self.editor.toPlainText()
        matches = find_all(text, needle, case_sensitive)
        color = QColor(self.palette().color(QPalette.Highlight))
        color.setAlpha(80)

        selections = []
        document = self.editor.document()
        for start, length in matches[:1500]:
            cursor = QTextCursor(document)
            cursor.setPosition(start)
            cursor.setPosition(start + length, QTextCursor.KeepAnchor)
            selection = QTextEdit.ExtraSelection()
            selection.cursor = cursor
            fmt = QTextCharFormat()
            fmt.setBackground(color)
            selection.format = fmt
            selections.append(selection)
        self.editor.setExtraSelections(selections)
        return len(matches)

    def clear_highlights(self) -> None:
        self.editor.setExtraSelections([])

    def refresh_icons(self, factory: IconFactory) -> None:
        self.jump_top.setIcon(factory.icon("top", 16))
        self.jump_bottom.setIcon(factory.icon("bottom", 16))
