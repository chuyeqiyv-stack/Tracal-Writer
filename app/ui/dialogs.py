# -*- coding: utf-8 -*-
"""各类功能弹窗：新建作品、字体、写作主题、起名助手、查找替换等。"""
from __future__ import annotations

import os
import time

from PySide6.QtCore import QDate, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor, QFont, QFontDatabase, QIcon, QKeySequence, QPainter, QPainterPath, QPalette,
    QPen, QPixmap, QShortcut,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
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
    QRadioButton,
    QScrollArea,
    QSlider,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app import config
from app.services import backgrounds, font_store, name_data
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


def _close_icon_button(parent=None, tooltip: str = "关闭") -> QPushButton:
    """右上角 30x30 关闭钮：用全站统一 close 图标（跟随当前文字色）。"""
    button = QPushButton(parent)
    button.setObjectName("GhostButton")
    button.setCursor(Qt.PointingHandCursor)
    button.setFixedSize(30, 30)
    button.setToolTip(tooltip)
    button.setAccessibleName(tooltip)
    text_color = QApplication.palette().windowText().color().name()
    from app.ui.icons import IconFactory

    button.setIcon(IconFactory(text_color).icon("close", 14))
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

        from app.ui import surface as _surface

        if _surface.is_glass():
            # 玻璃风：QSS 已让 QFrame#MessageBox 透明，必须自绘外壳 + 内嵌内容板
            card = _surface.ReadableCard(14.0, self)
            from app.ui.motion import apply_elevation, app_is_dark
            apply_elevation(card, 1, app_is_dark())
        else:
            card = QFrame(self)
            card.setObjectName("MessageBox")
            effect = QGraphicsDropShadowEffect(card)
            effect.setBlurRadius(36)
            effect.setOffset(0, 10)
            effect.setColor(QColor(24, 26, 32, 70))
            card.setGraphicsEffect(effect)
        outer.addWidget(card)
        self._surface_card = card

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


