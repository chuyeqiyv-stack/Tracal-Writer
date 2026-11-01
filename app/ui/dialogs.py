# -*- coding: utf-8 -*-
"""各类功能弹窗：新建作品、字体、写作主题、起名助手、查找替换等。"""
from __future__ import annotations

import os
import time

from PySide6.QtCore import QDate, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QKeySequence, QPainter, QPen, QPixmap, QShortcut, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app import config
from app.services import name_data
from app.services.search import find_all, line_number_at, line_snippet
from app.services.word_count import format_word_count
from app.storage import Storage
from app.ui.theme import WRITING_THEMES
from app.ui.widgets import CircleSwatch, ToastLabel


def _primary(text: str, parent=None) -> QPushButton:
    button = QPushButton(text, parent)
    button.setObjectName("Primary")
    button.setCursor(Qt.PointingHandCursor)
    return button


def _ghost(text: str, parent=None) -> QPushButton:
    button = QPushButton(text, parent)
    button.setCursor(Qt.PointingHandCursor)
    return button


def _add_buttons(dialog: QDialog, layout: QVBoxLayout, ok_text: str, on_ok) -> None:
    row = QHBoxLayout()
    row.setContentsMargins(0, 6, 0, 0)
    row.addStretch(1)
    cancel = _ghost("取消", dialog)
    cancel.clicked.connect(dialog.reject)
    row.addWidget(cancel)
    ok = _primary(ok_text, dialog)
    ok.clicked.connect(on_ok)
    row.addWidget(ok)
    layout.addLayout(row)


class MessageBox(QDialog):
    """统一样式的提示 / 确认弹窗：圆角卡片 + 阴影 + 中文按钮。

    取代原生 QMessageBox（原生样式无框无影、按钮在部分系统显示英文）。
    """

    def __init__(
        self,
        parent,
        title: str,
        text: str,
        ok_text: str = "确定",
        cancel_text: str = "",
    ) -> None:
        super().__init__(parent)
        self.choice = False
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setModal(True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 28, 28, 28)   # 给阴影留出空间

        card = QFrame(self)
        card.setObjectName("MessageBox")
        effect = QGraphicsDropShadowEffect(card)
        effect.setBlurRadius(36)
        effect.setOffset(0, 10)
        effect.setColor(QColor(24, 26, 32, 70))
        card.setGraphicsEffect(effect)
        outer.addWidget(card)

        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(26, 22, 26, 20)
        card_layout.setSpacing(12)

        title_label = QLabel(title, card)
        title_label.setObjectName("MsgTitle")
        card_layout.addWidget(title_label)

        text_label = QLabel(text, card)
        text_label.setObjectName("MsgText")
        text_label.setWordWrap(True)
        card_layout.addWidget(text_label)

        row = QHBoxLayout()
        row.setSpacing(10)
        row.addStretch(1)
        if cancel_text:
            cancel = _ghost(cancel_text, card)
            cancel.clicked.connect(self.reject)
            row.addWidget(cancel)
        ok = _primary(ok_text, card)
        ok.setMinimumWidth(88)
        ok.clicked.connect(self._accept_ok)
        row.addWidget(ok)
        card_layout.addLayout(row)

    def _accept_ok(self) -> None:
        self.choice = True
        self.accept()

    # 与 QMessageBox 同名静态方法，方便一行替换
    @staticmethod
    def information(parent, title: str, text: str) -> None:
        MessageBox(parent, title, text).exec()

    @staticmethod
    def warning(parent, title: str, text: str) -> None:
        MessageBox(parent, title, text, ok_text="知道了").exec()

    @staticmethod
    def question(parent, title: str, text: str) -> bool:
        box = MessageBox(parent, title, text, ok_text="确定", cancel_text="取消")
        box.exec()
        return box.choice


class CoverPickBox(QPushButton):
    """新建作品弹窗里的封面选择框：虚线边框，点击选择图片并预览。"""

    cover_changed = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._pixmap: QPixmap | None = None
        self.setFixedSize(126, 168)
        self.setCursor(Qt.PointingHandCursor)
        self.setFlat(True)
        self.clicked.connect(self._pick)

    def _pick(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self, "选择封面图片", "", "图片文件 (*.png *.jpg *.jpeg *.webp *.bmp)"
        )
        if path:
            self.set_image(path)
            self.cover_changed.emit(path)

    def set_image(self, path: str) -> None:
        pix = QPixmap(path) if path else QPixmap()
        self._pixmap = None if pix.isNull() else pix
        self.update()

    def clear_image(self) -> None:
        self._pixmap = None
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        rect = self.rect().adjusted(1, 1, -1, -1)

        if self._pixmap is not None:
            painter.drawPixmap(rect, self._pixmap)
            painter.setPen(QPen(QColor(0, 0, 0, 30), 1))
            painter.drawRect(rect.adjusted(0, 0, -1, -1))
        else:
            pen = QPen(QColor(QColor("#B9BEC7")), 1.4, Qt.DashLine)
            pen.setDashPattern([4, 3])
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(rect, 8, 8)
            painter.setPen(QPen(QColor("#8A9099")))
            font = QFont(self.font())
            font.setPixelSize(26)
            painter.setFont(font)
            painter.drawText(rect.adjusted(0, -18, 0, -18), Qt.AlignCenter, "＋")
            font.setPixelSize(11)
            painter.setFont(font)
            painter.drawText(rect.adjusted(0, 30, 0, 8), Qt.AlignHCenter | Qt.AlignTop, "添加封面")
        painter.end()


