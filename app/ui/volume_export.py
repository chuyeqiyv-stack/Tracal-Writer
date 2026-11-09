# -*- coding: utf-8 -*-
"""导出格式选择对话框：整卷导出与整本导出共用（txt / docx 两个单选）。"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QRadioButton,
    QVBoxLayout,
)

from app.exporter import DOCX_FORMAT, TXT_FORMAT

MODE_VOLUME = "volume"
MODE_BOOK = "book"


class VolumeExportDialog(QDialog):
    """选导出格式：txt / docx 两个单选；mode 决定标题与说明文案。"""

    def __init__(self, parent=None, volume_title: str = "",
                 mode: str = MODE_VOLUME) -> None:
        super().__init__(parent)
        self.mode = mode
        self.setModal(True)
        self.resize(420, 220)

        is_book = mode == MODE_BOOK
        self.setWindowTitle("导出整本书" if is_book else "导出本卷")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 14)
        layout.setSpacing(10)

        title = QLabel(
            f"导出整本书：{volume_title}" if is_book else f"导出本卷：{volume_title}",
            self,
        )
        title.setWordWrap(True)
        layout.addWidget(title)

        hint_text = (
            "一章一个文件，按当前章节顺序连续编号；空章也会保留章名。"
            if is_book
            else "一卷一个文件，卷内章节按当前顺序合并；空章也会保留章名。"
        )
        hint = QLabel(hint_text, self)
        hint.setObjectName("DrawerHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.radio_txt = QRadioButton("纯文本（.txt，UTF-8 带 BOM）", self)
        self.radio_docx = QRadioButton("Word 文档（.docx，章名标题样式 + 章前分页）", self)
        self.radio_txt.setChecked(True)
        self._group = QButtonGroup(self)
        self._group.addButton(self.radio_txt)
        self._group.addButton(self.radio_docx)
        layout.addWidget(self.radio_txt)
        layout.addWidget(self.radio_docx)
        layout.addStretch(1)

        buttons = QDialogButtonBox(self)
        buttons.setOrientation(Qt.Horizontal)
        ok_button = buttons.addButton("下一步…", QDialogButtonBox.AcceptRole)
        buttons.addButton("取消", QDialogButtonBox.RejectRole)
        ok_button.setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def chosen_format(self) -> str:
        return DOCX_FORMAT if self.radio_docx.isChecked() else TXT_FORMAT