class CharacterImportDialog(QDialog):
    """角色导入预检 + 同名冲突策略（二选一，策略对用户可见）。

    pre 为 Storage.analyze_character_import 的返回值：可导入条数、坏数据计数、
    与现有数据的同 id / 同名冲突数。确认后才真正写盘。
    """

    def __init__(self, parent, pre: dict) -> None:
        super().__init__(parent)
        self._strategy = "merge"
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setModal(True)
        self.setFixedWidth(430)

        from app.ui.motion import card_surface

        layout = card_surface(self, gutter=28, readable=True)
        card = self._surface_card
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(10)

        title = QLabel("导入角色 JSON", card)
        title.setObjectName("MsgTitle")
        layout.addWidget(title)

        valid_chars = len(pre.get("valid_characters") or [])
        valid_rels = len(pre.get("valid_relations") or [])
        skipped_chars = int(pre.get("skipped_characters") or 0)
        skipped_rels = int(pre.get("skipped_relations") or 0)
        summary = QLabel(
            f"文件版本：v{int(pre.get('version') or 1)}　现有角色：{pre.get('existing_characters', 0)}\n"
            f"可导入：{valid_chars} 名角色、{valid_rels} 条关系\n"
            f"坏数据已跳过：角色 {skipped_chars} 条、关系 {skipped_rels} 条",
            card,
        )
        summary.setObjectName("MsgText")
        summary.setWordWrap(True)
        layout.addWidget(summary)

        conflict_id = int(pre.get("conflict_id") or 0)
        conflict_name = int(pre.get("conflict_name") or 0)
        if conflict_id or conflict_name:
            hint = QLabel(
                f"检测到冲突：同 id {conflict_id} 个、同名 {conflict_name} 个。请选择处理方式：",
                card,
            )
            hint.setObjectName("MsgText")
            hint.setWordWrap(True)
            layout.addWidget(hint)

            self.rb_merge = QRadioButton("同 id / 同名合并更新（推荐）", card)
            self.rb_merge.setChecked(True)
            self.rb_merge.toggled.connect(
                lambda on: setattr(self, "_strategy", "merge") if on else None
            )
            layout.addWidget(self.rb_merge)
            merge_hint = QLabel("保留现有角色 id 与创建时间，用导入内容覆盖字段。", card)
            merge_hint.setObjectName("DrawerHint")
            layout.addWidget(merge_hint)

            self.rb_skip = QRadioButton("同名角色跳过（保留现有数据）", card)
            self.rb_skip.toggled.connect(
                lambda on: setattr(self, "_strategy", "skip") if on else None
            )
            layout.addWidget(self.rb_skip)
            skip_hint = QLabel("冲突条目不写入，导入完成时会提示跳过条数。", card)
            skip_hint.setObjectName("DrawerHint")
            layout.addWidget(skip_hint)
        else:
            layout.addWidget(QLabel("未发现同 id / 同名冲突，将直接新增到角色页。", card))

        row = QHBoxLayout()
        row.addStretch(1)
        cancel = _ghost("取消", card)
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        ok = _primary("开始导入", card)
        ok.clicked.connect(self.accept)
        row.addWidget(ok)
        layout.addLayout(row)

    def strategy(self) -> str:
        return self._strategy


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
            # ＋ 用全站统一矢量图标（不再用文字字形）
            from app.ui.icons import IconFactory

            plus = IconFactory("#8A9099").pixmap("add", 26, dpr=self.devicePixelRatioF())
            painter.drawPixmap(
                int((self.width() - plus.width() / plus.devicePixelRatio()) / 2),
                int(self.height() / 2 - 30),
                plus,
            )
            font = QFont(self.font())
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
        # 之前已导入的数量 + 本次导入，用于标签展示
        self._imported_count = len(imported or [])
        self._color = str(color or "")
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setModal(True)
        self.setFixedWidth(372)

        # 文字密集弹层：玻璃风下外壳保持原值（本体膜 0.55–0.72），
        # 内部嵌入 10px 内缩内容板；非玻璃风仍是实色 QFrame。
        from app.ui.motion import card_surface

        layout = card_surface(self, gutter=28, readable=True)
        card = self._surface_card
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(12)

        # ---- 标题行：字体 + 关闭 ----
        head = QHBoxLayout()
        title = QLabel("字体", card)
        title.setObjectName("MsgTitle")
        head.addWidget(title)
        head.addStretch(1)
        close_button = _close_icon_button(card, "关闭（不保存修改）")
        close_button.clicked.connect(self.reject)
        head.addWidget(close_button)
        layout.addLayout(head)

        # ---- 编辑器字体 ----
        cap_font = QLabel("编辑器字体", card)
        cap_font.setObjectName("DrawerHint")
        layout.addWidget(cap_font)
        self.family_combo = QComboBox(card)
        self._fill_family_combo(str(family or ""))
        self._opaque_combo_popup(self.family_combo)
        layout.addWidget(self.family_combo)

        # ---- 大小与颜色 ----
        cap_size = QLabel("大小与颜色", card)
        cap_size.setObjectName("DrawerHint")
        layout.addWidget(cap_size)
        size_row = QHBoxLayout()
        size_row.setSpacing(8)
        self.size_combo = QComboBox(card)
        self._fill_size_combo(float(size))
        self._opaque_combo_popup(self.size_combo)
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
    @staticmethod
    def _opaque_combo_popup(combo: QComboBox) -> None:
        """字体弹层特例：下拉列表给近实底（玻璃风 $panel 半透明会透出正文）。

        颜色取应用调色板（随深浅 / 品牌色），hover/选中态保留；只作用于
        字体对话框内的两个下拉，不触碰全局 QSS 与其它弹窗。
        """
        pal = QApplication.palette()
        bg = pal.color(QPalette.ColorRole.Window).name()
        fg = pal.color(QPalette.ColorRole.WindowText).name()
        sel_bg = pal.color(QPalette.ColorRole.Highlight).name()
        sel_fg = pal.color(QPalette.ColorRole.HighlightedText).name()
        border = QColor(sel_bg)
        border.setAlpha(90)
        combo.view().setStyleSheet(
            f"QAbstractItemView {{"
            f" background: {bg}; color: {fg}; border: 1px solid {border.name()};"
            " border-radius: 8px; padding: 4px; outline: 0; }\n"
            "QAbstractItemView::item {"
            " min-height: 26px; padding: 5px 10px; border-radius: 6px; }\n"
            f"QAbstractItemView::item:hover {{ background: {sel_bg}; "
            "color: white; }\n"
            f"QAbstractItemView::item:selected {{ background: {sel_bg};"
            f" color: {sel_fg}; }}"
        )

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

    def _add_family_items(self, names: list[str]) -> None:
        """把新导入的字体族名补进下拉（导入后立即可选，无需重开面板）。"""
        combo = self.family_combo
        existing = {combo.itemData(i) for i in range(combo.count())}
        for name in names:
            if name and name not in existing:
                combo.addItem(name, name)
                existing.add(name)

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
        self._import_paths(paths)

    def _import_paths(self, paths: list[str]) -> int:
        """导入给定字体文件（供 UI 与测试共用），返回成功个数。"""
        added = 0
        new_families: list[str] = []
        for path in paths:
            try:
                dest, font_id = font_store.register(path)
            except (OSError, ValueError):
                continue
            if font_id < 0:
                continue
            self._new_fonts.append(str(dest))
            added += 1
            new_families.extend(font_store.family_names(font_id))
        if added:
            self._imported_count += added
            self.imported_label.setText(f"已导入 {self._imported_count} 个本地字体")
            # 关键：导入后立即补进下拉并选中，让用户点一次就能看到效果
            self._add_family_items(new_families)
            if new_families:
                index = self.family_combo.findData(new_families[0])
                if index >= 0:
                    self.family_combo.setCurrentIndex(index)
                self._emit_live()
            self.fonts_imported.emit(list(self._new_fonts))
            MessageBox.information(
                self, "导入成功",
                f"已加载 {added} 个字体文件，并已选中「{new_families[0] if new_families else '新字体'}」。\n"
                "字体文件已复制到数据目录，重装/重启后仍会自动加载。",
            )
        else:
            MessageBox.warning(self, "导入失败", "没有可用的字体文件（支持 .ttf / .otf / .ttc）。")
        return added


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


