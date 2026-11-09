# -*- coding: utf-8 -*-
"""正文编辑区：章节标题、编辑器、状态栏、悬浮快捷控件。"""
from __future__ import annotations

import math

from PySide6.QtCore import (
    QEvent, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer,
    QVariantAnimation, Signal,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QPixmap,
    QRadialGradient,
    QTextBlockFormat,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
)
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QSizePolicy,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.models import DEFAULT_FONT_FAMILY, Settings
from app.services import backgrounds
from app.services.proofread import scan_text
from app.services.search import find_all
from app.services.word_count import count_chars
from app.ui import motion
from app.ui.icons import IconFactory
from app.ui.widgets import apply_shadow


class PaperFrame(QFrame):
    """编辑区“纸张”：支持纯色（QSS）或自定义背景图片。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._pixmap: QPixmap | None = None
        # 按窗口尺寸缓存的缩放图：避免每次重绘都对大图做 SmoothTransformation
        self._scaled: QPixmap | None = None
        self._scaled_key: tuple[int, int] | None = None

    def set_bg_image(self, path: str) -> None:
        if path:
            pix = QPixmap(path)
            self._pixmap = None if pix.isNull() else pix
        else:
            self._pixmap = None
        self._scaled = None
        self._scaled_key = None
        self.setAttribute(Qt.WA_StyledBackground, self._pixmap is None)
        self.update()

    def resizeEvent(self, event) -> None:  # noqa: N802
        self._scaled = None
        self._scaled_key = None
        super().resizeEvent(event)

    def _scaled_pixmap(self) -> QPixmap | None:
        if self._pixmap is None or self.width() <= 0 or self.height() <= 0:
            return None
        key = (self.width(), self.height())
        if self._scaled is None or self._scaled_key != key:
            self._scaled = self._pixmap.scaled(
                self.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation
            )
            self._scaled_key = key
        return self._scaled

    def paintEvent(self, event) -> None:  # noqa: N802
        scaled = self._scaled_pixmap()
        if scaled is None:
            super().paintEvent(event)
            return
        from PySide6.QtGui import QPainter

        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
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
        self.tab_cjk_indent = False  # Tab 插入两个全角空格（设置面板开关，默认关）
        self._paste_insert = False   # 正在执行粘贴/拖放插入（这些字不计入打字统计）
        self._undoing = False        # 正在执行“只撤文字”的撤销循环
        # 撤销后由 EditorPage 挂上的“把排版补回来”的钩子（见 _undo_text_only）
        self.after_undo = None
        # 撤销 / 重做结束后由 EditorPage 挂上的状态同步钩子（字数 + 自动保存）
        self.after_history = None
        # 点击 / 聚焦前由 EditorPage 挂上的自愈钩子（见 _run_press_hook）
        self.before_mouse_press = None
        # 即将插入文字前由 EditorPage 挂上的钩子：先把空块换成正文段距，
        # 让“格式”命令排在输入之前，连续输入的 10 个字仍合并为一步撤销
        self.before_type = None
        self._in_press_hook = False
        self.setAcceptRichText(False)
        self.setLineWrapMode(QTextEdit.WidgetWidth)
        self.setFrameShape(QFrame.NoFrame)
        # 写作页用鼠标滚轮上下滑动；不显示滚动条滑块
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setCursorWidth(1)
        # 正文内联提示：当前章问题标注 + 悬停 / 光标处建议字（不改文档内容）
        self._inline_enabled = True
        self._inline_issues: list[dict] = []          # 整章问题列表（内存中保留）
        self._inline_selections: list[QTextEdit.ExtraSelection] = []  # 仅可见附近
        self._inline_built_span: tuple[int, int] | None = None
        self._inline_fmt: QTextCharFormat | None = None
        self._search_selections: list[QTextEdit.ExtraSelection] = []
        self._ghost_pos = -1
        self._ghost_end = -1
        self._ghost_text = ""
        self._ghost_rect: QRect | None = None
        self._ghost_pending: tuple[int, int, str] | None = None
        self._ghost_timer = QTimer(self)
        self._ghost_timer.setSingleShot(True)
        self._ghost_timer.setInterval(50)
        self._ghost_timer.timeout.connect(self._commit_ghost)
        # 滚动 / 尺寸变化后合并重建可见标注：16ms 单次定时器，一帧最多一次
        self._inline_rebuild_timer = QTimer(self)
        self._inline_rebuild_timer.setSingleShot(True)
        self._inline_rebuild_timer.setInterval(16)
        self._inline_rebuild_timer.timeout.connect(self._rebuild_inline_marks)
        self.viewport().setMouseTracking(True)
        self.viewport().installEventFilter(self)
        self.cursorPositionChanged.connect(self._queue_ghost_from_cursor)
        self.verticalScrollBar().valueChanged.connect(self._hide_ghost)
        self.verticalScrollBar().valueChanged.connect(self._schedule_inline_rebuild)
        self.adapt_caret()           # 光标粗细跟随初始字号（高度由行高自动决定）
        self.setTabChangesFocus(False)
        self.setPlaceholderText("从这里开始你的故事……")
        self.document().setDocumentMargin(6)

    def _run_press_hook(self) -> None:
        """点击 / 聚焦前先跑 EditorPage 挂上的自检钩子。

        钩子只修“异常状态”（被显式禁用 / 只读 / 布局被关掉），正常时零改动；
        用 _in_press_hook 防重入，避免钩子内部再次触发聚焦事件时递归。
        """
        hook = getattr(self, "before_mouse_press", None)
        if not callable(hook) or self._in_press_hook:
            return
        self._in_press_hook = True
        try:
            hook()
        finally:
            self._in_press_hook = False

    def _run_type_hook(self) -> None:
        """文字即将插入前跑一次 EditorPage 的“空块转正文段距”钩子。"""
        hook = getattr(self, "before_type", None)
        if callable(hook):
            hook()

    def inputMethodEvent(self, event) -> None:  # noqa: N802
        # 中文输入法提交换行 / 上屏前也要先换段距，否则格式命令会落在
        # 插入之后，把一次上屏拆成两个撤销步
        self._run_type_hook()
        super().inputMethodEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self._run_press_hook()
        super().mousePressEvent(event)

    def focusInEvent(self, event) -> None:  # noqa: N802
        self._run_press_hook()
        super().focusInEvent(event)

    def adapt_caret(self) -> None:
        """插入符随当前字体自适应：宽度按设备像素比分流（≥1.25 缩放恒 1 逻辑像素；
        100% 屏沿用原 1–2px：小字 1px、大字 2px）。

        高度沿用 QTextEdit 原生插入符——它按当前文本块的行布局（含字体度量与
        行距设置）绘制，字号 / 字体 / 行距变化后自动变高变矮，无需手画。
        在字号变更（字体弹窗实时预览、设置、主题切换）后立即调用，不用等重聚焦。
        """
        size = float(self.font().pointSizeF() or 0.0)
        if size <= 0.0:                  # 像素字号等兜底：直接看行高
            size = self.fontMetrics().height() / 1.35
        if self.devicePixelRatioF() >= 1.25:
            width = 1                    # 125% 及以上缩放：大字也用 1 个逻辑像素
        else:
            width = 2 if size >= 16.0 else 1   # 100% 屏：恢复原 2px 档
        if self.cursorWidth() != width:
            self.setCursorWidth(width)

    def insertFromMimeData(self, source) -> None:  # noqa: N802
        """粘贴 / 拖放插入：标记后不计入“键盘打字”统计。

        粘贴进来的新段落沿用当前段落的行高 / 缩进 / 段距，
        否则新段落走 Qt 默认段落格式，会出现“一行高一行矮”。
        有文本时按纯文本插入：剥离外部字体 / 字号 / 颜色，正文继续跟随
        文档默认字体（改字号才能即时生效）。
        """
        self._paste_insert = True
        # 粘贴到空块时同样先把段距换回正文段距（格式命令在插入之前）
        self._run_type_hook()
        cursor = self.textCursor()
        start = cursor.position()
        block_format = QTextBlockFormat(cursor.blockFormat())
        wrap = QTextCursor(self.document())
        wrap.beginEditBlock()
        try:
            if source.hasText():
                cursor.insertText(source.text())
                self.setTextCursor(cursor)
            else:
                super().insertFromMimeData(source)
            self._apply_block_format_range(start, self.textCursor().position(), block_format)
        finally:
            wrap.endEditBlock()
            self._paste_insert = False

    def _apply_block_format_range(self, start: int, end: int, fmt: QTextBlockFormat) -> None:
        """把指定范围内的段落格式统一为 fmt（用于回车 / 粘贴出的新段落）。"""
        doc = self.document()
        block = doc.findBlock(start)
        last = doc.findBlock(end)
        while block.isValid():
            QTextCursor(block).mergeBlockFormat(fmt)
            if block == last:
                break
            block = block.next()

    def undo(self) -> None:
        """撤销时跳过“排版格式”步骤，只撤文字。

        Qt 的块格式修改（行高 / 段距）会单独压一步撤销；如果直接 undo，
        用户按 Ctrl+Z 会先看到排版回退、正文不动。这里连续撤销直到正文
        真正发生变化，再通过钩子把排版按当前设置补回来。
        """
        doc = self.document()
        before = doc.toPlainText()
        self._undoing = True
        skipped = 0
        try:
            doc.undo()
            while doc.toPlainText() == before and doc.isUndoAvailable() and skipped < 4:
                doc.undo()
                skipped += 1
            # 只有跳过了“排版格式”步骤才需要补回排版；普通撤销不重排，
            # 以免压入新步骤把 Ctrl+Y 的重做栈清掉。
            if skipped and callable(self.after_undo):
                self.after_undo()
        finally:
            self._undoing = False
        if callable(self.after_history):
            self.after_history(before)

    def redo(self) -> None:
        """重做时同样跳过“排版格式”步骤，只重做文字。"""
        doc = self.document()
        before = doc.toPlainText()
        self._undoing = True
        try:
            doc.redo()
            guard = 0
            while doc.toPlainText() == before and doc.isRedoAvailable() and guard < 4:
                doc.redo()
                guard += 1
        finally:
            self._undoing = False
        if callable(self.after_history):
            self.after_history(before)

    def _insert_formatted_block(self) -> None:
        """回车：新段落沿用当前段落的行高 / 缩进 / 段距。

        用 insertBlock 自带格式，一次撤销即可回退；字符格式留空，
        让新输入的文字继续跟随文档默认字体（改字号才能即时生效）。
        """
        cursor = self.textCursor()
        block_format = QTextBlockFormat(cursor.blockFormat())
        cursor.beginEditBlock()
        try:
            if cursor.hasSelection():
                cursor.removeSelectedText()
            cursor.insertBlock(block_format, QTextCharFormat())
        finally:
            cursor.endEditBlock()
        self.setTextCursor(cursor)

    # ------------------------------------------------------------------
    def _char_after(self, cursor: QTextCursor) -> str:
        probe = QTextCursor(cursor)
        probe.movePosition(QTextCursor.NextCharacter, QTextCursor.KeepAnchor)
        return probe.selectedText()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        # 注意：Qt6 的修饰键是枚举对象，不能直接和整数 0 比较
        blocked = event.modifiers() & (Qt.ControlModifier | Qt.AltModifier)
        # 可打印字符即将落笔：先把空块的段距换回正文段距（格式命令在输入之前，
        # 才不会把连续输入拆成多个撤销步）
        text = event.text()
        if text and len(text) == 1 and blocked == Qt.NoModifier and text.isprintable():
            self._run_type_hook()
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

        # 5) 回车：新段落的行高与既有段落一致（见 _insert_formatted_block）
        #    Shift+Enter 保留 Qt 原生“软换行”行为
        if (event.key() in (Qt.Key_Return, Qt.Key_Enter)
                and blocked == Qt.NoModifier
                and not (event.modifiers() & Qt.ShiftModifier)):
            self._insert_formatted_block()
            event.accept()
            return

        # 6) Tab：可选的中文缩进（两个全角空格）。关闭时保持 Qt 原生制表符；
        #    Shift+Tab 交给 Qt 处理，不在这里拦截。
        if (event.key() == Qt.Key_Tab and self.tab_cjk_indent
                and blocked == Qt.NoModifier
                and not (event.modifiers() & Qt.ShiftModifier)):
            cursor = self.textCursor()
            cursor.insertText("\u3000\u3000")
            self.setTextCursor(cursor)
            event.accept()
            return

        # 7) 撤销：跳过“排版格式”步骤，只撤文字（见 undo / redo）
        if event.key() == Qt.Key_Z and event.modifiers() & Qt.ControlModifier:
            if event.modifiers() & Qt.ShiftModifier:
                self.redo()
            else:
                self.undo()
            event.accept()
            return
        if event.key() == Qt.Key_Y and event.modifiers() & Qt.ControlModifier:
            self.redo()
            event.accept()
            return

        super().keyPressEvent(event)

    # ------------------------------------------------------------------
    # 专注模式：光标行滚动居中
    # ------------------------------------------------------------------
    def center_cursor(self) -> None:
        """把当前光标行滚到视口中线（QTextEdit 没有 centerCursor）。"""
        bar = self.verticalScrollBar()
        if bar.maximum() <= 0:
            return
        rect = self.cursorRect()
        target = bar.value() + rect.center().y() - self.viewport().height() // 2
        bar.setValue(max(0, min(bar.maximum(), target)))

    # ------------------------------------------------------------------
    # 正文内联提示：红色波浪线 + 当前字右侧的建议字
    # ------------------------------------------------------------------
    def set_inline_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if self._inline_enabled == enabled:
            return
        self._inline_enabled = enabled
        if not enabled:
            self.clear_inline_marks()

    def clear_inline_marks(self) -> None:
        """清空当前章标注与建议字（编辑、切章、关闭开关时调用）。"""
        had_marks = bool(self._inline_selections or self._ghost_rect)
        self._inline_issues = []
        self._inline_selections = []
        self._inline_built_span = None
        self._sync_extra_selections()
        self._hide_ghost()
        if had_marks:
            self.viewport().update()

    def set_inline_issues(self, issues: list[dict]) -> None:
        """整章问题列表放内存；ExtraSelection 只为可见区域附近构建一次。"""
        self._inline_issues = [dict(item) for item in (issues or [])]
        self._inline_built_span = None
        self._rebuild_inline_marks()
        self._hide_ghost()

    def _inline_mark_format(self) -> QTextCharFormat:
        if self._inline_fmt is None:
            fmt = QTextCharFormat()
            fmt.setUnderlineStyle(QTextCharFormat.UnderlineStyle.WaveUnderline)
            fmt.setUnderlineColor(QColor(206, 54, 54))
            self._inline_fmt = fmt
        return self._inline_fmt

    def _visible_position_span(self) -> tuple[int, int]:
        """视口首尾字符位置，上下各扩一屏；长段落 / 多段落都适用。"""
        viewport = self.viewport()
        document = self.document()
        max_pos = max(0, document.characterCount() - 1)
        top = self.cursorForPosition(QPoint(0, 0)).position()
        bottom = self.cursorForPosition(QPoint(0, max(0, viewport.height() - 1))).position()
        if bottom < top:
            top, bottom = bottom, top
        if max_pos <= 0:
            return 0, 0
        span = max(200, bottom - top)
        return max(0, top - span), min(max_pos, bottom + span)

    def _schedule_inline_rebuild(self, *_args) -> None:
        """滚动 / 尺寸变化时合并重建：一帧最多一次，不随滚动像素逐次重建。"""
        if not self._inline_issues:
            return
        if not self._inline_rebuild_timer.isActive():
            self._inline_rebuild_timer.start()

    def _rebuild_inline_marks(self) -> None:
        """按当前视口重建可见附近的 ExtraSelection。

        只替换 extraSelections 并刷新 viewport：不改文本 / 格式 / 行高 /
        滚动位置，也不清掉正在显示的建议字。
        """
        if not self._inline_enabled or not self._inline_issues:
            if self._inline_selections:
                self._inline_selections = []
                self._inline_built_span = None
                self._sync_extra_selections()
                self.viewport().update()
            return
        span = self._visible_position_span()
        if span == self._inline_built_span:
            return
        document = self.document()
        max_pos = max(0, document.characterCount() - 1)
        start, end = span
        fmt = self._inline_mark_format()
        selections: list[QTextEdit.ExtraSelection] = []
        for issue in self._inline_issues:
            pos = int(issue.get("start", -1))
            length = max(1, int(issue.get("length", 1)))
            if pos < 0 or pos >= max_pos:
                continue
            if pos + length <= start or pos >= end:
                continue
            cursor = QTextCursor(document)
            cursor.setPosition(pos)
            cursor.setPosition(min(max_pos, pos + length), QTextCursor.KeepAnchor)
            selection = QTextEdit.ExtraSelection()
            selection.cursor = cursor
            selection.format = fmt
            selections.append(selection)
        self._inline_built_span = span
        self._inline_selections = selections
        self._sync_extra_selections()
        self.viewport().update()

    def set_search_selections(self, selections) -> None:
        """查找高亮走这里，与内联标注合并，避免互相清空。"""
        self._search_selections = list(selections or [])
        self._sync_extra_selections()

    def _sync_extra_selections(self) -> None:
        selections = self._search_selections + self._inline_selections
        self.setExtraSelections(selections if selections else [])

    def _issue_at(self, position: int) -> dict | None:
        for issue in self._inline_issues:
            start = int(issue.get("start", -1))
            length = max(1, int(issue.get("length", 1)))
            if start <= position < start + length:
                return issue
        return None

    @staticmethod
    def _ghost_text_for(issue: dict | None) -> str:
        if not issue or not bool(issue.get("fixable", True)):
            return ""
        replacement = str(issue.get("replacement") or "")
        return replacement[:1]

    def _queue_ghost_from_cursor(self) -> None:
        self._queue_ghost(self.textCursor().position())

    def _queue_ghost(self, position: int) -> None:
        if not self._inline_enabled:
            self._hide_ghost()
            return
        issue = self._issue_at(position)
        text = self._ghost_text_for(issue)
        if not text:
            self._hide_ghost()
            return
        start = int(issue.get("start", -1))
        end = start + max(1, int(issue.get("length", 1)))
        if (start, end, text) == (self._ghost_pos, self._ghost_end, self._ghost_text):
            return
        if (start, end, text) == self._ghost_pending:
            return
        self._ghost_pending = (start, end, text)
        self._ghost_timer.start()

    def _commit_ghost(self) -> None:
        """防抖 50ms 后落定建议字；只有位置 / 文字变化时才局部重绘。"""
        pending = self._ghost_pending
        self._ghost_pending = None
        if not pending or not self._inline_enabled:
            return
        start, end, text = pending
        rect = self._ghost_rect_for(start, end, text)
        if rect is None:
            self._hide_ghost()
            return
        old = self._ghost_rect
        self._ghost_pos = start
        self._ghost_end = end
        self._ghost_text = text
        self._ghost_rect = rect
        if old is not None:
            self.viewport().update(old.united(rect))
        else:
            self.viewport().update(rect)

    def _hide_ghost(self, *_args) -> None:
        self._ghost_timer.stop()
        self._ghost_pending = None
        old = self._ghost_rect
        self._ghost_pos = -1
        self._ghost_end = -1
        self._ghost_text = ""
        self._ghost_rect = None
        if old is not None:
            self.viewport().update(old)

    def _ghost_rect_for(self, start: int, end: int, text: str) -> QRect | None:
        """建议字矩形；行尾放不下或已换行时返回 None（不挪位置、不换行）。"""
        document = self.document()
        max_pos = max(0, document.characterCount() - 1)
        if start < 0 or start >= max_pos:
            return None
        end = max(start + 1, min(end, max_pos))
        font = QFont(self.font())
        font.setPixelSize(11)
        arrow_font = QFont(font)
        arrow_font.setPixelSize(8)
        fm = QFontMetricsF(font, self)
        afm = QFontMetricsF(arrow_font, self)
        gap = 2
        width = afm.horizontalAdvance("→") + gap + fm.horizontalAdvance(text) + 2
        begin_cursor = QTextCursor(document)
        begin_cursor.setPosition(start)
        line = self.cursorRect(begin_cursor)
        end_cursor = QTextCursor(document)
        end_cursor.setPosition(end)
        end_rect = self.cursorRect(end_cursor)
        if end_rect.y() != line.y():          # 该字正好位于软换行处
            return None
        margin = int(document.documentMargin())
        limit = self.viewport().width() - margin - 2
        if end_rect.x() + width > limit:      # 行尾剩余宽度不够
            return None
        rect = QRect(int(end_rect.x() + gap), line.top(), int(width), max(1, line.height()))
        if not self.viewport().rect().intersects(rect):
            return None
        return rect

    def _paint_ghost(self, painter: QPainter) -> None:
        if not self._inline_enabled or not self._ghost_text or self._ghost_rect is None:
            return
        rect = self._ghost_rect
        base = self.palette().color(QPalette.Text)
        ink = QColor(base)
        ink.setAlpha(75)
        arrow_ink = QColor(base)
        arrow_ink.setAlpha(48)
        font = QFont(self.font())
        font.setPixelSize(11)
        arrow_font = QFont(font)
        arrow_font.setPixelSize(8)
        fm = QFontMetricsF(font, self)
        afm = QFontMetricsF(arrow_font, self)
        baseline = rect.top() + (rect.height() + fm.ascent() - fm.descent()) / 2
        arrow_x = rect.x()
        painter.setFont(arrow_font)
        painter.setPen(arrow_ink)
        painter.drawText(QPointF(arrow_x, baseline), "→")
        painter.setFont(font)
        painter.setPen(ink)
        painter.drawText(
            QPointF(arrow_x + afm.horizontalAdvance("→") + 2, baseline),
            self._ghost_text,
        )

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is self.viewport():
            if event.type() == QEvent.Leave:
                self._hide_ghost()
            elif event.type() == QEvent.MouseMove:
                try:
                    position = event.position().toPoint()
                except AttributeError:
                    position = event.pos()
                self._queue_ghost(self.cursorForPosition(position).position())
        return super().eventFilter(obj, event)

    def focusOutEvent(self, event) -> None:  # noqa: N802
        self._hide_ghost()
        super().focusOutEvent(event)

    def resizeEvent(self, event) -> None:  # noqa: N802
        self._hide_ghost()
        super().resizeEvent(event)
        # 视口尺寸变化后可见范围变了：合并到下一帧重建标注
        self._schedule_inline_rebuild()

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        if not (self._inline_enabled and self._ghost_text and self._ghost_rect):
            return
        painter = QPainter(self.viewport())
        self._paint_ghost(painter)
        painter.end()


class GlassUnitButton(QToolButton):
    """时速面板的立体玻璃块圆钮（直径 38px）。

    - 左键循环速度单位：字/分 → 字/时 → 字/分；
    - 右键菜单：暂停 / 继续统计、立即刷新；
    - 自绘立体玻璃质感（侧壁亮暗渐变 + 顶部高光 + 底部内阴影），
      按下 2px 内陷 + 高光收敛（170ms ease-out），切换时字形 90° 旋转淡入。
    """

    cycled = Signal()
    tracking_toggled = Signal(bool)
    refresh_requested = Signal()

    SCALES = ("char", "hour")
    GLYPH = {"char": "分", "hour": "时"}
    TIP = {"char": "字/分", "hour": "字/时"}

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(38, 38)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self._scale = "char"
        self._press = 0.0          # 0 松开 / 1 按下（动画插值）
        self._angle = 0.0         # 字形旋转角
        self._alpha = 1.0
        self.setCheckable(False)
        self.clicked.connect(self._on_click)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        # 按下内陷 170ms
        self._press_anim = QVariantAnimation(self)
        self._press_anim.setDuration(170)
        self._press_anim.valueChanged.connect(self._set_press)
        # 字形 90° 旋出 → 换字 → 旋入（合计约 180ms）
        self._spin_anim = QVariantAnimation(self)
        self._spin_anim.setDuration(90)
        self._spin_anim.valueChanged.connect(self._set_spin)
        self._spin_anim.finished.connect(self._spin_finished)
        self._spin_phase = 0
        self._sync_tip()

    # ------------------------------------------------------------------
    def scale(self) -> str:
        return self._scale

    def set_scale(self, scale: str) -> None:
        if scale in self.SCALES and scale != self._scale:
            self._scale = scale
            self._sync_tip()
            self.update()

    def _sync_tip(self) -> None:
        self.setToolTip(f"切换速度单位：字/分 / 字/时（当前：{self.TIP[self._scale]}）\n"
                        "右键：暂停统计 / 立即刷新；空格或回车也可切换")
        self.setAccessibleName(f"速度单位：{self.TIP[self._scale]}（点击切换 字/分、字/时）")

    # ------------------------------------------------------------------
    def _on_click(self) -> None:
        self.do_cycle()          # 90° 旋转动画（鼠标与键盘空格/回车都走这里）
        self.cycled.emit()

    def do_cycle(self) -> None:
        """左键 / 键盘触发的单位循环（带动画）。"""
        nxt = self.SCALES[(self.SCALES.index(self._scale) + 1) % len(self.SCALES)]
        self._spin_phase = 1
        self._spin_anim.stop()
        self._spin_anim.setStartValue(0.0)
        self._spin_anim.setEndValue(1.0)
        self._spin_anim.start()
        self._pending_scale = nxt

    def _set_spin(self, v) -> None:
        self._angle = -90.0 * float(v) if self._spin_phase == 1 else -90.0 + 90.0 * float(v)
        self._alpha = 1.0 - 0.75 * float(v) if self._spin_phase == 1 else 0.25 + 0.75 * float(v)
        self.update()

    def _spin_finished(self) -> None:
        if self._spin_phase == 1:
            self.set_scale(getattr(self, "_pending_scale", self._scale))
            self._spin_phase = 2
            self._spin_anim.stop()
            self._spin_anim.start()
        else:
            self._spin_phase = 0
            self._angle, self._alpha = 0.0, 1.0
            self.update()

    def _menu(self, pos: QPoint) -> None:
        menu = QMenu(self)
        act_pause = menu.addAction("继续统计" if getattr(self, "_tracking_off", False) else "暂停统计")
        act_refresh = menu.addAction("立即刷新")
        chosen = menu.exec(self.mapToGlobal(pos))
        if chosen is act_pause:
            self._tracking_off = not getattr(self, "_tracking_off", False)
            self.tracking_toggled.emit(not self._tracking_off)
        elif chosen is act_refresh:
            self.refresh_requested.emit()

    def set_tracking_on(self, on: bool) -> None:
        self._tracking_off = not on
        self.update()

    def enterEvent(self, event) -> None:  # noqa: N802
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self.update()
        super().leaveEvent(event)

    # ------------------------------------------------------------------
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._press_anim.stop()
            self._press_anim.setStartValue(self._press)
            self._press_anim.setEndValue(1.0)
            self._press_anim.start()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._press_anim.stop()
            self._press_anim.setStartValue(self._press)
            self._press_anim.setEndValue(0.0)
            self._press_anim.start()
        super().mouseReleaseEvent(event)

    def _set_press(self, v) -> None:
        self._press = float(v)
        self.update()

    # ------------------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        pal = self.palette()
        dark = pal.window().color().lightness() < 128
        inset = 1.5 + self._press * 2.0     # 按下时整体内陷 2px
        r = QRectF(inset, inset, self.width() - inset * 2, self.height() - inset * 2)

        path = QPainterPath()
        path.addEllipse(r)
        # 底：面板色微竖向渐变（按下时高光收敛、对比略增强）
        top = pal.button().color().lighter(106 if not dark else 122)
        bot = pal.button().color().darker(108 if not dark else 118)
        grad = QLinearGradient(0, r.top(), 0, r.bottom())
        grad.setColorAt(0, top)
        grad.setColorAt(1, bot)
        p.setPen(Qt.NoPen)
        p.setBrush(grad)
        p.drawPath(path)

        # 侧壁挤出：2px 亮暗渐变环（左上提亮 / 右下压暗）
        ring = QPainterPath()
        rr = r.adjusted(-1.0, -1.0, 1.0, 1.0)
        ring.addEllipse(rr)
        ring.addEllipse(r)
        edge_grad = QLinearGradient(rr.topLeft(), rr.bottomRight())
        edge_grad.setColorAt(0, QColor(255, 255, 255, 70 if not dark else 46))
        edge_grad.setColorAt(1, QColor(0, 0, 0, 46 if not dark else 70))
        p.setBrush(edge_grad)
        p.drawPath(ring)

        p.save()
        p.setClipPath(path)
        # 顶部渐隐高光（hover 提亮 35%；按下时 alpha 收敛）
        hover_boost = 1.35 if self.underMouse() and self._press < 0.5 else 1.0
        glow_a = (0.10 if not dark else 0.07) * (1.0 - 0.45 * self._press) * hover_boost
        gg = QLinearGradient(0, r.top(), 0, r.top() + r.height() * 0.42)
        gg.setColorAt(0, QColor(255, 255, 255, int(255 * glow_a)))
        gg.setColorAt(1, QColor(255, 255, 255, 0))
        p.fillRect(r, gg)
        # 高光斑（偏左上，随按下收敛）
        sheen = 0.08 * (1.0 - 0.5 * self._press)
        cx, cy = r.center().x() - r.width() * 0.16, r.center().y() - r.height() * 0.20
        rad_g = QRadialGradient(QPointF(cx, cy), r.width() * 0.55)
        rad_g.setColorAt(0, QColor(255, 255, 255, int(255 * sheen)))
        rad_g.setColorAt(1, QColor(255, 255, 255, 0))
        p.setBrush(rad_g)
        p.drawEllipse(r)
        # 底部内阴影（按下时加深，强化内陷）
        band = QRectF(r.left(), r.bottom() - 4.0, r.width(), 4.0)
        ig = QLinearGradient(0, band.top(), 0, band.bottom())
        ig.setColorAt(0, QColor(0, 0, 0, 0))
        ig.setColorAt(1, QColor(0, 0, 0, int(255 * (0.10 + 0.06 * self._press))))
        p.fillRect(band, ig)
        p.restore()

        # 焦点环（键盘可用）
        if self.hasFocus():
            pen = QPen(QColor("#7C5CFF"), 1.6)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(r.adjusted(-1.5, -1.5, 1.5, 1.5))

        # 字形（90° 旋转 + 淡入淡出）
        p.save()
        p.setOpacity(self._alpha)
        p.translate(r.center().x(), r.center().y())
        p.rotate(self._angle)
        font = QFont(self.font())
        font.setPointSizeF(11)
        font.setBold(True)
        p.setFont(font)
        p.setPen(QColor(pal.buttonText().color()))
        p.drawText(QRectF(-r.width() / 2, -r.height() / 2, r.width(), r.height()),
                   Qt.AlignCenter, self.GLYPH[self._scale])
        p.restore()
        p.end()


class SpeedPanel(QFrame):
    """右侧常驻的码字时速面板（可隐藏）。

    v3：标题不再带「?」；第二行的控件合并为一颗立体玻璃圆钮，
    左键在 字/分 ↔ 字/时 两个速度单位间循环（本次字数始终原始口径），
    右键菜单承担暂停/继续统计与立即刷新。
    """

    hide_requested = Signal()
    unit_changed = Signal(str)   # "char" / "hour"
    tracking_changed = Signal(bool)   # 统计暂停 / 继续（持久化用）

    SCALES = ("char", "hour")
    SPEED_UNIT = {"char": "字/分", "hour": "字/时"}

    def __init__(self, settings: Settings, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("SidePanel")
        self.setFixedWidth(212)
        self._settings = settings
        self._tracker = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(8)

        # 标题行：码字时速（帮助并入底部说明 + tooltip）…… 右侧关闭
        header = QHBoxLayout()
        header.setSpacing(4)
        title = QLabel("码字时速", self)
        title.setObjectName("SidePanelTitle")
        title.setToolTip(
            "实时统计本次码字数据：字数、速度、时长与空闲。\n"
            "停写 30 秒后开始记录空闲；打字即停，不清零。"
        )
        header.addWidget(title)
        header.addStretch(1)
        hide_button = QToolButton(self)
        hide_button.setObjectName("DrawerClose")
        hide_button.setCursor(Qt.PointingHandCursor)
        hide_button.setToolTip("隐藏时速面板")
        hide_button.setAccessibleName("隐藏时速面板")
        hide_button.clicked.connect(self.hide_requested.emit)
        header.addWidget(hide_button)
        self._speed_hide_button = hide_button
        layout.addLayout(header)

        # 第二行：状态（左）…… 立体玻璃圆钮（切换显示口径；右键暂停/刷新）
        status_row = QHBoxLayout()
        status_row.setSpacing(6)
        self.status_label = QLabel("未开始", self)
        self.status_label.setObjectName("SidePanelStatus")
        status_row.addWidget(self.status_label)
        status_row.addStretch(1)
        self.unit_button = GlassUnitButton(self)
        self.unit_button.cycled.connect(self._cycle_unit)
        self.unit_button.tracking_toggled.connect(self._set_tracking)
        self.unit_button.refresh_requested.connect(self.refresh)
        status_row.addWidget(self.unit_button)
        layout.addLayout(status_row)
        layout.addSpacing(6)

        # 四个数据行：浅灰圆角卡片，左灰标签右加粗数值
        self.session_value = self._add_row(layout, "本次字数")
        self.speed_value = self._add_row(layout, "码字速度")
        self.time_value = self._add_row(layout, "码字时长")
        self.idle_value = self._add_row(layout, "空闲时长")

        layout.addStretch(1)

        hint = QLabel(
            "点圆钮切换速度单位 字/分 / 字/时（仅显示口径，统计不变）；\n"
            "右键可暂停统计或立即刷新。停写 30 秒后开始记录空闲，不清零。", self)
        hint.setObjectName("SidePanelKey")
        hint.setWordWrap(True)
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
        motion.register_timer(self._speed_anim)

        # 统计开关 / 显示口径与圆钮同步（旧 settings 缺键：开统计、按字）
        self.unit_button.set_tracking_on(bool(getattr(self._settings, "speed_tracking", True)))
        self.unit_button.set_scale(self._speed_scale)
        self.refresh()

    def apply_icons(self, factory: IconFactory) -> None:
        """关闭文字字形钮换成全站统一图标（问号钮 v3 已移除，刷新迁入右键菜单）。"""
        self._speed_hide_button.setText("")
        self._speed_hide_button.setIconSize(QSize(15, 15))
        self._speed_hide_button.setIcon(factory.icon("close", 15))

    # ------------------------------------------------------------------
    def _add_row(self, layout: QVBoxLayout, key: str) -> QLabel:
        row = QFrame(self)
        row.setObjectName("SideCard")
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(10, 6, 10, 6)
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

    @property
    def _speed_scale(self) -> str:
        scale = str(getattr(self._settings, "speed_unit_scale", "") or "")
        # 旧配置里可能存的是 "kilo"/"wan"，平滑回退到「字/分」
        return scale if scale in self.SCALES else "char"

    def _cycle_unit(self) -> None:
        """圆钮：字/分 → 字/时 → 字/分（圆钮自身做旋转动画，这里只换数据口径）。"""
        nxt = self.SCALES[(self.SCALES.index(self._speed_scale) + 1) % len(self.SCALES)]
        self._settings.speed_unit_scale = nxt
        self.unit_button.set_scale(nxt)
        self.refresh()
        self.unit_changed.emit(nxt)

    def _set_tracking(self, on: bool) -> None:
        """右键菜单 / 程序化：暂停 / 继续本次统计（暂停后不累计，不清零已有数据）。"""
        on = bool(on)
        self._settings.speed_tracking = on
        if self._tracker is not None:
            self._tracker.paused = not on
        self.unit_button.set_tracking_on(on)   # 双向同步（不回发信号）
        self.refresh()
        self.tracking_changed.emit(on)

    def _count_text(self, total: int) -> str:
        # 本次字数始终显示原始口径（单位只切换「速度」）
        return f"{total} 字"

    def _speed_text(self, per_hour: float) -> str:
        # 两档速度单位：字/分（整数）与 字/时（1 位小数）
        if self._speed_scale == "hour":
            return "0.0 字/时" if per_hour <= 0 else f"{per_hour:.1f} 字/时"
        if per_hour <= 0:
            return "0 字/分"
        return f"{int(per_hour / 60)} 字/分"

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
        if tracker is not None:
            tracker.paused = not bool(getattr(self._settings, "speed_tracking", True))
            self.unit_button.set_tracking_on(not tracker.paused)
        self.refresh()

    def refresh(self) -> None:
        if self._tracker is None:
            self.status_label.setText("未开始")
            for label in (self.session_value, self.time_value, self.idle_value):
                label.setText("—")
            self._animate_speed_to(0.0)
            return
        tracker = self._tracker
        paused = bool(getattr(tracker, "paused", False))
        self.status_label.setText("已暂停" if paused else getattr(tracker, "status", "未开始"))
        self.session_value.setText(self._count_text(int(tracker.total_chars)))
        self.time_value.setText(tracker.elapsed_text)
        self._animate_speed_to(0.0 if paused else float(tracker.speed_per_hour))
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


class SaveSpinner(QWidget):
    """保存指示器：圆环旋转 → 绿勾 / 红叉（左下角）。

    状态机只由真实落盘结果驱动：``start_saving`` 在写入前调用，
    ``finish_ok`` / ``finish_error`` 只在写入完成（或抛错）后调用。
    停留时长结束后回到空闲（隐藏），不冒充保存成功。
    """

    IDLE = "idle"
    SAVING = "saving"
    OK = "ok"
    ERROR = "error"

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(30, 30)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._state = self.IDLE
        self._angle = 0
        self._spin = QTimer(self)
        self._spin.setInterval(33)
        self._spin.timeout.connect(self._tick)
        motion.register_timer(self._spin)
        self._hold = QTimer(self)
        self._hold.setSingleShot(True)
        self._hold.timeout.connect(lambda: self.set_state(self.IDLE))
        self.hide()

    def set_state(self, state: str, hold_ms: int = 0) -> None:
        """切换绘制状态；``hold_ms > 0`` 时到点自动回到空闲。"""
        self._state = state
        if state == self.SAVING:
            if not self._spin.isActive():
                self._spin.start()
        else:
            self._spin.stop()
        if hold_ms > 0 and state in (self.OK, self.ERROR):
            self._hold.start(hold_ms)
        elif state != self.IDLE:
            self._hold.stop()
        if state == self.IDLE:
            self.hide()
        else:
            self.show()
            self.raise_()
        self.update()

    def start_saving(self) -> None:
        self.set_state(self.SAVING)

    def finish_ok(self, hold_ms: int = 600) -> None:
        self.set_state(self.OK, hold_ms)

    def finish_error(self, hold_ms: int = 1500) -> None:
        self.set_state(self.ERROR, hold_ms)

    def state(self) -> str:
        return self._state

    def _tick(self) -> None:
        self._angle = (self._angle + 12) % 360
        self.update()

    def hideEvent(self, event) -> None:  # noqa: N802
        self._spin.stop()
        super().hideEvent(event)

    def _is_dark(self) -> bool:
        return self.palette().window().color().lightness() < 128

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(4, 4, 22, 22)
        dark = self._is_dark()
        ring = QColor(255, 255, 255, 205) if dark else QColor(20, 22, 26, 150)
        pen = QPen(ring, 3.0)
        painter.setPen(pen)
        painter.drawEllipse(rect)                       # 圆环底
        if self._state == self.SAVING:
            pen.setColor(QColor(24, 26, 30, 200) if not dark else QColor(235, 238, 245, 210))
            pen.setCapStyle(Qt.RoundCap)
            painter.setPen(pen)
            painter.drawArc(rect, self._angle * 16, 100 * 16)
            return
        if self._state == self.OK:
            color = QColor("#2E9E63") if not dark else QColor("#6FE0A8")
            self._draw_check(painter, rect, color)
            return
        if self._state == self.ERROR:
            color = QColor("#D0443E") if not dark else QColor("#FF7B72")
            pen.setColor(color)
            pen.setWidthF(2.8)
            painter.setPen(pen)
            a, b = rect.adjusted(6, 6, -6, -6).topLeft(), rect.adjusted(6, 6, -6, -6).bottomRight()
            painter.drawLine(a, b)
            c, d = rect.adjusted(6, 6, -6, -6).topRight(), rect.adjusted(6, 6, -6, -6).bottomLeft()
            painter.drawLine(c, d)

    def _draw_check(self, painter: QPainter, rect: QRectF, color: QColor) -> None:
        pen = QPen(color, 3.0)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        p1 = QPointF(rect.left() + 5.5, rect.center().y())
        p2 = QPointF(rect.left() + 10.0, rect.bottom() - 6.0)
        p3 = QPointF(rect.right() - 5.0, rect.top() + 6.0)
        painter.drawLine(p1, p2)
        painter.drawLine(p2, p3)


class TitleLabel(QLabel):
    """章节标题：双击进入行内编辑（与左侧目录重命名形成“双通道”）。"""

    double_clicked = Signal()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.double_clicked.emit()
        super().mouseDoubleClickEvent(event)


class EditorPage(QWidget):
    """完整的正文页面。"""

    content_edited = Signal()
    chapter_chars_changed = Signal(int)
    speed_delta = Signal(int)
    typed_delta = Signal(int)          # 仅键盘输入的新增字数（粘贴不计）
    speed_panel_toggled = Signal(bool)  # 右侧时速面板显隐变化
    title_edited = Signal(str)         # 双击标题后提交的新章节名
    paragraph_settings_requested = Signal()  # 正文右键 / 顶栏请求打开段落设置
    smart_format_requested = Signal()        # 正文右键请求智能排版
    proofread_requested = Signal()           # 正文右键请求文本体检
    focus_mode_requested = Signal()          # 正文右键请求进入 / 退出专注模式
    find_requested = Signal()                # 正文右键请求查找替换
    new_chapter_requested = Signal()         # 正文右键请求新建章节

    def __init__(self, settings: Settings, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self._loading = False
        self._last_count = 0
        self._total_chars = 0
        self._applied_font: tuple[str, str, float] | None = None
        self._applied_spacing: float | None = None
        self._applied_para: tuple | None = None
        # 布局冻结的可重入计数：只有归零时才真正 setLayoutEnabled(True)，
        # 任何早退 / 异常都不会把文档永久留在“布局关闭”状态。
        self._layout_depth = 0
        # 最近一次有效滚动位置（QSS 重建会把滚动条归零，热改字号时要滚回去）
        self._last_scroll: tuple[int, int] = (0, 0)
        # 字数统计防抖定时器（流畅度：打字时不做全文统计）
        self._pending_keyboard = False
        self._count_timer = QTimer(self)
        self._count_timer.setSingleShot(True)
        self._count_timer.setInterval(120)
        self._count_timer.timeout.connect(self._recount)
        self._focus_mode = False       # 专注模式：隐藏状态栏、正文铺满、光标行居中
        self._focus_last_block = -1
        # 正文内联提示：切章扫一次，编辑后 500ms 防抖重扫；只扫当前章
        self._inline_enabled = True
        self._inline_timer = QTimer(self)
        self._inline_timer.setSingleShot(True)
        self._inline_timer.setInterval(500)
        self._inline_timer.timeout.connect(self._refresh_inline)

        self._build_ui()
        self._connect()
        # 正文被禁用时鼠标事件会冒泡到纸张层：在这里兜底自愈，见 eventFilter
        self.paper.installEventFilter(self)
        # 点击 / 聚焦正文前自检：禁用 / 只读 / 布局关闭等异常状态先复位再继续
        self.editor.before_mouse_press = self.unlock_editor
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
        # v1.4.4：下沿不再留 8px——全文字数移入纸张内部，纸张下沿直接贴窗口底边
        self._base_outer = (6, 10, 6, 0)
        self._outer_margins = self._base_outer
        self._top_flush = False
        self._left_flush = False
        self._right_flush = False
        outer.setContentsMargins(*self._outer_margins)
        outer.setSpacing(6)
        self._body_outer = outer

        # 中间是“悬浮白纸”：章节标题融合在纸张内部顶部，铺满写作区（仅留 3mm 边距）
        paper = PaperFrame(self)
        paper.setObjectName("EditorPaper")
        paper.setProperty("topFlush", False)
        paper.setProperty("leftFlush", False)
        paper.setProperty("rightFlush", False)
        paper.setMinimumWidth(320)
        paper.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        apply_shadow(paper, blur=36, alpha=36, dy=8)
        paper_layout = QVBoxLayout(paper)
        # 内部左右不再留白（外层 6px + 文档边距 6px ≈ 3mm 已够），上 30px；
        # 下沿 0：状态栏（字数 / 光标 / 保存提示）放在纸张最底部，与窗口底边贴合
        paper_layout.setContentsMargins(0, 30, 0, 0)
        paper_layout.setSpacing(30)

        self.title_label = TitleLabel("未选择章节", self)
        self.title_label.setObjectName("EditorTitle")
        self.title_label.setAlignment(Qt.AlignCenter)
        self.title_label.setMinimumHeight(34)
        self.title_label.setToolTip("双击可直接修改章节名")
        self.title_label.double_clicked.connect(self.begin_title_edit)
        paper_layout.addWidget(self.title_label)

        # 标题“双通道”编辑：双击正文标题时显示的行内输入框
        self.title_edit = QLineEdit(paper)
        self.title_edit.setObjectName("EditorTitleEdit")
        self.title_edit.setAlignment(Qt.AlignCenter)
        self.title_edit.hide()
        self.title_edit.returnPressed.connect(self._commit_title_edit)
        self.title_edit.editingFinished.connect(self._commit_title_edit)
        self.title_edit.installEventFilter(self)
        paper_layout.addWidget(self.title_edit)

        # 正文 + 底部状态栏：状态栏进背板（纸张）内部，纸张下沿才能贴住窗口底边
        body_box = QWidget(paper)
        body_layout = QVBoxLayout(body_box)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)

        self.editor = NovelEditor(body_box)
        self.editor.verticalScrollBar().valueChanged.connect(self._remember_scroll)
        self.editor.after_undo = self._restore_format_after_undo
        self.editor.after_history = self._after_history_nav
        body_layout.addWidget(self.editor, 1)

        # 状态栏：字数统计在最左下角，其后是光标位置，保存提示靠右。
        # 外层包一层控件，专注模式可整行隐藏。
        self.status_bar = QWidget(body_box)
        status_row = QHBoxLayout(self.status_bar)
        status_row.setContentsMargins(18, 4, 18, 8)
        status_row.setSpacing(10)
        self.count_label = QLabel("本章 0 字 · 全书 0 字", self.status_bar)
        self.count_label.setObjectName("EditorStatus")
        self.cursor_label = QLabel("第 1 行 · 第 1 列", self.status_bar)
        self.cursor_label.setObjectName("EditorStatus")
        self.save_label = QLabel("", self.status_bar)
        self.save_label.setObjectName("SaveHint")
        status_row.addWidget(self.count_label)
        status_row.addWidget(self.cursor_label)
        status_row.addStretch(1)
        status_row.addWidget(self.save_label)
        body_layout.addWidget(self.status_bar)

        paper_layout.addWidget(body_box, 1)

        outer.addWidget(paper, 1)
        self.paper = paper

        # 保存指示器：左下角转圈动画（0.6 秒自动消失）
        self.save_spinner = SaveSpinner(self)

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

        self._scroll_flasher = ScrollFlasher(
            self.editor, Qt.Vertical, inset=12, track_scroll=True
        )
        self.scroll_handle = self._scroll_flasher.handle

    def _connect(self) -> None:
        self.editor.textChanged.connect(self._on_text_changed)
        self.editor.cursorPositionChanged.connect(self._update_cursor_label)
        self.editor.cursorPositionChanged.connect(self._follow_focus_cursor)
        # 输入前把空块换成正文段距（格式命令必须先于插入，见 _prepare_typing_block）
        self.editor.before_type = self._prepare_typing_block
        # 滚轮监听由 ScrollFlasher 自行挂在编辑器视口上
        # 正文右键菜单：标准编辑菜单 + 段落设置 / 智能排版
        self.editor.setContextMenuPolicy(Qt.CustomContextMenu)
        self.editor.customContextMenuRequested.connect(self._show_editor_menu)

    def _build_editor_menu(self):
        menu = self.editor.createStandardContextMenu()
        menu.addSeparator()
        menu.addAction("新建章节", lambda: self.new_chapter_requested.emit())
        menu.addSeparator()
        menu.addAction("段落设置…", lambda: self.paragraph_settings_requested.emit())
        menu.addAction("智能排版（本章）", lambda: self.smart_format_requested.emit())
        menu.addAction("文本体检…", lambda: self.proofread_requested.emit())
        menu.addAction("专注模式（F11）", lambda: self.focus_mode_requested.emit())
        menu.addSeparator()
        menu.addAction("粘贴为纯文本", self._paste_plain)
        menu.addAction("查找替换…", lambda: self.find_requested.emit())
        return menu

    def _show_editor_menu(self, pos) -> None:
        menu = self._build_editor_menu()
        menu.exec(self.editor.viewport().mapToGlobal(pos))
        menu.deleteLater()

    def _paste_plain(self) -> None:
        """只取剪贴板纯文本插入，不带外部字体 / 颜色 / 段落格式。"""
        text = QApplication.clipboard().text()
        if text:
            self.editor.textCursor().insertText(text)

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------
    def set_inline_hints_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if self._inline_enabled == enabled:
            return
        self._inline_enabled = enabled
        self._inline_timer.stop()
        self.editor.set_inline_enabled(enabled)
        if enabled and not self._loading:
            self._refresh_inline()

    def _refresh_inline(self) -> None:
        """只扫当前章；关掉开关时不产任何标注。"""
        if self._loading:
            return
        if not self._inline_enabled:
            self.editor.clear_inline_marks()
            return
        text = self.editor.toPlainText()
        self.editor.set_inline_issues(scan_text(text) if text else [])

    def load_text(self, title: str, text: str) -> None:
        """载入章节内容（会清空撤销栈）。"""
        self._loading = True
        try:
            # 关键：clear_page() 曾把编辑器显式禁用；Qt 里父控件 setEnabled(True)
            # 不会恢复“子控件自己”的禁用标志，必须在这里显式解除，否则换章 /
            # 新建书之后正文永久点不进、输不进（标题不受影响）。
            self.unlock_editor()
            self.title_label.setText(title)
            self.editor.set_search_selections([])
            self.editor.clear_inline_marks()
            self._inline_timer.stop()
            # 字体先落到控件与文档默认字体；正文不烘焙显式字体，
            # 之后改字号才会自动重排（见 _apply_typography）
            self._applied_font = None
            self._apply_typography()
            self.editor.setPlainText(text)
            # 新文本块是默认段落格式：行距与段距都要按新字号重新套一次
            self._applied_spacing = None
            self._applied_para = None
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
        # 进入 / 切换章节时扫一次当前章
        self._refresh_inline()

    def clear_page(self) -> None:
        """没有打开任何书籍时显示的空状态。"""
        self._loading = True
        self._inline_timer.stop()
        self.title_label.setText("未选择章节")
        self.editor.setPlainText("")
        self.editor.clear_inline_marks()
        self._loading = False
        self._last_count = 0
        self._total_chars = 0
        self._update_status()
        self.editor.setDisabled(True)
        self.jump_top.setVisible(False)
        self.jump_bottom.setVisible(False)
        self._scroll_flasher.reset()

    # ------------------------------------------------------------------
    # 可编辑自愈（修复“正文点不进 / 输不进”的兜底）
    # ------------------------------------------------------------------
    def unlock_editor(self) -> list[str]:
        """把正文恢复到可编辑状态，返回实际修正项。

        只动“被意外置坏”的状态，正常状态下一项都不改（因此可以在焦点事件里
        高频调用）：编辑器 / 视口禁用、只读、布局被冻结、纸张层残留遮罩。
        """
        fixed: list[str] = []
        ed = self.editor
        if not self.isEnabled():
            self.setDisabled(False)
            fixed.append("page_disabled")
        if not ed.isEnabled():
            ed.setDisabled(False)
            fixed.append("editor_disabled")
        if not ed.viewport().isEnabled():
            ed.viewport().setDisabled(False)
            fixed.append("viewport_disabled")
        if ed.isReadOnly():
            ed.setReadOnly(False)
            fixed.append("readonly")
        doc = ed.document()
        if not doc.isLayoutEnabled() or self._layout_depth:
            self._layout_depth = 0
            doc.setLayoutEnabled(True)
            fixed.append("layout_released")
        return fixed

    def current_text(self) -> str:
        return self.editor.toPlainText()

    def set_title(self, title: str) -> None:
        self.title_label.setText(title)

    # ------------------------------------------------------------------
    # 标题行内编辑（双通道之一）
    # ------------------------------------------------------------------
    def begin_title_edit(self) -> None:
        """双击标题：显示行内输入框，回车提交，Esc 取消。"""
        title = self.title_label.text()
        if not title or title == "未选择章节":
            return
        self.title_edit.setText(title)
        self.title_label.hide()
        self.title_edit.show()
        self.title_edit.setFocus()
        self.title_edit.selectAll()

    def _commit_title_edit(self) -> None:
        if self.title_edit.isHidden():
            return
        text = self.title_edit.text().strip()
        self.title_edit.hide()
        self.title_label.show()
        if text and text != self.title_label.text():
            self.title_edited.emit(text)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is self.title_edit and event.type() == QEvent.KeyPress:
            if event.key() == Qt.Key_Escape:
                self.title_edit.hide()
                self.title_label.show()
                return True
            if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                self._commit_title_edit()
                self.focus_editor()
                return True
        paper = getattr(self, "paper", None)   # 构造期 title_edit 已装过滤器，paper 可能还没赋值
        if obj is paper and paper is not None and event.type() == QEvent.MouseButtonPress:
            # 正文可编辑时事件不会走到纸张层；能走到说明正文被禁用或被上层吞掉。
            pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
            vp = self.editor.viewport()
            vp_pos = vp.mapFrom(self.paper, pos)
            if vp.rect().contains(vp_pos):
                fixed = self.unlock_editor()
                if not fixed:
                    return False
                # 自愈后再把这次点击落成光标位置，用户不需要点第二次
                self.editor.setFocus()
                self.editor.setTextCursor(self.editor.cursorForPosition(vp_pos))
                return True
        return super().eventFilter(obj, event)

    def set_total_chars(self, total: int) -> None:
        self._total_chars = max(0, total)
        self._update_status()

    def begin_save(self) -> None:
        """写入前调用：左下角圆环开始旋转（不发成功信号）。"""
        self.save_spinner.start_saving()

    def set_save_result(self, ok: bool, minute: str = "", reason: str = "") -> None:
        """按真实落盘结果切换指示：成功绿勾 0.6 秒，失败红叉 1.5 秒并保留原因。"""
        if ok:
            self.save_label.setText(f"已自动保存 {minute}" if minute else "")
            self.save_spinner.finish_ok(600)
        else:
            brief = (reason or "未知原因").splitlines()[0][:120]
            self.save_label.setText(f"保存失败：{brief}")
            self.save_spinner.finish_error(1500)

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

    # ------------------------------------------------------------------
    # 设置
    # ------------------------------------------------------------------
    def apply_settings(self, settings: Settings) -> None:
        self.settings = settings
        self.editor.auto_pair = settings.auto_pair
        self.editor.tab_cjk_indent = bool(settings.tab_cjk_indent)
        self.set_inline_hints_enabled(bool(settings.inline_hints))
        self.jump_top.setVisible(settings.float_jump)
        self.jump_bottom.setVisible(settings.float_jump)
        self._scroll_flasher.set_enabled(settings.fast_scroll)

        self._apply_typography()

        # 切换“不计标点”后重新计算，避免把差值算成新增字数
        text = self.editor.toPlainText()
        new_count = count_chars(text, settings.exclude_punct) if text else 0
        self._last_count = new_count
        self._update_status()
        self.chapter_chars_changed.emit(new_count)

        self._position_floating()

    def _apply_outer_margins(self) -> None:
        """按顶栏 / 左栏 / 右栏的贴边状态重算纸张外边距。

        贴边只改纸张绘制边界：对应一侧外边距归 0，另一侧仍保留 6px；
        正文换行宽度仍由编辑器视口决定，滚动位置在变更后按比例恢复。
        """
        left, top, right, bottom = self._base_outer
        self._outer_margins = (
            0 if self._left_flush else left,
            0 if self._top_flush else top,
            0 if self._right_flush else right,
            bottom,
        )
        self._body_outer.setContentsMargins(*self._outer_margins)

    def _set_paper_flag(self, name: str, value: bool) -> None:
        """更新纸张的动态属性并重刷 QSS（圆角是否收平由属性选择器决定）。"""
        if bool(self.paper.property(name)) == value:
            return
        self.paper.setProperty(name, value)
        style = self.paper.style()
        style.unpolish(self.paper)
        style.polish(self.paper)
        self.paper.update()

    def set_top_flush(self, flush: bool) -> None:
        """顶栏收起时让纸张上沿贴住窗口上沿，并收掉上方两角圆角。

        外层 10px 的顶部留白只在展开态保留；收起态连圆角一起清零，
        避免窗口上沿出现“底色条 / 圆角空档”。
        """
        flush = bool(flush)
        if self._top_flush == flush:
            return
        self._top_flush = flush
        self._apply_outer_margins()
        self._set_paper_flag("topFlush", flush)

    def set_side_flush(self, left: bool, right: bool) -> None:
        """左右栏收起时让纸张对应侧贴住窗口边缘（无底色条、无圆角空档）。

        侧栏显隐没有动画（瞬时切布局），因此不存在“板先跳、栏后收”；
        若后续给侧栏加动画，需在动画每帧调用本方法做同样处理。
        """
        left, right = bool(left), bool(right)
        if left == self._left_flush and right == self._right_flush:
            return
        bar = self.editor.verticalScrollBar()
        old_value, old_max = bar.value(), bar.maximum()
        self._left_flush = left
        self._right_flush = right
        self._apply_outer_margins()
        self._set_paper_flag("leftFlush", left)
        self._set_paper_flag("rightFlush", right)

        def _restore() -> None:
            hi = bar.maximum()
            if old_max <= 0 or hi <= 0:
                return
            bar.setValue(min(hi, int(old_value / old_max * hi)))

        QTimer.singleShot(0, _restore)

    # ------------------------------------------------------------------
    # 专注模式
    # ------------------------------------------------------------------
    def set_focus_mode(self, enabled: bool) -> None:
        """进入 / 退出专注模式：只切显隐，不碰正文、光标和撤销栈。"""
        enabled = bool(enabled)
        if self._focus_mode == enabled:
            return
        self._focus_mode = enabled
        self.status_bar.setVisible(not enabled)
        if enabled:
            self.set_top_flush(True)
            self.set_side_flush(True, True)
            self._focus_last_block = -1
            self.editor.center_cursor()
            # 侧栏贴边会按比例延迟恢复滚动，这里再排一次居中，确保晚于它执行
            QTimer.singleShot(0, self._focus_center)

    def _focus_center(self) -> None:
        if not self._focus_mode:
            return
        self.editor.center_cursor()
        self._focus_last_block = self.editor.textCursor().blockNumber()
        self.editor.viewport().update()

    def _follow_focus_cursor(self) -> None:
        """专注模式：跨行移动时把当前行保持在中线，并刷新压暗区域。"""
        if not self._focus_mode:
            return
        block = self.editor.textCursor().blockNumber()
        if block != self._focus_last_block:
            self._focus_last_block = block
            self.editor.center_cursor()
        self.editor.viewport().update()

    def _build_font(self) -> QFont:
        family = self.settings.font_family or DEFAULT_FONT_FAMILY
        font = QFont()
        # 必须用单一族名：Qt 只会采用 families() 里「第一个可用」的字体来解析
        # 整个 QFont，CJK 缺字再走系统兜底字体；写成 [font_latin, family] 时
        # Latin 族永远排第一且必然可用，中文字体族从未生效——换中文字体族
        # 正文看起来毫无变化（字号仍会变，因为 pointSize 与族名解析无关）。
        font.setFamily(family)
        font.setPointSizeF(float(self.settings.font_size))
        font.setStyleHint(QFont.SansSerif)
        font.setBold(bool(self.settings.font_bold))
        font.setItalic(bool(self.settings.font_italic))
        font.setUnderline(bool(self.settings.font_underline))
        return font

    def _font_key(self) -> tuple:
        return (
            self.settings.font_family or DEFAULT_FONT_FAMILY,
            self.settings.font_latin,
            round(float(self.settings.font_size), 2),
            bool(self.settings.font_bold),
            bool(self.settings.font_italic),
            bool(self.settings.font_underline),
        )

    def _widget_font_matches(self, font: QFont) -> bool:
        """控件上的字体是否仍是用户设置。

        全局 QSS 的 ``QWidget { font-size }`` 会在每次 repolish（切主题 / 字体
        实时预览都会触发）后覆盖控件字体，只看 ``_applied_font`` 缓存会误判
        “已应用”，于是字号看起来莫名变小、调整失效。
        """
        actual = self.editor.font()
        size = float(actual.pointSizeF())
        if size <= 0.0 or abs(size - font.pointSizeF()) > 0.01:
            return False
        return (actual.family() == font.family()
                and actual.bold() == font.bold()
                and actual.italic() == font.italic()
                and actual.underline() == font.underline())

    def _remember_scroll(self, value: int) -> None:
        """记住有效的滚动位置（归零/重建时忽略），供热改字号恢复用。"""
        if value > 0:
            self._last_scroll = (int(value), int(self.editor.verticalScrollBar().maximum()))

    def _restore_format_after_undo(self) -> None:
        """撤销文字后把行高 / 段距按当前设置补回（排版本身不参与撤销）。"""
        self._applied_spacing = None
        self._applied_para = None
        self._apply_typography()

    def _keep_scroll_after_font_change(self, old_value: int, old_max: int) -> None:
        """字号变了文档高度会重算，Qt 会把滚动位置归零；范围刷新后按比例滚回原处。"""
        bar = self.editor.verticalScrollBar()

        def _restore(*_args) -> None:
            hi = bar.maximum()
            if hi <= 0:
                return
            ratio = (hi / old_max) if old_max > 0 else 1.0
            bar.setValue(min(hi, int(old_value * ratio)))

        def _cleanup() -> None:
            try:
                bar.rangeChanged.disconnect(_restore)
            except (RuntimeError, TypeError):
                pass

        bar.rangeChanged.connect(_restore)
        QTimer.singleShot(0, _restore)
        QTimer.singleShot(300, _cleanup)

    def _apply_typography(self) -> None:
        font = self._build_font()
        font_key = self._font_key()
        changed = font_key != self._applied_font
        stale = not self._widget_font_matches(font)
        spacing_key = round(float(self.settings.line_spacing), 2)
        para_key = (
            self.settings.para_align,
            int(self.settings.para_indent),
            round(float(self.settings.para_space_before), 2),
            round(float(self.settings.para_space_after), 2),
        )
        spacing_changed = spacing_key != self._applied_spacing
        para_changed = para_key != self._applied_para

        doc = self.editor.document()
        # 字体族/字号与行高、段距同批写入时先冻结布局，最后一次性打开：
        # 9 万字实测从“三遍全文重排 ~118ms”降到“一遍 ~56ms”，换字体族
        # 在下一帧就能看到新字形，不再需要切章或重开。
        bulk = bool(changed or stale or spacing_changed or para_changed)
        # 可重入计数 + finally 配平：只有最外层真正冻结/解冻，异常/嵌套都不会
        # 把文档永久留在“布局关闭”状态（关闭后正文既不重排也不响应输入）。
        self._layout_depth += 1
        frozen = False
        try:
            if bulk and self._layout_depth == 1 and doc.isLayoutEnabled():
                doc.setLayoutEnabled(False)
                frozen = True
            if changed or stale:
                bar = self.editor.verticalScrollBar()
                old_value, old_max = bar.value(), bar.maximum()
                self.editor.setFont(font)
                # 只改“文档默认字体”：正文不烘焙显式字体，已排版的文字会按新字号
                # 原生重排（实测 setDefaultFont 即触发），不必全文 mergeCharFormat。
                # 因此文档内容不被改动：撤销栈原样保留，Ctrl+Z 只撤文字不撤排版。
                doc.setDefaultFont(font)
                self._applied_font = font_key
                # 字号变化立即调整光标粗细（高度随新行高自动生效），不等重聚焦
                self.editor.adapt_caret()
                if changed and not self._loading:
                    # 行高与段距都由字号派生：字体变了，两者都要按新字重重算一次
                    self._applied_spacing = None
                    self._applied_para = None
                    if old_max <= 0 or old_value <= 0:
                        old_value, old_max = self._last_scroll
                    if old_max > 0 and old_value > 0:
                        # 正在滚动阅读时热改字号，别跳回文首
                        self._keep_scroll_after_font_change(old_value, old_max)

            if self._applied_spacing != spacing_key:
                self._applied_spacing = spacing_key
                # 行高与段距、缩进一次写入（见 _build_body_block_format），
                # 统一由下面的 _apply_paragraph_format 落盘。
                self._applied_para = None
            if para_changed or self._applied_para is None:
                self._apply_paragraph_format()
                self._applied_para = para_key
        finally:
            self._layout_depth = max(0, self._layout_depth - 1)
            if frozen or (self._layout_depth == 0 and not doc.isLayoutEnabled()):
                doc.setLayoutEnabled(True)
        if bulk:
            # 布局恢复后强制重绘一次，视口不会残留旧字形
            self.editor.viewport().update()

    def _run_format_edit(self, fn) -> None:
        """批量改段落格式。

        有撤销历史时合并成一步，避免清掉用户已有的 Ctrl+Z 记录；
        没有历史（刚载入章节）时临时关闭撤销记录，保持撤销栈为空。
        """
        document = self.editor.document()
        if document.isUndoAvailable():
            cursor = QTextCursor(document)
            cursor.beginEditBlock()
            try:
                fn()
            finally:
                cursor.endEditBlock()
        else:
            document.setUndoRedoEnabled(False)
            try:
                fn()
            finally:
                document.setUndoRedoEnabled(True)

    def _em_px(self) -> float:
        """字号 em 的逻辑像素值（pt × 逻辑 DPI / 72）。

        v1.4.4 起行距倍数以“字号 em”为基准，不再用 fontMetrics().height()：
        后者随字体自带行间距浮动（同一字号换字体行高就变），跨字体不可预测。
        Qt 逻辑坐标固定按 96dpi 计算（高 DPI 只体现在 devicePixelRatio），
        因此 12pt = 16 逻辑 px，125% 屏上 = 20 物理 px，与缩放无关。
        """
        points = float(self.editor.font().pointSizeF())
        if points <= 0:
            points = float(self.settings.font_size)
        dpi = float(self.editor.logicalDpiY() or 96.0)
        return points * dpi / 72.0

    def _row_height(self) -> float:
        """一个正文行的高度：字号 em × 行距（不是写死像素，也不取字体度量行高）。"""
        return self._em_px() * float(self.settings.line_spacing)

    def _natural_line_height(self) -> float:
        """Qt 单行文本的自然行高。

        QTextLine 的实际行盒取 max(ceil(ascent+descent), fontMetrics().lineSpacing())：
        真实 Windows 上雅黑 12pt 为 22（lineSpacing=21 向下取整），
        离屏平台则会直接采用 lineSpacing（如 23pt 为 36）。按同一口径取最大值，
        行高补足后空块/单行块整块高度才严格等于 _row_height()。
        """
        metrics = QFontMetricsF(self.editor.font(), self.editor)
        natural = max(
            float(math.ceil(metrics.ascent() + metrics.descent())),
            float(self.editor.fontMetrics().lineSpacing()),
        )
        return natural

    def _line_deficit(self) -> float:
        """首行的“行高补足”。

        Qt 的 QTextBlockFormat.setLineHeight 只作用于同一块内的**行间距离**，
        单行块与空块的首行仍是字体自然行高；这里把差额补到块的上边距，
        使单行正文块 / 空块的整块高度也严格等于 _row_height()。
        """
        return max(0.0, self._row_height() - self._natural_line_height())

    def _build_body_block_format(self) -> QTextBlockFormat:
        """正文段落的格式（对齐 / 首行缩进 / 段前段后），度量随字号与行高派生。"""
        fm = self.editor.fontMetrics()
        align = {
            "left": Qt.AlignLeft,
            "center": Qt.AlignHCenter,
            "right": Qt.AlignRight,
        }.get(self.settings.para_align, Qt.AlignLeft)
        row_h = self._row_height()
        block_format = QTextBlockFormat()
        block_format.setAlignment(align)
        # 行高与段距同属一套“字号派生”度量，一次 merge 全部写下去，
        # 避免改行距时对全文做两遍格式遍历（9 万字实测可省一半时间）。
        block_format.setLineHeight(
            row_h,
            QTextBlockFormat.LineHeightTypes.FixedHeight.value,
        )
        block_format.setTextIndent(
            float(fm.horizontalAdvance("字") * max(0, int(self.settings.para_indent)))
        )
        block_format.setTopMargin(
            self._line_deficit()
            + row_h * max(0.0, float(self.settings.para_space_before))
        )
        block_format.setBottomMargin(row_h * max(0.0, float(self.settings.para_space_after)))
        return block_format

    def _build_empty_block_format(self) -> QTextBlockFormat:
        """空段落：没有段前 / 段后距，首行缩进为 0；只保留行高补足。"""
        block_format = QTextBlockFormat(self._build_body_block_format())
        block_format.setTextIndent(0.0)
        block_format.setTopMargin(self._line_deficit())
        block_format.setBottomMargin(0.0)
        return block_format

    def _apply_paragraph_format(self) -> None:
        """给全文段落套用排版（对齐 / 首行缩进 / 段前段后间距）。

        度量全部基于正文当前字体与行高，因此自动跟随“字体 / 行距”设置；
        空段落单独清零段距与缩进，保证空行高度严格等于一个正文行高。
        """
        document = self.editor.document()
        bar = self.editor.verticalScrollBar()
        old_value, old_max = bar.value(), bar.maximum()
        if old_max <= 0 or old_value <= 0:
            old_value, old_max = self._last_scroll

        block_format = self._build_body_block_format()
        empty_format = self._build_empty_block_format()

        was_loading = self._loading
        self._loading = True
        try:
            def _format_all() -> None:
                cursor = QTextCursor(document)
                cursor.select(QTextCursor.Document)
                cursor.mergeBlockFormat(block_format)
                block = document.begin()
                while block.isValid():
                    if not block.text().strip():
                        QTextCursor(block).mergeBlockFormat(empty_format)
                    block = block.next()

            self._run_format_edit(_format_all)
        finally:
            self._loading = was_loading

        if old_max > 0 and old_value > 0:
            self._keep_scroll_after_font_change(old_value, old_max)
        else:
            bar.setValue(min(old_value, bar.maximum()))

    def _prepare_typing_block(self) -> None:
        """空块即将落笔：在插入文字“之前”换成正文段距。

        若等 textChanged 回调里再改（_sync_block_spacing），块格式命令会落在
        输入命令之后，把连续输入拆成两个撤销步——一次 Ctrl+Z 只撤掉后半段，
        第一个字撤不掉。这里把格式命令提前，保证整段输入仍是一步撤销。
        """
        if self._loading or self._applied_para is None:
            return
        block = self.editor.textCursor().block()
        if not block.isValid() or block.text().strip():
            return
        want = self._build_body_block_format()
        fmt = block.blockFormat()
        if (abs(fmt.topMargin() - want.topMargin()) < 0.05
                and abs(fmt.bottomMargin() - want.bottomMargin()) < 0.05
                and abs(fmt.textIndent() - want.textIndent()) < 0.05):
            return
        # 改块格式也会触发 textChanged；临时置 _loading 挡掉 _sync_block_spacing，
        # 否则空块会先被换成正文段距、又被判回空块，来回抖动并把撤销拆开
        was_loading = self._loading
        self._loading = True
        try:
            QTextCursor(block).mergeBlockFormat(want)
        finally:
            self._loading = was_loading

    def _sync_block_spacing(self) -> None:
        """把光标所在块的“空 / 非空”状态与段落格式对齐。

        空块清零段距与缩进（空行 = 一个正文行高）；开始输入文字后恢复正文段距。
        这段修正在 textChanged 内完成，与这次输入同属一步撤销。
        """
        if self._loading or self._applied_para is None:
            return
        block = self.editor.textCursor().block()
        if not block.isValid():
            return
        want = (self._build_body_block_format() if block.text().strip()
                else self._build_empty_block_format())
        fmt = block.blockFormat()
        if (abs(fmt.topMargin() - want.topMargin()) < 0.05
                and abs(fmt.bottomMargin() - want.bottomMargin()) < 0.05
                and abs(fmt.textIndent() - want.textIndent()) < 0.05):
            return
        # 并入“这次输入”的撤销步：否则第一次输入时把空块换成正文段距会另起一个
        # 撤销块，一次 Ctrl+Z 只撤掉后面几个字，第一个字撤不掉（排版污染撤销）。
        cursor = QTextCursor(block)
        cursor.joinPreviousEditBlock()
        cursor.mergeBlockFormat(want)
        cursor.endEditBlock()

    def apply_writing_background(self) -> None:
        """根据设置应用写作区背景：预设色块走 QSS，自定义图片走自绘。"""
        image = ""
        if self.settings.writing_theme == "custom" and self.settings.writing_image:
            image = backgrounds.resolve(self.settings.writing_image)
        self.paper.set_bg_image(image)

    # ------------------------------------------------------------------
    # 内部事件
    # ------------------------------------------------------------------
    def _on_text_changed(self) -> None:
        if self._loading or self.editor._undoing:
            return
        # 空块 / 正文块的段距与缩进随“空 / 非空”即时对齐
        self._sync_block_spacing()
        # 文本变化后查找高亮与内联标注位置全部失效：先清空，静止 500ms 后重扫当前章
        self.editor.set_search_selections([])
        self.editor.clear_inline_marks()
        if self._inline_enabled:
            self._inline_timer.start()
        # 只有键盘敲出来的字才计入码字速度与每日打字统计（粘贴 / 拖放不计）
        if not getattr(self.editor, "_paste_insert", False):
            self._pending_keyboard = True
        # 字数统计防抖：避免每个按键都全文 toPlainText + 逐字统计（长章节卡顿）
        self._count_timer.start()
        self.content_edited.emit()

    def _recount(self) -> None:
        """防抖后的字数重算（120ms 静止后执行一次）。"""
        text = self.editor.toPlainText()
        count = count_chars(text, self.settings.exclude_punct)
        delta = count - self._last_count
        self._last_count = count
        if delta > 0 and self._pending_keyboard:
            self.speed_delta.emit(delta)
            self.typed_delta.emit(delta)
            # 打字后尽快刷新时速面板（面板可见时），不必等 1 秒定时器
            if self.speed_panel.isVisible():
                self.speed_panel.notify_activity()
        self._pending_keyboard = False
        self.chapter_chars_changed.emit(count)
        self._update_status()

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

    # ------------------------------------------------------------------
    # 隐形快速滚动滑块（实现见 app/ui/scroll_float.py 的 ScrollFlasher）
    # ------------------------------------------------------------------
    def flash_scroll_handle(self) -> None:
        """滚轮滚动时显示滑块并同步到当前滚动位置，3 秒后自动隐藏。"""
        self._scroll_flasher.flash()

    def _position_floating(self) -> None:
        width = self.width()
        height = self.height()
        # 左下角保存指示器：贴左、往上避开状态栏
        self.save_spinner.move(14, height - 64)
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

    def _after_history_nav(self, before: str) -> None:
        """撤销 / 重做后补齐一次状态同步。

        ``NovelEditor.undo/redo`` 期间 ``_undoing`` 为真，``_on_text_changed``
        会直接返回：底部字数会停在撤销前的值，``content_edited`` 也不发，
        导致撤销后不再触发自动保存。这里在操作结束后补算一次字数并通知落盘；
        撤销不是键盘输入，不写入码字速度统计。
        """
        if self._loading or self.editor.toPlainText() == before:
            return
        self._sync_block_spacing()
        self.editor.set_search_selections([])
        self.editor.clear_inline_marks()
        if self._inline_enabled:
            self._inline_timer.start()
        self._count_timer.stop()
        self._pending_keyboard = False
        self._recount()
        self.content_edited.emit()

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

    def apply_replacements(self, ranges: list[tuple[int, int, str]]) -> int:
        """按 (起点, 长度, 替换文本) 批量替换正文；整批合并为一个撤销步骤。

        调用方负责保证位置与当前正文一致；从后往前替换避免偏移错位。
        """
        if not ranges:
            return 0
        document = self.editor.document()
        max_pos = max(0, document.characterCount() - 1)
        group = QTextCursor(document)
        group.beginEditBlock()
        applied = 0
        for start, length, replacement in sorted(ranges, key=lambda r: r[0], reverse=True):
            begin = max(0, min(start, max_pos))
            end = max(begin, min(begin + max(1, length), max_pos))
            cursor = QTextCursor(document)
            cursor.setPosition(begin)
            cursor.setPosition(end, QTextCursor.KeepAnchor)
            cursor.insertText(replacement)
            applied += 1
        group.endEditBlock()
        return applied

    def highlight_all(self, needle: str, case_sensitive: bool) -> int:
        """高亮当前章节中所有匹配项，返回匹配总数。"""
        self.editor.set_search_selections([])
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
        self.editor.set_search_selections(selections)
        return len(matches)

    def refresh_icons(self, factory: IconFactory) -> None:
        self.jump_top.setIcon(factory.icon("top", 16))
        self.jump_bottom.setIcon(factory.icon("bottom", 16))
        panel = getattr(self, "speed_panel", None)
        if panel is not None:
            panel.apply_icons(factory)
