# -*- coding: utf-8 -*-
"""右侧“更多设置”抽屉面板：外观（浅/深色 + 独立配色）、排版、云同步等。"""
from __future__ import annotations

from PySide6.QtCore import (
    QEasingCurve,
    QPoint,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app import config
from app.models import Settings
from app.ui.widgets import AvatarView, ColorButton, HLine, SegmentedControl, SettingRow, ToggleSwitch


class SettingsDrawer(QWidget):
    """从右侧滑出的设置面板，修改后立即生效并自动保存。"""

    settings_changed = Signal()
    profile_changed = Signal(str, str)      # (昵称, 头像路径)
    webdav_upload_requested = Signal()
    webdav_restore_requested = Signal()
    data_dir_change_requested = Signal(str)  # 修改默认保存（数据）路径
    opened = Signal()
    closed = Signal()

    WIDTH = 356
    TOP_INSET = 64   # 顶部让出工具栏高度，避免盖住「设置」按钮

    def __init__(self, settings: Settings, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.setObjectName("Drawer")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedWidth(self.WIDTH)
        # 侧边抽屉：2 档浮层阴影（构造时挂一次，避免 enter/leave 动态挂卸闪退）
        from app.ui import motion
        motion.apply_elevation(self, 2, motion.app_is_dark())
        self.hide()

        self._is_open = False
        self._syncing = False
        self._switches: dict[str, ToggleSwitch] = {}
        self._segments: dict[str, SegmentedControl] = {}
        self._colors: dict[str, ColorButton] = {}
        # 液态玻璃：缓存的背景模糊位图（仅打开 / 尺寸稳定 120–150ms 后重算，绝不每帧）
        self._backdrop: QPixmap | None = None
        self._sheen_pos = None
        self._backdrop_timer = QTimer(self)
        self._backdrop_timer.setSingleShot(True)
        self._backdrop_timer.setInterval(135)
        self._backdrop_timer.timeout.connect(self._rebuild_backdrop)
        from app.ui import surface

        surface.attach_sheen(self)   # 高光斑 180ms 跟随指针

        self._anim = QPropertyAnimation(self, b"pos", self)
        self._anim.setDuration(220)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.finished.connect(self._on_anim_finished)

        self._build_ui()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 16, 14, 12)
        outer.setSpacing(10)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        title = QLabel("更多设置", self)
        title.setObjectName("DrawerTitle")
        self.close_button = QPushButton(self)
        self.close_button.setObjectName("DrawerClose")
        self.close_button.setFixedSize(28, 28)
        self.close_button.setCursor(Qt.PointingHandCursor)
        self.close_button.setToolTip("关闭设置（Esc）")
        self.close_button.setAccessibleName("关闭设置")
        self.close_button.clicked.connect(lambda: self.close_drawer())
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.close_button)
        outer.addLayout(header)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # 设置面板：同正文一样的隐形快速滚动滑块
        from app.ui.scroll_float import ScrollFlasher

        self._scroll_flasher = ScrollFlasher(scroll, Qt.Vertical, inset=6)

        content = QWidget(scroll)
        self._content = QVBoxLayout(content)
        self._content.setContentsMargins(2, 4, 8, 8)
        self._content.setSpacing(12)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)

        self._build_sections()

        hint = QLabel("所有设置会自动保存到本地", self)
        hint.setObjectName("DrawerHint")
        hint.setAlignment(Qt.AlignCenter)
        outer.addWidget(hint)

    def _add_section(self, title: str) -> QVBoxLayout:
        container = QWidget(self._content.parentWidget())
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(9)
        label = QLabel(title, container)
        label.setObjectName("SectionTitle")
        layout.addWidget(label)
        self._content.addWidget(container)
        self._content.addWidget(HLine(self._content.parentWidget()))
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

        # 存储（默认保存路径）
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
        self.webdav_url_edit.editingFinished.connect(self._on_webdav_edited)
        section.addWidget(SettingRow("WebDAV 地址", self.webdav_url_edit))

        self.webdav_user_edit = QLineEdit(self.settings.webdav_user, self)
        self.webdav_user_edit.setPlaceholderText("坚果云账号（邮箱）")
        self.webdav_user_edit.editingFinished.connect(self._on_webdav_edited)
        section.addWidget(SettingRow("账号", self.webdav_user_edit))

        self.webdav_password_edit = QLineEdit(self.settings.webdav_password, self)
        self.webdav_password_edit.setPlaceholderText("应用密码（不是登录密码）")
        self.webdav_password_edit.setEchoMode(QLineEdit.Password)
        self.webdav_password_edit.editingFinished.connect(self._on_webdav_edited)
        section.addWidget(SettingRow("密码", self.webdav_password_edit))

        sync_buttons = QHBoxLayout()
        upload_button = QPushButton("上传备份到云端", self)
        upload_button.setObjectName("Primary")
        upload_button.setCursor(Qt.PointingHandCursor)
        upload_button.clicked.connect(self.webdav_upload_requested.emit)
        restore_button = QPushButton("从云端恢复数据", self)
        restore_button.setCursor(Qt.PointingHandCursor)
        restore_button.clicked.connect(self.webdav_restore_requested.emit)
        sync_buttons.addWidget(upload_button)
        sync_buttons.addWidget(restore_button)
        section.addLayout(sync_buttons)

        note = QLabel(
            "上传会把本地全部 JSON 数据打包为备份文件；恢复会用云端备份覆盖本地数据。"
            "操作均为手动触发，不会自动同步。",
            self,
        )
        note.setObjectName("DrawerHint")
        note.setWordWrap(True)
        section.addWidget(note)

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
        url = self.webdav_url_edit.text().strip()
        user = self.webdav_user_edit.text().strip()
        password = self.webdav_password_edit.text()
        changed = (
            url != self.settings.webdav_url
            or user != self.settings.webdav_user
            or password != self.settings.webdav_password
        )
        if not changed:
            return
        self.settings.webdav_url = url or "https://dav.jianguoyun.com/dav/"
        self.settings.webdav_user = user
        self.settings.webdav_password = password
        self.settings_changed.emit()

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
            self.webdav_user_edit.setText(self.settings.webdav_user)
            self.webdav_password_edit.setText(self.settings.webdav_password)
        finally:
            self._syncing = False

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
    # 抽屉动画
    # ------------------------------------------------------------------
    def toggle(self) -> None:
        if self._is_open:
            self.close_drawer()
        else:
            self.open_drawer()

    def _rebuild_backdrop(self) -> None:
        """液态玻璃：抓取背后主内容区并模糊一次（缓存到下次开合 / 尺寸变化）。"""
        from app.ui import surface

        self._backdrop = None
        if not surface.is_glass():
            self.update()
            return
        # 关闭「玻璃背景模糊」设置：只保留半透明本体，不抓不糊
        if not surface.blur_enabled():
            self.update()
            return
        parent = self.parentWidget()
        if parent is None or not self._is_open:
            return
        origin = QPoint(max(0, parent.width() - self.WIDTH), self.TOP_INSET)
        raw = surface.grab_behind(self, parent, origin=origin)
        self._backdrop = surface.blur_pixmap(raw, surface.blur_sigma())
        self.update()

    def set_backdrop(self, pixmap: QPixmap | None) -> None:
        self._backdrop = pixmap

    def refresh_surface(self) -> None:
        """apply_theme 后调用：质感令牌已变，失效缓存并重画。"""
        if self._is_open:
            self._rebuild_backdrop()
        else:
            self._backdrop = None
        self.update()

    def refresh_icons(self, factory) -> None:
        """主题切换后重设关闭图标颜色（14px，跟随文字令牌）。"""
        self.close_button.setIconSize(QSize(14, 14))
        self.close_button.setIcon(factory.icon("close", 14))

    def paintEvent(self, event) -> None:  # noqa: N802
        from app.ui import surface

        if surface.is_glass():
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing, True)
            # 玻璃风抽屉：左侧圆角 14，右侧贴屏边；用缓存模糊位图 + 着色 + 边缘光学
            r = QRectF(self.rect())
            painter.save()
            painter.setClipRect(self.rect())
            surface.paint_liquid_sheet(
                painter, r, float(surface.card_radius()), self._backdrop,
                getattr(self, "_sheen_pos", None), readable=True)
            painter.restore()
            painter.end()
        else:
            super().paintEvent(event)

    def open_drawer(self, animate: bool = True) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        self._is_open = True
        # 顶部让出工具栏高度，保证「设置」按钮始终可见可点，随时可以退出
        top = getattr(self, "TOP_INSET", 0)
        self.setGeometry(
            parent.width() - self.WIDTH, top, self.WIDTH, parent.height() - top
        )
        # 液态玻璃：必须在自身 show() 之前抓背景，避免把抽屉自己拍进快照
        self._rebuild_backdrop()
        target = QPoint(parent.width() - self.WIDTH, top)
        self._anim.stop()
        self.show()
        self.raise_()
        if animate:
            self.move(parent.width(), top)
            self._anim.setStartValue(QPoint(parent.width(), top))
            self._anim.setEndValue(target)
            self._anim.start()
        else:
            self.move(target)
        self.opened.emit()

    def close_drawer(self, animate: bool = True) -> None:
        parent = self.parentWidget()
        self._is_open = False
        self._anim.stop()
        if parent is None:
            self.hide()
            self.closed.emit()
            return
        target = QPoint(parent.width(), getattr(self, "TOP_INSET", 0))
        if animate and self.isVisible():
            self._anim.setStartValue(self.pos())
            self._anim.setEndValue(target)
            self._anim.start()
        else:
            self.move(target)
            self.hide()
        self.closed.emit()

    def _on_anim_finished(self) -> None:
        if not self._is_open:
            self.hide()
            self._backdrop_timer.stop()
            self._backdrop = None

    def relayout(self) -> None:
        """主窗口尺寸变化时重新贴合右侧。"""
        parent = self.parentWidget()
        if parent is None:
            return
        top = getattr(self, "TOP_INSET", 0)
        self.setFixedHeight(parent.height() - top)
        if self._is_open:
            self.setGeometry(parent.width() - self.WIDTH, top, self.WIDTH,
                             parent.height() - top)
            self.move(parent.width() - self.WIDTH, top)
            # 尺寸连续变化时防抖，稳定后只重抓 / 模糊一次
            self._backdrop_timer.start()
        else:
            self.move(parent.width(), top)