class ParagraphDialog(QDialog):
    """段落设置（借鉴 Word 段落对话框）：对齐方式、首行缩进、段前/段后间距与行距。

    字体不在此设置——段落格式一律基于正文当前字体计算，自动跟随“字体”设置。
    """

    ALIGN_LABELS = [("left", "左对齐"), ("center", "居中"), ("right", "右对齐")]

    def __init__(self, parent, settings) -> None:
        super().__init__(parent)
        self.setWindowTitle("段落设置")
        self.setModal(True)
        self.resize(420, 330)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 18)
        layout.setSpacing(12)

        def group_title(text: str) -> QLabel:
            label = QLabel(text, self)
            label.setObjectName("DrawerTitle")
            return label

        # ---- 常规：对齐方式 ----
        layout.addWidget(group_title("常规"))
        align_row = QHBoxLayout()
        align_row.addWidget(QLabel("对齐方式：", self))
        self.align_combo = QComboBox(self)
        for _key, label in self.ALIGN_LABELS:
            self.align_combo.addItem(label)
        keys = [key for key, _ in self.ALIGN_LABELS]
        self.align_combo.setCurrentIndex(
            keys.index(settings.para_align) if settings.para_align in keys else 0
        )
        align_row.addWidget(self.align_combo, 1)
        layout.addLayout(align_row)

        # ---- 缩进：首行 ----
        layout.addWidget(group_title("缩进"))
        indent_row = QHBoxLayout()
        indent_row.addWidget(QLabel("首行：", self))
        self.indent_spin = QDoubleSpinBox(self)
        self.indent_spin.setRange(0, 8)
        self.indent_spin.setDecimals(0)
        self.indent_spin.setSingleStep(1)
        self.indent_spin.setSuffix(" 字符")
        self.indent_spin.setValue(float(settings.para_indent))
        indent_row.addWidget(self.indent_spin)
        indent_row.addStretch(1)
        indent_hint = QLabel("正文已有全角空格缩进时请保持 0，避免双重缩进", self)
        indent_hint.setObjectName("DrawerHint")
        indent_row.addWidget(indent_hint, 1)
        layout.addLayout(indent_row)

        # ---- 间距：段前 / 段后 / 行距 ----
        layout.addWidget(group_title("间距"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(10)
        grid.addWidget(QLabel("段前：", self), 0, 0)
        self.before_spin = QDoubleSpinBox(self)
        self.before_spin.setRange(0.0, 4.0)
        self.before_spin.setSingleStep(0.5)
        self.before_spin.setDecimals(1)
        self.before_spin.setSuffix(" 行")
        self.before_spin.setValue(float(settings.para_space_before))
        grid.addWidget(self.before_spin, 0, 1)
        grid.addWidget(QLabel("段后：", self), 0, 2)
        self.after_spin = QDoubleSpinBox(self)
        self.after_spin.setRange(0.0, 4.0)
        self.after_spin.setSingleStep(0.5)
        self.after_spin.setDecimals(1)
        self.after_spin.setSuffix(" 行")
        self.after_spin.setValue(float(settings.para_space_after))
        grid.addWidget(self.after_spin, 0, 3)
        grid.addWidget(QLabel("行距：", self), 1, 0)
        self.spacing_spin = QDoubleSpinBox(self)
        self.spacing_spin.setRange(1.0, 3.5)
        self.spacing_spin.setSingleStep(0.05)
        self.spacing_spin.setDecimals(2)
        self.spacing_spin.setSuffix(" 倍")
        self.spacing_spin.setValue(float(settings.line_spacing))
        grid.addWidget(self.spacing_spin, 1, 1)
        grid.setColumnStretch(4, 1)
        layout.addLayout(grid)

        sample = QLabel("段落格式基于正文当前字体即时计算，自动跟随“字体”设置。", self)
        sample.setObjectName("DrawerHint")
        sample.setWordWrap(True)
        layout.addWidget(sample)
        layout.addStretch(1)

        _add_buttons(self, layout, "确定", self.accept)

    def values(self) -> tuple[str, int, float, float, float]:
        keys = [key for key, _ in self.ALIGN_LABELS]
        align = keys[self.align_combo.currentIndex()] if self.align_combo.currentIndex() >= 0 else "left"
        return (
            align,
            int(self.indent_spin.value()),
            round(self.before_spin.value(), 2),
            round(self.after_spin.value(), 2),
            round(self.spacing_spin.value(), 2),
        )


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
        from app.ui.scroll_float import attach_flasher

        attach_flasher(self.preview, Qt.Vertical, inset=6)
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
        from app.ui.scroll_float import attach_flasher

        attach_flasher(self.preview, Qt.Vertical, inset=6)
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
    """起名助手：顶部 7 类页签，左侧子类型筛选 + 后缀 + 生成按钮，
    右侧名称胶囊墙（点击复制）。整体为圆角卡片，标题栏可拖动。"""

    TABS = [
        ("person", "人物"), ("place", "地点"), ("faction", "势力"),
        ("equip", "装备"), ("skill", "功法"), ("monster", "怪兽"),
        ("other", "其他"),
    ]
    SUBTYPES = {
        "person": [("any", "不限"), ("male", "男"), ("female", "女")],
        "place": [("east", "东方"), ("west", "西方"), ("jp", "日式")],
        "faction": [("right", "正道"), ("evil", "魔道"), ("neutral", "中立")],
        "equip": [("weapon", "武器"), ("armor", "防具"), ("accessory", "饰品")],
        "skill": [("sword", "剑诀"), ("palm", "掌法"), ("mind", "心法"), ("move", "身法")],
        "monster": [("yao", "妖兽"), ("ling", "灵兽"), ("xiong", "凶兽")],
        "other": [("pill", "丹药"), ("title", "称号"), ("item", "灵物")],
    }
    STYLES = [("cn", "中文"), ("west", "西方"), ("jp", "日式")]
    COLUMNS = 4
    COUNT = 24

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("起名助手")
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setModal(True)
        self.resize(880, 600)
        self._kind = "person"
        self._subtype = "any"
        self._style = "cn"
        self._drag_pos: QPoint | None = None
        # 每类的状态缓存：候选 / 选中 / 筛选条件（对话框实例复用，重开不丢）
        self._kind_state: dict[str, dict] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 28, 28, 28)   # 给阴影留出空间

        from app.ui import surface as _surface

        if _surface.is_glass():
            # 起名助手是文字最密集的浮层：外壳玻璃原样 + 内嵌内容板，正文不透上来
            card = _surface.ReadableCard(14.0, self)
            from app.ui.motion import apply_elevation, app_is_dark
            apply_elevation(card, 1, app_is_dark())
        else:
            card = QFrame(self)
            card.setObjectName("MessageBox")
            effect = QGraphicsDropShadowEffect(card)
            effect.setBlurRadius(36)
            effect.setOffset(0, 10)
            effect.setColor(QColor(24, 26, 32, 70))
            card.setGraphicsEffect(effect)
        outer.addWidget(card)
        self._surface_card = card

        root = QVBoxLayout(card)
        root.setContentsMargins(22, 16, 18, 18)
        root.setSpacing(12)

        # ---- 头部：标题居中 + 右上关闭 ----
        head = QHBoxLayout()
        head.addStretch(1)
        title = QLabel("起名助手", card)
        title.setObjectName("MsgTitle")
        head.addWidget(title)
        head.addStretch(1)
        close_button = _close_icon_button(card, "关闭")
        close_button.clicked.connect(self.reject)
        head.addWidget(close_button)
        root.addLayout(head)

        # ---- 页签行：7 大类 ----
        tab_row = QHBoxLayout()
        tab_row.setSpacing(4)
        self._tab_buttons: dict[str, QPushButton] = {}
        for key, text in self.TABS:
            button = QPushButton(text, card)
            button.setObjectName("TabButton")
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setChecked(key == self._kind)
            button.clicked.connect(lambda _c=False, k=key: self._switch_kind(k))
            tab_row.addWidget(button, 1)
            self._tab_buttons[key] = button
        root.addLayout(tab_row)

        body = QHBoxLayout()
        body.setSpacing(14)

        # ---- 左侧筛选列 ----
        left_frame = QFrame(card)
        left_frame.setObjectName("NameGenFilter")
        left_frame.setFixedWidth(236)
        left = QVBoxLayout(left_frame)
        left.setContentsMargins(16, 14, 16, 14)
        left.setSpacing(10)

        cap_type = QLabel("类型", left_frame)
        cap_type.setObjectName("DrawerHint")
        left.addWidget(cap_type)
        self._subtype_box = QVBoxLayout()   # 切页签时重建
        self._subtype_box.setSpacing(6)
        left.addLayout(self._subtype_box)

        # 人物专属：风格 + 指定姓氏/名字
        self._person_extra = QWidget(left_frame)
        person_form = QFormLayout(self._person_extra)
        person_form.setContentsMargins(0, 2, 0, 0)
        person_form.setSpacing(8)
        person_form.setLabelAlignment(Qt.AlignRight)
        style_row = QHBoxLayout()
        style_row.setSpacing(6)
        self._style_buttons: dict[str, QPushButton] = {}
        for key, text in self.STYLES:
            button = QPushButton(text, self._person_extra)
            button.setObjectName("ViewChip")
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setChecked(key == self._style)
            button.clicked.connect(lambda _c=False, k=key: self._set_style(k))
            style_row.addWidget(button)
            self._style_buttons[key] = button
        person_form.addRow("风格", style_row)
        self.surname_edit = QLineEdit(self._person_extra)
        self.surname_edit.setPlaceholderText("留空则随机")
        self.given_edit = QLineEdit(self._person_extra)
        self.given_edit.setPlaceholderText("留空则随机")
        person_form.addRow("指定姓氏", self.surname_edit)
        person_form.addRow("指定名字", self.given_edit)
        left.addWidget(self._person_extra)

        cap_suffix = QLabel("后缀", left_frame)
        cap_suffix.setObjectName("DrawerHint")
        left.addWidget(cap_suffix)
        self.suffix_edit = QLineEdit(left_frame)
        self.suffix_edit.setPlaceholderText("请输入指定后缀（选填）")
        left.addWidget(self.suffix_edit)

        left.addStretch(1)
        self.generate_button = QPushButton("生成", left_frame)
        self.generate_button.setObjectName("Primary")
        self.generate_button.setFixedHeight(42)
        self.generate_button.setCursor(Qt.PointingHandCursor)
        self.generate_button.clicked.connect(self.generate)
        left.addWidget(self.generate_button)
        body.addWidget(left_frame)

        # ---- 右侧结果区：胶囊墙 ----
        right = QVBoxLayout()
        right.setSpacing(8)
        result_header = QHBoxLayout()
        self.result_hint = QLabel("点击任意名字即可复制", card)
        self.result_hint.setObjectName("DrawerHint")
        refresh_button = _ghost("换一批", card)
        refresh_button.clicked.connect(self.generate)
        result_header.addWidget(self.result_hint, 1)
        result_header.addWidget(refresh_button)
        right.addLayout(result_header)

        self._grid_widget = QWidget(card)
        self._grid = QGridLayout(self._grid_widget)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(8)
        self._grid.setVerticalSpacing(8)

        scroll = QScrollArea(card)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        from app.ui.scroll_float import ScrollFlasher

        self._grid_flasher = ScrollFlasher(scroll, Qt.Vertical, inset=6)
        scroll.setWidget(self._grid_widget)
        right.addWidget(scroll, 1)
        body.addLayout(right, 1)

        root.addLayout(body, 1)

        # ---- 底部操作条：显式关闭入口（图标 + 文案；Esc 等价）----
        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 2, 0, 0)
        esc_hint = QLabel("Esc 快速关闭", card)
        esc_hint.setObjectName("DrawerHint")
        action_row.addWidget(esc_hint)
        action_row.addStretch(1)
        self.clear_button = _ghost("清空候选", card)
        self.clear_button.setToolTip("清空当前分类已生成的候选与选中名字")
        self.clear_button.clicked.connect(self.clear_candidates)
        action_row.addWidget(self.clear_button)
        self.close_action = _primary("关闭", card)
        self.close_action.setMinimumWidth(112)
        self.close_action.setFixedHeight(38)
        self.close_action.setToolTip("关闭并返回（Esc）")
        self.close_action.clicked.connect(self.reject)
        action_row.addWidget(self.close_action)
        root.addLayout(action_row)

        self.toast = ToastLabel(card)

        self._rebuild_subtype_chips()
        self.generate()
        self._refresh_icons()

    # ------------------------------------------------------------------
    # 主题化图标（每次显示时按当前调色板刷新，深浅主题切换后仍清晰）
    # ------------------------------------------------------------------
    def _refresh_icons(self) -> None:
        from app.ui.icons import IconFactory

        text_color = self.palette().windowText().color().name()
        self.clear_button.setIcon(IconFactory(text_color).icon("trash", 16))
        # 主色按钮上的图标用 on_accent 白色，保证对比
        self.close_action.setIcon(IconFactory("#FFFFFF").icon("close", 16))

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._refresh_icons()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        # 无边框窗口没有原生标题栏，Esc 显式兜底为关闭
        if event.key() == Qt.Key_Escape:
            self.reject()
            event.accept()
            return
        super().keyPressEvent(event)

    # ------------------------------------------------------------------
    # 拖动：按住卡片空白处 / 标题栏即可移动窗口
    # ------------------------------------------------------------------
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_pos is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._drag_pos = None
        super().mouseReleaseEvent(event)

    # ------------------------------------------------------------------
    def _new_state(self) -> dict:
        return {"names": [], "selected": set(), "subtype": "any",
                "style": "cn", "surname": "", "given": "", "suffix": ""}

    def _snapshot_filters(self) -> None:
        """把当前筛选条件记入本类缓存（切走前调用）。"""
        st = self._kind_state.setdefault(self._kind, self._new_state())
        st["subtype"] = self._subtype
        st["style"] = self._style
        st["surname"] = self.surname_edit.text()
        st["given"] = self.given_edit.text()
        st["suffix"] = self.suffix_edit.text()

    def _switch_kind(self, key: str) -> None:
        if key == self._kind:
            return
        self._snapshot_filters()
        self._kind = key
        st = self._kind_state.get(key)
        if st is None:
            st = self._new_state()
            st["subtype"] = self.SUBTYPES[key][0][0]
            self._kind_state[key] = st
        self._subtype = st["subtype"]
        self._style = st["style"]
        for k, button in self._tab_buttons.items():
            button.setChecked(k == key)
        for k, button in self._style_buttons.items():
            button.setChecked(k == self._style)
        self.surname_edit.setText(st["surname"])
        self.given_edit.setText(st["given"])
        self.suffix_edit.setText(st["suffix"])
        self._rebuild_subtype_chips()
        self._person_extra.setVisible(key == "person")
        if st["names"]:
            self._populate(st["names"])
        else:
            self.generate()

    def _rebuild_subtype_chips(self) -> None:
        """按当前页签重建「类型」子筛选胶囊。"""
        while self._subtype_box.count():
            item = self._subtype_box.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
                continue
            inner = item.layout()
            if inner is not None:   # 行布局：逐个删除里面的胶囊按钮
                while inner.count():
                    child = inner.takeAt(0)
                    if child.widget() is not None:
                        child.widget().deleteLater()
        row = QHBoxLayout()
        row.setSpacing(6)
        for key, text in self.SUBTYPES[self._kind]:
            button = QPushButton(text, self._person_extra.parentWidget())
            button.setObjectName("ViewChip")
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setChecked(key == self._subtype)
            button.clicked.connect(lambda _c=False, k=key: self._set_subtype(k))
            row.addWidget(button)
        row.addStretch(1)
        self._subtype_box.addLayout(row)

    def _set_subtype(self, key: str) -> None:
        self._subtype = key
        self.generate()

    def _set_style(self, key: str) -> None:
        self._style = key
        for k, button in self._style_buttons.items():
            button.setChecked(k == key)
        self.generate()

    def generate(self) -> None:
        names = name_data.generate_names(
            kind=self._kind,
            subtype=self._subtype,
            style=self._style,
            surname=self.surname_edit.text(),
            given=self.given_edit.text(),
            suffix=self.suffix_edit.text(),
            count=self.COUNT,
        )
        # 记入本类缓存（候选与当前筛选条件），切走再回来时原样恢复
        st = self._kind_state.setdefault(self._kind, self._new_state())
        st.update(
            subtype=self._subtype, style=self._style,
            surname=self.surname_edit.text(), given=self.given_edit.text(),
            suffix=self.suffix_edit.text(), names=list(names),
        )
        self._populate(names)

    def clear_candidates(self) -> None:
        """用户主动清空：本类候选与选中都清掉（筛选条件保留）。"""
        st = self._kind_state.setdefault(self._kind, self._new_state())
        st["names"] = []
        st["selected"] = set()
        self._populate([])

    def _populate(self, names: list[str]) -> None:
        # 清空旧结果
        while self._grid.count():
            item = self._grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        selected = self._kind_state.get(self._kind, {}).get("selected", set())
        for index, name in enumerate(names):
            chip = QPushButton(name, self._grid_widget)
            chip.setObjectName("NameChip")
            chip.setCheckable(True)
            chip.setChecked(name in selected)
            chip.setCursor(Qt.PointingHandCursor)
            chip.setMinimumHeight(36)
            chip.setToolTip("点击复制；再点一次可标记 / 取消选中")
            chip.clicked.connect(lambda _c=False, b=chip, n=name: self._on_chip(b, n))
            row, col = divmod(index, self.COLUMNS)
            self._grid.addWidget(chip, row, col)
        for col in range(self.COLUMNS):
            self._grid.setColumnStretch(col, 1)
        if names:
            picked = len(selected)
            tail = f" · 已选 {picked} 个" if picked else ""
            self.result_hint.setText(f"已生成 {len(names)} 个名字 · 点击即可复制{tail}")
        else:
            self.result_hint.setText("暂无候选 · 点击左侧「生成」")

    def _on_chip(self, chip: QPushButton, name: str) -> None:
        st = self._kind_state.setdefault(self._kind, self._new_state())
        selected = st.setdefault("selected", set())
        if chip.isChecked():
            selected.add(name)
        else:
            selected.discard(name)
        picked = len(selected)
        tail = f" · 已选 {picked} 个" if picked else ""
        self.result_hint.setText(f"已生成 {len(st.get('names', []))} 个名字 · 点击即可复制{tail}")
        self._copy(name)

    def _copy(self, name: str) -> None:
        QApplication.clipboard().setText(name)
        self.toast.show_message(f"已复制：{name}")


