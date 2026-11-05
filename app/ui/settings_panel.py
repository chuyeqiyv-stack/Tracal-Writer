# -*- coding: utf-8 -*-
"""设置面板：居中的大窗浮层（两列分组），修改后立即生效并自动保存。"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizeGrip,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app import config
from app.models import Settings
from app.ui.widgets import AvatarView, ColorButton, HLine, SegmentedControl, SettingRow, ToggleSwitch


class _DimOverlay(QWidget):
    """主窗口变暗遮罩：盖住主窗口并拦截点击，点窗口外即关闭设置窗口。"""

    def __init__(self, parent: QWidget, dialog: "SettingsDrawer") -> None:
        super().__init__(parent)
        self._dialog = dialog
        self.setObjectName("SettingsDim")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet("QWidget#SettingsDim { background: rgba(8, 10, 14, 0.34); }")
        self.hide()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self._dialog.close_drawer()
        event.accept()


class _TitleBar(QWidget):
    """可拖动的标题栏（无边框窗口靠它移动）。"""

    def __init__(self, dialog: "SettingsDrawer", parent=None) -> None:
        super().__init__(parent)
        self._dialog = dialog
        self._drag_offset: QPoint | None = None

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._drag_offset = (
                event.globalPosition().toPoint() - self._dialog.frameGeometry().topLeft()
            )
            event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            self._dialog.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._drag_offset = None
        super().mouseReleaseEvent(event)


class SettingsDrawer(QDialog):
    """居中的大窗设置浮层：两列分组、尺寸 / 位置记忆、Esc 与点外部关闭。

    类名与原有信号 / 方法保持不变，主窗口仍通过 self.drawer 访问；设置项的
    读写、即时生效与防抖保存逻辑不变，只把原来的右侧窄抽屉换成大窗口。
    """

    settings_changed = Signal()
    profile_changed = Signal(str, str)      # (昵称, 头像路径)
    webdav_upload_requested = Signal()
    webdav_restore_requested = Signal()
    webdav_test_requested = Signal()
    webdav_create_dir_requested = Signal()
    webdav_prune_requested = Signal()
    auto_sync_now_requested = Signal()
    data_dir_change_requested = Signal(str)  # 修改默认保存（数据）路径
    geometry_changed = Signal()              # 窗口尺寸 / 位置变化（轻量落盘）
    opened = Signal()
    closed = Signal()

    WIDTH = 356        # 兼容旧引用；大窗不再使用
    TOP_INSET = 64
    GUTTER = 22        # 卡片四周留给阴影的透明边距
    MIN_WIDTH = 900
    MIN_HEIGHT = 640
    PASSWORD_HINT = (
        "应用密码生成方式：坚果云网页版 → 账户信息 → 安全选项 → "
        "第三方应用管理 → 添加应用密码（不是账号登录密码）。"
    )
    PASSWORD_EMAIL_WARN = (
        "这看起来像邮箱，应用密码是一串字符；请到 账户信息 → 安全选项 → "
        "第三方应用管理 → 添加应用密码。"
    )

    def __init__(self, settings: Settings, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setModal(False)   # 用遮罩拦截主窗口点击，效果同模态且能点外部关闭
        self.hide()

        self._is_open = False
        self._syncing = False
        self._switches: dict[str, ToggleSwitch] = {}
        self._segments: dict[str, SegmentedControl] = {}
        self._colors: dict[str, ColorButton] = {}
        self._overlay = _DimOverlay(parent, self) if parent is not None else None
        if parent is not None:
            parent.installEventFilter(self)

        self._build_ui()
        self._apply_initial_geometry()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        from app.ui import motion, surface

        outer = motion.card_surface(
            self, gutter=self.GUTTER, content=(26, 18, 26, 18),
            spacing=12, readable=True, force_readable=True,
        )
        card = self._surface_card
        surface.attach_sheen(card)   # 高光斑 180ms 跟随指针

        header = _TitleBar(self, card)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(10)
        title = QLabel("设置", header)
        title.setObjectName("DrawerTitle")
        self.close_button = QPushButton(header)
        self.close_button.setObjectName("DrawerClose")
        self.close_button.setFixedSize(28, 28)
        self.close_button.setCursor(Qt.PointingHandCursor)
        self.close_button.setToolTip("关闭设置（Esc）")
        self.close_button.setAccessibleName("关闭设置")
        self.close_button.clicked.connect(lambda: self.close_drawer())
        header_layout.addWidget(title)
        header_layout.addStretch(1)
        header_layout.addWidget(self.close_button)
        outer.addWidget(header)

        scroll = QScrollArea(card)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # 设置面板：同正文一样的隐形快速滚动滑块
        from app.ui.scroll_float import ScrollFlasher

        self._scroll_flasher = ScrollFlasher(scroll, Qt.Vertical, inset=6)

        self._content_host = QWidget(scroll)
        columns = QHBoxLayout(self._content_host)
        columns.setContentsMargins(2, 4, 8, 8)
        columns.setSpacing(22)
        self._left_column = QVBoxLayout()
        self._left_column.setSpacing(12)
        self._right_column = QVBoxLayout()
        self._right_column.setSpacing(12)
        columns.addLayout(self._left_column, 1)
        columns.addLayout(self._right_column, 1)
        scroll.setWidget(self._content_host)
        outer.addWidget(scroll, 1)

        self._active_column = self._left_column
        self._build_sections()
        self._left_column.addStretch(1)
        self._right_column.addStretch(1)

        footer = QHBoxLayout()
        footer.setSpacing(8)
        hint = QLabel("所有设置会自动保存到本地", card)
        hint.setObjectName("DrawerHint")
        footer.addWidget(hint, 1)
        grip = QSizeGrip(card)
        grip.setFixedSize(16, 16)
        grip.setToolTip("拖动调整窗口大小")
        footer.addWidget(grip, 0, Qt.AlignRight | Qt.AlignBottom)
        outer.addLayout(footer)

    def _add_section(self, title: str) -> QVBoxLayout:
        host = self._content_host
        container = QWidget(host)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(9)
        label = QLabel(title, container)
        label.setObjectName("SectionTitle")
        layout.addWidget(label)
        self._active_column.addWidget(container)
        self._active_column.addWidget(HLine(host))
        return layout

    def _add_switch(self, layout: QVBoxLayout, field: str, title: str, hint: str = "") -> None:
        switch = ToggleSwitch(bool(getattr(self.settings, field)))
        switch.toggled.connect(lambda checked, f=field: self._set_value(f, checked))
        self._switches[field] = switch
        layout.addWidget(SettingRow(title, switch, hint))

    def _add_segment(
        self,
        layout: QVBoxLayout,
        field: str,
        title: str,
        options: list[tuple[str, str]],
    ) -> None:
        segment = SegmentedControl(options, str(getattr(self.settings, field)))
        segment.value_changed.connect(lambda value, f=field: self._set_value(f, value))
        self._segments[field] = segment
        layout.addWidget(SettingRow(title, segment))

    def _add_color(self, layout: QVBoxLayout, field: str, title: str, hint: str = "") -> None:
        button = ColorButton(str(getattr(self.settings, field) or ""))
        button.value_changed.connect(lambda value, f=field: self._set_color(f, value))
        self._colors[field] = button
        layout.addWidget(SettingRow(title, button, hint))

    # 品牌主色调预设色块
    ACCENT_PRESETS = [
        ("#7C5CE6", "主题紫"),
        ("#2F5FA8", "湖蓝"),
        ("#2F7D4F", "松绿"),
        ("#C4761F", "橘橙"),
        ("#C2413B", "砖红"),
        ("#D9527E", "桃粉"),
        ("#8C6B1F", "棕黄"),
        ("#4FC3F7", "天青"),
    ]

    def _build_accent_presets(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(7)
        row.setContentsMargins(0, 0, 0, 0)
        for hex_color, name in self.ACCENT_PRESETS:
            swatch = QToolButton(self)
            swatch.setFixedSize(20, 20)
            swatch.setCursor(Qt.PointingHandCursor)
            swatch.setToolTip(f"{name} {hex_color}")
            swatch.setStyleSheet(
                f"QToolButton {{ background: {hex_color}; border: none; border-radius: 10px; }}"
                "QToolButton:hover { border: 2px solid rgba(255,255,255,0.85); }"
            )
            swatch.clicked.connect(
                lambda _=False, c=hex_color: self._apply_accent_preset(c)
            )
            row.addWidget(swatch)
        row.addStretch(1)
        return row

    def _apply_accent_preset(self, hex_color: str) -> None:
        button = self._colors.get("accent_color")
        if button is not None:
            button.set_value(hex_color)
        self._set_color("accent_color", hex_color)

    def _build_sections(self) -> None:
        # 外观：风格 + 深浅色 + 品牌主色调 + 独立配色
        section = self._add_section("外观")
        self._add_segment(section, "mode", "深色 / 浅色", [("浅色", "light"), ("深色", "dark")])
        self._add_segment(
            section, "ui_style", "外观风格",
            [("默认", "default"), ("纸感", "paper"), ("玻璃", "glass"), ("秩序", "order")],
        )
        self._add_segment(
            section, "glass_mode", "玻璃质感",
            [("立体玻璃块", "block"), ("液态玻璃", "liquid"), ("磨砂雾面", "frosted")],
        )
        self._add_switch(
            section, "glass_blur", "玻璃背景模糊",
            "外观风格选「玻璃」时，对抽屉 / 对话框背后的内容做高斯模糊；关闭后仅保留半透明玻璃面，可在低配置设备上提升流畅度。",
        )
        self._add_switch(
            section, "boot_animation", "启动莲花动画",
            "启动时播放约 0.6 秒的黑白线稿莲花绽放，点击或按任意键可跳过；与加载并行不拖慢启动。",
        )
        self._add_color(
            section, "accent_color", "品牌主色调",
            "按钮、选中目录项与焦点边框都会使用此颜色，留空跟随默认主题。",
        )
        section.addLayout(self._build_accent_presets())
        self._add_color(section, "toolbar_bg", "工具栏背景", "留空表示跟随当前模式。")
        self._add_color(section, "toolbar_text", "工具栏文字")
        self._add_color(section, "statusbar_bg", "状态栏背景")
        self._add_color(section, "statusbar_text", "状态栏文字")
        self._add_color(section, "editor_bg", "写作区背景")
        self._add_color(section, "editor_text", "写作区文字")

        # 个人资料
        section = self._add_section("个人资料")
        profile_row = QHBoxLayout()
        profile_row.setSpacing(10)
        self.avatar_view = AvatarView(44)
        self.avatar_view.set_data(self.settings.avatar_path, self.settings.nickname)
        profile_row.addWidget(self.avatar_view)
        profile_box = QVBoxLayout()
        profile_box.setSpacing(6)
        self.nickname_edit = QLineEdit(self.settings.nickname, self)
        self.nickname_edit.setPlaceholderText("昵称")
        self.nickname_edit.editingFinished.connect(self._on_profile_edited)
        self.avatar_edit = QLineEdit(self.settings.avatar_path, self)
        self.avatar_edit.setPlaceholderText("头像图片绝对路径（可留空）")
        self.avatar_edit.editingFinished.connect(self._on_profile_edited)
        profile_box.addWidget(self.nickname_edit)
        profile_box.addWidget(self.avatar_edit)
        profile_row.addLayout(profile_box, 1)
        section.addLayout(profile_row)

        # 排版设置
        section = self._add_section("排版设置")
        self._add_segment(section, "indent", "段首缩进", [("2字符", "two"), ("无", "none")])
        self._add_segment(
            section, "blank_line", "段间空行", [("空一行含缩进", "blank"), ("无", "none")]
        )
        self._add_switch(section, "clean_spaces", "清除多余空格", "智能排版时清理行尾空格与标点旁的空格。")
        self._add_switch(section, "unify_punct", "半角标点转全角", "智能排版时把中文段落里的半角标点统一为全角。")
        self._add_switch(section, "ellipsis_fix", "规范省略号破折号", "智能排版时把「。。。」「---」规范为「……」「——」。")

        # 输入设置
        section = self._add_section("输入设置")
        self._add_switch(section, "auto_pair", "成对标点", "输入左引号、左括号时自动补全另一半。")

        # 字数设置
        section = self._add_section("字数设置")
        self._add_switch(section, "exclude_punct", "不计标点符号", "字数统计和码字时速都不计算标点。")

        # 操作设置
        section = self._add_section("操作设置")
        self._add_switch(section, "fast_scroll", "快速滚动滑块", "在正文右侧显示纵向快速滚动条。")
        self._add_switch(section, "float_jump", "置顶置底按钮", "在正文右下角浮动显示回到顶部 / 底部按钮。")
        self._add_switch(section, "open_at_end", "打开时定位至文章末尾", "打开章节后，光标自动停在文末。")

        # 存储（默认保存路径）起切到右列；左列保留外观 / 资料 / 排版 / 输入 / 字数 / 操作
        self._active_column = self._right_column
        section = self._add_section("存储")
        dir_title = QLabel("默认保存路径", section.parentWidget())
        dir_title.setObjectName("SectionTitle")
        section.addWidget(dir_title)
        self.data_dir_label = QLabel(str(config.DATA_DIR), section.parentWidget())
        self.data_dir_label.setObjectName("DrawerHint")
        self.data_dir_label.setWordWrap(True)
        section.addWidget(self.data_dir_label)
        change_dir_button = QPushButton("修改默认保存路径…", section.parentWidget())
        change_dir_button.setCursor(Qt.PointingHandCursor)
        change_dir_button.clicked.connect(self._pick_data_dir)
        section.addWidget(change_dir_button)
        dir_hint = QLabel(
            "修改后会自动把现有数据迁移到新位置，重启应用后生效。",
            section.parentWidget(),
        )
        dir_hint.setObjectName("DrawerHint")
        dir_hint.setWordWrap(True)
        section.addWidget(dir_hint)

        # 云同步（坚果云 WebDAV，纯手动）
        section = self._add_section("云同步（坚果云 WebDAV）")
        self.webdav_url_edit = QLineEdit(self.settings.webdav_url, self)
        self.webdav_url_edit.setPlaceholderText("https://dav.jianguoyun.com/dav/")
        self.webdav_url_edit.setClearButtonEnabled(True)
        self.webdav_url_edit.setToolTip(
            "必须 https 且以 /dav/ 结尾，例如 https://dav.jianguoyun.com/dav/"
        )
        self.webdav_url_edit.editingFinished.connect(self._on_webdav_edited)
        section.addWidget(SettingRow("WebDAV 地址", self.webdav_url_edit))

        self.webdav_dir_edit = QLineEdit(str(getattr(self.settings, "webdav_dir", "")), self)
        self.webdav_dir_edit.setPlaceholderText("留空默认「催客写作」，可填 测试 或 写作备份/催客")
        self.webdav_dir_edit.setClearButtonEnabled(True)
        self.webdav_dir_edit.setToolTip("同步目录最多 3 级；中文与空格会自动按百分号编码")
        self.webdav_dir_edit.editingFinished.connect(self._on_webdav_edited)
        section.addWidget(SettingRow("同步目录", self.webdav_dir_edit))

        self.webdav_user_edit = QLineEdit(self.settings.webdav_user, self)
        self.webdav_user_edit.setPlaceholderText("坚果云账号邮箱")
        self.webdav_user_edit.setToolTip("只填邮箱，不需要加 https:// 或 dav.jianguoyun.com")
        self.webdav_user_edit.editingFinished.connect(self._on_webdav_edited)
        section.addWidget(SettingRow("账号", self.webdav_user_edit))

        self.webdav_password_edit = QLineEdit(self.settings.webdav_password, self)
        self.webdav_password_edit.setPlaceholderText("16 位字母数字组合（不是登录密码）")
        self.webdav_password_edit.setEchoMode(QLineEdit.Password)
        self.webdav_password_edit.setToolTip(
            "坚果云：账户信息 → 安全选项 → 第三方应用管理 → 添加应用密码"
        )
        self.webdav_password_edit.editingFinished.connect(self._on_webdav_edited)
        section.addWidget(SettingRow("应用密码", self.webdav_password_edit))

        self.webdav_password_hint = QLabel(self.PASSWORD_HINT, self)
        self.webdav_password_hint.setObjectName("DrawerHint")
        self.webdav_password_hint.setWordWrap(True)
        if getattr(self.settings, "webdav_password_lost", False):
            self.webdav_password_hint.setText("原来的应用密码无法解密，请重新填写应用密码。")
        elif "@" in (self.settings.webdav_password or ""):
            self.webdav_password_hint.setText(self.PASSWORD_EMAIL_WARN)
        section.addWidget(self.webdav_password_hint)

        sync_buttons = QHBoxLayout()
        upload_button = QPushButton("上传备份到云端", self)
        upload_button.setObjectName("Primary")
        upload_button.setCursor(Qt.PointingHandCursor)
        upload_button.clicked.connect(self.webdav_upload_requested.emit)
        restore_button = QPushButton("从云端恢复数据", self)
        restore_button.setCursor(Qt.PointingHandCursor)
        restore_button.clicked.connect(self.webdav_restore_requested.emit)
        prune_button = QPushButton("清理旧备份", self)
        prune_button.setCursor(Qt.PointingHandCursor)
        prune_button.setToolTip("删除云端旧备份，只保留最近 3 份")
        prune_button.clicked.connect(self.webdav_prune_requested.emit)
        sync_buttons.addWidget(upload_button)
        sync_buttons.addWidget(restore_button)
        sync_buttons.addWidget(prune_button)
        section.addLayout(sync_buttons)

        # 自动同步：开关 + 四档间隔 + 上次同步状态 + 立即同步
        self._add_switch(
            section, "auto_sync_enabled", "自动同步",
            "开启后按所选间隔在后台静默上传备份；内容未变化（哈希+大小一致）时不发请求。",
        )
        self._add_segment(
            section, "auto_sync_interval", "同步间隔",
            [("10 分钟", "10"), ("20 分钟", "20"), ("30 分钟", "30"), ("60 分钟", "60")],
        )
        status_row = QHBoxLayout()
        status_row.setSpacing(10)
        self.auto_sync_status = QLabel(self)
        self.auto_sync_status.setObjectName("DrawerHint")
        self.auto_sync_status.setWordWrap(True)
        self.auto_sync_status_label = self.auto_sync_status
        status_row.addWidget(self.auto_sync_status, 1)
        self.webdav_test_button = QPushButton("测试连接", self)
        self.webdav_test_button.setCursor(Qt.PointingHandCursor)
        self.webdav_test_button.setToolTip("只做一次 PROPFIND，不改动云端文件")
        self.webdav_test_button.clicked.connect(self.webdav_test_requested.emit)
        status_row.addWidget(self.webdav_test_button)
        self.create_dir_button = QPushButton("创建该目录", self)
        self.create_dir_button.setCursor(Qt.PointingHandCursor)
        self.create_dir_button.setToolTip("逐级创建同步目录（最多 3 级），成功后会复测")
        self.create_dir_button.clicked.connect(self.webdav_create_dir_requested.emit)
        self.create_dir_button.hide()
        status_row.addWidget(self.create_dir_button)
        self.auto_sync_now_button = QPushButton("立即同步", self)
        self.auto_sync_now_button.setCursor(Qt.PointingHandCursor)
        self.auto_sync_now_button.clicked.connect(self.auto_sync_now_requested.emit)
        status_row.addWidget(self.auto_sync_now_button)
        section.addLayout(status_row)
        self._refresh_auto_sync_status()

        note = QLabel(
            "上传会把本地全部 JSON 数据打包为备份文件；恢复会用云端备份覆盖本地数据。"
            "手动上传 / 恢复需确认，自动同步仅在后台上传且不弹窗。",
            self,
        )
        note.setObjectName("DrawerHint")
        note.setWordWrap(True)
        section.addWidget(note)

        switch_note = QLabel(
            "切换账号或同步目录不会迁移旧账号的备份：旧备份仍在原账号/原目录里，"
            "需要在新账号或新目录重新上传一次（切换后状态会重置为「尚未同步」）。",
            self,
        )
        switch_note.setObjectName("DrawerHint")
        switch_note.setWordWrap(True)
        section.addWidget(switch_note)

        # 关于
        section = self._add_section("关于")
        about_title = QLabel(f"{config.APP_NAME} {config.APP_NAME_EN}", section.parentWidget())
        about_title.setObjectName("SectionTitle")
        section.addWidget(about_title)
        version_label = QLabel(f"版本 {config.APP_VERSION} · 纯本地单机写作软件", section.parentWidget())
        version_label.setObjectName("DrawerHint")
        section.addWidget(version_label)
        author_label = QLabel("设计与开发：夜夜笙歌林夜歌 · AI 协作", section.parentWidget())
        author_label.setObjectName("DrawerHint")
        section.addWidget(author_label)

    # ------------------------------------------------------------------
    def _set_value(self, field: str, value) -> None:
        if self._syncing:
            return
        if field == "auto_sync_interval":
            try:
                value = int(value)
            except (TypeError, ValueError):
                return
        if getattr(self.settings, field) == value:
            return
        setattr(self.settings, field, value)
        self.settings_changed.emit()

    def _set_color(self, field: str, value: str) -> None:
        self._set_value(field, value.upper())

    def _on_profile_edited(self) -> None:
        nickname = self.nickname_edit.text().strip() or "写作者"
        avatar = self.avatar_edit.text().strip()
        if nickname == self.settings.nickname and avatar == self.settings.avatar_path:
            return
        self.settings.nickname = nickname
        self.settings.avatar_path = avatar
        self.avatar_view.set_data(avatar, nickname)
        self.settings_changed.emit()
        self.profile_changed.emit(nickname, avatar)

    def _on_webdav_edited(self) -> None:
        from app.services.webdav_sync import WebDavError, normalize_remote_dir, normalize_webdav_url

        raw_url = self.webdav_url_edit.text()
        url = normalize_webdav_url(raw_url)
        if url != raw_url:
            self.webdav_url_edit.setText(url)   # 去重重复粘贴的协议头
        raw_dir = self.webdav_dir_edit.text()
        try:
            dir_text = normalize_remote_dir(raw_dir)
        except WebDavError as exc:
            self.set_auto_sync_status(str(exc))
            return   # 非法目录：不保存，保留用户输入便于修改
        if raw_dir.strip() and dir_text != raw_dir.strip():
            self.webdav_dir_edit.setText(dir_text)   # 合并多余斜杠
        stored_dir = "" if not raw_dir.strip() else dir_text   # 空值回退默认目录
        user = self.webdav_user_edit.text().strip()
        password = self.webdav_password_edit.text()   # 原样保存，不 strip / 不改大小写
        if "@" in password:
            self.webdav_password_hint.setText(self.PASSWORD_EMAIL_WARN)
        else:
            self.webdav_password_hint.setText(self.PASSWORD_HINT)
        changed = (
            url != self.settings.webdav_url
            or stored_dir != str(getattr(self.settings, "webdav_dir", ""))
            or user != self.settings.webdav_user
            or password != self.settings.webdav_password
        )
        if not changed:
            return
        self.settings.webdav_url = url or "https://dav.jianguoyun.com/dav/"
        self.settings.webdav_dir = stored_dir
        self.settings.webdav_user = user
        self.settings.webdav_password = password
        self.settings_changed.emit()

    def set_create_dir_available(self, available: bool) -> None:
        """测试连接返回「目录不存在」时，就地出现「创建该目录」按钮。"""
        if hasattr(self, "create_dir_button"):
            self.create_dir_button.setVisible(bool(available))

    def refresh_values(self) -> None:
        """外部修改设置（例如字体弹窗、主题面板）后，同步控件显示。"""
        self._syncing = True
        try:
            for field, switch in self._switches.items():
                switch.setChecked(bool(getattr(self.settings, field)))
                switch.update()
            for field, segment in self._segments.items():
                segment.set_value(str(getattr(self.settings, field)))
            for field, color in self._colors.items():
                color.set_value(str(getattr(self.settings, field) or ""))
            self.nickname_edit.setText(self.settings.nickname)
            self.avatar_edit.setText(self.settings.avatar_path)
            self.avatar_view.set_data(self.settings.avatar_path, self.settings.nickname)
            self.webdav_url_edit.setText(self.settings.webdav_url)
            self.webdav_dir_edit.setText(str(getattr(self.settings, "webdav_dir", "")))
            self.webdav_user_edit.setText(self.settings.webdav_user)
            self.webdav_password_edit.setText(self.settings.webdav_password)
            self.create_dir_button.hide()
            self._refresh_auto_sync_status()
        finally:
            self._syncing = False

    # ------------------------------------------------------------------
    # 自动同步状态
    # ------------------------------------------------------------------
    def _refresh_auto_sync_status(self) -> None:
        if not hasattr(self, "auto_sync_status"):
            return
        if not self.settings.auto_sync_last_time:
            text = "上次同步：尚未同步"
        else:
            state = "成功" if self.settings.auto_sync_last_ok else "失败"
            text = f"上次同步 {self.settings.auto_sync_last_time} · {state}"
        if not self.settings.auto_sync_enabled:
            text += "（自动同步已关闭）"
        self.auto_sync_status.setText(text)

    def set_auto_sync_status(self, text: str) -> None:
        """主窗口在同步完成 / 失败 / 暂停时更新状态文案。"""
        if hasattr(self, "auto_sync_status"):
            self.auto_sync_status.setText(text)

    def repaint_switches(self) -> None:
        for switch in self._switches.values():
            switch.update()

    # ------------------------------------------------------------------
    # 默认保存路径
    # ------------------------------------------------------------------
    def set_data_dir(self, path: str) -> None:
        """主窗口更新当前数据目录显示。"""
        if hasattr(self, "data_dir_label"):
            self.data_dir_label.setText(path)

    def _pick_data_dir(self) -> None:
        import os

        current = self.data_dir_label.text() if hasattr(self, "data_dir_label") else ""
        start = current if current and os.path.isdir(current) else os.path.expanduser("~")
        path = QFileDialog.getExistingDirectory(self, "选择默认保存路径", start)
        if path:
            self.data_dir_change_requested.emit(path)

    # ------------------------------------------------------------------
    # 窗口尺寸 / 位置
    # ------------------------------------------------------------------
    def _target_screen(self):
        parent = self.parentWidget()
        if parent is not None:
            screen = QGuiApplication.screenAt(parent.mapToGlobal(parent.rect().center()))
            if screen is not None:
                return screen
        return QGuiApplication.primaryScreen()

    def _bounds(self) -> QRect:
        screen = self._target_screen()
        return screen.availableGeometry() if screen is not None else QRect()

    def _min_size(self) -> tuple[int, int]:
        b = self._bounds()
        return (min(self.MIN_WIDTH, b.width()), min(self.MIN_HEIGHT, b.height()))

    def _apply_initial_geometry(self) -> None:
        """默认取屏幕可用区约 65%×82%，并恢复上次尺寸 / 位置（夹在屏幕内）。"""
        b = self._bounds()
        w = int(getattr(self.settings, "settings_win_w", 0) or 0)
        h = int(getattr(self.settings, "settings_win_h", 0) or 0)
        if w <= 0 or h <= 0:
            w = int(b.width() * 0.65)
            h = int(b.height() * 0.82)
        x = int(getattr(self.settings, "settings_win_x", -1))
        y = int(getattr(self.settings, "settings_win_y", -1))
        self._place(w, h, x, y)

    def _place(self, w: int, h: int, x: int, y: int) -> None:
        b = self._bounds()
        mw, mh = self._min_size()
        w = max(mw, min(w, b.width()))
        h = max(mh, min(h, b.height()))
        if x < 0 or y < 0:
            x = b.x() + (b.width() - w) // 2
            y = b.y() + (b.height() - h) // 2
        else:
            x = max(b.x(), min(x, b.x() + b.width() - w))
            y = max(b.y(), min(y, b.y() + b.height() - h))
        self.setMinimumSize(mw, mh)
        self.setMaximumSize(b.width(), b.height())
        self.setGeometry(x, y, w, h)

    def _remember_geometry(self) -> None:
        g = self.geometry()
        self.settings.settings_win_w = g.width()
        self.settings.settings_win_h = g.height()
        self.settings.settings_win_x = g.x()
        self.settings.settings_win_y = g.y()
        self.geometry_changed.emit()

    def _clamp_into_screen(self) -> None:
        g = self.geometry()
        self._place(g.width(), g.height(), g.x(), g.y())

    def _show_overlay(self) -> None:
        parent = self.parentWidget()
        if parent is None or self._overlay is None:
            return
        self._overlay.setGeometry(parent.rect())
        self._overlay.show()
        self._overlay.raise_()

    def _hide_overlay(self) -> None:
        if self._overlay is not None:
            self._overlay.hide()

    # ------------------------------------------------------------------
    # 打开 / 关闭
    # ------------------------------------------------------------------
    def open_drawer(self, animate: bool = True) -> None:
        # 只有“已打开且真的可见”才复用；平台层把窗口隐藏（最小化到任务栏 /
        # Win+D 时 Qt 会 hide 带父窗口的对话框）后 _is_open 会残留 True，
        # 若直接 return 会让遮罩永久盖住主窗口、设置也再也打不开。
        if self._is_open and self.isVisible():
            self.raise_()
            self.activateWindow()
            return
        self._is_open = False
        self._hide_overlay()
        self.refresh_values()
        self._apply_initial_geometry()
        self._is_open = True
        self._show_overlay()
        self.show()
        self.raise_()
        self.activateWindow()
        self.opened.emit()

    def showEvent(self, event) -> None:  # noqa: N802
        """被平台层重新显示（如主窗口从最小化还原）时补齐遮罩，保持一致的交互。

        外部隐藏时 hideEvent 已发过 closed（入口按钮取消勾选）；这里补发 opened，
        让按钮勾选态与「设置窗可见」重新一致，避免显示着却显示未选中。
        """
        super().showEvent(event)
        if not self._is_open:
            self._is_open = True
            self._show_overlay()
            self.opened.emit()

    def hideEvent(self, event) -> None:  # noqa: N802
        """任何外部隐藏都必须撤掉遮罩。

        主窗口最小化 / Win+D / 切换虚拟桌面时，Qt 会把带父窗口的对话框隐藏，
        但不会走 close_drawer()；遮罩是主窗口的子控件会跟着还原，于是变成
        “屏幕变暗 + 整个主窗口点击被吞掉 + 设置打不开”。这里把状态复位。
        """
        if self._is_open:
            self._is_open = False
            self._hide_overlay()
            self.closed.emit()
        super().hideEvent(event)

    def close_drawer(self, animate: bool = True) -> None:
        self._remember_geometry()
        self._is_open = False
        self._hide_overlay()
        self.hide()
        self.closed.emit()

    def toggle(self) -> None:
        if self._is_open:
            self.close_drawer()
        else:
            self.open_drawer()

    def reject(self) -> None:   # Esc 关闭
        self.close_drawer()

    def closeEvent(self, event) -> None:  # noqa: N802
        self._is_open = False
        self._remember_geometry()
        self._hide_overlay()
        event.accept()
        self.closed.emit()

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is self.parentWidget() and event.type() == QEvent.Resize:
            if self._overlay is not None and self._overlay.isVisible():
                self._overlay.setGeometry(obj.rect())
        return super().eventFilter(obj, event)

    # 兼容旧调用：主窗口尺寸变化不再联动，只保证窗口仍在屏幕内
    def relayout(self) -> None:
        if self.isVisible():
            self._clamp_into_screen()

    def set_backdrop(self, pixmap=None) -> None:
        """保留旧 API：大窗由卡片自绘，无外部背景位图。"""

    def refresh_surface(self) -> None:
        """风格 / 玻璃档切换后重抓背后位图并重绘承载面（底板始终存在）。"""
        card = getattr(self, "_surface_card", None)
        if card is None:
            return
        rebuild = getattr(card, "rebuild_backdrop", None)
        if rebuild is not None:
            rebuild()
        card.update()

    def refresh_icons(self, factory) -> None:
        """主题切换后重设关闭图标颜色（14px，跟随文字令牌）。"""
        self.close_button.setIconSize(QSize(14, 14))
        self.close_button.setIcon(factory.icon("close", 14))
