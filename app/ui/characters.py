# -*- coding: utf-8 -*-
"""角色卡与角色关系思维导图。

- CharactersPanel：角色页主体（关键数字 + 搜索筛选 + 对齐表格 + 详情卡），
  嵌在书架主页「角色」页里，所有增删改直接读写 Storage 并发出 data_changed；
- CharacterDialog：角色卡编辑表单（新增 / 编辑共用）；
- RelationDialog：关系编辑（名称可自定义、有向 / 无向、亲密—敌对强度）；
- RelationCanvas + RelationBoardDialog：独立大窗口的思维导图画布，
  交互习惯与大纲画布一致（拖卡片、连线、Ctrl+滚轮缩放、按空白平移、
  边界「＋」扩容、左下角复位、隐形滚动滑块）。

数据保存在 library.json 的 characters / character_relations / character_board 中，
随项目备份与数据目录迁移一并带走。
"""
from __future__ import annotations

import math
import time

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QKeySequence,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QScrollArea,
    QSlider,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.models import (
    CHARACTER_ROLES,
    RELATION_NAMES,
    Character,
    GENDER_FIELD,
    new_id,
)
from app.services.character_templates import (
    MAX_NAME_LEN,
    CharacterTemplateStore,
    TemplateLimitError,
)
from app.ui import motion
from app.ui.dialogs import MessageBox
from app.ui.icons import IconFactory, flash_saved

# 画布几何（与大纲画布同一套习惯）
CANVAS_WIDTH = 4800
CANVAS_HEIGHT = 3000
EXTEND_STEP = 2400
ZOOM_MIN, ZOOM_MAX = 0.5, 2.0
ZOOM_STEP_FACTOR = 1.1

NODE_W, NODE_H = 156, 68          # 角色节点卡片固定尺寸
HANDLE_DIST = 10                  # 连接点命中半径
EDGE_HIT_DIST = 8                 # 连线命中距离

# 强度区间 [-3, 3] → 文案（亲密 ↔ 敌对）
INTENSITY_TEXT = {
    3: "亲密", 2: "亲近", 1: "友好", 0: "中性",
    -1: "疏远", -2: "敌对", -3: "死敌",
}
INTENSITY_POS = "#17B26A"         # 偏亲密（绿色）
INTENSITY_NEG = "#E5484D"         # 偏敌对（红色）

# 八方连接点方向（与大纲画布一致）
HANDLES = [
    (0.0, -1.0), (0.75, -1.0), (1.0, 0.0), (1.0, 0.75),
    (0.0, 1.0), (-0.75, 1.0), (-1.0, 0.0), (-1.0, -0.75),
]

ROLE_COLORS = {   # 定位色点（仅用于定位标识，同屏不超过 3 种数据色）
    "主角": "#7C5CE6",
    "反派": INTENSITY_NEG,
}
DEFAULT_ROLE_COLOR = "#8F959E"


def _tags_text(tags: list | None) -> str:
    return " · ".join(str(t) for t in (tags or []) if str(t).strip())


def _date_text(ts: float) -> str:
    try:
        return time.strftime("%Y-%m-%d", time.localtime(float(ts or 0)))
    except (TypeError, ValueError, OverflowError):
        return ""


# ======================================================================
# 角色卡编辑对话框
# ======================================================================
MEMO_MAX_LEN = 2000   # 人物短章长度上限（字符）


def build_card_text(v: dict) -> str:
    """角色卡 → 纯文本（只读视图复制 / 编辑卡复制共用）。"""
    lines = [f"角色卡：{v.get('name') or '未命名角色'}"]
    if v.get("alias"):
        lines.append(f"道号/别称：{v['alias']}")
    for f in v.get("card_fields") or []:
        if f.get("value"):
            lines.append(f"{f.get('label') or ''}：{f['value']}")
    if v.get("backstory"):
        lines.append(f"生平：{v['backstory']}")
    if v.get("memo"):
        lines.append(f"人物短章：{v['memo']}")
    return "\n".join(lines)