class NewBookDialog(QDialog):
    """新建作品：书名 / 作者 / 标签默认全空，封面真实选图并预览。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("新建作品")
        self.setModal(True)
        self.resize(470, 320)
        self.cover_path = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 18)
        layout.setSpacing(14)

        body = QHBoxLayout()
        body.setSpacing(18)

        # 左：封面选择
        cover_box = QVBoxLayout()
        cover_box.setSpacing(6)
        self.cover_box = CoverPickBox(self)
        # 选图后把路径同步到对话框（此前漏接信号导致新书永远显示默认封面）
        self.cover_box.cover_changed.connect(self._on_cover_picked)
        cover_box.addWidget(self.cover_box)
        clear_button = _ghost("清除封面", self)
        clear_button.clicked.connect(self._clear_cover)
        cover_box.addWidget(clear_button)
        body.addLayout(cover_box)

        # 右：表单
        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignRight)
        self.title_edit = QLineEdit(self)
        self.title_edit.setPlaceholderText("请输入书名")
        self.author_edit = QLineEdit(self)
        self.author_edit.setPlaceholderText("请输入作者名")
        self.tags_edit = QLineEdit(self)
        self.tags_edit.setPlaceholderText("用空格或逗号分隔，如：玄幻 穿越")
        form.addRow("书名", self.title_edit)
        form.addRow("作者", self.author_edit)
        form.addRow("标签", self.tags_edit)
        body.addLayout(form)
        layout.addLayout(body, 1)

        hint = QLabel("新书将包含「第一卷 / 第一章」，可在左侧目录中继续添加。", self)
        hint.setObjectName("DrawerHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        _add_buttons(self, layout, "创建", self._on_ok)
        self.title_edit.setFocus()

    def _on_cover_picked(self, path: str) -> None:
        self.cover_path = path

    def _clear_cover(self) -> None:
        self.cover_path = ""
        self.cover_box.clear_image()

    def _on_ok(self) -> None:
        if not self.title_edit.text().strip():
            MessageBox.warning(self, "提示", "书名不能为空。")
            self.title_edit.setFocus()
            return
        self.accept()

    def values(self) -> tuple[str, str, list[str], str]:
        """返回 (书名, 作者, 标签列表, 封面图片路径)。"""
        raw = self.tags_edit.text().strip().replace("，", " ").replace(",", " ")
        tags = [t for t in raw.split() if t]
        return self.title_edit.text().strip(), self.author_edit.text().strip(), tags, self.cover_path


# 字号下拉选项：8.0 ~ 32.0，步进 0.5
FONT_SIZE_OPTIONS = [f"{8 + i * 0.5:.1f}" for i in range(49)]
# 默认字号（下拉里该项显示「默认」）
FONT_SIZE_DEFAULT = 16.0

# 文字颜色预设（字体面板颜色块按钮的弹出菜单）
PRESET_TEXT_COLORS = [
    ("#1F2329", "近黑"),
    ("#4E5969", "深灰"),
    ("#C2413B", "砖红"),
    ("#C4761F", "橘橙"),
    ("#8C6B1F", "棕黄"),
    ("#2F7D4F", "松绿"),
    ("#2F5FA8", "湖蓝"),
    ("#7C4DBE", "藤紫"),
]


class FontDialog(QDialog):
    """字体设置（右侧窄面板）：字体、大小与颜色、加粗 / 斜体 / 下划线。

    任何改动都会通过 live_changed 实时广播，主窗口立即应用到正文预览
    （确定才落盘，取消还原）。
    """

    fonts_imported = Signal(list)  # 新导入的字体文件路径
    live_changed = Signal(str, float, str, bool, bool, bool)
    #                (字体族, 字号, 文字颜色, 加粗, 斜体, 下划线)

    def __init__(
        self,
        parent,
        family: str,
        size: float,
        color: str,
        bold: bool,
        italic: bool,
        underline: bool,
        imported: list[str],
    ) -> None:
        super().__init__(parent)
        self._new_fonts: list[str] = []
        self._color = str(color or "")
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setModal(True)
        self.setFixedWidth(372)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 28, 28, 28)   # 给阴影留出空间

        card = QFrame(self)
        card.setObjectName("MessageBox")
        effect = QGraphicsDropShadowEffect(card)   # 构造时挂一次（静态安全）
        effect.setBlurRadius(36)
        effect.setOffset(0, 10)
        effect.setColor(QColor(24, 26, 32, 70))
        card.setGraphicsEffect(effect)
        outer.addWidget(card)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(12)

        # ---- 标题行：字体 + 关闭 ----
        head = QHBoxLayout()
        title = QLabel("字体", card)
        title.setObjectName("MsgTitle")
        head.addWidget(title)
        head.addStretch(1)
        close_button = _ghost("×", card)
        close_button.setFixedSize(30, 30)
        close_button.setToolTip("关闭（不保存修改）")
        close_button.clicked.connect(self.reject)
        head.addWidget(close_button)
        layout.addLayout(head)

        # ---- 编辑器字体 ----
        cap_font = QLabel("编辑器字体", card)
        cap_font.setObjectName("DrawerHint")
        layout.addWidget(cap_font)
        self.family_combo = QComboBox(card)
        self._fill_family_combo(str(family or ""))
        layout.addWidget(self.family_combo)

        # ---- 大小与颜色 ----
        cap_size = QLabel("大小与颜色", card)
        cap_size.setObjectName("DrawerHint")
        layout.addWidget(cap_size)
        size_row = QHBoxLayout()
        size_row.setSpacing(8)
        self.size_combo = QComboBox(card)
        self._fill_size_combo(float(size))
        size_row.addWidget(self.size_combo, 1)
        self.color_button = QToolButton(card)
        self.color_button.setObjectName("ColorSwatch")
        self.color_button.setFixedSize(44, 34)
        self.color_button.setCursor(Qt.PointingHandCursor)
        self.color_button.clicked.connect(self._show_color_menu)
        size_row.addWidget(self.color_button)
        layout.addLayout(size_row)

        # ---- 字体样式 ----
        cap_style = QLabel("字体样式", card)
        cap_style.setObjectName("DrawerHint")
        layout.addWidget(cap_style)
        style_row = QHBoxLayout()
        style_row.setSpacing(8)
        self.bold_button = self._make_style_button("B", bold)
        self.italic_button = self._make_style_button("I", italic)
        self.underline_button = self._make_style_button("U", underline)
        italic_font = self.italic_button.font()
        italic_font.setItalic(True)
        self.italic_button.setFont(italic_font)
        underline_font = self.underline_button.font()
        underline_font.setUnderline(True)
        self.underline_button.setFont(underline_font)
        bold_font = self.bold_button.font()
        bold_font.setBold(True)
        self.bold_button.setFont(bold_font)
        for button in (self.bold_button, self.italic_button, self.underline_button):
            style_row.addWidget(button)
        style_row.addStretch(1)
        layout.addLayout(style_row)

        layout.addStretch(1)

        # ---- 本地字体导入（保留原功能） ----
        import_row = QHBoxLayout()
        self.imported_label = QLabel(
            f"已导入 {len(imported)} 个本地字体" if imported else "尚未导入本地字体", card
        )
        self.imported_label.setObjectName("DrawerHint")
        import_button = _ghost("导入本地字体…", card)
        import_button.clicked.connect(self._import_font)
        import_row.addWidget(self.imported_label, 1)
        import_row.addWidget(import_button)
        layout.addLayout(import_row)

        # 确定 = 保存并关闭；取消 / × = 还原为打开前的字体
        _add_buttons(self, layout, "确定", self.accept)

        # 实时预览：任何改动立即广播
        self.family_combo.currentIndexChanged.connect(self._emit_live)
        self.size_combo.currentIndexChanged.connect(self._emit_live)
        self.bold_button.toggled.connect(self._emit_live)
        self.italic_button.toggled.connect(self._emit_live)
        self.underline_button.toggled.connect(self._emit_live)
        self._update_swatch()

    # ------------------------------------------------------------------
    def _fill_family_combo(self, family: str) -> None:
        """首项「默认字体」+ 常用中文字体 + 其余系统字体。"""
        combo = self.family_combo
        combo.addItem("默认字体", "")
        added = {""}
        for name in FontDialog._cn_presets():
            if name not in added:
                combo.addItem(name, name)
                added.add(name)
        for name in QFontDatabase.families():
            if name not in added:
                combo.addItem(name, name)
                added.add(name)
        index = combo.findData(family)
        if index < 0:
            index = combo.findText(family)
        combo.setCurrentIndex(max(0, index))

    @staticmethod
    def _cn_presets() -> list[str]:
        presets = ["等线", "仿宋", "黑体", "华文中宋", "楷体", "宋体", "微软雅黑", "隶书", "幼圆"]
        families = set(QFontDatabase.families())
        return [name for name in presets if name in families]

    def _fill_size_combo(self, size: float) -> None:
        """数字字号下拉（0.5 步进）；默认字号一项显示「默认」，超范围值插到最前。"""
        combo = self.size_combo
        size = max(5.0, min(72.0, float(size)))
        text = f"{size:.1f}"
        if text not in FONT_SIZE_OPTIONS:
            combo.addItem(f"{text}（当前）", size)
        for opt in FONT_SIZE_OPTIONS:
            value = float(opt)
            label = f"{opt} 默认" if abs(value - FONT_SIZE_DEFAULT) < 1e-9 else opt
            combo.addItem(label, value)
        index = combo.findData(size)
        combo.setCurrentIndex(max(0, index))

    def _make_style_button(self, text: str, checked: bool) -> QToolButton:
        button = QToolButton(self)
        button.setObjectName("StyleButton")
        button.setText(text)
        button.setCheckable(True)
        button.setFixedSize(44, 34)
        button.setCursor(Qt.PointingHandCursor)
        button.setChecked(bool(checked))
        return button

    # ------------------------------------------------------------------
    def _current_family(self) -> str:
        return str(self.family_combo.currentData() or "")

    def _current_size(self) -> float:
        data = self.size_combo.currentData()
        try:
            return max(5.0, min(72.0, float(data)))
        except (TypeError, ValueError):
            return FONT_SIZE_DEFAULT

    def _emit_live(self, *_args) -> None:
        self.live_changed.emit(
            self._current_family(),
            self._current_size(),
            self._color,
            self.bold_button.isChecked(),
            self.italic_button.isChecked(),
            self.underline_button.isChecked(),
        )

    def values(self) -> tuple[str, float, str, bool, bool, bool]:
        return (
            self._current_family(),
            self._current_size(),
            self._color,
            self.bold_button.isChecked(),
            self.italic_button.isChecked(),
            self.underline_button.isChecked(),
        )

    # ------------------------------------------------------------------
    # 颜色块
    # ------------------------------------------------------------------
    def _update_swatch(self) -> None:
        """颜色块按钮显示当前文字颜色（空 = 跟随主题，显示近黑）。"""
        color = QColor(self._color) if self._color else QColor("#1F2329")
        self.color_button.setStyleSheet(
            "QToolButton#ColorSwatch{"
            f"background:{color.name()};"
            "border:1px solid rgba(0,0,0,28);border-radius:6px;}"
        )
        self.color_button.setToolTip("文字颜色：" + ("跟随主题" if not self._color else color.name()))

    def _set_color(self, color: str) -> None:
        self._color = color
        self._update_swatch()
        self._emit_live()

    def _show_color_menu(self) -> None:
        menu = QMenu(self)
        follow = menu.addAction("跟随主题")
        follow.setCheckable(True)
        follow.setChecked(self._color == "")
        follow.triggered.connect(lambda: self._set_color(""))
        menu.addSeparator()
        for hex_color, name in PRESET_TEXT_COLORS:
            action = menu.addAction(name)
            pix = QPixmap(12, 12)
            pix.fill(QColor(hex_color))
            action.setIcon(QIcon(pix))
            action.setCheckable(True)
            action.setChecked(self._color == hex_color)
            action.triggered.connect(lambda _c=False, v=hex_color: self._set_color(v))
        menu.addSeparator()
        menu.addAction("自定义颜色…", self._pick_custom_color)
        menu.exec(self.color_button.mapToGlobal(self.color_button.rect().bottomLeft()))

    def _pick_custom_color(self) -> None:
        from PySide6.QtWidgets import QColorDialog

        initial = QColor(self._color) if self._color else QColor("#1F2329")
        color = QColorDialog.getColor(initial, self, "选择文字颜色")
        if color.isValid():
            self._set_color(color.name())

    # ------------------------------------------------------------------
    def _import_font(self) -> None:
        paths, _filter = QFileDialog.getOpenFileNames(
            self, "导入字体文件", "", "字体文件 (*.ttf *.otf *.ttc)"
        )
        added = 0
        for path in paths:
            font_id = QFontDatabase.addApplicationFont(path)
            if font_id >= 0:
                self._new_fonts.append(path)
                added += 1
        if added:
            total = len(self._new_fonts)
            self.imported_label.setText(f"本次导入 {added} 个，会话累计 {total} 个")
            self.fonts_imported.emit(list(self._new_fonts))
            MessageBox.information(self, "导入成功", f"已加载 {added} 个字体文件，重启后仍会自动加载。")
        else:
            MessageBox.warning(self, "导入失败", "没有可用的字体文件（支持 .ttf / .otf / .ttc）。")


class LineSpacingDialog(QDialog):
    """行距设置。"""

    def __init__(self, parent, spacing: float) -> None:
        super().__init__(parent)
        self.setWindowTitle("行距设置")
        self.setModal(True)
        self.resize(430, 240)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 18)
        layout.setSpacing(14)

        self.value_label = QLabel(self)
        self.value_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.value_label)

        self.slider = QSlider(Qt.Horizontal, self)
        self.slider.setRange(120, 300)
        self.slider.setSingleStep(5)
        self.slider.setValue(int(round(float(spacing) * 100)))
        self.slider.valueChanged.connect(self._update_label)
        layout.addWidget(self.slider)

        sample = QLabel(
            "床前明月光，疑是地上霜。\n举头望明月，低头思故乡。", self
        )
        sample.setAlignment(Qt.AlignCenter)
        sample.setObjectName("DrawerHint")
        layout.addWidget(sample)

        _add_buttons(self, layout, "应用", self.accept)
        self._update_label()

    def _update_label(self) -> None:
        self.value_label.setText(f"当前行距：{self.slider.value() / 100:.2f} 倍")

    def value(self) -> float:
        return round(self.slider.value() / 100, 2)


class FindReplaceDialog(QDialog):
    """查找替换小窗，支持当前章节与全书。"""

    def __init__(self, window, parent=None) -> None:
        super().__init__(parent)
        self.window = window
        self.setWindowTitle("查找替换")
        self.setModal(False)
        self.resize(560, 300)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 16)
        layout.setSpacing(10)

        self.find_edit = QLineEdit(self)
        self.find_edit.setPlaceholderText("查找内容")
        self.replace_edit = QLineEdit(self)
        self.replace_edit.setPlaceholderText("替换为（可留空表示删除）")
        layout.addWidget(self.find_edit)
        layout.addWidget(self.replace_edit)

        options = QHBoxLayout()
        options.setContentsMargins(0, 0, 0, 0)
        self.scope_combo = QComboBox(self)
        self.scope_combo.addItems(["当前章节", "全书"])
        self.case_box = QCheckBox("区分大小写", self)
        options.addWidget(QLabel("范围", self))
        options.addWidget(self.scope_combo)
        options.addSpacing(8)
        options.addWidget(self.case_box)
        options.addStretch(1)
        layout.addLayout(options)

        self.status_label = QLabel("", self)
        self.status_label.setObjectName("DrawerHint")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 4, 0, 0)
        actions.setSpacing(8)
        find_next = _ghost("查找下一个", self)
        find_next.clicked.connect(lambda: self.find_next())
        replace_one = _ghost("替换", self)
        replace_one.clicked.connect(self.replace_current)
        replace_all = _primary("全部替换", self)
        replace_all.clicked.connect(self.replace_all)
        close_button = _ghost("关闭", self)
        close_button.clicked.connect(self.close)
        actions.addWidget(find_next)
        actions.addWidget(replace_one)
        actions.addWidget(replace_all)
        actions.addStretch(1)
        actions.addWidget(close_button)
        layout.addLayout(actions)

        self.find_edit.returnPressed.connect(lambda: self.find_next())
        self.replace_edit.returnPressed.connect(self.replace_current)

    # ------------------------------------------------------------------
    def _inputs(self) -> tuple[str, str, bool]:
        return (
            self.find_edit.text(),
            self.replace_edit.text(),
            self.case_box.isChecked(),
        )

    def find_next(self, forward: bool = True) -> None:
        needle, _, case = self._inputs()
        if not needle:
            self._status("请输入要查找的内容。")
            return
        page = self.window.editor_page
        if page.find_next(needle, case, forward):
            page.highlight_all(needle, case)
            self._status(f"已定位到「{needle}」。")
            return
        if self.scope_combo.currentIndex() == 1:
            if self.window.find_next_in_book(needle, case):
                self.window.editor_page.highlight_all(needle, case)
                self._status(f"已在下一章找到「{needle}」。")
                return
        self._status(f"未找到「{needle}」。")

    def replace_current(self) -> None:
        needle, replacement, case = self._inputs()
        if not needle:
            self._status("请输入要查找的内容。")
            return
        page = self.window.editor_page
        cursor = page.editor.textCursor()
        selected = cursor.selectedText().replace("\u2029", "\n")
        matched = selected == needle if case else selected.lower() == needle.lower()
        if matched and page.replace_selection(replacement):
            self._status("已替换 1 处。")
            page.highlight_all(needle, case)
            return
        self.find_next()

    def replace_all(self) -> None:
        needle, replacement, case = self._inputs()
        if not needle:
            self._status("请输入要查找的内容。")
            return
        whole_book = self.scope_combo.currentIndex() == 1
        count, chapters = self.window.replace_all(needle, replacement, case, whole_book)
        if count:
            self._status(f"已替换 {count} 处，涉及 {chapters} 个章节。")
        else:
            self._status(f"未找到「{needle}」。")
        self.window.editor_page.highlight_all(needle, case)

    def _status(self, text: str) -> None:
        self.status_label.setText(text)

    def refresh(self) -> None:
        """每次打开时重置状态并聚焦查找框。"""
        self.status_label.setText("")
        self.find_edit.setFocus()
        self.find_edit.selectAll()


class GlobalSearchDialog(QDialog):
    """全书搜索，结果显示章节与行号，双击可定位。"""

    def __init__(self, window, parent=None) -> None:
        super().__init__(parent)
        self.window = window
        self._results: list[dict] = []
        self.setWindowTitle("全局搜索")
        self.setModal(False)
        self.resize(600, 480)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 16)
        layout.setSpacing(10)

        search_row = QHBoxLayout()
        search_row.setContentsMargins(0, 0, 0, 0)
        self.search_edit = QLineEdit(self)
        self.search_edit.setPlaceholderText("搜索全书内容…")
        search_button = _primary("搜索", self)
        search_button.clicked.connect(self.do_search)
        search_row.addWidget(self.search_edit, 1)
        search_row.addWidget(search_button)
        layout.addLayout(search_row)

        self.count_label = QLabel("输入关键词后按回车，即可在全书范围内查找。", self)
        self.count_label.setObjectName("DrawerHint")
        layout.addWidget(self.count_label)

        self.results = QListWidget(self)
        self.results.setFrameShape(QFrame.NoFrame)
        self.results.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.results.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        from app.ui.scroll_float import ScrollFlasher

        self._results_flasher = ScrollFlasher(self.results, Qt.Vertical, inset=6)
        self.results.itemDoubleClicked.connect(self._open_result)
        layout.addWidget(self.results, 1)

        self.preview = QPlainTextEdit(self)
        self.preview.setObjectName("Preview")
        self.preview.setReadOnly(True)
        self.preview.setFixedHeight(92)
        layout.addWidget(self.preview)

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        close_button = _ghost("关闭", self)
        close_button.clicked.connect(self.close)
        bottom.addWidget(close_button)
        layout.addLayout(bottom)

        self.search_edit.returnPressed.connect(self.do_search)
        self.results.currentRowChanged.connect(self._show_preview)
        self.results.itemClicked.connect(self._on_item_clicked)

    # ------------------------------------------------------------------
    def do_search(self) -> None:
        needle = self.search_edit.text().strip()
        self.results.clear()
        self.preview.setPlainText("")
        self._results = []
        if not needle:
            self.count_label.setText("请输入关键词。")
            return
        book = self.window.book
        if book is None:
            self.count_label.setText("还没有打开书籍。")
            return

        total = 0
        limited = False
        for chapter in book.all_chapters():
            matches = find_all(chapter.content, needle, False)
            for start, length in matches:
                total += 1
                if len(self._results) >= 300:
                    limited = True
                    continue
                line = line_number_at(chapter.content, start)
                snippet = line_snippet(chapter.content, start)
                item = QListWidgetItem(f"{chapter.title}  ·  第 {line} 行\n{snippet}")
                item.setData(Qt.UserRole, (chapter.id, start, length))
                self.results.addItem(item)
                self._results.append(
                    {
                        "chapter_id": chapter.id,
                        "start": start,
                        "length": length,
                        "snippet": snippet,
                    }
                )

        if total:
            text = f"共找到 {total} 处匹配"
            if limited:
                text += "（仅显示前 300 条）"
            self.count_label.setText(text)
            self.results.setCurrentRow(0)
        else:
            self.count_label.setText("没有找到匹配内容。")

    def _show_preview(self, row: int) -> None:
        if row < 0 or row >= len(self._results):
            self.preview.setPlainText("")
            return
        self.preview.setPlainText(self._results[row]["snippet"])

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        self._open_result(item)

    def _open_result(self, item: QListWidgetItem) -> None:
        data = item.data(Qt.UserRole)
        if not data:
            return
        chapter_id, start, length = data
        self.window.jump_to_match(chapter_id, int(start), int(length))

    def refresh(self) -> None:
        self.search_edit.setFocus()
        self.search_edit.selectAll()


class HistoryDialog(QDialog):
    """历史版本列表与预览。"""

    def __init__(self, storage: Storage, book_id: str, chapter_id: str, title: str, parent=None) -> None:
        super().__init__(parent)
        self.storage = storage
        self.book_id = book_id
        self.chapter_id = chapter_id
        self.snapshots = storage.list_snapshots(book_id, chapter_id)
        self.setWindowTitle(f"历史版本 · {title}")
        self.setModal(True)
        self.resize(640, 500)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 16)
        layout.setSpacing(10)

        hint = QLabel(
            "写作时每次自动保存都会记录一个历史版本，每本书最多保留 "
            f"{config.MAX_SNAPSHOTS} 个（获得「坚持不懈」成就后 +2）。选择后可恢复到对应版本。",
            self,
        )
        hint.setObjectName("DrawerHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.list = QListWidget(self)
        self.list.setFrameShape(QFrame.NoFrame)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        from app.ui.scroll_float import ScrollFlasher

        self._list_flasher = ScrollFlasher(self.list, Qt.Vertical, inset=6)
        for snapshot in self.snapshots:
            chars = int(snapshot.get("chars") or 0)
            item = QListWidgetItem(
                f"{self.storage.snapshot_time_text(snapshot)}   ·   {format_word_count(chars)} 字"
            )
            item.setData(Qt.UserRole, snapshot)
            self.list.addItem(item)
        layout.addWidget(self.list, 1)

        self.preview = QPlainTextEdit(self)
        self.preview.setObjectName("Preview")
        self.preview.setReadOnly(True)
        self.preview.setPlaceholderText("选择左侧版本后，这里会显示当时的正文。")
        layout.addWidget(self.preview, 1)

        self.restore_button = _primary("恢复此版本", self)
        self.restore_button.clicked.connect(self.accept)
        self.restore_button.setEnabled(bool(self.snapshots))

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        close_button = _ghost("关闭", self)
        close_button.clicked.connect(self.reject)
        bottom.addWidget(close_button)
        bottom.addWidget(self.restore_button)
        layout.addLayout(bottom)

        self.list.currentRowChanged.connect(self._show_preview)
        if self.snapshots:
            self.list.setCurrentRow(0)
        else:
            hint.setText("当前章节还没有历史快照，继续写作后会自动生成。")

    def _show_preview(self, row: int) -> None:
        if row < 0 or row >= len(self.snapshots):
            self.preview.setPlainText("")
            return
        content = self.storage.snapshot_content(self.snapshots[row])
        self.preview.setPlainText(content[:4000])

    def selected_content(self) -> str | None:
        row = self.list.currentRow()
        if row < 0 or row >= len(self.snapshots):
            return None
        return self.storage.snapshot_content(self.snapshots[row])


class NameGeneratorDialog(QDialog):
    """起名助手：左 40% 筛选，右 60% 结果（3 列网格，点击复制）。"""

    KIND_TABS = ["人物", "地点"]
    STYLE_KEYS = [("cn", "中文"), ("west", "西方"), ("jp", "日式")]
    GENDER_KEYS = [("any", "不限"), ("male", "男"), ("female", "女")]

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("起名助手")
        self.setModal(True)
        self.resize(860, 560)
        self._kind = "person"

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ============ 左侧筛选区（40%） ============
        left_frame = QFrame(self)
        left_frame.setObjectName("NameGenFilter")
        left_frame.setFixedWidth(int(860 * 0.40))
        left = QVBoxLayout(left_frame)
        left.setContentsMargins(20, 20, 20, 20)
        left.setSpacing(14)

        title = QLabel("起名助手", left_frame)
        title.setObjectName("NameGenTitle")
        left.addWidget(title)

        # 类型 Tab：人物 / 地点
        tab_row = QHBoxLayout()
        tab_row.setSpacing(6)
        self._tab_buttons: list[QPushButton] = []
        for index, tab in enumerate(self.KIND_TABS):
            button = QPushButton(tab, left_frame)
            button.setObjectName("TabButton")
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setChecked(index == 0)
            button.clicked.connect(lambda _c=False, i=index: self._switch_kind(i))
            tab_row.addWidget(button)
            self._tab_buttons.append(button)
        left.addLayout(tab_row)

        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignRight)

        # 类型（中西日）
        style_row = QHBoxLayout()
        style_row.setSpacing(6)
        self._style_buttons: dict[str, QPushButton] = {}
        for key, text in self.STYLE_KEYS:
            button = QPushButton(text, left_frame)
            button.setObjectName("ViewChip")
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setChecked(key == "cn")
            button.clicked.connect(
                lambda _c=False, k=key: self._set_style(k)
            )
            style_row.addWidget(button)
            self._style_buttons[key] = button
        form.addRow("类型", style_row)

        # 性别
        gender_row = QHBoxLayout()
        gender_row.setSpacing(6)
        self._gender_buttons: dict[str, QPushButton] = {}
        for key, text in self.GENDER_KEYS:
            button = QPushButton(text, left_frame)
            button.setObjectName("ViewChip")
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setChecked(key == "any")
            button.clicked.connect(
                lambda _c=False, k=key: self._set_gender(k)
            )
            gender_row.addWidget(button)
            self._gender_buttons[key] = button
        form.addRow("性别", gender_row)

        self.surname_edit = QLineEdit(left_frame)
        self.surname_edit.setPlaceholderText("留空则随机")
        self.given_edit = QLineEdit(left_frame)
        self.given_edit.setPlaceholderText("留空则随机")
        form.addRow("指定姓氏", self.surname_edit)
        form.addRow("指定名字", self.given_edit)
        left.addLayout(form)

        self.gender_row_widget = gender_row.parentWidget()
        self._style = "cn"
        self._gender = "any"

        left.addStretch(1)
        self.generate_button = QPushButton("生成", left_frame)
        self.generate_button.setObjectName("Primary")
        self.generate_button.setFixedHeight(40)
        self.generate_button.setCursor(Qt.PointingHandCursor)
        self.generate_button.clicked.connect(self.generate)
        left.addWidget(self.generate_button)
        layout.addWidget(left_frame)

        # ============ 右侧结果区（60%） ============
        right_frame = QFrame(self)
        right = QVBoxLayout(right_frame)
        right.setContentsMargins(18, 20, 18, 16)
        right.setSpacing(10)

        result_header = QHBoxLayout()
        self.result_hint = QLabel("点击任意名字即可复制", right_frame)
        self.result_hint.setObjectName("DrawerHint")
        refresh_button = _ghost("换一批", right_frame)
        refresh_button.clicked.connect(self.generate)
        result_header.addWidget(self.result_hint, 1)
        result_header.addWidget(refresh_button)
        right.addLayout(result_header)

        self._grid_widget = QWidget(right_frame)
        self._grid = QGridLayout(self._grid_widget)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(10)
        self._grid.setVerticalSpacing(10)

        scroll = QScrollArea(right_frame)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        from app.ui.scroll_float import ScrollFlasher

        self._grid_flasher = ScrollFlasher(scroll, Qt.Vertical, inset=6)
        scroll.setWidget(self._grid_widget)
        right.addWidget(scroll, 1)

        self.toast = ToastLabel(right_frame)
        layout.addWidget(right_frame, 1)

        self.generate()

    # ------------------------------------------------------------------
    def _switch_kind(self, index: int) -> None:
        self._kind = "person" if index == 0 else "place"
        for i, button in enumerate(self._tab_buttons):
            button.setChecked(i == index)
        # 性别只对人物有意义
        for button in self._gender_buttons.values():
            button.setEnabled(self._kind == "person")
        self.generate()

    def _set_style(self, key: str) -> None:
        self._style = key
        for k, button in self._style_buttons.items():
            button.setChecked(k == key)
        self.generate()

    def _set_gender(self, key: str) -> None:
        self._gender = key
        for k, button in self._gender_buttons.items():
            button.setChecked(k == key)
        self.generate()

    def generate(self) -> None:
        names = name_data.generate_names(
            kind=self._kind,
            style=self._style,
            gender=self._gender,
            surname=self.surname_edit.text(),
            given=self.given_edit.text(),
            count=21,
        )
        # 清空旧结果
        while self._grid.count():
            item = self._grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        columns = 3
        for index, name in enumerate(names):
            chip = QPushButton(name, self._grid_widget)
            chip.setObjectName("NameChip")
            chip.setCursor(Qt.PointingHandCursor)
            chip.setMinimumHeight(44)
            chip.setToolTip("点击复制")
            chip.clicked.connect(lambda _c=False, n=name: self._copy(n))
            row, col = divmod(index, columns)
            self._grid.addWidget(chip, row, col)
        for col in range(columns):
            self._grid.setColumnStretch(col, 1)
        self.result_hint.setText(f"已生成 {len(names)} 个名字 · 点击即可复制")

    def _copy(self, name: str) -> None:
        QApplication.clipboard().setText(name)
        self.toast.show_message(f"已复制：{name}")


class WritingThemeDialog(QDialog):
    """写作主题面板：网格圆形色块（全部预设）+ 自定义背景图片。"""

    theme_applied = Signal(str, str)  # (writing_theme, writing_image)

    COLUMNS = 6  # 每行 6 个圆形色块

    def __init__(self, parent, settings) -> None:
        super().__init__(parent)
        self._theme = settings.writing_theme
        self._image = settings.writing_image
        self.setWindowTitle("写作主题")
        self.setModal(True)
        self.resize(560, 520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 18)
        layout.setSpacing(14)

        title = QLabel("写作区背景", self)
        title.setObjectName("NameGenTitle")
        layout.addWidget(title)

        # 圆形色块网格：遍历全部预设主题
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(4)
        grid.setVerticalSpacing(8)
        self._swatches: list[CircleSwatch] = []
        for index, key in enumerate(WRITING_THEMES.keys()):
            info = WRITING_THEMES[key]
            color = info["bg"] or ("#F2F3F5" if key == "auto" else "#FFFFFF")
            swatch = CircleSwatch(info["name"], color, key == self._theme, self)
            swatch.clicked.connect(lambda _c=False, k=key: self._apply(k))
            row, col = divmod(index, self.COLUMNS)
            grid.addWidget(swatch, row, col, Qt.AlignHCenter)
            self._swatches.append(swatch)
        for col in range(self.COLUMNS):
            grid.setColumnStretch(col, 1)
        layout.addLayout(grid)

        image_row = QHBoxLayout()
        self.image_label = QLabel(
            self._image if self._image else "未使用自定义图片背景", self
        )
        self.image_label.setObjectName("DrawerHint")
        self.image_button = _ghost("上传自定义图片…", self)
        self.image_button.clicked.connect(self._pick_image)
        image_row.addWidget(self.image_label, 1)
        image_row.addWidget(self.image_button)
        layout.addLayout(image_row)

        _add_buttons(self, layout, "完成", self.accept)

    def _apply(self, key: str) -> None:
        self._theme = key
        for swatch in self._swatches:
            swatch.set_selected(False)
        sender = self.sender()
        if isinstance(sender, CircleSwatch):
            sender.set_selected(True)
        self.theme_applied.emit(self._theme, self._image)

    def _pick_image(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self, "选择背景图片", "", "图片文件 (*.png *.jpg *.jpeg *.webp *.bmp)"
        )
        if not path:
            return
        self._image = path
        self._theme = "custom"
        self.image_label.setText(path)
        for swatch in self._swatches:
            swatch.set_selected(False)
        self.theme_applied.emit(self._theme, self._image)

    def values(self) -> tuple[str, str]:
        return self._theme, self._image


class StatsDialog(QDialog):
    """字数统计：最上面是累计打字，下面按日历显示每天打了多少字。"""

    def __init__(
        self,
        parent,
        storage: Storage,
        book_title: str = "",
        total: int = 0,
        total_no_punct: int = 0,
        chapter_title: str = "",
        chapter_chars: int = 0,
        chapter_count: int = 0,
        volume_count: int = 0,
        show_book_stats: bool = True,
    ) -> None:
        super().__init__(parent)
        self.storage = storage
        self.words = storage.load_words()
        self._month = QDate.currentDate()
        self.setWindowTitle(book_title or ("打字统计" if not show_book_stats else "字数统计"))
        self.setModal(True)
        self.resize(520, 640 if show_book_stats else 560)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 18)
        layout.setSpacing(14)

        title = QLabel(book_title or "打字统计", self)
        title.setObjectName("EditorTitle")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        # ---- 累计打字 ----
        total_card = QFrame(self)
        total_card.setObjectName("EditorPaper")
        total_layout = QVBoxLayout(total_card)
        total_layout.setContentsMargins(22, 16, 22, 16)
        total_layout.setSpacing(4)
        total_caption = QLabel("累计打字", total_card)
        total_caption.setObjectName("DrawerHint")
        total_caption.setAlignment(Qt.AlignCenter)
        self.total_value = QLabel(f"{int(self.words.get('total') or 0)} 字", total_card)
        self.total_value.setObjectName("EditorTitle")
        self.total_value.setAlignment(Qt.AlignCenter)
        total_layout.addWidget(total_caption)
        total_layout.addWidget(self.total_value)
        layout.addWidget(total_card)

        # ---- 日历：每天打了多少字 ----
        cal_card = QFrame(self)
        cal_card.setObjectName("EditorPaper")
        cal_layout = QVBoxLayout(cal_card)
        cal_layout.setContentsMargins(18, 14, 18, 14)
        cal_layout.setSpacing(10)
        cal_header = QHBoxLayout()
        self.prev_button = _ghost("‹", cal_card)
        self.prev_button.setFixedSize(34, 30)
        self.prev_button.clicked.connect(lambda: self._shift_month(-1))
        self.next_button = _ghost("›", cal_card)
        self.next_button.setFixedSize(34, 30)
        self.next_button.clicked.connect(lambda: self._shift_month(1))
        self.month_label = QLabel("", cal_card)
        self.month_label.setObjectName("NameGenTitle")
        self.month_label.setAlignment(Qt.AlignCenter)
        cal_header.addWidget(self.prev_button)
        cal_header.addStretch(1)
        cal_header.addWidget(self.month_label)
        cal_header.addStretch(1)
        cal_header.addWidget(self.next_button)
        cal_layout.addLayout(cal_header)

        self.cal_grid = QGridLayout()
        self.cal_grid.setHorizontalSpacing(6)
        self.cal_grid.setVerticalSpacing(6)
        cal_layout.addLayout(self.cal_grid)
        layout.addWidget(cal_card, 1)

        # ---- 本书统计（书架「统计」页打开时不显示这一块） ----
        if show_book_stats:
            card = QFrame(self)
            card.setObjectName("EditorPaper")
            card_layout = QFormLayout(card)
            card_layout.setContentsMargins(22, 18, 22, 18)
            card_layout.setSpacing(10)

            def add_row(label: str, value: str) -> None:
                key = QLabel(label, card)
                key.setObjectName("DrawerHint")
                val = QLabel(value, card)
                card_layout.addRow(key, val)

            add_row("本书总字数（含标点）", f"{total} 字")
            add_row("本书总字数（不含标点）", f"{total_no_punct} 字")
            add_row("章节数量", f"{chapter_count} 章")
            add_row("卷数量", f"{volume_count} 卷")
            add_row("当前章节", chapter_title)
            add_row("当前章节字数", f"{chapter_chars} 字")
            layout.addWidget(card)

            rule = QLabel("统计规则：日历按电脑日期记录每天的键盘打字字数；本书字数不计空格，可选不计标点。", self)  # noqa: E501
            rule.setObjectName("DrawerHint")
            rule.setWordWrap(True)
            layout.addWidget(rule)
        else:
            rule = QLabel("统计规则：日历按电脑日期记录每天的打字字数。打开一本书后，可在顶栏「统计」查看本书数据。", self)
            rule.setObjectName("DrawerHint")
            rule.setWordWrap(True)
            layout.addWidget(rule)

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        close_button = _primary("知道了", self)
        close_button.clicked.connect(self.accept)
        bottom.addWidget(close_button)
        layout.addLayout(bottom)

        self._rebuild_calendar()

    # ------------------------------------------------------------------
    def _shift_month(self, months: int) -> None:
        self._month = self._month.addMonths(months)
        self._rebuild_calendar()

    def _rebuild_calendar(self) -> None:
        """重建当月日历：日期在上，当天打字字数显示在日期下面。"""
        while self.cal_grid.count():
            item = self.cal_grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()

        self.month_label.setText(f"{self._month.year()} 年 {self._month.month()} 月")
        today = QDate.currentDate()
        days = self.words.get("days") or {}

        for col, name in enumerate(("一", "二", "三", "四", "五", "六", "日")):
            head = QLabel(name, self)
            head.setObjectName("DrawerHint")
            head.setAlignment(Qt.AlignCenter)
            self.cal_grid.addWidget(head, 0, col)

        first = QDate(self._month.year(), self._month.month(), 1)
        offset = first.dayOfWeek() - 1          # 周一 = 0
        days_in_month = first.daysInMonth()
        rows = (offset + days_in_month + 6) // 7
        for index in range(rows * 7):
            row, col = divmod(index, 7)
            day = index - offset + 1
            cell = QFrame(self)
            cell.setFixedHeight(56)
            cell_layout = QVBoxLayout(cell)
            cell_layout.setContentsMargins(2, 4, 2, 4)
            cell_layout.setSpacing(1)
            if 1 <= day <= days_in_month:
                date = QDate(self._month.year(), self._month.month(), day)
                is_today = date == today
                key = date.toString("yyyy-MM-dd")
                cell.setObjectName("CalCellToday" if is_today else "CalCell")
                cell.setAttribute(Qt.WA_StyledBackground, True)
                day_label = QLabel(str(day), cell)
                day_label.setObjectName("CalDay")
                day_label.setAlignment(Qt.AlignCenter)
                count = int(days.get(key) or 0)
                count_label = QLabel("" if date > today else str(count), cell)
                count_label.setObjectName("CalCount")
                count_label.setAlignment(Qt.AlignCenter)
                cell_layout.addWidget(day_label)
                cell_layout.addWidget(count_label, 1)
            self.cal_grid.addWidget(cell, row + 1, col)


class AchievementToast(QDialog):
    """成就解锁庆祝弹窗：屏幕右下角展示勋章图与鼓励的话（点击任意处关闭）。"""

    def __init__(self, key: str, parent=None) -> None:
        super().__init__(parent, Qt.Tool | Qt.FramelessWindowHint)
        from app.services.achievements import ACHIEVEMENTS
        from app.ui.medal_art import medal_pixmap

        info = ACHIEVEMENTS.get(key, {})
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.setModal(False)
        self.setWindowTitle("成就解锁")
        self.setFixedWidth(320)

        card = QFrame(self)
        card.setObjectName("EditorPaper")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(22, 18, 22, 16)
        lay.setSpacing(6)

        medal_pix = medal_pixmap(key, 128)
        if not medal_pix.isNull():
            medal = QLabel(card)
            medal.setPixmap(medal_pix.scaled(96, 96, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            medal.setAlignment(Qt.AlignCenter)
            lay.addWidget(medal)

        name = QLabel(f"成就解锁 · {info.get('name') or ''}", card)
        name.setObjectName("NameGenTitle")
        name.setAlignment(Qt.AlignCenter)
        lay.addWidget(name)

        sub = QLabel(str(info.get("sub") or ""), card)
        sub.setObjectName("DrawerHint")
        sub.setAlignment(Qt.AlignCenter)
        lay.addWidget(sub)

        praise = QLabel(str(info.get("praise") or ""), card)
        praise.setObjectName("DrawerHint")
        praise.setWordWrap(True)
        praise.setAlignment(Qt.AlignCenter)
        lay.addWidget(praise)

        hint = QLabel("点击任意处关闭", card)
        hint.setObjectName("DrawerHint")
        hint.setAlignment(Qt.AlignCenter)
        lay.addWidget(hint)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(card)
        self.adjustSize()

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.close)

    def show_at(self) -> None:
        """显示在屏幕右下角。"""
        screen = QApplication.primaryScreen()
        geo = screen.availableGeometry() if screen is not None else QRect()
        x = geo.right() - self.width() - 26
        y = geo.bottom() - self.height() - 26
        self.move(x, y)
        self.show()
        self.raise_()
        self.activateWindow()
        self._timer.start(7000)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self.close()
        super().mousePressEvent(event)


class SpeedDialog(QDialog):
    """码字时速实时面板。"""

    def __init__(self, tracker, parent=None) -> None:
        super().__init__(parent)
        self.tracker = tracker
        self.setWindowTitle("码字时速")
        self.setModal(False)
        self.resize(380, 300)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 18)
        layout.setSpacing(12)

        card = QFrame(self)
        card.setObjectName("EditorPaper")
        form = QFormLayout(card)
        form.setContentsMargins(22, 18, 22, 18)
        form.setSpacing(12)
        self.session_label = QLabel("0 字", card)
        self.time_label = QLabel("0 秒", card)
        self.speed_label = QLabel("暂无数据", card)
        self.recent_label = QLabel("暂无数据", card)
        for key, value in (
            ("本次会话码字", self.session_label),
            ("有效写作时长", self.time_label),
            ("平均时速", self.speed_label),
            ("最近一分钟", self.recent_label),
        ):
            label = QLabel(key, card)
            label.setObjectName("DrawerHint")
            form.addRow(label, value)
        layout.addWidget(card)

        hint = QLabel("连续两分钟没有输入会暂停计时，挂机时间不计入。", self)
        hint.setObjectName("DrawerHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        bottom = QHBoxLayout()
        reset_button = _ghost("重置本次统计", self)
        reset_button.clicked.connect(self._reset)
        close_button = _primary("关闭", self)
        close_button.clicked.connect(self.close)
        bottom.addWidget(reset_button)
        bottom.addStretch(1)
        bottom.addWidget(close_button)
        layout.addLayout(bottom)

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._refresh)
        self._timer.start()
        self._refresh()

    def _refresh(self) -> None:
        self.session_label.setText(f"{self.tracker.total_chars} 字")
        self.time_label.setText(self.tracker.elapsed_text)
        speed = self.tracker.speed_per_hour
        self.speed_label.setText(f"{int(speed)} 字/小时" if speed > 0 else "暂无数据")
        recent = self.tracker.recent_speed_per_hour
        self.recent_label.setText(f"{int(recent)} 字/小时" if recent > 0 else "暂无数据")

    def _reset(self) -> None:
        self.tracker.reset()
        self._refresh()

    def showEvent(self, event) -> None:  # noqa: N802
        """每次重新打开面板时恢复实时刷新（关闭时计时器会停止）。"""
        super().showEvent(event)
        self._timer.start()
        self._refresh()

    def done(self, result: int) -> None:
        self._timer.stop()
        super().done(result)


class RelatedDialog(QDialog):
    """新增 / 编辑“相关”设定。"""

    def __init__(self, parent=None, kind: str = "角色", name: str = "", note: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle("相关设定")
        self.setModal(True)
        self.resize(440, 340)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 18)
        layout.setSpacing(12)

        form = QFormLayout()
        form.setSpacing(10)
        self.kind_combo = QComboBox(self)
        self.kind_combo.addItems(["角色", "地点", "物品", "设定"])
        if kind in ["角色", "地点", "物品", "设定"]:
            self.kind_combo.setCurrentText(kind)
        self.name_edit = QLineEdit(self)
        self.name_edit.setText(name)
        self.name_edit.setPlaceholderText("名称")
        form.addRow("类型", self.kind_combo)
        form.addRow("名称", self.name_edit)
        layout.addLayout(form)

        note_label = QLabel("备注", self)
        note_label.setObjectName("DrawerHint")
        layout.addWidget(note_label)
        self.note_edit = QPlainTextEdit(self)
        self.note_edit.setObjectName("Preview")
        self.note_edit.setPlainText(note)
        self.note_edit.setPlaceholderText("人物关系、外貌、背景等……")
        layout.addWidget(self.note_edit, 1)

        _add_buttons(self, layout, "保存", self._on_ok)
        self.name_edit.setFocus()

    def _on_ok(self) -> None:
        if not self.name_edit.text().strip():
            MessageBox.warning(self, "提示", "名称不能为空。")
            self.name_edit.setFocus()
            return
        self.accept()

    def values(self) -> tuple[str, str, str]:
        return (
            self.kind_combo.currentText(),
            self.name_edit.text().strip(),
            self.note_edit.toPlainText().strip(),
        )


class NoteEditorWindow(QWidget):
    """独立设定文档编辑窗口：标题 + 大面积正文，自动保存。"""

    saved = Signal()
    closed = Signal(str)   # 参数为设定 ID（关闭时通知主窗口清理引用）

    SAVE_DELAY_MS = 600

    def __init__(self, storage: Storage, doc: dict, parent=None) -> None:
        super().__init__(
            parent, Qt.Window | Qt.WindowMinMaxButtonsHint | Qt.WindowCloseButtonHint
        )
        self.storage = storage
        self.doc = dict(doc)
        self.setWindowTitle(f"设定 · {self.doc.get('title') or '未命名'}")
        self.resize(860, 640)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        bar = QWidget(self)
        bar.setObjectName("TimelineBar")
        bar.setAttribute(Qt.WA_StyledBackground, True)
        bar.setFixedHeight(56)
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(18, 8, 18, 8)
        bar_layout.setSpacing(10)

        self.title_edit = QLineEdit(str(self.doc.get("title") or ""), bar)
        self.title_edit.setObjectName("NoteTitleEdit")
        self.title_edit.setPlaceholderText("设定名称")
        self.title_edit.editingFinished.connect(self._save)
        bar_layout.addWidget(self.title_edit, 1)

        self.saved_label = QLabel("", bar)
        self.saved_label.setObjectName("DrawerHint")
        bar_layout.addWidget(self.saved_label)
        self.export_button = QPushButton("导出", bar)
        self.export_button.setObjectName("GhostButton")
        self.export_button.setCursor(Qt.PointingHandCursor)
        self.export_button.setToolTip("导出为 TXT 文本（路径实时选择）")
        self.export_button.clicked.connect(self._export_txt)
        bar_layout.addWidget(self.export_button)
        layout.addWidget(bar)

        self.content_edit = QPlainTextEdit(self)
        self.content_edit.setObjectName("Preview")
        self.content_edit.setPlaceholderText("在这里自由书写世界观、人物、力量体系等设定……")
        self.content_edit.setPlainText(str(self.doc.get("content") or ""))
        layout.addWidget(self.content_edit, 1)

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(self.SAVE_DELAY_MS)
        self._save_timer.timeout.connect(self._save)
        self.content_edit.textChanged.connect(self._save_timer.start)

        # Ctrl+S 立即保存（关闭窗口时 closeEvent 也会兜底保存）
        save_sc = QShortcut(QKeySequence("Ctrl+S"), self)
        save_sc.activated.connect(self._save)

    # ------------------------------------------------------------------
    def reload(self, doc: dict) -> None:
        """外部重命名后刷新标题显示。"""
        self.doc = dict(doc)
        self.setWindowTitle(f"设定 · {self.doc.get('title') or '未命名'}")
        self.title_edit.setText(str(self.doc.get("title") or ""))

    def _save(self) -> None:
        self._save_timer.stop()
        title = self.title_edit.text().strip() or "未命名设定"
        content = self.content_edit.toPlainText()
        if title == self.doc.get("title") and content == self.doc.get("content"):
            return
        self.doc["title"] = title
        self.doc["content"] = content
        self.doc["updated_at"] = time.time()
        self.storage.save_note(self.doc)
        self.setWindowTitle(f"设定 · {title}")
        self.saved_label.setText(f"已保存 {time.strftime('%H:%M:%S')}")
        self.saved.emit()

    def _export_txt(self) -> None:
        """把设定内容导出为 TXT 文本（导出路径实时选择）。"""
        import os

        from PySide6.QtWidgets import QFileDialog

        self._save()
        title = str(self.doc.get("title") or "设定")
        path, _f = QFileDialog.getSaveFileName(
            self, "导出设定", f"{title}.txt", "文本文档 (*.txt)"
        )
        if not path:
            return
        if not path.lower().endswith(".txt"):
            path += ".txt"
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(f"{title}\n\n{self.content_edit.toPlainText()}\n")
        except OSError as exc:
            MessageBox.warning(self, "导出失败", f"无法写入文件：{exc}")
            return
        MessageBox.information(self, "导出成功", f"已导出到：\n{os.path.abspath(path)}")

    def closeEvent(self, event) -> None:  # noqa: N802
        self._save()
        self.closed.emit(str(self.doc.get("id") or ""))
        super().closeEvent(event)