class BackgroundThumb(QAbstractButton):
    """自定义背景缩略框：与预设色卡同尺寸（78×88），圆角缩略图 cover 裁切。

    右上角小叉（或右键菜单）删除；文件缺失时画占位框，不崩。
    """

    delete_requested = Signal(str)   # 文件名

    _SIDE = 50
    _RADIUS = 12

    def __init__(self, name: str, path: str, label: str,
                 selected: bool = False, parent=None) -> None:
        super().__init__(parent)
        self._name = name
        self._path = path
        self._label = label
        self._pixmap = background_thumb_pixmap(path)
        self._selected = selected
        self._hover = False
        self.setFixedSize(78, 88)
        self.setCursor(Qt.PointingHandCursor)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)

    def name(self) -> str:
        return self._name

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        self.update()

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def _delete_rect(self) -> QRect:
        return QRect(self.width() - 18, 4, 16, 16)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if (event.button() == Qt.LeftButton
                and self._delete_rect().contains(event.position().toPoint())):
            self.delete_requested.emit(self._name)
            event.accept()
            return
        super().mousePressEvent(event)

    def _menu(self, pos) -> None:
        menu = QMenu(self)
        action = menu.addAction("删除此背景")
        if menu.exec(self.mapToGlobal(pos)) is action:
            self.delete_requested.emit(self._name)

    def _cover_source(self) -> QRectF:
        width, height = self._pixmap.width(), self._pixmap.height()
        side = min(width, height)
        return QRectF((width - side) / 2, (height - side) / 2, side, side)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        side = self._SIDE if not self._hover else self._SIDE + 2
        left = (self.width() - side) / 2
        top = 12 - (side - self._SIDE) / 2
        rect = QRectF(left, top, side, side)

        if self._selected or self._hover:
            color = QColor(self.palette().color(QPalette.Highlight))
            if not self._selected:
                color.setAlpha(110)
            painter.setPen(QPen(color, 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(rect.adjusted(-3, -3, 3, 3),
                                    self._RADIUS + 3, self._RADIUS + 3)

        clip = QPainterPath()
        clip.addRoundedRect(rect, self._RADIUS, self._RADIUS)
        painter.save()
        painter.setClipPath(clip)
        if self._pixmap.isNull():
            painter.fillRect(rect, QColor(0, 0, 0, 28))
            painter.setPen(QPen(QColor(0, 0, 0, 120)))
            font = QFont(self.font())
            font.setPixelSize(10)
            painter.setFont(font)
            painter.drawText(rect, Qt.AlignCenter, "缺图")
        else:
            painter.drawPixmap(rect, self._pixmap, self._cover_source())
        painter.restore()

        painter.setPen(QPen(QColor(0, 0, 0, 40), 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(rect, self._RADIUS, self._RADIUS)

        cross = QRectF(self._delete_rect())
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(0, 0, 0, 190 if self._hover else 110))
        painter.drawEllipse(cross)
        painter.setPen(QPen(QColor("#FFFFFF"), 1.6))
        painter.drawLine(QPointF(cross.left() + 5, cross.top() + 5),
                         QPointF(cross.right() - 5, cross.bottom() - 5))
        painter.drawLine(QPointF(cross.right() - 5, cross.top() + 5),
                         QPointF(cross.left() + 5, cross.bottom() - 5))

        painter.setPen(QPen(QColor(self.palette().color(QPalette.WindowText))))
        font = QFont(self.font())
        font.setPixelSize(11)
        painter.setFont(font)
        painter.drawText(QRectF(0, 68, self.width(), 18),
                         Qt.AlignHCenter | Qt.AlignVCenter, self._label)
        painter.end()


_background_thumb_cache: dict[str, QPixmap] = {}


def background_thumb_pixmap(path: str) -> QPixmap:
    """缩略图位图（缓存到 100px，125%/200% 缩放也不糊；按内容寻址文件名天然不变）。"""
    if not path:
        return QPixmap()
    cached = _background_thumb_cache.get(path)
    if cached is not None:
        return cached
    pixmap = QPixmap(path)
    if pixmap.isNull():
        pixmap = QPixmap()
    else:
        pixmap = pixmap.scaled(
            100, 100, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation
        )
    if len(_background_thumb_cache) >= 32:
        _background_thumb_cache.clear()
    _background_thumb_cache[path] = pixmap
    return pixmap


class WritingThemeDialog(QDialog):
    """写作主题面板：网格圆形色块（预设）+ 最多 5 个自定义背景缩略框。"""

    theme_applied = Signal(str, str)     # (writing_theme, writing_image)
    backgrounds_changed = Signal(list)   # 已保存背景文件名列表

    COLUMNS = 6  # 每行 6 个圆形色块

    def __init__(self, parent, settings) -> None:
        super().__init__(parent)
        self._theme = settings.writing_theme
        if self._theme != "custom" and self._theme not in WRITING_THEMES:
            self._theme = "auto"   # 旧配置指向已删除的预设 → 面板选中「跟随主题」
        self._image = settings.writing_image
        self._backgrounds = [name for name in (settings.backgrounds or []) if name]
        self.setWindowTitle("写作主题")
        self.setModal(True)
        self.resize(560, 560)

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
        self._swatch_map: dict[str, CircleSwatch] = {}
        for index, key in enumerate(WRITING_THEMES.keys()):
            info = WRITING_THEMES[key]
            color = info["bg"] or ("#F2F3F5" if key == "auto" else "#FFFFFF")
            swatch = CircleSwatch(info["name"], color, key == self._theme, self)
            swatch.clicked.connect(lambda _c=False, k=key: self._apply(k))
            row, col = divmod(index, self.COLUMNS)
            grid.addWidget(swatch, row, col, Qt.AlignHCenter)
            self._swatches.append(swatch)
            self._swatch_map[key] = swatch
        for col in range(self.COLUMNS):
            grid.setColumnStretch(col, 1)
        layout.addLayout(grid)

        # 我的背景：标题 + 计数 + 上传入口，下方是缩略框行
        head = QHBoxLayout()
        caption = QLabel("我的背景", self)
        caption.setObjectName("DrawerHint")
        self._count_label = QLabel("", self)
        self._count_label.setObjectName("DrawerHint")
        self.image_button = _ghost("上传自定义图片…", self)
        self.image_button.clicked.connect(self._pick_image)
        head.addWidget(caption)
        head.addWidget(self._count_label)
        head.addStretch(1)
        head.addWidget(self.image_button)
        layout.addLayout(head)

        self._thumb_host = QWidget(self)
        self._thumb_row = QHBoxLayout(self._thumb_host)
        self._thumb_row.setContentsMargins(0, 0, 0, 0)
        self._thumb_row.setSpacing(4)
        layout.addWidget(self._thumb_host)
        self._thumbs: list[BackgroundThumb] = []
        self._rebuild_thumbs()

        _add_buttons(self, layout, "完成", self.accept)

    # ------------------------------------------------------------------
    def _apply(self, key: str) -> None:
        self._theme = key
        for swatch in self._swatches:
            swatch.set_selected(False)
        sender = self.sender()
        if isinstance(sender, CircleSwatch):
            sender.set_selected(True)
        for thumb in self._thumbs:
            thumb.set_selected(False)
        self.theme_applied.emit(self._theme, self._image)

    def _rebuild_thumbs(self) -> None:
        while self._thumb_row.count():
            item = self._thumb_row.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._thumbs = []
        for index, name in enumerate(self._backgrounds):
            thumb = BackgroundThumb(
                name, backgrounds.resolve(name), f"背景 {index + 1}",
                name == self._image, self._thumb_host,
            )
            thumb.clicked.connect(lambda _c=False, n=name: self._select_background(n))
            thumb.delete_requested.connect(self._delete_background)
            self._thumb_row.addWidget(thumb)
            self._thumbs.append(thumb)
        self._thumb_row.addStretch(1)
        self._count_label.setText(
            f"{len(self._backgrounds)}/{backgrounds.MAX_BACKGROUNDS}"
        )

    def _select_background(self, name: str) -> None:
        if not backgrounds.resolve(name):
            MessageBox.warning(self, "图片缺失", "这张背景图片已丢失，请删除后重新上传。")
            return
        self._image = name
        self._theme = "custom"
        for swatch in self._swatches:
            swatch.set_selected(False)
        for thumb in self._thumbs:
            thumb.set_selected(thumb.name() == name)
        self.theme_applied.emit(self._theme, self._image)

    def _delete_background(self, name: str) -> None:
        if name not in self._backgrounds:
            return
        path = backgrounds.resolve(name)
        self._backgrounds.remove(name)
        backgrounds.delete(name)
        if path:
            _background_thumb_cache.pop(path, None)
        if self._image == name:
            # 正在用的那张被删 → 回落默认背景（跟随主题）
            self._image = ""
            self._theme = "auto"
        self._rebuild_thumbs()
        for swatch in self._swatches:
            swatch.set_selected(False)
        if self._theme == "auto":
            self._swatch_map["auto"].set_selected(True)
        elif self._theme == "custom":
            for thumb in self._thumbs:
                thumb.set_selected(thumb.name() == self._image)
        else:
            swatch = self._swatch_map.get(self._theme)
            if swatch is not None:
                swatch.set_selected(True)
        self.backgrounds_changed.emit(list(self._backgrounds))
        self.theme_applied.emit(self._theme, self._image)

    def _pick_image(self) -> None:
        if len(self._backgrounds) >= backgrounds.MAX_BACKGROUNDS:
            MessageBox.warning(
                self, "最多 5 张背景", "最多保存 5 张背景，请先删一张。"
            )
            return
        path, _filter = QFileDialog.getOpenFileName(
            self, "选择背景图片", "", "图片文件 (*.png *.jpg *.jpeg *.webp *.bmp)"
        )
        if not path:
            return
        try:
            name = backgrounds.import_image(path)
        except (OSError, ValueError):
            MessageBox.warning(self, "导入失败", "无法读取这张图片，请换一张试试。")
            return
        self._backgrounds.append(name)
        self._image = name
        self._theme = "custom"
        self._rebuild_thumbs()
        for swatch in self._swatches:
            swatch.set_selected(False)
        self.backgrounds_changed.emit(list(self._backgrounds))
        self.theme_applied.emit(self._theme, self._image)

    def backgrounds(self) -> list[str]:
        return list(self._backgrounds)

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
        self.prev_button = _ghost("", cal_card)
        self.prev_button.setFixedSize(34, 30)
        self.prev_button.setToolTip("上一个月")
        self.prev_button.setAccessibleName("上一个月")
        self.prev_button.setCursor(Qt.PointingHandCursor)
        self.prev_button.clicked.connect(lambda: self._shift_month(-1))
        self.next_button = _ghost("", cal_card)
        self.next_button.setFixedSize(34, 30)
        self.next_button.setToolTip("下一个月")
        self.next_button.setAccessibleName("下一个月")
        self.next_button.setCursor(Qt.PointingHandCursor)
        self.next_button.clicked.connect(lambda: self._shift_month(1))
        _nav_ink = QApplication.palette().windowText().color().name()
        from app.ui.icons import IconFactory

        self.prev_button.setIcon(IconFactory(_nav_ink).icon("left", 16))
        self.next_button.setIcon(IconFactory(_nav_ink).icon("right", 16))
        self.prev_button.setIconSize(QSize(16, 16))
        self.next_button.setIconSize(QSize(16, 16))
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
        from app.ui.scroll_float import attach_flasher

        attach_flasher(self.note_edit, Qt.Vertical, inset=6)
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
        from app.ui.scroll_float import attach_flasher

        attach_flasher(self.content_edit, Qt.Vertical, inset=10)
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

    def flush_pending(self) -> None:
        """主窗口退出前兜底：防抖中的文本立即落盘。"""
        self._save_timer.stop()
        self._save()

    def closeEvent(self, event) -> None:  # noqa: N802
        self._save()
        self.closed.emit(str(self.doc.get("id") or ""))
        super().closeEvent(event)