class CharacterDialog(QDialog):
    """新增 / 编辑角色卡（按参考图二：姓名 + 道号/别称 + 三列方框属性 + 生平）。

    方框属性（灵根/仙脉/性别…）可右键改名、删除、新增；右上小书本左键查看
    只读角色卡，右键弹出菜单。其余原有字段（定位 / 标签 / 外貌 / 性格等）
    收在「更多资料」里，能力不丢失。values() 返回完整角色 dict。
    """

    def __init__(self, parent, character: dict | None = None, storage=None) -> None:
        super().__init__(parent)
        src = character or {}
        # 新建角色：默认套用「最后一次使用」的模板（只预填标签类字段）
        self._storage = storage if storage is not None else getattr(parent, "storage", None)
        self._template_store = (
            CharacterTemplateStore(self._storage) if self._storage is not None else None
        )
        self._used_template_id = ""
        if not src and self._template_store is not None:
            tpl = self._template_store.default_template()
            if tpl is not None:
                src = dict(tpl["fields"])
                self._used_template_id = tpl["id"]
        self._src = dict(src)
        self.char_id = str(src.get("id") or "")
        # 人物短章（小书本右键编辑，随角色一起保存）
        self._memo = str(src.get("memo") or "")
        self.setWindowTitle("编辑角色" if self.char_id else "新建角色")
        self.resize(640, 720)
        self.setModal(True)

        outer = motion.card_surface(self, readable=True)

        # 顶行：标题 + 右上小书本（左键只读查看 / 右键编辑人物短章）
        top_row = QHBoxLayout()
        top_row.setSpacing(8)
        title = QLabel("角色卡", self)
        title.setObjectName("DrawerTitle")
        top_row.addWidget(title)
        top_row.addStretch(1)
        icon_color = QApplication.palette().color(QPalette.ColorRole.Text).name()
        win_theme = getattr(self.window(), "_theme", None)
        accent = str(win_theme.get("accent", "#7C5CFF")) if isinstance(win_theme, dict) else "#7C5CFF"
        self._book_factory = IconFactory(icon_color, active_color=accent)
        self.book_button = QToolButton(self)
        self.book_button.setObjectName("GhostButton")
        self.book_button.setIcon(self._book_factory.icon("book", 16, marked=bool(self._memo)))
        self.book_button.setCursor(Qt.PointingHandCursor)
        self.book_button.setFixedSize(34, 30)
        self.book_button.clicked.connect(self._view_card)
        self.book_button.setContextMenuPolicy(Qt.CustomContextMenu)
        self.book_button.customContextMenuRequested.connect(self._edit_memo)
        self._refresh_book_hint()
        top_row.addWidget(self.book_button)
        outer.addLayout(top_row)

        # 新建时才有模板入口：一行「模板 ▾ · 空白新建 · 存为模板 · ⋯」，不铺开面板
        self.template_box = None
        self.template_hint = None
        if not self.char_id and self._template_store is not None:
            outer.addLayout(self._build_template_row())

        # 长表单放进滚动区（常驻条关闭，只留隐形滑块）
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        from app.ui.scroll_float import ScrollFlasher as _SF

        self._form_flasher = _SF(scroll, Qt.Vertical, inset=6)
        form_widget = QWidget(scroll)
        form = QVBoxLayout(form_widget)
        form.setContentsMargins(0, 4, 8, 4)
        form.setSpacing(12)

        # —— 姓名 + 道号/别称 ——
        name_row = QHBoxLayout()
        name_row.setSpacing(10)
        name_lab = QLabel("姓名", form_widget)
        name_lab.setObjectName("FormLabel")
        name_row.addWidget(name_lab)
        self.name_edit = QLineEdit(str(src.get("name") or ""), form_widget)
        self.name_edit.setPlaceholderText("角色姓名")
        name_row.addWidget(self.name_edit, 1)
        self.alias_chip = self._make_chip("道号/别称", form_widget)
        name_row.addWidget(self.alias_chip)
        self.alias_edit = QLineEdit(str(src.get("alias") or ""), form_widget)
        self.alias_edit.setPlaceholderText("道号、别称、头衔")
        name_row.addWidget(self.alias_edit, 1)
        form.addLayout(name_row)

        # —— 三列方框属性（可右键增删改） ——
        self._boxed_container = QWidget(form_widget)
        self._boxed_grid = QGridLayout(self._boxed_container)
        self._boxed_grid.setContentsMargins(0, 0, 0, 0)
        self._boxed_grid.setHorizontalSpacing(10)
        self._boxed_grid.setVerticalSpacing(8)
        self._boxed: list[dict] = Character._parse_card_fields(
            src.get("card_fields"), str(src.get("gender") or "")
        )
        for item in self._boxed:
            item["chip"] = None
            item["edit"] = None
        form.addWidget(self._boxed_container)
        self._rebuild_boxed()

        box_ops = QHBoxLayout()
        box_ops.setSpacing(8)
        hint = QLabel("方框标签右键：改名 / 删除 / 新增", form_widget)
        hint.setObjectName("CharDetailSub")
        box_ops.addWidget(hint)
        box_ops.addStretch(1)
        add_field_button = QPushButton("新增属性", form_widget)
        add_field_button.setObjectName("GhostButton")
        add_field_button.setCursor(Qt.PointingHandCursor)
        add_field_button.setIcon(IconFactory(icon_color).icon("add", 14))
        add_field_button.setIconSize(QSize(14, 14))
        add_field_button.clicked.connect(lambda: self._add_field_at(len(self._boxed)))
        box_ops.addWidget(add_field_button)
        form.addLayout(box_ops)

        # —— 生平（多线） ——
        life_lab = QLabel("生平", form_widget)
        life_lab.setObjectName("FormLabel")
        form.addWidget(life_lab)
        self._backstory_edit = QTextEditPlain(form_widget)
        self._backstory_edit.setPlainText(str(src.get("backstory") or ""))
        self._backstory_edit.setPlaceholderText("生平经历、重要转折……（可多段）")
        self._backstory_edit.setFixedHeight(170)
        form.addWidget(self._backstory_edit)

        # —— 更多资料：保留既有全部字段 ——
        more_head = QLabel("更多资料", form_widget)
        more_head.setObjectName("CharDetailSection")
        form.addWidget(more_head)

        more = QGridLayout()
        more.setHorizontalSpacing(12)
        more.setVerticalSpacing(10)

        def more_row(row: int, col: int, label: str, editor: QWidget) -> None:
            lab = QLabel(label, form_widget)
            lab.setObjectName("FormLabel")
            lab.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            lab.setMinimumHeight(30)
            more.addWidget(lab, row, col * 2)
            more.addWidget(editor, row, col * 2 + 1)

        self.role_box = QComboBox(form_widget)
        self.role_box.setEditable(True)
        existing_roles = list(CHARACTER_ROLES)
        current_role = str(src.get("role") or "配角")
        if current_role and current_role not in existing_roles:
            existing_roles.append(current_role)
        self.role_box.addItems(existing_roles)
        self.role_box.setCurrentText(current_role)
        more_row(0, 0, "定位", self.role_box)

        self.age_edit = QLineEdit(str(src.get("age") or ""), form_widget)
        self.age_edit.setPlaceholderText("如 17 / 不详")
        more_row(0, 1, "年龄", self.age_edit)

        self.first_edit = QLineEdit(str(src.get("first_chapter") or ""), form_widget)
        self.first_edit.setPlaceholderText("如 第一章")
        more_row(1, 0, "首次出场", self.first_edit)

        self.tags_edit = QLineEdit(_tags_text(src.get("tags")).replace(" · ", "，"), form_widget)
        self.tags_edit.setPlaceholderText("多个标签用逗号分隔，如：天才，傲娇")
        more.addWidget(self.tags_edit, 1, 3)
        form.addLayout(more)

        def add_big(label: str, key: str, placeholder: str, height: int = 60) -> None:
            lab = QLabel(label, form_widget)
            lab.setObjectName("FormLabel")
            form.addWidget(lab)
            editor = QTextEditPlain(form_widget)
            editor.setPlainText(str(src.get(key) or ""))
            editor.setPlaceholderText(placeholder)
            editor.setFixedHeight(height)
            form.addWidget(editor)
            setattr(self, f"_{key}_edit", editor)

        add_big("外貌", "appearance", "外貌特征……")
        add_big("性格", "personality", "性格特点……")
        add_big("目标与动机", "goals", "想要什么、为什么……")
        add_big("说话风格 / 口头禅", "speech", "语气、习惯用语……")
        add_big("备注", "note", "其他备忘……")

        form.addStretch(1)
        scroll.setWidget(form_widget)
        self._form_flasher.watch(form_widget)
        outer.addWidget(scroll, 1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("取消", self)
        cancel.setObjectName("GhostButton")
        cancel.setCursor(Qt.PointingHandCursor)
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        ok = QPushButton("保存", self)
        ok.setObjectName("Primary")
        ok.setCursor(Qt.PointingHandCursor)
        ok.clicked.connect(self._accept)
        buttons.addWidget(ok)
        outer.addLayout(buttons)

        self.name_edit.setFocus()

    # ------------------------------------------------------------------
    # 模板入口（仅新建）：套用 / 空白新建 / 存为模板 / 重命名 / 删除
    # ------------------------------------------------------------------
    def _build_template_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        lab = QLabel("模板", self)
        lab.setObjectName("FormLabel")
        row.addWidget(lab)

        self.template_box = QComboBox(self)
        self.template_box.setMinimumWidth(150)
        self.template_box.setToolTip("选择模板会立即套用标签类字段；不影响姓名与正文")
        self.template_box.currentIndexChanged.connect(self._on_template_selected)
        row.addWidget(self.template_box)

        self.blank_button = QPushButton("空白新建", self)
        self.blank_button.setObjectName("GhostButton")
        self.blank_button.setCursor(Qt.PointingHandCursor)
        self.blank_button.setToolTip("不套用任何模板，清空标签类字段")
        self.blank_button.clicked.connect(self._blank_new)
        row.addWidget(self.blank_button)

        save_button = QPushButton("存为模板", self)
        save_button.setObjectName("GhostButton")
        save_button.setCursor(Qt.PointingHandCursor)
        save_button.setToolTip("把当前标签类字段存成模板（最多 5 个）")
        save_button.clicked.connect(self._save_as_template)
        row.addWidget(save_button)

        self.template_more = QToolButton(self)
        self.template_more.setObjectName("GhostButton")
        self.template_more.setText("⋯")
        self.template_more.setCursor(Qt.PointingHandCursor)
        self.template_more.setToolTip("重命名 / 删除当前模板")
        menu = QMenu(self.template_more)
        menu.addAction("重命名当前模板", self._rename_current_template)
        menu.addAction("删除当前模板", self._delete_current_template)
        self.template_more.setMenu(menu)
        self.template_more.setPopupMode(QToolButton.InstantPopup)
        row.addWidget(self.template_more)

        self.template_hint = QLabel("", self)
        self.template_hint.setObjectName("CharDetailSub")
        row.addWidget(self.template_hint)
        row.addStretch(1)

        self._reload_template_box(self._used_template_id)
        return row

    def _reload_template_box(self, select_id: str = "") -> None:
        if self.template_box is None or self._template_store is None:
            return
        self.template_box.blockSignals(True)
        self.template_box.clear()
        self.template_box.addItem("（不使用模板）", "")
        for tpl in self._template_store.templates():
            self.template_box.addItem(tpl["name"], tpl["id"])
        idx = self.template_box.findData(select_id) if select_id else 0
        self.template_box.setCurrentIndex(max(0, idx))
        self.template_box.blockSignals(False)
        self._update_template_hint()

    def _update_template_hint(self) -> None:
        if self.template_hint is None or self._template_store is None:
            return
        tpl = self._template_store.default_template()
        self.template_hint.setText(
            f"默认：{tpl['name']}" if tpl is not None else "默认：无（新建时空白）"
        )

    def _on_template_selected(self, _index: int) -> None:
        tid = str(self.template_box.currentData() or "")
        if tid == self._used_template_id:
            return
        if not tid:
            self._blank_new()
            return
        tpl = self._template_store.get(tid) if self._template_store is not None else None
        if tpl is None:
            return
        self._fill_profile(tpl["fields"])
        self._used_template_id = tid
        self._update_template_hint()

    def _fill_profile(self, fields: dict) -> None:
        """只覆盖标签类字段；姓名 / 道号 / 生平 / 目标 / 备注保持用户当前输入。"""
        self.role_box.setCurrentText(str(fields.get("role") or "配角"))
        self.age_edit.setText(str(fields.get("age") or ""))
        self.tags_edit.setText("，".join(str(t) for t in fields.get("tags") or []))
        self._appearance_edit.setPlainText(str(fields.get("appearance") or ""))
        self._personality_edit.setPlainText(str(fields.get("personality") or ""))
        self._speech_edit.setPlainText(str(fields.get("speech") or ""))
        self._boxed = Character._parse_card_fields(
            fields.get("card_fields"), str(fields.get("gender") or "")
        )
        for item in self._boxed:
            item["chip"] = None
            item["edit"] = None
        self._rebuild_boxed()

    def _blank_new(self) -> None:
        self._fill_profile({})
        self._used_template_id = ""
        if self.template_box is not None:
            self.template_box.blockSignals(True)
            self.template_box.setCurrentIndex(0)
            self.template_box.blockSignals(False)
        self.name_edit.setFocus()

    def _save_as_template(self) -> None:
        if self._template_store is None:
            return
        values = self._current_values()
        default_name = "" if values["name"] == "未命名角色" else values["name"]
        name, ok = QInputDialog.getText(self, "保存为模板", "模板名称：", text=default_name)
        if not ok:
            return
        name = name.strip()[:MAX_NAME_LEN]
        if not name:
            MessageBox.warning(self, "模板名称为空", "请填写模板名称后再保存。")
            return
        existing = self._template_store.find_by_name(name)
        if existing is not None:
            if not MessageBox.question(
                self, "模板重名", f"已有同名模板「{name}」，要覆盖它吗？"
            ):
                return
            tpl = self._template_store.save_template(name, values, overwrite_id=existing["id"])
            self._reload_template_box(tpl["id"])
            self._used_template_id = tpl["id"]
            return
        try:
            tpl = self._template_store.save_template(name, values)
        except TemplateLimitError:
            oldest = self._template_store.oldest()
            if oldest is None or not MessageBox.question(
                self,
                "模板已满",
                f"最多保存 5 个模板。要覆盖最久未使用的「{oldest['name']}」吗？\n"
                "选择「否」将取消本次保存。",
            ):
                return
            tpl = self._template_store.save_template(
                name, values, overwrite_id=oldest["id"]
            )
        self._reload_template_box(tpl["id"])
        self._used_template_id = tpl["id"]

    def _rename_current_template(self) -> None:
        if self._template_store is None or self.template_box is None:
            return
        tid = str(self.template_box.currentData() or "")
        tpl = self._template_store.get(tid)
        if tpl is None:
            MessageBox.information(self, "未选择模板", "请先在下拉框选择一个模板。")
            return
        name, ok = QInputDialog.getText(
            self, "重命名模板", "新的模板名称：", text=tpl["name"]
        )
        if not ok:
            return
        name = name.strip()[:MAX_NAME_LEN]
        if not name:
            MessageBox.warning(self, "模板名称为空", "请填写模板名称。")
            return
        clash = self._template_store.find_by_name(name)
        overwrite = False
        if clash is not None and clash["id"] != tid:
            if not MessageBox.question(self, "模板重名", f"已有同名模板「{name}」，覆盖它吗？"):
                return
            overwrite = True
        if self._template_store.rename(tid, name, overwrite=overwrite):
            self._reload_template_box(tid)
            self._used_template_id = tid

    def _delete_current_template(self) -> None:
        if self._template_store is None or self.template_box is None:
            return
        tid = str(self.template_box.currentData() or "")
        tpl = self._template_store.get(tid)
        if tpl is None:
            MessageBox.information(self, "未选择模板", "请先在下拉框选择一个模板。")
            return
        if not MessageBox.question(self, "删除模板", f"确定删除模板「{tpl['name']}」吗？"):
            return
        self._template_store.delete(tid)
        self._used_template_id = ""
        self._reload_template_box("")

    # ------------------------------------------------------------------
    # 方框属性
    # ------------------------------------------------------------------
    def _make_chip(self, text: str, parent: QWidget) -> QToolButton:
        chip = QToolButton(parent)
        chip.setObjectName("TagChip")
        chip.setText(text)
        chip.setCursor(Qt.PointingHandCursor)
        chip.setFixedHeight(28)
        return chip

    def _rebuild_boxed(self, focus_index: int | None = None) -> None:
        # 拆控件前先把已输入的文本回写，避免增删/改名时丢掉其它行的内容
        for entry in self._boxed:
            if entry.get("edit") is not None:
                entry["value"] = entry["edit"].text()
        while self._boxed_grid.count():
            item = self._boxed_grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        for i, entry in enumerate(self._boxed):
            cell = QWidget(self._boxed_container)
            row_layout = QHBoxLayout(cell)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(8)
            chip = self._make_chip(entry["label"], cell)
            chip.setContextMenuPolicy(Qt.CustomContextMenu)
            chip.customContextMenuRequested.connect(
                lambda _pos, idx=i: self._field_menu(idx)
            )
            chip.clicked.connect(lambda _checked=False, idx=i: self._focus_field(idx))
            edit = QLineEdit(entry.get("value") or "", cell)
            edit.setPlaceholderText("—")
            row_layout.addWidget(chip)
            row_layout.addWidget(edit, 1)
            entry["chip"] = chip
            entry["edit"] = edit
            self._boxed_grid.addWidget(cell, i // 3, i % 3)
            if focus_index == i:
                edit.setFocus()

    def _focus_field(self, index: int) -> None:
        if 0 <= index < len(self._boxed):
            self._boxed[index]["edit"].setFocus()

    def _field_menu(self, index: int) -> None:
        entry = self._boxed[index]
        menu = QMenu(self)
        menu.addAction("编辑标签名", lambda: self._rename_field(index))
        menu.addAction("新增一项", lambda: self._add_field_at(index + 1))
        menu.addAction("删除此项", lambda: self._remove_field(index))
        chip = entry["chip"]
        menu.exec(chip.mapToGlobal(chip.rect().bottomLeft()))

    def _rename_field(self, index: int) -> None:
        entry = self._boxed[index]
        text, ok = QInputDialog.getText(
            self, "编辑标签名", "标签名：", text=entry["label"]
        )
        text = text.strip()
        if ok and text:
            entry["label"] = text
            self._rebuild_boxed(focus_index=index)

    def _add_field_at(self, index: int) -> None:
        text, ok = QInputDialog.getText(self, "新增属性", "新标签名：")
        text = text.strip()
        if not ok or not text:
            return
        index = max(0, min(index, len(self._boxed)))
        self._boxed.insert(index, {"label": text, "value": "", "chip": None, "edit": None})
        self._rebuild_boxed(focus_index=index)

    def _remove_field(self, index: int) -> None:
        label = self._boxed[index]["label"]
        if not MessageBox.question(self, "删除属性", f"确定删除「{label}」及其内容吗？"):
            return
        del self._boxed[index]
        self._rebuild_boxed()

    def _card_fields(self) -> list[dict]:
        return [
            {"label": str(e["label"] or "").strip(), "value": e["edit"].text().strip()}
            for e in self._boxed
            if str(e.get("label") or "").strip()
        ]

    # ------------------------------------------------------------------
    # 小书本：查看 / 复制
    # ------------------------------------------------------------------
    def _current_values(self) -> dict:
        tags = [
            t.strip()
            for t in self.tags_edit.text().replace("，", ",").split(",")
            if t.strip()
        ]
        now = time.time()
        src = self._src
        fields = self._card_fields()
        gender = next((f["value"] for f in fields if f["label"] == GENDER_FIELD), "")
        return {
            "id": self.char_id or new_id(),
            "name": self.name_edit.text().strip() or "未命名角色",
            "alias": self.alias_edit.text().strip(),
            "role": self.role_box.currentText().strip() or "配角",
            "gender": gender,
            "age": self.age_edit.text().strip(),
            "appearance": self._appearance_edit.toPlainText().strip(),
            "personality": self._personality_edit.toPlainText().strip(),
            "backstory": self._backstory_edit.toPlainText().strip(),
            "goals": self._goals_edit.toPlainText().strip(),
            "speech": self._speech_edit.toPlainText().strip(),
            "tags": tags,
            "first_chapter": self.first_edit.text().strip(),
            "note": self._note_edit.toPlainText().strip(),
            "memo": self._memo,
            "card_fields": fields,
            # 画布坐标、提取字段与创建时间不由表单编辑，原样保留
            "x": float(src.get("x") or 0),
            "y": float(src.get("y") or 0),
            "chapters": [str(c) for c in (src.get("chapters") or []) if str(c).strip()],
            "description": str(src.get("description") or ""),
            "created_at": float(src.get("created_at") or now),
            "updated_at": now,
        }

    def _view_card(self) -> None:
        CharacterViewDialog(self, self._current_values()).exec()

    # ------------------------------------------------------------------
    # 人物短章：右键小书本编辑（Ctrl+S 保存 / Esc 取消）
    # ------------------------------------------------------------------
    def _refresh_book_hint(self) -> None:
        if self._memo.strip():
            preview = self._memo.strip().replace("\n", " ")[:30]
            self.book_button.setToolTip(f"人物短章（已填写）：{preview}\n左键查看 · 右键编辑")
        else:
            self.book_button.setToolTip("人物短章：右键撰写；左键查看角色卡")
        self.book_button.setIcon(self._book_factory.icon("book", 16, marked=bool(self._memo.strip())))

    def _edit_memo(self, *_args) -> None:
        name = self.name_edit.text().strip() or "未命名角色"
        dlg = MemoDialog(self, name, self._memo)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._memo = dlg.memo_text()
            self._refresh_book_hint()

    def _copy_card_text(self, v: dict | None = None) -> None:
        v = v or self._current_values()
        QApplication.clipboard().setText(build_card_text(v))
        MessageBox.information(self, "已复制", "角色卡文本已复制到剪贴板。")

    def _accept(self) -> None:
        if not self.name_edit.text().strip():
            MessageBox.warning(self, "缺少姓名", "请先填写角色姓名。")
            self.name_edit.setFocus()
            return
        # 用模板新建成功 → 该模板成为下一次新建的默认模板
        if not self.char_id and self._used_template_id and self._template_store is not None:
            self._template_store.mark_used(self._used_template_id)
        self.accept()

    def values(self) -> dict:
        return self._current_values()


class CharacterViewDialog(QDialog):
    """角色卡只读查看（小书本左键打开）：图二分区，全部为只读 QLabel。"""

    def __init__(self, parent, character: dict) -> None:
        super().__init__(parent)
        self.setWindowTitle("查看角色卡")
        self.resize(620, 700)
        self.setModal(True)
        outer = motion.card_surface(self, readable=True)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget(scroll)
        form = QVBoxLayout(body)
        form.setContentsMargins(4, 4, 12, 4)
        form.setSpacing(12)
        # 只读卡同样使用隐形滑块（与编辑卡一致）
        from app.ui.scroll_float import attach_flasher

        self._view_flasher = attach_flasher(scroll, Qt.Vertical, inset=6)

        title = QLabel("角色卡", body)
        title.setObjectName("DrawerTitle")
        form.addWidget(title)

        name_row = QHBoxLayout()
        name = QLabel(str(character.get("name") or "未命名角色"), body)
        name.setObjectName("CharDetailTitle")
        name_row.addWidget(name)
        name_row.addStretch(1)
        alias = str(character.get("alias") or "")
        if alias:
            alias_chip = QLabel("道号/别称", body)
            alias_chip.setObjectName("TagChip")
            name_row.addWidget(alias_chip)
            alias_v = QLabel(alias, body)
            alias_v.setObjectName("CharDetailValue")
            name_row.addWidget(alias_v)
        form.addLayout(name_row)

        fields = character.get("card_fields") or Character._parse_card_fields(
            None, str(character.get("gender") or "")
        )
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        for i, f in enumerate(fields):
            cell = QFrame(body)
            cell.setObjectName("CharRelRow")
            cell_layout = QHBoxLayout(cell)
            cell_layout.setContentsMargins(8, 5, 8, 5)
            cell_layout.setSpacing(8)
            chip = QLabel(str(f.get("label") or ""), cell)
            chip.setObjectName("TagChip")
            cell_layout.addWidget(chip)
            value = QLabel(str(f.get("value") or "—"), cell)
            value.setObjectName("CharDetailValue")
            value.setWordWrap(True)
            cell_layout.addWidget(value, 1)
            grid.addWidget(cell, i // 3, i % 3)
        form.addLayout(grid)

        backstory = str(character.get("backstory") or "")
        head = QLabel("生平", body)
        head.setObjectName("CharDetailSection")
        form.addWidget(head)
        life = QLabel(backstory or "（暂无生平）", body)
        life.setObjectName("CharDetailValue")
        life.setWordWrap(True)
        life.setTextInteractionFlags(Qt.TextSelectableByMouse)
        form.addWidget(life)

        # 人物短章（小书本右键撰写的内容，只读展示；无内容不占位）
        memo_text = str(character.get("memo") or "")
        if memo_text.strip():
            memo_head = QLabel("人物短章", body)
            memo_head.setObjectName("CharDetailSection")
            form.addWidget(memo_head)
            memo_lab = QLabel(memo_text, body)
            memo_lab.setObjectName("CharDetailValue")
            memo_lab.setWordWrap(True)
            memo_lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
            form.addWidget(memo_lab)

        sub_parts = [
            p for p in (
                str(character.get("role") or ""),
                str(character.get("age") or ""),
                str(character.get("first_chapter") or ""),
            ) if p
        ]
        if sub_parts:
            extra = QLabel(" · ".join(sub_parts), body)
            extra.setObjectName("CharDetailSub")
            extra.setWordWrap(True)
            form.addWidget(extra)

        form.addStretch(1)
        scroll.setWidget(body)
        self._view_flasher.watch(body)
        outer.addWidget(scroll, 1)

        buttons = QHBoxLayout()
        # 复制能力原在编辑卡右键菜单，右键改为短章编辑后迁到只读视图此处
        copy_btn = QPushButton("复制卡片文本", self)
        copy_btn.setObjectName("GhostButton")
        copy_btn.setCursor(Qt.PointingHandCursor)
        copy_btn.clicked.connect(lambda: self._copy_text(character))
        buttons.addWidget(copy_btn)
        buttons.addStretch(1)
        close = QPushButton("关闭", self)
        close.setObjectName("Primary")
        close.setCursor(Qt.PointingHandCursor)
        close.setMinimumWidth(112)
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        outer.addLayout(buttons)

    def _copy_text(self, character: dict) -> None:
        QApplication.clipboard().setText(build_card_text(character))
        MessageBox.information(self, "已复制", "角色卡文本已复制到剪贴板。")


class QTextEditPlain(QTextEdit):
    """无富文本的多行输入（Tab 跳出焦点，不插入制表符）。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAcceptRichText(False)
        self.setTabChangesFocus(True)
        # 常驻滚动条关闭，统一用隐形滑块（打字带动滚动也浮现）
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        from app.ui.scroll_float import attach_flasher

        self._text_flasher = attach_flasher(self, Qt.Vertical, inset=6)


# ======================================================================
# 人物短章编辑（小书本右键）
# ======================================================================
class MemoDialog(QDialog):
    """人物短章多行纯文本编辑：≥520×360，右下保存 / 取消，Ctrl+S 保存，Esc 取消。

    长度上限 MEMO_MAX_LEN：超限红字提示并禁用保存（不强制截断，删回即可）。
    """

    def __init__(self, parent, character_name: str, text: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle(f"人物短章 · {character_name}")
        self.setModal(True)
        self.resize(560, 440)
        self.setMinimumSize(520, 360)
        outer = motion.card_surface(self, content=(22, 18, 22, 16), readable=True)

        title = QLabel("人物短章", self)
        title.setObjectName("DrawerTitle")
        outer.addWidget(title)
        sub = QLabel(
            f"{character_name} · 记录这个人物的小传、心声或片段；随角色卡一起保存",
            self)
        sub.setObjectName("CharDetailSub")
        sub.setWordWrap(True)
        outer.addWidget(sub)

        self.editor = QTextEditPlain(self)
        self.editor.setPlaceholderText("为这个人物写一段短章吧……（纯文本，保留换行与空行）")
        # 旧数据若异常超长，先原样载入再提示
        self.editor.setPlainText(str(text or ""))
        outer.addWidget(self.editor, 1)

        bar = QHBoxLayout()
        self.count_label = QLabel(self)
        self.count_label.setObjectName("CharDetailSub")
        bar.addWidget(self.count_label)
        bar.addStretch(1)
        cancel = QPushButton("取消", self)
        cancel.setObjectName("GhostButton")
        cancel.setCursor(Qt.PointingHandCursor)
        cancel.setMinimumWidth(88)
        cancel.clicked.connect(self.reject)
        bar.addWidget(cancel)
        self.save_btn = QPushButton("保存", self)
        self.save_btn.setObjectName("Primary")
        self.save_btn.setCursor(Qt.PointingHandCursor)
        self.save_btn.setMinimumWidth(88)
        self.save_btn.clicked.connect(self._save)
        bar.addWidget(self.save_btn)
        outer.addLayout(bar)

        QShortcut(QKeySequence("Ctrl+S"), self, activated=self._save)
        self.editor.textChanged.connect(self._refresh_count)
        self._refresh_count()
        self.editor.setFocus()

    def _refresh_count(self) -> None:
        n = len(self.editor.toPlainText())
        over = n > MEMO_MAX_LEN
        if over:
            self.count_label.setStyleSheet("color:#D03A2C;")
            self.count_label.setText(f"已超出 {n - MEMO_MAX_LEN} 字（上限 {MEMO_MAX_LEN} 字，无法保存）")
        else:
            self.count_label.setStyleSheet("")
            self.count_label.setText(f"{n} / {MEMO_MAX_LEN} 字")
        self.save_btn.setEnabled(not over)

    def _save(self) -> None:
        if len(self.editor.toPlainText()) > MEMO_MAX_LEN:
            return
        self.accept()

    def memo_text(self) -> str:
        return self.editor.toPlainText()


# ======================================================================
# 关系编辑对话框
# ======================================================================
class RelationDialog(QDialog):
    """新增 / 编辑一条角色关系。values() 返回关系 dict 片段。"""

    def __init__(
        self,
        parent,
        from_name: str,
        to_name: str,
        relation: dict | None = None,
    ) -> None:
        super().__init__(parent)
        src = relation or {}
        self.setWindowTitle("编辑关系" if src.get("id") else "新建关系")
        self.setFixedWidth(440)
        self.setModal(True)

        layout = motion.card_surface(self, content=(24, 20, 24, 16), readable=True)

        title = QLabel("编辑关系" if src.get("id") else "新建关系", self)
        title.setObjectName("DrawerTitle")
        layout.addWidget(title)

        self._pair_text = (from_name, to_name)
        self._color = str(src.get("color") or "")
        self.pair = QLabel(self)
        self.pair.setObjectName("CharDetailSub")
        layout.addWidget(self.pair)

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(10)

        lab = QLabel("关系", self)
        lab.setObjectName("FormLabel")
        lab.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.name_box = QComboBox(self)
        self.name_box.setEditable(True)
        names = list(RELATION_NAMES)
        current = str(src.get("name") or "")
        if current and current not in names:
            names.append(current)
        self.name_box.addItems(names)
        self.name_box.setCurrentText(current or "朋友")
        grid.addWidget(lab, 0, 0)
        grid.addWidget(self.name_box, 0, 1)

        lab = QLabel("方向", self)
        lab.setObjectName("FormLabel")
        lab.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        # v3：新建默认有向；同两个角色可各持一条反向独立关系
        self.directed_check = QCheckBox("有向（按下方方向画箭头）", self)
        self.directed_check.setChecked(True if not src.get("id") else bool(src.get("directed")))
        self.directed_check.toggled.connect(self._sync_direction)
        grid.addWidget(lab, 1, 0)
        grid.addWidget(self.directed_check, 1, 1)

        # 方向二选一：A → B（默认）/ B → A；反向关系可再建一条
        from PySide6.QtWidgets import QRadioButton, QButtonGroup

        self._a_name, self._b_name = from_name, to_name
        self.rb_ab = QRadioButton(f"{from_name} → {to_name}", self)
        self.rb_ba = QRadioButton(f"{to_name} → {from_name}", self)
        # 旧 bidirectional 单线编辑时默认保持正向；用户可改向或直接保存（保存即拆边）
        self.rb_ab.setChecked(str(src.get("from_id") or "") != "__REV__")
        self.rb_ba.setChecked(False)
        self._dir_group = QButtonGroup(self)
        self._dir_group.addButton(self.rb_ab)
        self._dir_group.addButton(self.rb_ba)
        for rb in (self.rb_ab, self.rb_ba):
            rb.toggled.connect(self._sync_direction)
        dir_box = QHBoxLayout()
        dir_box.setSpacing(12)
        dir_box.addWidget(self.rb_ab)
        dir_box.addWidget(self.rb_ba)
        dir_box.addStretch(1)
        grid.addWidget(QLabel("", self), 2, 0)
        grid.addLayout(dir_box, 2, 1)

        self._legacy_bidir = src.get("bidirectional") is True and bool(src.get("directed"))
        if self._legacy_bidir:
            warn = QLabel("这是旧版「一根线两端箭头」关系；保存后会自动拆成两条反向独立关系。", self)
            warn.setObjectName("CharDetailSub")
            warn.setWordWrap(True)
            warn.setStyleSheet("color:#B25E09;")
            grid.addWidget(QLabel("", self), 3, 0)
            grid.addWidget(warn, 3, 1)

        lab = QLabel("强度", self)
        lab.setObjectName("FormLabel")
        lab.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        try:
            current_intensity = int(src.get("intensity") or 0)
        except (TypeError, ValueError):
            current_intensity = 0
        current_intensity = max(-3, min(3, current_intensity))
        self.intensity_slider = QSlider(Qt.Horizontal, self)
        self.intensity_slider.setRange(-3, 3)
        self.intensity_slider.setValue(current_intensity)
        self.intensity_label = QLabel("", self)
        self.intensity_label.setAlignment(Qt.AlignCenter)
        self.intensity_label.setFixedWidth(44)
        self._sync_intensity()
        self.intensity_slider.valueChanged.connect(lambda _v: self._sync_intensity())
        slider_row = QHBoxLayout()
        slider_row.setSpacing(8)
        slider_row.addWidget(self.intensity_slider, 1)
        slider_row.addWidget(self.intensity_label)
        row_intensity = 4
        grid.addWidget(lab, row_intensity, 0)
        grid.addLayout(slider_row, row_intensity, 1)

        lab = QLabel("备注", self)
        lab.setObjectName("FormLabel")
        lab.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.note_edit = QLineEdit(str(src.get("note") or ""), self)
        self.note_edit.setPlaceholderText("补充说明（可留空）")
        grid.addWidget(lab, 5, 0)
        grid.addWidget(self.note_edit, 5, 1)
        layout.addLayout(grid)

        hint = QLabel("滑杆向左偏敌对，向右偏亲密；反向关系（对方 → TA）可以再建一条，二者各自独立。", self)
        hint.setObjectName("CharDetailSub")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("取消", self)
        cancel.setObjectName("GhostButton")
        cancel.setCursor(Qt.PointingHandCursor)
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        ok = QPushButton("保存", self)
        ok.setObjectName("Primary")
        ok.setCursor(Qt.PointingHandCursor)
        ok.clicked.connect(self.accept)
        buttons.addWidget(ok)
        layout.addLayout(buttons)

        self._sync_direction()

    def _sync_direction(self) -> None:
        directed = self.directed_check.isChecked()
        reverse = self.rb_ba.isChecked()
        for rb in (self.rb_ab, self.rb_ba):
            rb.setEnabled(directed)
        a, b = self._pair_text
        if not directed:
            self.pair.setText(f"{a}   ↔   {b} （无向：不画箭头）")
        elif reverse:
            self.pair.setText(f"{b}   →   {a} （反向：箭头从 {b} 指向 {a}）")
        else:
            self.pair.setText(f"{a}   →   {b} （箭头从 {a} 指向 {b}）")

    def is_reverse(self) -> bool:
        return self.directed_check.isChecked() and self.rb_ba.isChecked()

    def legacy_bidir(self) -> bool:
        return self._legacy_bidir

    def _sync_intensity(self) -> None:
        value = self.intensity_slider.value()
        self.intensity_label.setText(INTENSITY_TEXT.get(value, "中性"))
        color = INTENSITY_POS if value > 0 else (INTENSITY_NEG if value < 0 else "#8F959E")
        self.intensity_label.setStyleSheet(f"color:{color};font-weight:600;")

    def values(self) -> dict:
        # v3：新数据一律独立有向/无向单边，不再写 bidirectional=true
        return {
            "name": self.name_box.currentText().strip() or "关系",
            "directed": self.directed_check.isChecked(),
            "bidirectional": False,
            "intensity": self.intensity_slider.value(),
            "note": self.note_edit.text().strip(),
            "color": self._color,
        }


# ======================================================================
# 关系图画布
# ======================================================================
class RelationCanvas(QWidget):
    """角色关系思维导图画布：节点卡片与关系连线全部自绘。"""

    changed = Signal()                    # 数据变化（触发保存）
    bounds_changed = Signal()             # 画布尺寸变化（更新边界按钮）
    edit_character_requested = Signal(str)          # 双击节点 → 编辑角色卡
    delete_character_requested = Signal(str)        # 右键删除角色（主窗口确认）
    edit_relation_requested = Signal(dict)          # 双击 / 右键编辑关系
    create_relation_requested = Signal(str, str)    # 从连接点拖到另一节点
    add_character_requested = Signal(QPointF)       # 空白右键新建角色（带画布坐标）

    def __init__(self, theme: dict, parent=None) -> None:
        super().__init__(parent)
        self.theme = theme
        self.characters: list[dict] = []
        self.relations: list[dict] = []
        self._width = CANVAS_WIDTH
        self._height = CANVAS_HEIGHT
        self._zoom = 1.0
        self._apply_view_size()
        self.setCursor(Qt.OpenHandCursor)
        self.setAttribute(Qt.WA_NoSystemBackground, True)

        self._selected_char = ""
        self._selected_edge = ""
        self._drag_node: dict | None = None
        self._press_pos = QPointF()
        self._press_node_pos = QPointF()
        self._moved = False
        self._panning = False
        self._press_scroll = None
        self._press_raw = QPointF()
        self._connecting = False
        self._connect_from: dict | None = None
        self._connect_pos = QPointF()
        self._hover_node: dict | None = None
        self._hover_edge: dict | None = None
        self.setMouseTracking(True)
        self._build_paint_cache()

    # ------------------------------------------------------------------
    # 数据
    # ------------------------------------------------------------------
    def set_theme(self, theme: dict) -> None:
        self.theme = theme
        self._build_paint_cache()
        self.update()

    # ------------------------------------------------------------------
    # 绘制缓存（避免每帧新建 QFont / QPen / QColor，50+ 节点也不掉帧）
    # ------------------------------------------------------------------
    def _build_paint_cache(self) -> None:
        theme = self.theme or {}
        panel_alt = str(theme.get("panel_alt", "#F7F8F9"))
        panel = str(theme.get("panel", "#FFFFFF"))
        text = str(theme.get("text", "#1F2329"))
        subtext = str(theme.get("subtext", "#8F959E"))
        border = str(theme.get("border", "#EDEEF1"))
        border_strong = str(theme.get("border_strong", "#DFE3E9"))
        accent = str(theme.get("accent", "#7C5CE6"))

        font = QFont(self.font())
        self._pf_label = QFont(font)
        self._pf_label.setPixelSize(11)
        self._pf_name = QFont(font)
        self._pf_name.setPixelSize(14)
        self._pf_name.setBold(True)
        self._pf_sub = QFont(font)
        self._pf_sub.setPixelSize(11)
        self._pf_label_m = QFontMetrics(self._pf_label)
        self._pf_name_m = QFontMetrics(self._pf_name)
        self._pf_sub_m = QFontMetrics(self._pf_sub)
        self._elide_cache: dict[tuple, str] = {}
        # v3 性能：角色 id 索引 + 关系配对记忆化（关系整体替换/增删改端点时版本号 +1，
        # 拖拽只改节点坐标不影响分组；配对分组只取决于关系的端点，改 name/intensity 不失效）
        self._char_index: dict[str, dict] = {}
        self._pair_cache: dict | None = None
        self._relations_version = -1
        self._rect_frame: dict[str, QRectF] = {}   # 单次 paint 内复用的节点矩形

        self._pc_bg = QColor(panel_alt)
        self._pc_dot = QColor(border)
        self._pc_dot.setAlpha(150)
        self._pc_text = QColor(text)
        self._pc_subtext = QColor(subtext)
        self._pc_panel = QColor(panel)
        self._pc_accent = QColor(accent)
        self._pc_accent_soft = QColor(accent).lighter(130)
        self._pc_border = QColor(border_strong)
        self._pc_shadow = QColor(0, 0, 0, 90 if str(theme.get("window")) != "#F5F6F8" else 26)

        edge_colors = {1: QColor(INTENSITY_POS), -1: QColor(INTENSITY_NEG), 0: QColor(subtext)}
        self._pc_edge_color = edge_colors
        self._pc_edge_pen = {}
        for key, color in edge_colors.items():
            pen = QPen(color, 1.4)
            pen.setCapStyle(Qt.RoundCap)
            self._pc_edge_pen[key] = pen
            pen_hi = QPen(color, 2.0)
            pen_hi.setCapStyle(Qt.RoundCap)
            self._pc_edge_pen[(key, True)] = pen_hi
        self._pc_pill_pen = {key: QPen(color, 1) for key, color in edge_colors.items()}
        self._pc_node_border_pen = QPen(self._pc_border, 1)
        self._pc_node_border_sel = QPen(self._pc_accent, 2)
        self._pc_node_border_hover = QPen(self._pc_accent_soft, 1)
        self._pc_text_pen = QPen(self._pc_text)
        self._pc_sub_pen = QPen(self._pc_subtext)
        self._pc_handle_pen = QPen(self._pc_panel, 2)
        self._pc_rubber_pen = QPen(self._pc_accent, 1.6, Qt.PenStyle.DashLine)
        self._pc_role = {role: QColor(color) for role, color in ROLE_COLORS.items()}
        self._pc_role_default = QColor(DEFAULT_ROLE_COLOR)

    def set_data(self, characters: list[dict], relations: list[dict],
                 width: int = 0, height: int = 0) -> None:
        self.characters = [dict(c) for c in (characters or [])]
        self.relations = [dict(r) for r in (relations or [])]
        self._invalidate_indexes()
        try:
            self._width = max(CANVAS_WIDTH, int(width or 0))
            self._height = max(CANVAS_HEIGHT, int(height or 0))
        except (TypeError, ValueError):
            self._width, self._height = CANVAS_WIDTH, CANVAS_HEIGHT
        self._layout_new_nodes()
        self._selected_char = ""
        self._selected_edge = ""
        self._elide_cache.clear()
        self._apply_view_size()
        self.update()

    def reload_characters(self, characters: list[dict]) -> None:
        """角色卡在别处被编辑后刷新（保留画布上的选择）。"""
        selected = self._selected_char
        self.characters = [dict(c) for c in (characters or [])]
        self._invalidate_indexes()
        self._layout_new_nodes()
        self._elide_cache.clear()
        self._selected_char = selected if self._char_by_id(selected) else ""
        self.update()

    def remove_character(self, char_id: str) -> None:
        """角色已被删除：同步移除节点与相关连线。"""
        self.characters = [c for c in self.characters if c.get("id") != char_id]
        self.relations = [
            r for r in self.relations
            if r.get("from_id") != char_id and r.get("to_id") != char_id
        ]
        self._invalidate_indexes()
        if self._selected_char == char_id:
            self._selected_char = ""
        self.update()

    def _invalidate_indexes(self) -> None:
        """角色 / 配对索引失效（数据整体替换或增删后调用；拖拽只改坐标不影响分组）。"""
        self._char_index = {str(c.get("id")): c for c in self.characters}
        self._bump_relations()

    def _bump_relations(self) -> None:
        """关系数据结构变更（整体替换 / 增 / 删 / 改端点）：配对缓存版本号 +1。"""
        self._relations_version += 1
        self._pair_cache = None

    def collect(self) -> tuple[list[dict], list[dict], int, int]:
        return (
            [dict(c) for c in self.characters],
            [dict(r) for r in self.relations],
            self._width,
            self._height,
        )

    def _layout_new_nodes(self) -> None:
        """从未布置过（x=y=0）的角色在画布中心附近散开。"""
        pending = [
            c for c in self.characters
            if not float(c.get("x") or 0) and not float(c.get("y") or 0)
        ]
        if not pending:
            return
        cx, cy = self._width / 2, self._height / 2
        for i, node in enumerate(pending):
            if i == 0:
                node["x"], node["y"] = cx - NODE_W / 2, cy - NODE_H / 2
                continue
            angle = math.radians(i * 137.5)      # 黄金角散开，避免一条直线
            radius = 120 + (i // 8) * 150
            node["x"] = cx + radius * math.cos(angle) - NODE_W / 2
            node["y"] = cy + radius * 0.65 * math.sin(angle) - NODE_H / 2

    # ------------------------------------------------------------------
    # 缩放（Ctrl + 滚轮）：与大纲画布一致
    # ------------------------------------------------------------------
    def _apply_view_size(self) -> None:
        self.setFixedSize(
            max(1, int(self._width * self._zoom)),
            max(1, int(self._height * self._zoom)),
        )

    def _to_logical(self, pos: QPointF) -> QPointF:
        z = self._zoom
        return QPointF(pos.x() / z, pos.y() / z)

    def zoom_at(self, steps: float, _device_pos: QPointF) -> None:
        old = self._zoom
        new = min(ZOOM_MAX, max(ZOOM_MIN, old * (ZOOM_STEP_FACTOR ** steps)))
        if abs(new - old) < 1e-9:
            return
        h_bar, v_bar = self._scrollbars()
        viewport = self._viewport()
        vw = viewport.width() if viewport is not None else self.width()
        vh = viewport.height() if viewport is not None else self.height()
        cx = (h_bar.value() + vw / 2) / old if h_bar is not None else vw / 2 / old
        cy = (v_bar.value() + vh / 2) / old if v_bar is not None else vh / 2 / old
        self._zoom = new
        self._apply_view_size()
        if h_bar is not None:
            h_bar.setValue(int(cx * new - vw / 2))
        if v_bar is not None:
            v_bar.setValue(int(cy * new - vh / 2))
        self.update()
        self.bounds_changed.emit()

    def wheelEvent(self, event) -> None:  # noqa: N802
        if event.modifiers() & Qt.ControlModifier:
            steps = event.angleDelta().y() / 120.0
            if steps:
                self.zoom_at(steps, QPointF(event.position()))
            event.accept()
            return
        super().wheelEvent(event)

    # ------------------------------------------------------------------
    # 画布扩容：向左 / 上扩容时平移全部节点
    # ------------------------------------------------------------------
    def extend(self, direction: str) -> None:
        shift_x = EXTEND_STEP if direction == "left" else 0
        shift_y = EXTEND_STEP if direction == "top" else 0
        if shift_x or shift_y:
            for node in self.characters:
                node["x"] = float(node.get("x") or 0) + shift_x
                node["y"] = float(node.get("y") or 0) + shift_y
            self._selected_char = ""
        if direction in ("left", "right"):
            self._width += EXTEND_STEP
        else:
            self._height += EXTEND_STEP
        self._apply_view_size()
        self.update()
        self.bounds_changed.emit()
        if shift_x or shift_y:
            h_bar, v_bar = self._scrollbars()

            def _compensate(dx: int, dy: int, h, v) -> None:
                if dx and h is not None:
                    h.setValue(h.value() + dx)
                if dy and v is not None:
                    v.setValue(v.value() + dy)

            QTimer.singleShot(0, lambda: _compensate(shift_x, shift_y, h_bar, v_bar))
        self.changed.emit()

    # ------------------------------------------------------------------
    # 几何辅助
    # ------------------------------------------------------------------
    def node_rect(self, node: dict) -> QRectF:
        # 单次 paint 内复用（100 边每边取两端 + 节点循环，避免重复构造）
        cid = str(node.get("id") or "")
        rect = self._rect_frame.get(cid) if self._rect_frame else None
        if rect is not None:
            return rect
        return QRectF(float(node.get("x") or 0), float(node.get("y") or 0), NODE_W, NODE_H)

    def _char_by_id(self, char_id: str) -> dict | None:
        # 热路径：100 边帧里每条边查 2 次，O(N) 线性扫描会放大成 O(E·N)
        node = self._char_index.get(char_id)
        if node is not None:
            return node
        node = next((c for c in self.characters if c.get("id") == char_id), None)
        if node is not None:        # 漏网（外部直接改列表）自愈进索引
            self._char_index[char_id] = node
        return node

    def _handle_pos(self, rect: QRectF, index: int) -> QPointF:
        fx, fy = HANDLES[index]
        return QPointF(
            rect.center().x() + fx * rect.width() / 2,
            rect.center().y() + fy * rect.height() / 2,
        )

    def _hit_test(self, pos: QPointF) -> tuple[dict | None, int]:
        """返回 (角色节点, 连接点序号)；-1 表示命中卡片主体。"""
        for node in reversed(self.characters):
            rect = self.node_rect(node)
            if node.get("id") == self._selected_char:
                for i in range(len(HANDLES)):
                    if (self._handle_pos(rect, i) - pos).manhattanLength() <= HANDLE_DIST:
                        return node, i
            if rect.contains(pos):
                return node, -1
        return None, -1

    @staticmethod
    def _border_point(rect: QRectF, toward: QPointF) -> QPointF:
        """线段（矩形中心 → toward）与矩形边框的交点。"""
        c = rect.center()
        d = toward - c
        if d.x() == 0 and d.y() == 0:
            return c
        tx = (rect.width() / 2) / abs(d.x()) if d.x() else float("inf")
        ty = (rect.height() / 2) / abs(d.y()) if d.y() else float("inf")
        t = min(tx, ty)
        return c + d * t

    @staticmethod
    def _seg_distance(p: QPointF, a: QPointF, b: QPointF) -> float:
        ab = b - a
        length2 = ab.x() * ab.x() + ab.y() * ab.y()
        if length2 == 0:
            return (p - a).manhattanLength()
        t = max(0.0, min(1.0, ((p - a).x() * ab.x() + (p - a).y() * ab.y()) / length2))
        foot = a + ab * t
        return ((p - foot).x() ** 2 + (p - foot).y() ** 2) ** 0.5

    def _edge_ends(self, edge: dict) -> tuple[dict | None, dict | None]:
        a = self._char_by_id(str(edge.get("from_id") or ""))
        b = self._char_by_id(str(edge.get("to_id") or ""))
        return a, b

    # ------------------------------------------------------------------
    # v3：同一对角色的两条反向有向边走互补弧线，不重叠
    # ------------------------------------------------------------------
    EDGE_CURVE = 34.0        # 弧凸固定量（逻辑像素）

    def _pair_map(self) -> dict:
        """无序角色对 → 该对上的有向边列表（只收集 directed 且非旧 bidir）。

        记忆化：分组只取决于关系的端点；键为自增版本号，关系整体替换 / 增 /
        删 / 改端点时调用方 +1（避免同长度不同内容错命中）。拖拽只改节点坐标、
        改 name/intensity 不 bump，直接沿用缓存。
        """
        if self._pair_cache is not None:
            return self._pair_cache
        groups: dict[tuple[str, str], list[dict]] = {}
        for e in self.relations:
            if not e.get("directed") or e.get("bidirectional") is True:
                continue
            f, t = str(e.get("from_id") or ""), str(e.get("to_id") or "")
            if not f or not t or f == t:
                continue
            pair = (f, t) if f < t else (t, f)
            groups.setdefault(pair, []).append(e)
        self._pair_cache = groups
        return groups

    def _arc_side(self, edge: dict, mate: dict | None) -> int:
        """本边弧线凸向：+1 或 -1。一对边按 id 字典序稳定分到相反两侧。"""
        if mate is None:
            return 0
        a = str(edge.get("id") or "")
        b = str(mate.get("id") or "")
        return 1 if a < b else -1

    def _edge_geometry(self, edge: dict, pair_map: dict | None = None):
        """返回 (start, end, control|None, side)；无反向配对时 control=None（直线）。"""
        a, b = self._edge_ends(edge)
        if a is None or b is None:
            return None
        ra, rb = self.node_rect(a), self.node_rect(b)
        pm = pair_map if pair_map is not None else self._pair_map()
        mate = None
        if edge.get("directed") and edge.get("bidirectional") is not True:
            peers = pm.get(tuple(sorted((str(edge.get("from_id")), str(edge.get("to_id"))))), [])
            opp = [e for e in peers if e is not edge
                   and str(e.get("from_id")) == str(edge.get("to_id"))
                   and str(e.get("to_id")) == str(edge.get("from_id"))]
            mate = opp[0] if opp else None
        side = self._arc_side(edge, mate)
        if mate is None or side == 0:
            start = self._border_point(ra, rb.center())
            end = self._border_point(rb, ra.center())
            return start, end, None, 0
        mid = (ra.center() + rb.center()) / 2
        # 法线方向以「节点 id 字典序」为固定基准，不随边方向反转，
        # 保证一对反向边一条凸向左上、一条凸向右下，几何互补不重叠
        id_a, id_b = str(a.get("id") or ""), str(b.get("id") or "")
        c0 = ra.center() if id_a < id_b else rb.center()
        c1 = rb.center() if id_a < id_b else ra.center()
        d = c1 - c0
        dlen = (d.x() * d.x() + d.y() * d.y()) ** 0.5 or 1.0
        nx, ny = -d.y() / dlen, d.x() / dlen
        ctrl = QPointF(mid.x() + nx * self.EDGE_CURVE * side,
                       mid.y() + ny * self.EDGE_CURVE * side)
        # 端点朝弧腹吸附：弧线在节点边缘的切入口与凸向一致，不与配对边重叠
        start = self._border_point(ra, ctrl)
        end = self._border_point(rb, ctrl)
        return start, end, ctrl, side

    @staticmethod
    def _quad_point(p0: QPointF, pc: QPointF, p2: QPointF, t: float) -> QPointF:
        u = 1.0 - t
        return QPointF(u * u * p0 + 2 * u * t * pc + t * t * p2)

    @staticmethod
    def _quad_tangent(p0: QPointF, pc: QPointF, p2: QPointF, t: float) -> QPointF:
        vx = 2 * (1 - t) * (pc.x() - p0.x()) + 2 * t * (p2.x() - pc.x())
        vy = 2 * (1 - t) * (pc.y() - p0.y()) + 2 * t * (p2.y() - pc.y())
        v = QPointF(vx, vy)
        ln = (v.x() * v.x() + v.y() * v.y()) ** 0.5 or 1.0
        return QPointF(v.x() / ln, v.y() / ln)

    def _edge_path(self, start, end, ctrl):
        path = QPainterPath(start)
        if ctrl is None:
            path.lineTo(end)
        else:
            path.quadTo(ctrl, end)
        return path

    def _hit_edge(self, pos: QPointF) -> dict | None:
        pair_map = self._pair_map()
        for edge in reversed(self.relations):
            geo = self._edge_geometry(edge, pair_map)
            if geo is None:
                continue
            start, end, ctrl, _side = geo
            if ctrl is None:
                if self._seg_distance(pos, start, end) <= EDGE_HIT_DIST:
                    return edge
                continue
            # 弧线：细分 28 段取最小距离；名称胶囊中点额外计入命中
            near = 1e9
            prev = start
            for i in range(1, 29):
                cur = self._quad_point(start, ctrl, end, i / 28)
                near = min(near, self._seg_distance(pos, prev, cur))
                prev = cur
            pill_mid = self._quad_point(start, ctrl, end, 0.5)
            d = ((pos.x() - pill_mid.x()) ** 2 + (pos.y() - pill_mid.y()) ** 2) ** 0.5
            near = min(near, max(0.0, d - 10.0))   # 名称胶囊命中宽容
            if near <= EDGE_HIT_DIST:
                return edge
        return None

    def _edge_color(self, edge: dict) -> QColor:
        raw = str(edge.get("color") or "").strip()
        if raw.startswith("#") and len(raw) == 7:
            color = QColor(raw)
            if color.isValid():
                return color
        try:
            intensity = int(edge.get("intensity") or 0)
        except (TypeError, ValueError):
            intensity = 0
        if intensity > 0:
            return QColor(INTENSITY_POS)
        if intensity < 0:
            return QColor(INTENSITY_NEG)
        return QColor(self.theme["subtext"])

    @staticmethod
    def _arrow_head(painter: QPainter, tip: QPointF, ux: float, uy: float, color: QColor) -> None:
        """在 tip 处沿单位方向 (ux, uy) 画实心三角箭头（全图唯一画法）。"""
        left = QPointF(tip.x() - uy * 5 - ux * 9, tip.y() + ux * 5 - uy * 9)
        right = QPointF(tip.x() + uy * 5 - ux * 9, tip.y() - ux * 5 - uy * 9)
        painter.setBrush(color)
        painter.setPen(Qt.NoPen)
        painter.drawPolygon([tip, left, right])

    # ------------------------------------------------------------------
    # 绘制
    # ------------------------------------------------------------------
    def _edge_intensity_key(self, edge: dict) -> int:
        try:
            v = int(edge.get("intensity") or 0)
        except (TypeError, ValueError):
            v = 0
        return 1 if v > 0 else (-1 if v < 0 else 0)

    def paintEvent(self, event) -> None:  # noqa: N802
        # 帧内节点矩形缓存：一次构建，边几何 / 节点绘制 / 连接点共用
        self._rect_frame = {
            str(c.get("id")): QRectF(float(c.get("x") or 0), float(c.get("y") or 0),
                                     NODE_W, NODE_H)
            for c in self.characters
        }
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        painter.fillRect(self.rect(), self._pc_bg)
        painter.scale(self._zoom, self._zoom)

        # 可视逻辑区域（视口裁剪用，标签 / 箭头预留余量）
        dev = event.rect()
        z = self._zoom
        clip = QRectF(dev.x() / z, dev.y() / z, dev.width() / z, dev.height() / z)
        clip_items = clip.adjusted(-60, -40, 60, 40)

        # 点阵背景（只画可视区域）
        step = 44
        painter.setPen(Qt.NoPen)
        painter.setBrush(self._pc_dot)
        x0 = max(0, int(clip.left()) // step) * step + step // 2
        y0 = max(0, int(clip.top()) // step) * step + step // 2
        y = y0
        while y <= clip.bottom():
            x = x0
            while x <= clip.right():
                painter.drawEllipse(QPointF(x, y), 1.2, 1.2)
                x += step
            y += step

        # 关系连线（视口外整段跳过）
        metrics = self._pf_label_m
        pair_map = self._pair_map()
        for edge in self.relations:
            geo = self._edge_geometry(edge, pair_map)
            if geo is None:
                continue
            start, end, ctrl, _side = geo
            line_box = QRectF(QRectF(start, end).normalized())
            if ctrl is not None:
                line_box = line_box.united(QRectF(ctrl, ctrl))
            if not line_box.adjusted(-50, -50, 50, 50).intersects(clip_items):
                continue
            highlight = (
                self._selected_edge == str(edge.get("id"))
                or self._selected_char in (edge.get("from_id"), edge.get("to_id"))
                or self._hover_edge is edge
            )
            key = self._edge_intensity_key(edge)
            color = self._pc_edge_color[key]
            painter.setPen(self._pc_edge_pen[(key, True)] if highlight else self._pc_edge_pen[key])
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(self._edge_path(start, end, ctrl))

            # 箭头：独立有向边在 to 端一个；旧 bidirectional 单线两端各一个（兼容未迁移数据）
            if edge.get("directed"):
                if ctrl is not None:
                    _t = self._quad_tangent(start, ctrl, end, 1.0)
                    ux, uy = _t.x(), _t.y()
                else:
                    d = end - start
                    dlen = (d.x() * d.x() + d.y() * d.y()) ** 0.5 or 1.0
                    ux, uy = d.x() / dlen, d.y() / dlen
                self._arrow_head(painter, end, ux, uy, color)
                if edge.get("bidirectional") is True:
                    if ctrl is not None:
                        _t0 = self._quad_tangent(start, ctrl, end, 0.0)
                        ux0, uy0 = _t0.x(), _t0.y()
                    else:
                        ux0, uy0 = -ux, -uy
                    self._arrow_head(painter, start, ux0, uy0, color)

            # 关系名称胶囊：直线取中点，弧线取 t=0.5
            name = str(edge.get("name") or "").strip()
            if name:
                mid = self._quad_point(start, ctrl or QPointF((start + end) / 2), end, 0.5)
                text_w = metrics.horizontalAdvance(name)
                pill = QRectF(mid.x() - text_w / 2 - 8, mid.y() - 10, text_w + 16, 20)
                painter.setPen(self._pc_pill_pen[key])
                painter.setBrush(self._pc_panel)
                painter.drawRoundedRect(pill, 10, 10)
                painter.setPen(self._pc_text_pen)
                painter.setFont(self._pf_label)
                painter.drawText(pill, Qt.AlignCenter, name)

        # 角色节点（视口外跳过）
        for node in self.characters:
            rect = self.node_rect(node)
            if rect.intersects(clip_items):
                self._paint_node(painter, node, rect)

        # 选中节点的八方连接点
        selected = self._char_by_id(self._selected_char)
        if selected is not None:
            rect = self.node_rect(selected)
            painter.setPen(self._pc_handle_pen)
            painter.setBrush(self._pc_accent)
            for i in range(len(HANDLES)):
                painter.drawEllipse(self._handle_pos(rect, i), 5, 5)

        # 拖出连线的橡皮筋
        if self._connecting and self._connect_from is not None:
            start = self.node_rect(self._connect_from).center()
            painter.setPen(self._pc_rubber_pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawLine(start, self._connect_pos)
            painter.setBrush(self._pc_accent)
            painter.drawEllipse(self._connect_pos, 4, 4)
        painter.end()
        self._rect_frame = {}   # 出帧即失效（坐标随时可能被拖拽改变）

    def _elided(self, node_id, kind, raw, width) -> str:
        key = (node_id, kind)
        cached = self._elide_cache.get(key)
        if cached is not None and cached[0] == raw:
            return cached[1]
        fm = self._pf_name_m if kind == "name" else self._pf_sub_m
        text = fm.elidedText(raw, Qt.ElideRight, width)
        self._elide_cache[key] = (raw, text)
        return text

    def _paint_node(self, painter: QPainter, node: dict, rect: QRectF) -> None:
        node_id = str(node.get("id") or "")
        selected = node_id == self._selected_char
        hovered = node is self._hover_node

        # 柔和投影
        painter.setPen(Qt.NoPen)
        painter.setBrush(self._pc_shadow)
        painter.drawRoundedRect(rect.adjusted(2.5, 3.5, 2.5, 3.5), 12, 12)

        if selected:
            painter.setPen(self._pc_node_border_sel)
        elif hovered:
            painter.setPen(self._pc_node_border_hover)
        else:
            painter.setPen(self._pc_node_border_pen)
        painter.setBrush(self._pc_panel)
        painter.drawRoundedRect(rect, 12, 12)

        role = str(node.get("role") or "")
        role_color = self._pc_role.get(role, self._pc_role_default)

        name = str(node.get("name") or "未命名角色")
        name = self._elided(node_id, "name", name, NODE_W - 48)

        painter.setFont(self._pf_name)
        painter.setPen(self._pc_text_pen)
        painter.drawText(
            QRectF(rect.x() + 14, rect.y() + 10, NODE_W - 28, 22),
            Qt.AlignLeft | Qt.AlignVCenter, name,
        )
        dot_x = rect.x() + 16 + self._pf_name_m.horizontalAdvance(name) + 8
        if dot_x < rect.right() - 20:
            painter.setPen(Qt.NoPen)
            painter.setBrush(role_color)
            painter.drawEllipse(QPointF(dot_x, rect.y() + 21), 3.2, 3.2)

        sub = str(node.get("alias") or "").strip() or role or " "
        sub = self._elided(node_id, "sub", sub, NODE_W - 28)
        painter.setFont(self._pf_sub)
        painter.setPen(self._pc_sub_pen)
        painter.drawText(
            QRectF(rect.x() + 14, rect.y() + 36, NODE_W - 28, 18),
            Qt.AlignLeft | Qt.AlignVCenter, sub,
        )

    # ------------------------------------------------------------------
    # 鼠标交互
    # ------------------------------------------------------------------
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.LeftButton:
            return super().mousePressEvent(event)
        raw = QPointF(event.position())
        pos = self._to_logical(raw)
        self._press_raw = raw
        node, handle = self._hit_test(pos)

        if node is not None and handle >= 0:
            self._connecting = True
            self._connect_from = node
            self._connect_pos = pos
            self.setCursor(Qt.CrossCursor)
            self.update()
            return

        if node is not None:
            self._selected_char = str(node.get("id"))
            self._selected_edge = ""
            self._drag_node = node
            self._press_pos = pos
            self._press_node_pos = QPointF(float(node.get("x") or 0), float(node.get("y") or 0))
            self._moved = False
            self.setCursor(Qt.ClosedHandCursor)
            self.update()
            return

        edge = self._hit_edge(pos)
        if edge is not None:
            self._selected_edge = str(edge.get("id"))
            self._selected_char = ""
            self.update()
            return

        # 空白处：平移画布
        self._panning = True
        self._press_scroll = self._scroll_values()
        self._press_pos = raw
        self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        raw = QPointF(event.position())
        pos = self._to_logical(raw)

        if self._connecting and self._connect_from is not None:
            self._connect_pos = pos
            self.update()
            return

        if self._drag_node is not None:
            delta = pos - self._press_pos
            if not self._moved and delta.manhattanLength() > 3:
                self._moved = True
            if self._moved:
                self._drag_node["x"] = self._press_node_pos.x() + delta.x()
                self._drag_node["y"] = self._press_node_pos.y() + delta.y()
                self.update()
            return

        if self._panning and self._press_scroll is not None:
            h_bar, v_bar = self._scrollbars()
            delta = raw - self._press_raw
            if h_bar is not None:
                h_bar.setValue(int(self._press_scroll[0] - delta.x()))
            if v_bar is not None:
                v_bar.setValue(int(self._press_scroll[1] - delta.y()))
            return

        # 悬停高亮
        node, _handle = self._hit_test(pos)
        if node is not self._hover_node:
            self._hover_node = node
            self.setCursor(Qt.PointingHandCursor if node is not None else Qt.OpenHandCursor)
            self.update()
        edge = None if node is not None else self._hit_edge(pos)
        if edge is not self._hover_edge:
            self._hover_edge = edge
            if edge is not None:
                self.setCursor(Qt.PointingHandCursor)
            self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.LeftButton:
            return super().mouseReleaseEvent(event)
        pos = self._to_logical(QPointF(event.position()))

        if self._connecting:
            source = self._connect_from
            self._connecting = False
            self._connect_from = None
            self.setCursor(Qt.OpenHandCursor)
            if source is not None:
                target, _handle = self._hit_test(pos)
                if target is not None and target is not source:
                    self.create_relation_requested.emit(
                        str(source.get("id")), str(target.get("id"))
                    )
            self.update()
            return

        if self._drag_node is not None:
            self._drag_node = None
            self.setCursor(Qt.OpenHandCursor)
            if self._moved:
                self.changed.emit()    # 坐标持久化
            else:
                self.update()          # 单击：仅选中
            return

        if self._panning:
            self._panning = False
            self._press_scroll = None
            self.setCursor(Qt.OpenHandCursor)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        pos = self._to_logical(QPointF(event.position()))
        node, _handle = self._hit_test(pos)
        if node is not None:
            self._selected_char = str(node.get("id"))
            self.update()
            self.edit_character_requested.emit(str(node.get("id")))
            return
        edge = self._hit_edge(pos)
        if edge is not None:
            self._selected_edge = str(edge.get("id"))
            self.update()
            self.edit_relation_requested.emit(dict(edge))
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        pos = self._to_logical(QPointF(event.pos()))
        node, _handle = self._hit_test(pos)

        if node is None:
            edge = self._hit_edge(pos)
            if edge is not None:
                self._selected_edge = str(edge.get("id"))
                self.update()
                menu = self._make_menu()
                menu.addAction(
                    "编辑关系",
                    lambda: self.edit_relation_requested.emit(dict(edge)),
                )
                menu.addAction("删除关系", lambda: self._delete_edge(edge))
                menu.exec(event.globalPos())
                menu.deleteLater()
                return
            menu = self._make_menu()
            menu.addAction(
                "新建角色",
                lambda: self.add_character_requested.emit(pos),
            )
            menu.exec(event.globalPos())
            return

        self._selected_char = str(node.get("id"))
        self.update()
        menu = self._make_menu()
        menu.addAction("编辑角色卡", lambda: self.edit_character_requested.emit(str(node.get("id"))))
        menu.addAction("删除角色", lambda: self.delete_character_requested.emit(str(node.get("id"))))
        menu.exec(event.globalPos())

    def _make_menu(self) -> QMenu:
        """统一的右键菜单：2 档浮层阴影。"""
        menu = QMenu(self)
        motion.apply_elevation(menu, 2, motion.app_is_dark())
        return menu

    def _delete_edge(self, edge: dict) -> None:
        self.relations = [r for r in self.relations if r is not edge and r.get("id") != edge.get("id")]
        self._bump_relations()
        if self._selected_edge == str(edge.get("id")):
            self._selected_edge = ""
        self.update()
        self.changed.emit()

    def add_edge(self, from_id: str, to_id: str, values: dict) -> None:
        """新建一条关系（已在 RelationDialog 里确认过）。"""
        if from_id == to_id:
            return
        # v3：同方向只允许一条；反方向（B→A）是另一条独立关系，允许并存
        for edge in self.relations:
            if str(edge.get("from_id")) == from_id and str(edge.get("to_id")) == to_id:
                MessageBox.information(
                    self, "同向关系已存在",
                    "这个方向的关系已有一条，双击连线可编辑；\n反向关系（对方 → TA）可以再建一条。")
                return
        edge = {"id": new_id(), "from_id": from_id, "to_id": to_id, "created_at": time.time()}
        edge.update(values)
        self.relations.append(edge)
        self._bump_relations()
        self._selected_edge = str(edge["id"])
        self.update()
        self.changed.emit()

    def update_edge(self, edge: dict, values: dict,
                    from_id: str | None = None, to_id: str | None = None) -> None:
        target = self._edge_by_id(str(edge.get("id")))
        if target is None:
            return
        # 端点可能被改向：先记录是否会真正变化，仅在端点变化时 bump（改 name/intensity 不动分组）
        old_from, old_to = str(target.get("from_id")), str(target.get("to_id"))
        target.update(values)
        if from_id is not None:
            target["from_id"] = from_id
        if to_id is not None:
            target["to_id"] = to_id
        # 编辑中改方向后，若与已有同向边撞车则放弃覆盖（避免静默合并）
        clash = any(
            e is not target
            and str(e.get("from_id")) == str(target.get("from_id"))
            and str(e.get("to_id")) == str(target.get("to_id"))
            for e in self.relations
        )
        if clash:
            MessageBox.information(self, "同向关系已存在", "改向后与已有同向关系重复，已忽略方向修改。")
            if from_id is not None:
                target["from_id"] = str(edge.get("from_id") or "")
            if to_id is not None:
                target["to_id"] = str(edge.get("to_id") or "")
        new_from, new_to = str(target.get("from_id")), str(target.get("to_id"))
        if (new_from, new_to) != (old_from, old_to):
            self._bump_relations()
        self.update()
        self.changed.emit()

    def _edge_by_id(self, edge_id: str) -> dict | None:
        return next((r for r in self.relations if r.get("id") == edge_id), None)

    # ------------------------------------------------------------------
    # 滚动辅助（与大纲画布一致）
    # ------------------------------------------------------------------
    def _scrollbars(self):
        parent = self.parentWidget()
        while parent is not None:
            from PySide6.QtWidgets import QScrollArea as _SA

            if isinstance(parent, _SA):
                return parent.horizontalScrollBar(), parent.verticalScrollBar()
            parent = parent.parentWidget()
        return None, None

    def _viewport(self):
        parent = self.parentWidget()
        while parent is not None:
            from PySide6.QtWidgets import QScrollArea as _SA

            if isinstance(parent, _SA):
                return parent.viewport()
            parent = parent.parentWidget()
        return None

    def _scroll_values(self) -> tuple[float, float]:
        h_bar, v_bar = self._scrollbars()
        return (
            float(h_bar.value()) if h_bar is not None else 0.0,
            float(v_bar.value()) if v_bar is not None else 0.0,
        )


# ======================================================================
# 关系图视图（内嵌在角色页中，交互与大纲画布一致）
# ======================================================================
class RelationBoardWidget(QWidget):
    """角色关系思维导图：工具条 + 无限画布，作为角色页的一个视图。"""

    changed = Signal()                        # 数据变化（面板负责持久化，已做防抖）
    edit_character_requested = Signal(str)    # 双击节点 → 打开角色卡编辑
    delete_character_requested = Signal(str)  # 右键删除角色（面板确认）
    add_character_requested = Signal(QPointF) # 空白右键新建角色（带画布坐标）

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._theme: dict = {}
        self._first_show = True

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        bar = QWidget(self)
        bar.setObjectName("RelationBar")
        bar.setAttribute(Qt.WA_StyledBackground, True)
        bar.setFixedHeight(40)
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(12, 4, 12, 4)
        bar_layout.setSpacing(10)

        self.count_label = QLabel("", bar)
        self.count_label.setObjectName("DrawerHint")
        bar_layout.addWidget(self.count_label)
        bar_layout.addStretch(1)
        hint = QLabel("拖动卡片布局 · 圆点拖出连线 · 双击卡片编辑 · 双击连线编辑关系 · 空白右键新建 · Ctrl+滚轮缩放", bar)
        hint.setObjectName("RelationHint")
        bar_layout.addWidget(hint)
        self.save_button = QPushButton("保存", bar)
        self.save_button.setObjectName("GhostButton")
        self.save_button.setCursor(Qt.PointingHandCursor)
        self.save_button.setToolTip("立即保存（Ctrl+S）")
        self.save_button.clicked.connect(self.flush_save)
        bar_layout.addWidget(self.save_button)
        layout.addWidget(bar)

        save_sc = QShortcut(QKeySequence("Ctrl+S"), self)
        save_sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        save_sc.activated.connect(self.flush_save)

        self.canvas = RelationCanvas({}, self)
        self.canvas.changed.connect(self._collect)
        self.canvas.edit_character_requested.connect(self.edit_character_requested.emit)
        self.canvas.delete_character_requested.connect(self.delete_character_requested.emit)
        self.canvas.create_relation_requested.connect(self._on_create_relation)
        self.canvas.edit_relation_requested.connect(self._on_edit_relation)
        self.canvas.add_character_requested.connect(self._on_add_character_at)

        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(False)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        from app.ui.scroll_float import ScrollFlasher

        self._h_flasher = ScrollFlasher(self.scroll, Qt.Horizontal, inset=8)
        self._v_flasher = ScrollFlasher(self.scroll, Qt.Vertical, inset=8)
        self.scroll.setWidget(self.canvas)
        # 画布自行处理 Ctrl+滚轮缩放，补挂画布监听保证滑块能浮现
        self._h_flasher.watch(self.canvas)
        self._v_flasher.watch(self.canvas)
        layout.addWidget(self.scroll, 1)

        # 四向边界「＋」与左下角「复位」（与大纲画布一致）
        self.left_plus = self._make_plus_button("left")
        self.right_plus = self._make_plus_button("right")
        self.top_plus = self._make_plus_button("top")
        self.bottom_plus = self._make_plus_button("bottom")
        self.left_plus.clicked.connect(lambda: self._extend("left"))
        self.right_plus.clicked.connect(lambda: self._extend("right"))
        self.top_plus.clicked.connect(lambda: self._extend("top"))
        self.bottom_plus.clicked.connect(lambda: self._extend("bottom"))

        self.reset_button = QPushButton("复位", self.scroll.viewport())
        self.reset_button.setObjectName("FloatButton")
        self.reset_button.setCursor(Qt.PointingHandCursor)
        self.reset_button.setFixedSize(64, 34)
        self.reset_button.clicked.connect(self.reset_view)

        self.canvas.bounds_changed.connect(self._update_edge_buttons)
        self.scroll.horizontalScrollBar().valueChanged.connect(self._update_edge_buttons)
        self.scroll.verticalScrollBar().valueChanged.connect(self._update_edge_buttons)
        self.scroll.viewport().installEventFilter(self)

        # 空状态浮层（无角色时盖在画布上）
        self.empty_hint = QLabel("还没有角色\n在空白处右键选择「新建角色」，或回角色列表新建", self.scroll.viewport())
        self.empty_hint.setObjectName("CharEmpty")
        self.empty_hint.setAlignment(Qt.AlignCenter)
        self.empty_hint.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        # 画布变化 → 防抖落盘（拖拽一次 release 只发一次，这里再合流突发信号）
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(400)
        self._save_timer.timeout.connect(self.changed.emit)

        self._sync_count()

    # ------------------------------------------------------------------
    # 数据（首次切入视图时由面板注入）
    # ------------------------------------------------------------------
    def set_data(self, characters: list[dict], relations: list[dict],
                 theme: dict, board_size: tuple[int, int] = (0, 0)) -> None:
        self._theme = dict(theme or {})
        self.canvas.set_theme(self._theme)
        self.canvas.set_data(characters, relations, *board_size)
        self._sync_count()
        self._update_empty_hint()
        self._update_edge_buttons()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        # 首次显示时视口才拿到正确尺寸，此时居中到内容
        if self._first_show:
            self._first_show = False
            QTimer.singleShot(0, self._center_on_content)

    # ------------------------------------------------------------------
    def _make_plus_button(self, side: str) -> QToolButton:
        button = QToolButton(self.scroll.viewport())
        button.setObjectName("FloatButton")
        button.setCursor(Qt.PointingHandCursor)
        button.setFixedSize(36, 36)
        button.setIconSize(QSize(18, 18))
        button.setIcon(IconFactory(self._theme.get("text", "#1F2329")).icon("add", 18))
        names = {"left": "左", "right": "右", "top": "上", "bottom": "下"}
        tip = "向%s扩展画布" % names.get(side, side)
        button.setToolTip(tip)
        button.setAccessibleName(tip)
        button.hide()
        return button

    def _extend(self, direction: str) -> None:
        self.canvas.extend(direction)
        if direction in ("right", "bottom"):
            bar = (
                self.scroll.horizontalScrollBar()
                if direction == "right"
                else self.scroll.verticalScrollBar()
            )
            bar.setValue(bar.maximum())
        self._update_edge_buttons()

    def reset_view(self) -> None:
        viewport = self.scroll.viewport()
        self.scroll.horizontalScrollBar().setValue(
            int((self.canvas.width() - viewport.width()) / 2)
        )
        self.scroll.verticalScrollBar().setValue(
            int((self.canvas.height() - viewport.height()) / 2)
        )

    def _update_edge_buttons(self) -> None:
        h_bar = self.scroll.horizontalScrollBar()
        v_bar = self.scroll.verticalScrollBar()
        viewport = self.scroll.viewport()
        width, height = viewport.width(), viewport.height()
        at_left = h_bar.value() <= h_bar.minimum()
        at_right = h_bar.value() >= h_bar.maximum()
        at_top = v_bar.value() <= v_bar.minimum()
        at_bottom = v_bar.value() >= v_bar.maximum()
        self.left_plus.setVisible(at_left)
        self.right_plus.setVisible(at_right)
        self.top_plus.setVisible(at_top)
        self.bottom_plus.setVisible(at_bottom)
        if at_left:
            self.left_plus.move(10, int(height / 2 - 18))
            self.left_plus.raise_()
        if at_right:
            self.right_plus.move(width - 46, int(height / 2 - 18))
            self.right_plus.raise_()
        if at_top:
            self.top_plus.move(int(width / 2 - 18), 10)
            self.top_plus.raise_()
        if at_bottom:
            self.bottom_plus.move(int(width / 2 - 18), height - 46)
            self.bottom_plus.raise_()
        self.reset_button.move(10, height - 48)
        self.reset_button.raise_()
        self._update_empty_hint()

    def _update_empty_hint(self) -> None:
        has = bool(self.canvas.characters)
        if has:
            self.empty_hint.setVisible(False)
            return
        # 首次出现时预挂透明度效果并淡入，避免直接出现的跳变
        if not self.empty_hint.isVisible():
            self.empty_hint.setVisible(True)
            motion.fade_in(self.empty_hint, motion.DUR_SLOW)
        viewport = self.scroll.viewport()
        self.empty_hint.resize(viewport.width(), viewport.height())
        self.empty_hint.move(0, 0)
        self.empty_hint.raise_()

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        from PySide6.QtCore import QEvent

        if obj is self.scroll.viewport() and event.type() == QEvent.Resize:
            self._update_edge_buttons()
        return super().eventFilter(obj, event)

    def _center_on_content(self) -> None:
        if self.canvas.characters:
            # 全部节点包围盒中心，避免只围绕首个角色居中时其他卡片贴边
            xs, ys_top, ys_bottom = [], [], []
            for node in self.canvas.characters:
                r = self.canvas.node_rect(node)
                xs.append(r.left())
                xs.append(r.right())
                ys_top.append(r.top())
                ys_bottom.append(r.bottom())
            cx = (min(xs) + max(xs)) / 2
            cy = (min(ys_top) + max(ys_bottom)) / 2
        else:
            cx, cy = self.canvas.width() / 2, self.canvas.height() / 2
        viewport = self.scroll.viewport()
        self.scroll.horizontalScrollBar().setValue(int(cx - viewport.width() / 2))
        self.scroll.verticalScrollBar().setValue(int(cy - viewport.height() / 2))
        self._update_edge_buttons()

    # ------------------------------------------------------------------
    # 关系增改（画布请求 → 弹窗 → 回写）
    # ------------------------------------------------------------------
    def _char_name(self, char_id: str) -> str:
        for c in self.canvas.characters:
            if c.get("id") == char_id:
                return str(c.get("name") or "未命名角色")
        return "未知角色"

    def _on_create_relation(self, from_id: str, to_id: str) -> None:
        dialog = RelationDialog(
            self, self._char_name(from_id), self._char_name(to_id), None
        )
        if dialog.exec() == RelationDialog.Accepted:
            f, t = (to_id, from_id) if dialog.is_reverse() else (from_id, to_id)
            self.canvas.add_edge(f, t, dialog.values())

    def _on_edit_relation(self, edge: dict) -> None:
        from_id = str(edge.get("from_id") or "")
        to_id = str(edge.get("to_id") or "")
        dialog = RelationDialog(
            self, self._char_name(from_id), self._char_name(to_id), edge
        )
        if dialog.exec() != RelationDialog.Accepted:
            return
        values = dialog.values()
        new_f, new_t = (to_id, from_id) if dialog.is_reverse() else (from_id, to_id)
        # 旧版双向单线：保存即拆——本边改为独立有向边，再补一条反向同名边
        legacy_split = dialog.legacy_bidir() and not dialog.is_reverse()
        self.canvas.update_edge(edge, values, new_f, new_t)
        if legacy_split:
            self.canvas.add_edge(to_id, from_id, {
                **values,
                "name": str(edge.get("name") or values.get("name") or "关系"),
                "intensity": edge.get("intensity", values.get("intensity", 0)),
                "note": str(edge.get("note") or values.get("note") or ""),
            })

    def _on_add_character_at(self, pos: QPointF) -> None:
        # 由面板弹角色卡表单并落盘，画布在 reload 后拿到新节点
        self.add_character_requested.emit(QPointF(pos))

    def select_character(self, char_id: str) -> None:
        """新建 / 编辑后选中对应节点。"""
        if self._char_by_id(char_id) is not None:
            self.canvas._selected_char = char_id
            self.canvas._selected_edge = ""
            self.canvas.update()

    def _char_by_id(self, char_id: str) -> dict | None:
        return next(
            (c for c in self.canvas.characters if str(c.get("id")) == char_id), None
        )

    def remove_character(self, char_id: str) -> None:
        self.canvas.remove_character(char_id)
        self._collect()

    def reload_characters(self, characters: list[dict]) -> None:
        self.canvas.reload_characters(characters)
        self._sync_count()
        self._update_empty_hint()

    # ------------------------------------------------------------------
    def _sync_count(self) -> None:
        self.count_label.setText(
            f"{len(self.canvas.characters)} 名角色 · {len(self.canvas.relations)} 条关系"
        )

    def _collect(self) -> None:
        self._sync_count()
        self._save_timer.start()    # 防抖后通知面板落盘

    def flush_save(self) -> None:
        """Ctrl+S / 点保存：立即落盘并给出反馈。"""
        self._save_timer.stop()
        self.changed.emit()
        flash_saved(self.save_button,
                    IconFactory(self._theme.get("text", "#1F2329")))

    def flush_pending(self) -> None:
        """切走视图前：把防抖中的改动立即落盘（无反馈文案）。"""
        if self._save_timer.isActive():
            self._save_timer.stop()
            self.changed.emit()

    def collect(self) -> tuple[list[dict], list[dict], int, int]:
        return self.canvas.collect()

    def set_theme(self, theme: dict) -> None:
        self._theme = dict(theme or {})
        self.canvas.set_theme(self._theme)
        for attr in ("left_plus", "right_plus", "top_plus", "bottom_plus"):
            btn = getattr(self, attr, None)
            if btn is not None:
                btn.setIcon(IconFactory(self._theme.get("text", "#1F2329")).icon("add", 18))


# ======================================================================
# 角色页主体（嵌在书架主页）
# ======================================================================
class CharactersPanel(QWidget):
    """角色页：关键数字 + 搜索筛选 + 角色表格 + 详情卡。"""

    data_changed = Signal()             # 数据变化 → 主窗口刷新计数
    view_changed = Signal(str)          # 列表 / 关系图视图切换（主窗口持久化偏好）
    import_requested = Signal()         # 角色页「导入」按钮 → 主窗口统一导入流程

    def __init__(self, storage, view: str = "list", parent=None) -> None:
        super().__init__(parent)
        self.storage = storage
        self.theme: dict = {}
        # 当前作品名（主窗口同步；用于导出文件名与文件头，无作品时为空）
        self.book_name: str = ""
        self._characters: list[dict] = []
        self._relations: list[dict] = []
        self._selected_id = ""
        self._view = "list"
        self._board: RelationBoardWidget | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 4, 0, 8)
        root.setSpacing(12)

        # 顶行：关键数字 + 视图分段 + 搜索筛选（仅列表视图）+ 新建
        top = QHBoxLayout()
        top.setSpacing(12)

        self.char_chip = self._make_chip()
        self.rel_chip = self._make_chip()
        top.addWidget(self.char_chip)
        top.addWidget(self.rel_chip)
        top.addStretch(1)

        # 视图分段切换（复用 #SegBox / #SegButton，与大纲连线风格切换同款）
        self.view_seg = QFrame(self)
        self.view_seg.setObjectName("SegBox")
        seg_layout = QHBoxLayout(self.view_seg)
        seg_layout.setContentsMargins(3, 3, 3, 3)
        seg_layout.setSpacing(2)
        self.list_seg = QToolButton(self.view_seg)
        self.list_seg.setObjectName("SegButton")
        self.list_seg.setText("角色列表")
        self.list_seg.setCheckable(True)
        self.list_seg.setCursor(Qt.PointingHandCursor)
        self.list_seg.clicked.connect(lambda: self._set_view("list"))
        seg_layout.addWidget(self.list_seg)
        self.board_seg = QToolButton(self.view_seg)
        self.board_seg.setObjectName("SegButton")
        self.board_seg.setText("关系图")
        self.board_seg.setCheckable(True)
        self.board_seg.setCursor(Qt.PointingHandCursor)
        self.board_seg.clicked.connect(lambda: self._set_view("board"))
        seg_layout.addWidget(self.board_seg)
        top.addWidget(self.view_seg)

        self.search_edit = QLineEdit(self)
        self.search_edit.setPlaceholderText("搜索姓名 / 别名 / 标签")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setFixedWidth(220)
        self.search_edit.textChanged.connect(lambda _t: self._refresh_table())
        top.addWidget(self.search_edit)

        self.role_filter = QComboBox(self)
        self.role_filter.setFixedWidth(120)
        self.role_filter.addItem("全部定位", "")
        self.role_filter.currentIndexChanged.connect(lambda _i: self._refresh_table())
        top.addWidget(self.role_filter)

        self.tag_filter = QComboBox(self)
        self.tag_filter.setFixedWidth(120)
        self.tag_filter.addItem("全部标签", "")
        self.tag_filter.currentIndexChanged.connect(lambda _i: self._refresh_table())
        top.addWidget(self.tag_filter)
        # 搜索 / 筛选只在列表视图使用
        self._list_tools = [self.search_edit, self.role_filter, self.tag_filter]

        self.add_button = QPushButton("新建角色", self)
        self.add_button.setObjectName("Primary")
        self.add_button.setCursor(Qt.PointingHandCursor)
        self.add_button.setIconSize(QSize(14, 14))
        self.add_button.setIcon(
            IconFactory(QApplication.palette().color(
                QPalette.ColorRole.WindowText).name()).icon("add", 14))
        self.add_button.clicked.connect(self.add_character)
        top.addWidget(self.add_button)

        # 导入 / 导出：与「新建角色」同排，沿用 GhostButton 规范
        self.import_button = QPushButton("导入", self)
        self.import_button.setObjectName("GhostButton")
        self.import_button.setCursor(Qt.PointingHandCursor)
        self.import_button.setToolTip("导入角色卡 JSON（预检后询问同名冲突策略）")
        self.import_button.clicked.connect(self.import_requested.emit)
        top.addWidget(self.import_button)

        self.export_button = QPushButton("导出", self)
        self.export_button.setObjectName("GhostButton")
        self.export_button.setCursor(Qt.PointingHandCursor)
        self.export_button.setToolTip("导出全部角色、关系与画布坐标/缩放为 JSON")
        self.export_button.clicked.connect(self.export_json)
        top.addWidget(self.export_button)
        root.addLayout(top)

        # 主体：列表视图（表格 + 详情） / 关系图视图（首次切入时按需创建）
        self.stack = QStackedWidget(self)
        self.list_page = QWidget(self.stack)
        list_layout = QVBoxLayout(self.list_page)
        list_layout.setContentsMargins(0, 0, 0, 0)
        list_layout.setSpacing(0)
        body = QHBoxLayout()
        body.setSpacing(12)
        self.table = QTableWidget(0, 6, self.list_page)
        self.table.setObjectName("CharTable")
        self.table.setHorizontalHeaderLabels(["姓名", "别名", "定位", "标签", "首次出场", "更新时间"])
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        header.setStretchLastSection(False)
        self.table.itemSelectionChanged.connect(self._on_table_selection)
        self.table.doubleClicked.connect(lambda _i: self.edit_character(self._selected_id))
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_table_menu)
        body.addWidget(self.table, 1)

        self.detail_scroll = QScrollArea(self.list_page)
        self.detail_scroll.setWidgetResizable(True)
        self.detail_scroll.setFrameShape(QFrame.NoFrame)
        self.detail_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.detail_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.detail_scroll.setFixedWidth(360)
        self.detail_holder = QWidget(self.detail_scroll)
        self.detail_layout = QVBoxLayout(self.detail_holder)
        self.detail_layout.setContentsMargins(0, 0, 0, 0)
        self.detail_scroll.setWidget(self.detail_holder)
        body.addWidget(self.detail_scroll)
        list_layout.addLayout(body, 1)

        # 隐形快速滚动滑块：角色表格与右侧详情卡（与其它区域一致）
        from app.ui.scroll_float import ScrollFlasher

        self._table_flasher = ScrollFlasher(self.table, Qt.Vertical, inset=6)
        self._detail_flasher = ScrollFlasher(self.detail_scroll, Qt.Vertical, inset=6)

        self.empty_label = QLabel(
            "还没有角色\n点击右上角「新建角色」创建第一张角色卡，\n或切到「关系图」在空白处右键新建",
            self.list_page,
        )
        self.empty_label.setObjectName("CharEmpty")
        self.empty_label.setAlignment(Qt.AlignCenter)
        list_layout.addWidget(self.empty_label, 1)

        self.stack.addWidget(self.list_page)
        root.addWidget(self.stack, 1)

        self.list_seg.setChecked(True)
        self.board_seg.setChecked(False)
        if view == "board":
            for w in self._list_tools:
                w.hide()
            self._set_view("board")

    def _make_chip(self) -> QFrame:
        chip = QFrame(self)
        chip.setObjectName("StatCard")
        layout = QVBoxLayout(chip)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(2)
        return chip

    # ------------------------------------------------------------------
    # 视图切换 / 内嵌关系图
    # ------------------------------------------------------------------
    def _set_view(self, key: str) -> None:
        if key not in ("list", "board"):
            key = "list"
        if key == self._view:
            return
        # 切走关系图前先把防抖未落盘的改动写入存储，避免 set_data 回读冲掉
        if self._board is not None:
            self._board.flush_pending()
        self._view = key
        self.list_seg.setChecked(key == "list")
        self.board_seg.setChecked(key == "board")
        for w in self._list_tools:
            w.setVisible(key == "list")
        if key == "board":
            board = self._ensure_board()
            # 离开列表前若有未落盘的画布数据先落盘（不会发生，保险）
            board.set_data(
                self.storage.load_characters(),
                self.storage.load_relations(),
                self.theme,
                self.storage.load_board_size(),
            )
            self.stack.setCurrentWidget(board)
        else:
            self.stack.setCurrentWidget(self.list_page)
            self._rebuild_filters()
            self._refresh_all()
        self.view_changed.emit(key)

    def _ensure_board(self) -> RelationBoardWidget:
        if self._board is None:
            board = RelationBoardWidget(self)
            self.stack.addWidget(board)
            board.changed.connect(self._on_board_changed)
            board.edit_character_requested.connect(self._on_board_edit_character)
            board.delete_character_requested.connect(self.delete_character)
            board.add_character_requested.connect(self._on_board_add_character)
            self._board = board
        return self._board

    def _on_board_changed(self) -> None:
        """画布数据变化：落盘 + 同步内存与计数（拖拽期间由此一路保存）。"""
        board = self._board
        if board is None:
            return
        characters, relations, width, height = board.collect()
        self.storage.save_characters(characters)
        self.storage.save_relations(relations)
        self.storage.save_board_size(width, height)
        self._characters = [dict(c) for c in characters]
        self._relations = [dict(r) for r in relations]
        self._sync_chips()
        self.data_changed.emit()

    def _on_board_add_character(self, pos: QPointF) -> None:
        dialog = CharacterDialog(self, None)
        if dialog.exec() != CharacterDialog.Accepted:
            return
        values = dialog.values()
        values["x"] = float(pos.x()) - NODE_W / 2
        values["y"] = float(pos.y()) - NODE_H / 2
        self.storage.save_character(values)
        self._refresh_after_mutation(select=str(values.get("id")))

    def _on_board_edit_character(self, char_id: str) -> None:
        char = next(
            (c for c in self.storage.load_characters() if str(c.get("id")) == char_id),
            None,
        )
        if char is None:
            return
        dialog = CharacterDialog(self, char)
        if dialog.exec() != CharacterDialog.Accepted:
            return
        values = dialog.values()
        # 画布坐标由画布持有，表单不编辑
        values["x"] = float(char.get("x") or 0)
        values["y"] = float(char.get("y") or 0)
        self.storage.save_character(values)
        self._refresh_after_mutation()

    def _refresh_after_mutation(self, select: str = "") -> None:
        """画布内增改角色后：刷新画布 / 列表数据与计数。"""
        characters = self.storage.load_characters()
        relations = self.storage.load_relations()
        self._characters = [dict(c) for c in characters]
        self._relations = [dict(r) for r in relations]
        if self._board is not None:
            self._board.reload_characters(characters)
            if select:
                self._board.select_character(select)
        self._rebuild_filters()
        self._sync_chips()
        if self._view == "list":
            self._refresh_table()
            self._refresh_detail()
        self.data_changed.emit()

    # ------------------------------------------------------------------
    # 数据
    # ------------------------------------------------------------------
    def set_theme(self, theme: dict) -> None:
        new_theme = dict(theme or {})
        # 同一组配色重复推送（切页 / 拖拽）时不重建详情卡控件树
        if new_theme == self.theme:
            return
        self.theme = new_theme
        self.add_button.setIcon(
            IconFactory(self.theme.get("on_accent", "#FFFFFF")).icon("add", 14))
        self._refresh_detail()
        if self._board is not None:
            self._board.set_theme(self.theme)

    def set_data(self, characters: list[dict], relations: list[dict]) -> None:
        self._characters = [dict(c) for c in (characters or [])]
        self._relations = [dict(r) for r in (relations or [])]
        if self._selected_id and not self._char_by_id(self._selected_id):
            self._selected_id = ""
        # 数据签名（id + 定位/标签/更新时间 + 关系集合）：切换页面等无变化的推送
        # 直接跳过表格 / 筛选 / 详情重建，避免每次进入角色页都全量重绘。
        sig = (
            tuple(
                (
                    str(c.get("id") or ""),
                    str(c.get("role") or ""),
                    tuple(sorted(str(t) for t in (c.get("tags") or []))),
                    str(c.get("updated_at") or 0),
                )
                for c in self._characters
            ),
            tuple(sorted(str(r.get("id") or "") for r in self._relations)),
        )
        if sig == getattr(self, "_data_sig", None):
            self._sync_chips()
            return
        self._data_sig = sig
        self._sync_chips()
        # 关系图视图激活时，画布自身持有坐标与缩放，不用外部数据覆盖；
        # 只在列表视图重建表格 / 详情，避免拖拽期间不可见的表格白白重建。
        if self._view == "list":
            self._rebuild_filters()
            self._refresh_table()
            self._refresh_detail()

    def _char_by_id(self, char_id: str) -> dict | None:
        return next((c for c in self._characters if c.get("id") == char_id), None)

    def _relations_of(self, char_id: str) -> list[dict]:
        return [
            r for r in self._relations
            if r.get("from_id") == char_id or r.get("to_id") == char_id
        ]

    def _rebuild_filters(self) -> None:
        roles: list[str] = []
        tags: list[str] = []
        for c in self._characters:
            role = str(c.get("role") or "").strip()
            if role and role not in roles:
                roles.append(role)
            for tag in c.get("tags") or []:
                tag = str(tag).strip()
                if tag and tag not in tags:
                    tags.append(tag)
        for combo, values in ((self.role_filter, roles), (self.tag_filter, tags)):
            current = combo.currentData()
            blocked = combo.blockSignals(True)
            while combo.count() > 1:
                combo.removeItem(combo.count() - 1)
            for value in sorted(values):
                combo.addItem(value, value)
            if current:
                index = combo.findData(current)
                combo.setCurrentIndex(index if index >= 0 else 0)
            combo.blockSignals(blocked)

    def _refresh_all(self) -> None:
        self._sync_chips()
        self._refresh_table()
        self._refresh_detail()

    def _sync_chips(self) -> None:
        # 计数未变时只更新文本，避免切页 / 拖拽时反复销毁重建 QLabel
        if getattr(self, "_chips_built", False):
            if self._char_chip_count != len(self._characters):
                self._char_chip_label.setText(str(len(self._characters)))
                self._char_chip_count = len(self._characters)
            if self._rel_chip_count != len(self._relations):
                self._rel_chip_label.setText(str(len(self._relations)))
                self._rel_chip_count = len(self._relations)
            return
        for chip, value, label in (
            (self.char_chip, len(self._characters), "角色"),
            (self.rel_chip, len(self._relations), "角色关系"),
        ):
            value_label = QLabel(str(value), chip)
            value_label.setObjectName("StatValue")
            key_label = QLabel(label, chip)
            key_label.setObjectName("StatLabel")
            key_label.setAlignment(Qt.AlignCenter)
            value_label.setAlignment(Qt.AlignCenter)
            chip.layout().addWidget(value_label)
            chip.layout().addWidget(key_label)
            chip.setFixedHeight(64)
            if chip is self.char_chip:
                self._char_chip_label = value_label
                self._char_chip_count = value
            else:
                self._rel_chip_label = value_label
                self._rel_chip_count = value
        self._chips_built = True

    # ------------------------------------------------------------------
    # 表格
    # ------------------------------------------------------------------
    def _filtered(self) -> list[dict]:
        keyword = self.search_edit.text().strip().lower()
        role = str(self.role_filter.currentData() or "")
        tag = str(self.tag_filter.currentData() or "")
        items = []
        for c in self._characters:
            if role and str(c.get("role") or "") != role:
                continue
            if tag and tag not in [str(t) for t in (c.get("tags") or [])]:
                continue
            if keyword:
                haystack = " ".join(
                    [
                        str(c.get("name") or ""),
                        str(c.get("alias") or ""),
                        str(c.get("role") or ""),
                        " ".join(str(t) for t in (c.get("tags") or [])),
                    ]
                ).lower()
                if keyword not in haystack:
                    continue
            items.append(c)
        items.sort(key=lambda c: float(c.get("updated_at") or 0), reverse=True)
        return items

    def _refresh_table(self) -> None:
        items = self._filtered()
        has_chars = bool(self._characters)
        self.table.setVisible(has_chars)
        self.detail_scroll.setVisible(has_chars)
        # 空状态用统一 ease-out 淡入（从有到无时立即隐藏）
        if has_chars:
            self.empty_label.setVisible(False)
        elif not self.empty_label.isVisible():
            self.empty_label.setVisible(True)
            motion.fade_in(self.empty_label, motion.DUR_SLOW)
        # 重建期间屏蔽选择信号：避免逐行 setItem 时的中间态把 _selected_id 清空，
        # 重建后统一恢复选中行并刷新一次详情。
        blocked = self.table.blockSignals(True)
        self.table.clearSelection()
        self.table.setRowCount(len(items))
        selected_row = -1
        for row, c in enumerate(items):
            tags = _tags_text(c.get("tags"))
            cells = [
                str(c.get("name") or "未命名角色"),
                str(c.get("alias") or ""),
                str(c.get("role") or ""),
                tags,
                str(c.get("first_chapter") or ""),
                _date_text(c.get("updated_at")),
            ]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if col == 0:
                    item.setData(Qt.UserRole, str(c.get("id") or ""))
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                if col == 3:
                    item.setForeground(QColor(self.theme.get("accent", "#7C5CE6")))
                item.setToolTip(text)
                self.table.setItem(row, col, item)
            if str(c.get("id") or "") == self._selected_id:
                selected_row = row
        if selected_row >= 0:
            self.table.selectRow(selected_row)
        elif self._selected_id:
            # 选中角色已被筛选排除或删除
            self._selected_id = ""
        self.table.blockSignals(blocked)
        self._refresh_detail()

    def _on_table_selection(self) -> None:
        row = self.table.currentRow()
        item = self.table.item(row, 0) if row >= 0 else None
        self._selected_id = str(item.data(Qt.UserRole) or "") if item is not None else ""
        self._refresh_detail()

    def _show_table_menu(self, pos) -> None:
        menu = QMenu(self)
        motion.apply_elevation(menu, 2, motion.app_is_dark())
        menu.addAction("新建角色", self.add_character)
        if self._selected_id:
            menu.addSeparator()
            menu.addAction("编辑角色", lambda: self.edit_character(self._selected_id))
            menu.addAction("删除角色", lambda: self.delete_character(self._selected_id))
        menu.exec(self.table.viewport().mapToGlobal(pos))
        menu.deleteLater()

    # ------------------------------------------------------------------
    # 详情卡
    # ------------------------------------------------------------------
    def _clear_layout(self, layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
            elif item.layout() is not None:
                self._clear_layout(item.layout())

    def _refresh_detail(self) -> None:
        self._clear_layout(self.detail_layout)
        char = self._char_by_id(self._selected_id)
        if char is None:
            hint = QLabel("在左侧选择一名角色查看详情", self.detail_holder)
            hint.setObjectName("CharDetailSub")
            hint.setAlignment(Qt.AlignCenter)
            self.detail_layout.addWidget(hint)
            self.detail_layout.addStretch(1)
            return

        inner = QWidget(self.detail_holder)
        box = QVBoxLayout(inner)
        box.setContentsMargins(16, 16, 16, 16)
        box.setSpacing(10)

        title = QLabel(str(char.get("name") or "未命名角色"), inner)
        title.setObjectName("CharDetailTitle")
        title.setWordWrap(True)
        box.addWidget(title)

        role = str(char.get("role") or "")
        sub_parts = [p for p in (role, str(char.get("gender") or ""), str(char.get("age") or "")) if p]
        sub = QLabel(" · ".join(sub_parts) if sub_parts else "未设置定位", inner)
        sub.setObjectName("CharDetailSub")
        sub.setWordWrap(True)
        box.addWidget(sub)

        alias = str(char.get("alias") or "")
        if alias:
            alias_label = QLabel(f"又称：{alias}", inner)
            alias_label.setObjectName("CharDetailValue")
            alias_label.setWordWrap(True)
            box.addWidget(alias_label)

        tags = _tags_text(char.get("tags"))
        if tags:
            tag_label = QLabel(tags, inner)
            tag_label.setObjectName("TagChip")
            tag_label.setAlignment(Qt.AlignLeft)
            box.addWidget(tag_label)

        # 卡面方框属性（灵根 / 仙脉 / 性别 …），只展示已填写的项
        card_fields = Character._parse_card_fields(
            char.get("card_fields"), str(char.get("gender") or "")
        )
        filled_fields = [f for f in card_fields if str(f.get("value") or "").strip()]
        if filled_fields:
            card_head = QLabel("卡面属性", inner)
            card_head.setObjectName("CharDetailSection")
            box.addWidget(card_head)
            fields_grid = QGridLayout()
            fields_grid.setHorizontalSpacing(8)
            fields_grid.setVerticalSpacing(6)
            for i, f in enumerate(filled_fields):
                cell = QFrame(inner)
                cell.setObjectName("CharRelRow")
                cell_layout = QHBoxLayout(cell)
                cell_layout.setContentsMargins(8, 4, 8, 4)
                cell_layout.setSpacing(8)
                chip = QLabel(str(f.get("label") or ""), cell)
                chip.setObjectName("TagChip")
                cell_layout.addWidget(chip)
                val = QLabel(str(f.get("value") or ""), cell)
                val.setObjectName("CharDetailValue")
                val.setWordWrap(True)
                cell_layout.addWidget(val, 1)
                fields_grid.addWidget(cell, i // 3, i % 3)
            box.addLayout(fields_grid)

        def kv(key: str, value: str) -> None:
            if not value:
                return
            row = QFrame(inner)
            row.setObjectName("CharRelRow")
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(10, 6, 10, 6)
            k = QLabel(key, row)
            k.setObjectName("CharDetailKey")
            v = QLabel(value, row)
            v.setObjectName("CharDetailValue")
            v.setWordWrap(True)
            row_layout.addWidget(k)
            row_layout.addSpacing(8)
            row_layout.addWidget(v, 1)
            box.addWidget(row)

        kv("首次出场", str(char.get("first_chapter") or ""))
        chapters = [str(c) for c in (char.get("chapters") or []) if str(c).strip()]
        if chapters:
            shown = "、".join(chapters[:20]) + ("…" if len(chapters) > 20 else "")
            chap_head = QLabel(f"出场章节（{len(chapters)}）", inner)
            chap_head.setObjectName("CharDetailSection")
            box.addWidget(chap_head)
            chap_body = QLabel(shown, inner)
            chap_body.setObjectName("CharDetailValue")
            chap_body.setWordWrap(True)
            box.addWidget(chap_body)

        def section(label: str, value: str) -> None:
            if not value:
                return
            head = QLabel(label, inner)
            head.setObjectName("CharDetailSection")
            box.addWidget(head)
            body_label = QLabel(value, inner)
            body_label.setObjectName("CharDetailValue")
            body_label.setWordWrap(True)
            body_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            box.addWidget(body_label)

        section("外貌", str(char.get("appearance") or ""))
        section("性格", str(char.get("personality") or ""))
        section("背景故事", str(char.get("backstory") or ""))
        section("目标与动机", str(char.get("goals") or ""))
        section("说话风格 / 口头禅", str(char.get("speech") or ""))
        section("原文摘录（自动提取）", str(char.get("description") or ""))
        section("备注", str(char.get("note") or ""))

        rels = self._relations_of(str(char.get("id") or ""))
        head = QLabel(f"角色关系（{len(rels)}）", inner)
        head.setObjectName("CharDetailSection")
        box.addSpacing(4)
        box.addWidget(head)
        if not rels:
            empty = QLabel("暂无关系 · 在「关系图」中拖出连线即可创建", inner)
            empty.setObjectName("CharDetailSub")
            empty.setWordWrap(True)
            box.addWidget(empty)

        def add_rel_row(rel, group: str) -> None:
            cid = str(char.get("id") or "")
            if group == "out":
                other_id = rel.get("to_id"); arrow = "→"
            elif group == "in":
                other_id = rel.get("from_id"); arrow = "←"
            else:
                other_id = (rel.get("to_id") if rel.get("from_id") == cid
                            else rel.get("from_id"))
                arrow = "⇄" if rel.get("bidirectional") is True else "↔"
            other = self._char_by_id(str(other_id or ""))
            other_name = str(other.get("name") or "已删除角色") if other else "已删除角色"
            if not rel.get("directed"):
                dir_text = "无向"
            elif rel.get("bidirectional") is True:
                dir_text = "旧双线"
            else:
                dir_text = "单向"
            try:
                intensity = int(rel.get("intensity") or 0)
            except (TypeError, ValueError):
                intensity = 0
            row = QFrame(inner)
            row.setObjectName("CharRelRow")
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(10, 6, 10, 6)
            row_layout.setSpacing(8)
            name_label = QLabel(str(rel.get("name") or "关系"), row)
            name_label.setObjectName("CharRelName")
            meta_label = QLabel(
                f"{arrow} {other_name} · {dir_text} · {INTENSITY_TEXT.get(intensity, '中性')}",
                row,
            )
            meta_label.setObjectName("CharRelMeta")
            row_layout.addWidget(name_label)
            row_layout.addWidget(meta_label, 1)
            box.addWidget(row)

        cid = str(char.get("id") or "")
        outgoing = [r for r in rels if r.get("from_id") == cid
                    and r.get("directed") and r.get("bidirectional") is not True]
        incoming = [r for r in rels if r.get("to_id") == cid
                    and r.get("directed") and r.get("from_id") != cid
                    and r.get("bidirectional") is not True]
        undirected = [r for r in rels if not r.get("directed") or r.get("bidirectional") is True]

        if outgoing:
            h = QLabel(f"出发 · 我对谁（{len(outgoing)}）", inner)
            h.setObjectName("CharDetailSection")
            box.addWidget(h)
            for r in outgoing:
                add_rel_row(r, "out")
        if incoming:
            h = QLabel(f"来源 · 谁对我（{len(incoming)}）", inner)
            h.setObjectName("CharDetailSection")
            box.addWidget(h)
            for r in incoming:
                add_rel_row(r, "in")
        if undirected:
            h = QLabel(f"无向 / 旧双线（{len(undirected)}）", inner)
            h.setObjectName("CharDetailSection")
            box.addWidget(h)
            for r in undirected:
                add_rel_row(r, "flat")

        box.addStretch(1)

        buttons = QHBoxLayout()
        edit_button = QPushButton("编辑", inner)
        edit_button.setObjectName("GhostButton")
        edit_button.setCursor(Qt.PointingHandCursor)
        edit_button.clicked.connect(lambda: self.edit_character(self._selected_id))
        buttons.addWidget(edit_button)
        buttons.addStretch(1)
        delete_button = QPushButton("删除角色", inner)
        delete_button.setObjectName("DangerButton")
        delete_button.setCursor(Qt.PointingHandCursor)
        delete_button.clicked.connect(lambda: self.delete_character(self._selected_id))
        buttons.addWidget(delete_button)
        box.addLayout(buttons)

        inner.setStyleSheet("QWidget{background:transparent;}")
        self.detail_layout.addWidget(inner)

    # ------------------------------------------------------------------
    # 增删改（直接读写 Storage）
    # ------------------------------------------------------------------
    def add_character(self) -> None:
        dialog = CharacterDialog(self, None)
        if dialog.exec() != CharacterDialog.Accepted:
            return
        values = dialog.values()
        # 新角色默认放在画布中心附近（画布打开时会自动散开 x=y=0 的节点）
        self.storage.save_character(values)
        self._selected_id = str(values.get("id") or "")
        self.reload()
        self.data_changed.emit()

    def edit_character(self, char_id: str) -> None:
        char = self._char_by_id(char_id)
        if char is None:
            return
        dialog = CharacterDialog(self, char)
        if dialog.exec() != CharacterDialog.Accepted:
            return
        values = dialog.values()
        # 保留关系图画布上的坐标（表单不编辑坐标）
        values["x"] = char.get("x") or 0
        values["y"] = char.get("y") or 0
        self.storage.save_character(values)
        self._selected_id = str(values.get("id") or "")
        self.reload()
        self.data_changed.emit()

    def delete_character(self, char_id: str) -> None:
        char = self._char_by_id(char_id)
        if char is None:
            return
        name = str(char.get("name") or "该角色")
        rel_count = len(self._relations_of(char_id))
        extra = f"\n与该角色相关的 {rel_count} 条关系将一并删除。" if rel_count else ""
        if not MessageBox.question(self, "删除角色", f"确定删除角色「{name}」？{extra}"):
            return
        self.storage.delete_character(char_id)
        self._selected_id = ""
        if self._board is not None:
            self._board.remove_character(char_id)
        self.reload()
        self.data_changed.emit()

    def export_json(self) -> None:
        """导出全部角色、关系、画布坐标/缩放与标签为 UTF-8(BOM) JSON。"""
        import datetime
        import json

        # 画布上 400ms 防抖中的拖拽/连线先落盘，保证导出的是最新状态
        self.flush_pending()
        characters = self.storage.load_characters()
        relations = self.storage.load_relations()
        board = self.storage.load_board()
        if self._board is not None:
            board["zoom"] = float(self._board.canvas._zoom)
            board["scroll_x"] = int(self._board.scroll.horizontalScrollBar().value())
            board["scroll_y"] = int(self._board.scroll.verticalScrollBar().value())
        now = datetime.datetime.now()
        stamp = now.strftime("%Y%m%d_%H%M%S")
        title = "".join(
            ch for ch in (self.book_name or "催客写作") if ch not in '\\/:*?"<>|'
        ).strip() or "催客写作"
        default_name = f"{title}_角色_{stamp}.json"
        path, _f = QFileDialog.getSaveFileName(
            self, "导出角色", default_name, "JSON 文件 (*.json)"
        )
        if not path:
            return
        if not path.lower().endswith(".json"):
            path += ".json"
        tags = sorted({
            str(tag)
            for char in characters
            for tag in (char.get("tags") or [])
            if str(tag).strip()
        })
        data = {
            "version": 2,
            "app": "催客写作",
            "exported_at": now.isoformat(timespec="seconds"),
            "book_name": self.book_name,
            "characters": characters,
            "relations": relations,
            "board": board,
            "tags": tags,
        }
        try:
            # utf-8-sig 带 BOM：Windows 记事本 / Excel 直接打开不乱码
            with open(path, "w", encoding="utf-8-sig", newline="\n") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except OSError as exc:
            MessageBox.warning(self, "导出失败", f"无法写入文件：{exc}")
            return
        MessageBox.information(
            self, "导出成功",
            f"已导出 {len(characters)} 名角色、{len(relations)} 条关系到：\n{path}",
        )

    def flush_pending(self) -> None:
        """主窗口退出前兜底：关系画布上防抖中的改动立即落盘。"""
        if self._board is not None:
            self._board.flush_pending()

    def reload(self) -> None:
        """从 Storage 重新读取并刷新（保持当前筛选与选择）。"""
        characters = self.storage.load_characters()
        relations = self.storage.load_relations()
        self.set_data(characters, relations)
        # 关系图已创建时同步节点（坐标 / 缩放由画布自持，这里只换角色数据）
        if self._board is not None:
            if self._view == "board":
                # 导入 / 外部改动会新增关系，画布需整体换数据（不只是刷新节点）
                self._board.set_data(
                    characters, relations, self.theme, self.storage.load_board_size()
                )
            else:
                self._board.reload_characters(characters)
