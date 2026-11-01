# -*- coding: utf-8 -*-
"""书架主页：左侧导航（首页 / 时间轴 / 大纲 / 设定 / 世界地图 / 成就 / 回收站）+ 右侧内容区。

- 书籍：大尺寸 3:4 封面卡片（网格）与列表行，带新作品角标与悬浮阴影；
- 时间轴 / 设定 / 大纲 / 世界地图：独立卡片，可创建 / 打开；
- 成就页：展示已解锁 / 未解锁的勋章；
- 空白处与卡片右键均提供现代圆角菜单（新建作品 / 新建设定 / 新建时间轴等）。
"""
from __future__ import annotations

import time

from app import config

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QCursor, QFontMetrics, QImage, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.services.achievements import ACHIEVEMENTS
from app.services.word_count import format_word_count
from app.ui.widgets import AvatarView, CardFrame, CoverView

# 大封面卡片尺寸（封面满铺卡片宽度，3:4 书籍比例）
CARD_W = 204
COVER_W = 204
COVER_H = 264
CARD_H = 336


def _elide(text: str, width: int, font) -> str:
    return QFontMetrics(font).elidedText(text, Qt.ElideRight, width)


def _date_text(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(float(ts or 0)))


def _is_new(meta: dict) -> bool:
    """创建不超过 7 天的书显示「新作品」角标。"""
    created = float(meta.get("created_at") or 0)
    return created > 0 and (time.time() - created) < 7 * 86400


class BookCard(CardFrame):
    """网格视图里的书籍卡片：立体封面 + 封面下方居中书名 / 作者（无信息底卡）。"""

    clicked = Signal(str)
    open_requested = Signal(str)
    rename_requested = Signal(str)
    cover_change_requested = Signal(str)
    trash_requested = Signal(str)
    move_group_requested = Signal(str)
    restore_requested = Signal(str)
    delete_forever_requested = Signal(str)
    export_requested = Signal(str)

    def __init__(self, meta: dict, parent=None) -> None:
        super().__init__(parent)
        self.book_id = str(meta.get("id") or "")
        self.in_trash = False  # 回收站中的卡片显示 恢复/彻底删除 菜单
        self.setObjectName("ShelfCard")
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(CARD_W, CARD_H)
        self.enable_lift(blur=16, alpha=24, dy=4)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.cover = CoverView(COVER_W, COVER_H, self)
        self.cover.set_data(
            str(meta.get("title") or "未命名"),
            "",
            int(meta.get("cover_index") or 0),
            _is_new(meta),
        )
        layout.addWidget(self.cover)

        # 封面下方直接居中排布书名与作者（和主流书城一致，不再套信息小卡片）
        text_box = QVBoxLayout()
        text_box.setContentsMargins(12, 10, 12, 12)
        text_box.setSpacing(4)
        self.title_label = QLabel(self)
        self.title_label.setObjectName("CardTitle")
        self.title_label.setAlignment(Qt.AlignCenter)
        self.title_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        text_box.addWidget(self.title_label)
        self.author_label = QLabel(self)
        self.author_label.setObjectName("CardAuthor")
        self.author_label.setAlignment(Qt.AlignCenter)
        self.author_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        text_box.addWidget(self.author_label)
        layout.addLayout(text_box, 1)

        self.set_meta(meta)

    def set_meta(self, meta: dict, cover_abs_path: str = "") -> None:
        self.book_id = str(meta.get("id") or self.book_id)
        title = str(meta.get("title") or "未命名")
        self.cover.set_data(
            title, cover_abs_path, int(meta.get("cover_index") or 0), _is_new(meta)
        )
        font = self.title_label.font()
        text_w = CARD_W - 32
        self.title_label.setText(_elide(title, text_w, font))
        self.title_label.setToolTip(title)
        author = str(meta.get("author") or "").strip() or "佚名"
        self.author_label.setText(_elide(author, text_w, self.author_label.font()))

    # ------------------------------------------------------------------
    # 注意：不要在 enter/leave 事件里动态挂/卸 QGraphicsDropShadowEffect
    # 或用动画移动控件——PySide6 6.12 下控件随后被隐藏会触发访问冲突闪退。
    # 悬停反馈完全交给 QSS 的 #ShelfCard:hover。
    # ------------------------------------------------------------------
    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.book_id)
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        self.open_requested.emit(self.book_id)
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        menu = QMenu(self)
        if self.in_trash:
            menu.addAction("恢复作品", lambda: self.restore_requested.emit(self.book_id))
            menu.addAction("彻底删除", lambda: self.delete_forever_requested.emit(self.book_id))
            menu.exec(event.globalPos())
            return
        menu.addAction("打开作品", lambda: self.open_requested.emit(self.book_id))
        menu.addAction("重命名", lambda: self.rename_requested.emit(self.book_id))
        menu.addAction("更换封面…", lambda: self.cover_change_requested.emit(self.book_id))
        menu.addAction("移动到分组…", lambda: self.move_group_requested.emit(self.book_id))
        menu.addSeparator()
        menu.addAction("导出 TXT…", lambda: self.export_requested.emit(self.book_id))
        menu.addSeparator()
        menu.addAction("移入回收站", lambda: self.trash_requested.emit(self.book_id))
        menu.exec(event.globalPos())


class BookRow(CardFrame):
    """列表视图里的单行书籍。"""

    clicked = Signal(str)
    open_requested = Signal(str)
    rename_requested = Signal(str)
    cover_change_requested = Signal(str)
    trash_requested = Signal(str)
    move_group_requested = Signal(str)
    restore_requested = Signal(str)
    delete_forever_requested = Signal(str)
    export_requested = Signal(str)

    def __init__(self, meta: dict, parent=None) -> None:
        super().__init__(parent)
        self.book_id = str(meta.get("id") or "")
        self.in_trash = False
        self.setObjectName("ShelfCard")
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(84)
        self.enable_lift(blur=14, alpha=18, dy=2)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 14, 10)
        layout.setSpacing(14)

        self.cover = CoverView(50, 66, self)
        layout.addWidget(self.cover)

        info = QVBoxLayout()
        info.setSpacing(3)
        self.title_label = QLabel(self)
        self.title_label.setObjectName("CardTitle")
        self.summary_label = QLabel(self)
        self.summary_label.setObjectName("CardSummary")
        info.addWidget(self.title_label)
        info.addWidget(self.summary_label, 1)
        layout.addLayout(info, 1)

        right = QVBoxLayout()
        right.setSpacing(3)
        right.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.count_label = QLabel(self)
        self.count_label.setObjectName("CardAuthor")
        self.count_label.setAlignment(Qt.AlignRight)
        self.date_label = QLabel(self)
        self.date_label.setObjectName("CardAuthor")
        self.date_label.setAlignment(Qt.AlignRight)
        right.addWidget(self.count_label)
        right.addWidget(self.date_label)
        layout.addLayout(right)

        self.set_meta(meta)

    def set_meta(self, meta: dict, cover_abs_path: str = "") -> None:
        self.book_id = str(meta.get("id") or self.book_id)
        title = str(meta.get("title") or "未命名")
        self.cover.set_data(
            title, cover_abs_path, int(meta.get("cover_index") or 0), _is_new(meta)
        )
        font = self.title_label.font()
        self.title_label.setText(_elide(title, 360, font))
        author = str(meta.get("author") or "").strip() or "佚名"
        last = str(meta.get("last_chapter_title") or "")
        summary = f"{author} · {last}" if last else author
        self.summary_label.setText(_elide(summary, 380, self.summary_label.font()))
        self.count_label.setText(
            f"{format_word_count(int(meta.get('word_count') or 0))} 字 · "
            f"{int(meta.get('chapter_count') or 0)} 章"
        )
        self.date_label.setText(_date_text(float(meta.get("updated_at") or 0)))

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.book_id)
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        self.open_requested.emit(self.book_id)
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        menu = QMenu(self)
        if self.in_trash:
            menu.addAction("恢复作品", lambda: self.restore_requested.emit(self.book_id))
            menu.addAction("彻底删除", lambda: self.delete_forever_requested.emit(self.book_id))
            menu.exec(event.globalPos())
            return
        menu.addAction("打开作品", lambda: self.open_requested.emit(self.book_id))
        menu.addAction("重命名", lambda: self.rename_requested.emit(self.book_id))
        menu.addAction("更换封面…", lambda: self.cover_change_requested.emit(self.book_id))
        menu.addAction("移动到分组…", lambda: self.move_group_requested.emit(self.book_id))
        menu.addSeparator()
        menu.addAction("导出 TXT…", lambda: self.export_requested.emit(self.book_id))
        menu.addSeparator()
        menu.addAction("移入回收站", lambda: self.trash_requested.emit(self.book_id))
        menu.exec(event.globalPos())


class ItemCard(CardFrame):
    """时间轴 / 设定的通用卡片：标题 + 日期 + 摘要。"""

    clicked = Signal(str)
    open_requested = Signal(str)
    rename_requested = Signal(str)
    delete_requested = Signal(str)

    def __init__(self, item_id: str, title: str, subtitle: str, accent: str, parent=None) -> None:
        super().__init__(parent)
        self.item_id = item_id
        self.setObjectName("ShelfCard")
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(CARD_W, 148)
        self.enable_lift(blur=16, alpha=24, dy=4)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 12)
        layout.setSpacing(6)

        self.strip = QFrame(self)
        self.strip.setFixedSize(30, 4)
        self.strip.setStyleSheet(f"background:{accent};border-radius:2px;")
        layout.addWidget(self.strip)

        self.title_label = QLabel(self)
        self.title_label.setObjectName("CardTitle")
        self.title_label.setWordWrap(True)
        layout.addWidget(self.title_label, 1)

        self.sub_label = QLabel(self)
        self.sub_label.setObjectName("CardSummary")
        self.sub_label.setWordWrap(True)
        layout.addWidget(self.sub_label)

        self.set_data(title, subtitle)

    def set_data(self, title: str, subtitle: str) -> None:
        font = self.title_label.font()
        self.title_label.setText(_elide(title, CARD_W - 28, font))
        self.title_label.setToolTip(title)
        self.sub_label.setText(_elide(subtitle, CARD_W - 28, self.sub_label.font()))

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.item_id)
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        self.open_requested.emit(self.item_id)
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        menu = QMenu(self)
        menu.addAction("打开", lambda: self.open_requested.emit(self.item_id))
        menu.addAction("重命名", lambda: self.rename_requested.emit(self.item_id))
        menu.addSeparator()
        menu.addAction("删除", lambda: self.delete_requested.emit(self.item_id))
        menu.exec(event.globalPos())


class AchievementCard(CardFrame):
    """成就页里的勋章卡片：勋章图 + 名称 + 称号 + 进度说明。

    右键已解锁的成就可以设为展示 / 卸下首页左下角的展示勋章。
    """

    showcase_requested = Signal(str)   # 携带成就 id；空串 = 卸下展示

    def __init__(self, key: str, info: dict, unlocked_ts: float,
                 showcase_key: str = "", parent=None) -> None:
        super().__init__(parent)
        self.ach_key = key
        self.unlocked = unlocked_ts > 0
        self.showcase_key = str(showcase_key or "")
        self.setObjectName("ShelfCard")
        self.setFixedSize(CARD_W, 268)
        self.enable_lift(blur=16, alpha=24, dy=4)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 12)
        layout.setSpacing(4)

        self.medal_label = QLabel(self)
        self.medal_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.medal_label)

        self.name_label = QLabel(self)
        self.name_label.setObjectName("CardTitle")
        self.name_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.name_label)

        self.sub_label = QLabel(self)
        self.sub_label.setObjectName("CardAuthor")
        self.sub_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.sub_label)

        self.desc_label = QLabel(self)
        self.desc_label.setObjectName("CardSummary")
        self.desc_label.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        self.desc_label.setWordWrap(True)
        layout.addWidget(self.desc_label, 1)

        from app.ui.medal_art import medal_pixmap

        unlocked = unlocked_ts > 0
        pix = medal_pixmap(key, 96)
        if not pix.isNull():
            if not unlocked:
                # 灰度化但保留透明通道（Format_Grayscale8 会丢 alpha，透明区变黑底）
                src = pix.toImage().convertToFormat(QImage.Format_ARGB32)
                gray = src.convertToFormat(QImage.Format_Grayscale8).convertToFormat(
                    QImage.Format_ARGB32
                )
                mask = src.convertToFormat(QImage.Format_Alpha8)
                from PySide6.QtGui import QPainter

                painter = QPainter(gray)
                painter.setCompositionMode(QPainter.CompositionMode_DestinationIn)
                painter.drawImage(0, 0, mask)
                painter.end()
                pix = QPixmap.fromImage(gray)
            self.medal_label.setPixmap(
                pix.scaled(84, 84, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )
        name = str(info.get("name") or "")
        sub = str(info.get("sub") or "")
        self.name_label.setText(_elide(name, CARD_W - 28, self.name_label.font()))
        self.sub_label.setText(_elide(sub, CARD_W - 28, self.sub_label.font()))
        if unlocked:
            self.desc_label.setText(
                f"{info.get('cond') or ''}\n解锁于 {_date_text(unlocked_ts)}"
            )
            if self.showcase_key == self.ach_key:
                tag = QLabel("展示中", self)
                tag.setObjectName("ShowcaseTag")
                tag.adjustSize()
                tag.move(self.width() - tag.width() - 12, 10)
        else:
            self.desc_label.setText(f"未解锁 · {info.get('cond') or ''}")

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        """右键已解锁成就：设为展示 / 卸下展示勋章。"""
        if not self.unlocked:
            return
        menu = QMenu(self)
        if self.showcase_key == self.ach_key:
            menu.addAction("卸下展示勋章", lambda: self.showcase_requested.emit(""))
        else:
            menu.addAction("设为展示勋章", lambda: self.showcase_requested.emit(self.ach_key))
        menu.exec(event.globalPos())


class ShelfNav(QFrame):
    """书架左侧导航：页面切换 + 分组 + 底部勋章展示与用户信息。"""

    page_changed = Signal(str)
    group_changed = Signal(str)
    new_book_requested = Signal()
    new_group_requested = Signal()
    delete_group_requested = Signal(str)   # 右键分组按钮 → 删除（组内作品回到未分组）
    profile_edit_requested = Signal()
    avatar_import_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("ShelfNav")
        self.setFixedWidth(196)

        # 常驻阴影（构造时挂一次，静态安全）：把导航栏与右侧内容区分开
        effect = QGraphicsDropShadowEffect(self)
        effect.setBlurRadius(32)
        effect.setOffset(6, 0)
        effect.setColor(QColor(24, 26, 32, 60))
        self.setGraphicsEffect(effect)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 18, 14, 14)
        layout.setSpacing(4)

        brand_box = QVBoxLayout()
        brand_box.setSpacing(1)
        brand = QLabel(config.APP_NAME, self)
        brand.setObjectName("ShelfBrand")
        brand_sub = QLabel(config.APP_NAME_EN.upper(), self)
        brand_sub.setObjectName("ShelfBrandSub")
        brand_box.addWidget(brand)
        brand_box.addWidget(brand_sub)
        layout.addLayout(brand_box)
        layout.addSpacing(12)

        self.page_buttons: dict[str, QPushButton] = {}

        def nav_button(key: str, text: str) -> QPushButton:
            button = QPushButton(text, self)
            button.setObjectName("NavButton")
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(lambda _c=False, k=key: self.set_page(k))
            layout.addWidget(button)
            self.page_buttons[key] = button
            return button

        nav_button("home", "首页")
        nav_button("timeline", "时间轴")
        nav_button("outline", "大纲")
        nav_button("notes", "设定")
        nav_button("map", "世界地图")
        nav_button("achievements", "成就")
        nav_button("stats", "统计")
        nav_button("trash", "回收站")

        layout.addSpacing(10)
        self.section = QLabel("分组", self)
        self.section.setObjectName("NavSection")
        layout.addWidget(self.section)

        self.group_buttons: dict[str, QPushButton] = {}
        self.all_button = QPushButton("全部作品", self)
        self.all_button.setObjectName("NavButton")
        self.all_button.setCheckable(True)
        self.all_button.setCursor(Qt.PointingHandCursor)
        self.all_button.clicked.connect(lambda: self.set_group(""))
        layout.addWidget(self.all_button)

        layout.addStretch(1)

        # 勋章展示位（在账户信息上方）：仅在成就页右键已解锁成就时更换
        self.medal_frame = QFrame(self)
        self.medal_frame.setObjectName("UserCard")
        medal_layout = QHBoxLayout(self.medal_frame)
        medal_layout.setContentsMargins(10, 10, 10, 10)
        medal_layout.setSpacing(10)
        self.medal_icon = QLabel(self.medal_frame)
        self.medal_icon.setFixedSize(64, 64)
        self.medal_icon.hide()
        medal_layout.addWidget(self.medal_icon)
        medal_name_box = QVBoxLayout()
        medal_name_box.setContentsMargins(0, 0, 0, 0)
        medal_name_box.setSpacing(2)
        self.medal_name = QLabel("尚未展示勋章", self.medal_frame)
        self.medal_name.setObjectName("UserHint")
        self.medal_name.setWordWrap(True)
        medal_font = self.medal_name.font()
        medal_font.setPixelSize(12)
        medal_font.setBold(True)
        self.medal_name.setFont(medal_font)
        medal_name_box.addWidget(self.medal_name, 1)
        medal_layout.addLayout(medal_name_box, 1)
        self.medal_frame.setToolTip("成就勋章展示位 · 在成就页右键已解锁成就可更换")
        layout.addWidget(self.medal_frame)

        self.user_card = QFrame(self)
        self.user_card.setObjectName("UserCard")
        user_layout = QHBoxLayout(self.user_card)
        user_layout.setContentsMargins(8, 8, 8, 8)
        user_layout.setSpacing(9)
        self.avatar = AvatarView(32, self.user_card)
        user_layout.addWidget(self.avatar)
        user_box = QVBoxLayout()
        user_box.setSpacing(1)
        self.user_name = QLabel("写作者", self.user_card)
        self.user_name.setObjectName("UserName")
        self.user_hint = QLabel("本地账户 · 点击修改", self.user_card)
        self.user_hint.setObjectName("UserHint")
        user_box.addWidget(self.user_name)
        user_box.addWidget(self.user_hint)
        user_layout.addLayout(user_box, 1)
        self.user_card.setCursor(Qt.PointingHandCursor)
        layout.addWidget(self.user_card)

        self.user_card.mouseReleaseEvent = lambda event: self.profile_edit_requested.emit()  # type: ignore
        # 头像本身可点击：直接选择图片导入，不必进昵称弹窗
        self.avatar.setCursor(Qt.PointingHandCursor)
        self.avatar.setToolTip("点击更换头像")
        self.avatar.mouseReleaseEvent = lambda event: (
            event.accept(),
            self.avatar_import_requested.emit(),
        )  # type: ignore

        self.set_page("home")

    # ------------------------------------------------------------------
    def set_page(self, key: str) -> None:
        for name, button in self.page_buttons.items():
            button.setChecked(name == key)
        self.page_changed.emit(key)

    def set_group(self, group_id: str) -> None:
        self.all_button.setChecked(group_id == "")
        for gid, button in self.group_buttons.items():
            button.setChecked(gid == group_id)
        self.group_changed.emit(group_id)

    def refresh_groups(self, groups: list[dict], current: str = "") -> None:
        """重建分组按钮列表。"""
        for button in self.group_buttons.values():
            button.deleteLater()
        self.group_buttons.clear()
        layout = self.layout()
        insert_at = layout.indexOf(self.all_button) + 1
        for group in groups:
            gid = str(group.get("id") or "")
            button = QPushButton(str(group.get("name") or "未命名分组"), self)
            button.setObjectName("NavButton")
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(lambda _c=False, g=gid: self.set_group(g))
            button.setContextMenuPolicy(Qt.CustomContextMenu)
            button.customContextMenuRequested.connect(
                lambda _pos, g=gid, b=button: self._show_group_menu(g, b)
            )
            layout.insertWidget(insert_at, button)
            insert_at += 1
            self.group_buttons[gid] = button
            button.setChecked(gid == current)

    def _show_group_menu(self, group_id: str, button: QPushButton) -> None:
        """分组按钮右键菜单：删除分组。"""
        menu = QMenu(button)
        name = button.text()
        menu.addAction(f"删除分组「{name}」", lambda: self.delete_group_requested.emit(group_id))
        menu.exec(QCursor.pos())

    def set_user(self, nickname: str, avatar_path: str = "") -> None:
        self.user_name.setText(nickname or "写作者")
        self.avatar.set_data(avatar_path, nickname or "写作者")

    # ------------------------------------------------------------------
    # 勋章展示位
    # ------------------------------------------------------------------
    def set_earned(self, unlocked: dict) -> None:
        """同步已解锁的成就（id -> 解锁时间戳）。"""
        self._unlocked = dict(unlocked or {})
        self._refresh_medal()

    def set_showcase(self, key: str) -> None:
        """设置当前展示的勋章（成就 id，空 = 不展示）。"""
        self._showcase = str(key or "")
        self._refresh_medal()

    def _refresh_medal(self) -> None:
        from app.services.achievements import ACHIEVEMENTS
        from app.ui.medal_art import medal_pixmap

        key = getattr(self, "_showcase", "")
        unlocked = getattr(self, "_unlocked", {})
        pix = None
        text = "尚未展示勋章"
        if key and key in unlocked:
            info = ACHIEVEMENTS.get(key)
            if info is not None:
                p = medal_pixmap(key, 96)
                if not p.isNull():
                    pix = p.scaled(64, 64, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                text = f"{info['name']} · {info['sub']}"
        if pix is None:
            self.medal_icon.hide()
        else:
            self.medal_icon.setPixmap(pix)
            self.medal_icon.show()
        self.medal_name.setText(text)


class BookshelfPage(QWidget):
    """书架页面整体（导航 + 内容）。"""

    open_book_requested = Signal(str)
    new_book_requested = Signal()
    rename_book_requested = Signal(str)
    cover_change_book_requested = Signal(str)
    trash_book_requested = Signal(str)
    restore_book_requested = Signal(str)
    empty_trash_requested = Signal()
    delete_forever_requested = Signal(str)
    delete_book_requested = Signal(str)
    new_group_requested = Signal()
    delete_group_requested = Signal(str)
    move_group_requested = Signal(str)
    view_changed = Signal(str)
    profile_edit_requested = Signal()
    avatar_import_requested = Signal()

    # 时间轴 / 设定 / 大纲
    open_timeline_requested = Signal(str)
    new_timeline_requested = Signal()
    rename_timeline_requested = Signal(str)
    delete_timeline_requested = Signal(str)
    open_note_requested = Signal(str)
    new_note_requested = Signal()
    rename_note_requested = Signal(str)
    delete_note_requested = Signal(str)
    open_outline_requested = Signal(str)
    new_outline_requested = Signal()
    rename_outline_requested = Signal(str)
    delete_outline_requested = Signal(str)

    # 世界地图（可创建多张）与书籍导出
    open_map_requested = Signal(str)
    new_map_requested = Signal()
    rename_map_requested = Signal(str)
    delete_map_requested = Signal(str)
    export_book_requested = Signal(str)

    # 导入（软件导出的同类型文件）
    import_book_requested = Signal()
    import_note_requested = Signal()
    import_timeline_requested = Signal()
    import_outline_requested = Signal()
    import_map_requested = Signal()

    # 书架主页「统计」页：打开完整统计窗口
    stats_open_requested = Signal()

    # 成就勋章展示（成就页右键已解锁成就触发；空串 = 卸下）
    medal_showcase_requested = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._page = "home"
        self._group = ""
        self._view = "grid"
        self._metas: list[dict] = []
        self._groups: list[dict] = []
        self._cover_paths: dict[str, str] = {}
        self._timelines: list[dict] = []
        self._notes: list[dict] = []
        self._outlines: list[dict] = []
        self._maps: list[dict] = []
        self._unlocked: dict[str, float] = {}
        self._showcase = ""

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.nav = ShelfNav(self)
        outer.addWidget(self.nav)

        right = QVBoxLayout()
        right.setContentsMargins(28, 22, 28, 16)
        right.setSpacing(14)

        header = QHBoxLayout()
        text_box = QVBoxLayout()
        text_box.setSpacing(4)
        self.title_label = QLabel("首页", self)
        self.title_label.setObjectName("ShelfTitle")
        self.subtitle_label = QLabel(self)
        self.subtitle_label.setObjectName("ShelfSubtitle")
        text_box.addWidget(self.title_label)
        text_box.addWidget(self.subtitle_label)
        header.addLayout(text_box)
        header.addStretch(1)

        self.grid_chip = QPushButton("网格", self)
        self.grid_chip.setObjectName("ViewChip")
        self.grid_chip.setCheckable(True)
        self.list_chip = QPushButton("列表", self)
        self.list_chip.setObjectName("ViewChip")
        self.list_chip.setCheckable(True)
        self.grid_chip.clicked.connect(lambda: self.set_view("grid"))
        self.list_chip.clicked.connect(lambda: self.set_view("list"))
        header.addWidget(self.grid_chip)
        header.addWidget(self.list_chip)
        header.addSpacing(8)

        self.empty_trash_button = QPushButton("清空回收站", self)
        self.empty_trash_button.setObjectName("DangerButton")
        self.empty_trash_button.setCursor(Qt.PointingHandCursor)
        self.empty_trash_button.clicked.connect(self.empty_trash_requested.emit)
        self.empty_trash_button.hide()
        header.addWidget(self.empty_trash_button)
        header.addSpacing(8)

        self.new_button = QPushButton("＋ 新建作品", self)
        self.new_button.setObjectName("Primary")
        self.new_button.setCursor(Qt.PointingHandCursor)
        self.new_button.clicked.connect(self._on_new_button)
        header.addWidget(self.new_button)
        right.addLayout(header)

        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # 书架内容区：同正文一样的隐形快速滚动滑块
        from app.ui.scroll_float import ScrollFlasher

        self._scroll_flasher = ScrollFlasher(self.scroll, Qt.Vertical, inset=10)
        self.content = QWidget(self.scroll)
        content_layout = QVBoxLayout(self.content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        # 网格视图容器
        self.grid_holder = QWidget(self.content)
        self.grid = QGridLayout(self.grid_holder)
        self.grid.setContentsMargins(2, 4, 10, 8)
        self.grid.setHorizontalSpacing(18)
        self.grid.setVerticalSpacing(18)
        self.grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        content_layout.addWidget(self.grid_holder)

        # 列表视图容器
        self.list_holder = QWidget(self.content)
        self.list_box = QVBoxLayout(self.list_holder)
        self.list_box.setContentsMargins(0, 4, 0, 8)
        self.list_box.setSpacing(8)
        self.list_box.setAlignment(Qt.AlignTop)
        content_layout.addWidget(self.list_holder)
        self.scroll.setWidget(self.content)
        right.addWidget(self.scroll, 1)

        self.empty_label = QLabel("这里空空如也\n右键空白处或点击右上角按钮开始创作", self)
        self.empty_label.setObjectName("ShelfSubtitle")
        self.empty_label.setAlignment(Qt.AlignCenter)
        right.addWidget(self.empty_label, 1)
        self.empty_label.hide()

        outer.addLayout(right, 1)

        self.nav.page_changed.connect(self._on_page_changed)
        self.nav.group_changed.connect(self._on_group_changed)
        self.nav.new_book_requested.connect(self.new_book_requested.emit)
        self.nav.new_group_requested.connect(self.new_group_requested.emit)
        self.nav.delete_group_requested.connect(self.delete_group_requested.emit)
        self.nav.profile_edit_requested.connect(self.profile_edit_requested.emit)
        self.nav.avatar_import_requested.connect(self.avatar_import_requested.emit)
        self.set_view("grid")

    # ------------------------------------------------------------------
    # 数据与渲染
    # ------------------------------------------------------------------
    def set_books(self, metas: list[dict], groups: list[dict], cover_paths: dict[str, str]) -> None:
        self._metas = metas
        self._groups = groups
        self._cover_paths = cover_paths
        self.nav.refresh_groups(groups)
        self._render()

    def set_timelines(self, items: list[dict]) -> None:
        self._timelines = items
        self._render()

    def set_notes(self, items: list[dict]) -> None:
        self._notes = items
        self._render()

    def set_outlines(self, items: list[dict]) -> None:
        self._outlines = items
        self._render()

    def set_maps(self, items: list[dict]) -> None:
        """同步世界地图列表（可创建多张）。"""
        self._maps = list(items or [])
        self._render()

    def set_achievements(self, unlocked: dict) -> None:
        self._unlocked = dict(unlocked or {})
        self.nav.set_earned(self._unlocked)
        self._render()

    def set_showcase(self, key: str) -> None:
        self._showcase = str(key or "")
        self.nav.set_showcase(key)
        if self._page == "achievements":
            self._render()   # 刷新成就卡的「展示中」标记

    def set_user(self, nickname: str, avatar_path: str = "") -> None:
        self.nav.set_user(nickname, avatar_path)

    def set_view(self, view: str) -> None:
        self._view = view if view in {"grid", "list"} else "grid"
        self.grid_chip.setChecked(self._view == "grid")
        self.list_chip.setChecked(self._view == "list")
        self.view_changed.emit(self._view)
        self._render()

    def _on_page_changed(self, page: str) -> None:
        self._page = page
        self._render()
        # 统计页不自动弹窗：由页面里的「打字统计」卡片左键打开完整统计

    def _on_group_changed(self, group_id: str) -> None:
        self._group = group_id
        self._render()

    def reset_group_if_deleted(self, group_id: str) -> None:
        """分组被删除后回到「全部作品」（当前正好选中该分组时）。"""
        if self._group == group_id:
            self.nav.set_group("")

    def _on_new_button(self) -> None:
        if self._page == "timeline":
            self.new_timeline_requested.emit()
        elif self._page == "notes":
            self.new_note_requested.emit()
        elif self._page == "outline":
            self.new_outline_requested.emit()
        elif self._page == "map":
            self.new_map_requested.emit()
        else:
            self.new_book_requested.emit()

    def _filtered(self) -> list[dict]:
        items = list(self._metas)
        if self._page == "trash":
            items = [m for m in items if m.get("trashed")]
        else:
            items = [m for m in items if not m.get("trashed")]
            if self._group:
                items = [m for m in items if m.get("group_id") == self._group]
            items = sorted(items, key=lambda m: float(m.get("created_at") or 0), reverse=True)
        return items

    def _render(self) -> None:
        # 标题与副标题
        titles = {
            "home": "首页",
            "timeline": "时间轴",
            "outline": "大纲",
            "notes": "设定",
            "map": "世界地图",
            "achievements": "成就",
            "stats": "统计",
            "trash": "回收站",
        }
        self.title_label.setText(titles.get(self._page, "首页"))
        all_count = len([m for m in self._metas if not m.get("trashed")])
        total_words = sum(int(m.get("word_count") or 0) for m in self._metas if not m.get("trashed"))
        if self._page == "trash":
            trash_count = len([m for m in self._metas if m.get("trashed")])
            self.subtitle_label.setText(
                f"回收站中的作品可以随时恢复 · 共 {trash_count} 本"
                if trash_count
                else "回收站是空的"
            )
            self.empty_trash_button.setVisible(trash_count > 0)
        elif self._page == "timeline":
            self.subtitle_label.setText(f"共 {len(self._timelines)} 条故事时间轴 · 点击卡片打开")
        elif self._page == "outline":
            self.subtitle_label.setText(f"共 {len(self._outlines)} 张大纲思维导图 · 点击卡片打开")
        elif self._page == "notes":
            self.subtitle_label.setText(f"共 {len(self._notes)} 篇设定文档 · 点击卡片打开")
        elif self._page == "map":
            count = len(self._maps)
            self.subtitle_label.setText(
                f"共 {count} 张世界地图 · 点击卡片打开绘制"
                if count
                else "还没有创建世界地图 · 点击右上角按钮新建"
            )
        elif self._page == "achievements":
            count = len(self._unlocked)
            self.subtitle_label.setText(
                f"已解锁 {count} / {len(ACHIEVEMENTS)} 枚勋章 · 继续加油"
            )
        elif self._page == "stats":
            self.subtitle_label.setText("累计打字与每日记录 · 点击卡片查看完整统计")
        elif self._group:
            name = next(
                (g["name"] for g in self._groups if g.get("id") == self._group), "分组"
            )
            self.subtitle_label.setText(f"分组「{name}」")
        else:
            self.subtitle_label.setText(
                f"共 {all_count} 本作品 · {format_word_count(total_words)} 字 · 数据保存在本机"
            )

        # 书籍页面才显示视图切换与「新建作品」按钮；回收站页显示清空按钮
        is_book_page = self._page in {"home", "trash", "discover"}
        self.grid_chip.setVisible(is_book_page)
        self.list_chip.setVisible(is_book_page)
        if self._page != "trash":
            self.empty_trash_button.hide()
        if self._page == "timeline":
            self.new_button.setText("＋ 新建时间轴")
        elif self._page == "notes":
            self.new_button.setText("＋ 新建设定")
        elif self._page == "outline":
            self.new_button.setText("＋ 新建大纲")
        elif self._page == "map":
            self.new_button.setText("＋ 新建世界地图")
        else:
            self.new_button.setText("＋ 新建作品")
        self.new_button.setVisible(self._page not in ("achievements", "stats"))

        # 清空旧卡片（立即隐藏避免删除延迟期间的残影）
        for layout in (self.grid, self.list_box):
            while layout.count():
                item = layout.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.hide()
                    widget.deleteLater()

        if self._page == "timeline":
            self._render_timelines()
            return
        if self._page == "outline":
            self._render_outlines()
            return
        if self._page == "notes":
            self._render_notes()
            return
        if self._page == "map":
            self._render_map()
            return
        if self._page == "achievements":
            self._render_achievements()
            return
        if self._page == "stats":
            self._render_stats()
            return

        items = self._filtered()
        if not items:
            self.empty_label.setText(
                "这里空空如也\n右键空白处或点击右上角「＋ 新建作品」，开始你的第一本书"
            )
            self.scroll.hide()
            self.empty_label.show()
            return
        self.empty_label.hide()
        self.scroll.show()

        self.grid_holder.setVisible(self._view == "grid")
        self.list_holder.setVisible(self._view == "list")
        if self._view == "grid":
            self._fill_grid(items)
        else:
            self._fill_list(items)

    def _cover_path_for(self, meta: dict) -> str:
        return self._cover_paths.get(str(meta.get("id") or ""), "")

    def _columns(self) -> int:
        """根据当前可视宽度自适应列数（大卡片通常 4~5 列）。"""
        try:
            width = self.scroll.viewport().width()
        except RuntimeError:
            width = 1000
        return max(3, (width - 20) // (CARD_W + 18))

    def _fill_grid(self, items: list[dict]) -> None:
        columns = self._columns()
        in_trash = self._page == "trash"
        for index, meta in enumerate(items):
            card = BookCard(meta, self.grid_holder)
            card.set_meta(meta, self._cover_path_for(meta))
            card.in_trash = in_trash
            self._connect_card(card)
            row, col = divmod(index, columns)
            self.grid.addWidget(card, row, col)
        for col in range(columns):
            self.grid.setColumnStretch(col, 1)
        rows = (len(items) - 1) // columns + 1
        self.grid.setRowStretch(rows, 1)

    def _fill_list(self, items: list[dict]) -> None:
        in_trash = self._page == "trash"
        for meta in items:
            row = BookRow(meta, self.list_holder)
            row.set_meta(meta, self._cover_path_for(meta))
            row.in_trash = in_trash
            self._connect_card(row)
            self.list_box.addWidget(row)

    def _fill_item_grid(self, cards: list[ItemCard]) -> None:
        self.empty_label.hide()
        self.scroll.show()
        self.grid_holder.setVisible(True)
        self.list_holder.setVisible(False)
        columns = self._columns()
        for index, card in enumerate(cards):
            row, col = divmod(index, columns)
            self.grid.addWidget(card, row, col)
        for col in range(columns):
            self.grid.setColumnStretch(col, 1)
        rows = (len(cards) - 1) // columns + 1 if cards else 0
        if rows:
            self.grid.setRowStretch(rows, 1)

    def _render_timelines(self) -> None:
        if not self._timelines:
            self.scroll.hide()
            self.empty_label.setText("还没有时间轴\n右键空白处选择「新建时间轴」")
            self.empty_label.show()
            return
        cards: list[ItemCard] = []
        for item in sorted(
            self._timelines, key=lambda t: float(t.get("updated_at") or 0), reverse=True
        ):
            count = len(item.get("events") or [])
            card = ItemCard(
                str(item.get("id")),
                str(item.get("title") or "未命名时间轴"),
                f"{count} 个事件 · 更新于 {_date_text(float(item.get('updated_at') or 0))}",
                "#17B26A",
                self.grid_holder,
            )
            card.clicked.connect(self.open_timeline_requested.emit)
            card.open_requested.connect(self.open_timeline_requested.emit)
            card.rename_requested.connect(self.rename_timeline_requested.emit)
            card.delete_requested.connect(self.delete_timeline_requested.emit)
            cards.append(card)
        self._fill_item_grid(cards)

    def _render_outlines(self) -> None:
        if not self._outlines:
            self.scroll.hide()
            self.empty_label.setText("还没有大纲\n右键空白处选择「新建大纲」")
            self.empty_label.show()
            return
        cards: list[ItemCard] = []
        for item in sorted(
            self._outlines, key=lambda o: float(o.get("updated_at") or 0), reverse=True
        ):
            count = len(item.get("nodes") or [])
            card = ItemCard(
                str(item.get("id")),
                str(item.get("title") or "未命名大纲"),
                f"{count} 个节点 · 更新于 {_date_text(float(item.get('updated_at') or 0))}",
                "#9A6BF0",
                self.grid_holder,
            )
            card.clicked.connect(self.open_outline_requested.emit)
            card.open_requested.connect(self.open_outline_requested.emit)
            card.rename_requested.connect(self.rename_outline_requested.emit)
            card.delete_requested.connect(self.delete_outline_requested.emit)
            cards.append(card)
        self._fill_item_grid(cards)

    def _render_map(self) -> None:
        """世界地图页：多张地图卡片，可新建 / 打开 / 重命名 / 删除。"""
        if not self._maps:
            self.scroll.hide()
            self.empty_label.setText("还没有世界地图\n点击右上角「＋ 新建世界地图」开始绘制")
            self.empty_label.show()
            return
        cards: list[ItemCard] = []
        for item in sorted(
            self._maps, key=lambda m: float(m.get("updated_at") or 0), reverse=True
        ):
            factions = len(item.get("factions") or [])
            pins = len(item.get("pins") or [])
            card = ItemCard(
                str(item.get("id")),
                str(item.get("title") or "未命名地图"),
                f"{factions} 个势力 · {pins} 个点位 · 更新于 {_date_text(float(item.get('updated_at') or 0))}",
                "#E0A23F",
                self.grid_holder,
            )
            card.clicked.connect(self.open_map_requested.emit)
            card.open_requested.connect(self.open_map_requested.emit)
            card.rename_requested.connect(self.rename_map_requested.emit)
            card.delete_requested.connect(self.delete_map_requested.emit)
            cards.append(card)
        self._fill_item_grid(cards)

    def _render_stats(self) -> None:
        """书架主页「统计」页：一张入口卡片，点开完整统计窗口。"""
        card = ItemCard(
            "stats",
            "打字统计",
            "累计字数 · 每日日历 · 点击查看",
            "#21A575",
            self.grid_holder,
        )
        # clicked/open_requested 带 item_id 参数，stats_open_requested 无参——
        # 必须经 lambda 转发（直连会在真实点击时抛 TypeError 导致卡片点不开统计）
        card.clicked.connect(lambda _item_id="": self.stats_open_requested.emit())
        card.open_requested.connect(lambda _item_id="": self.stats_open_requested.emit())

        def _menu(event) -> None:
            menu = QMenu(card)
            menu.addAction("打开完整统计", self.stats_open_requested.emit)
            menu.exec(event.globalPos())

        card.contextMenuEvent = _menu  # type: ignore
        self._fill_item_grid([card])

    def _render_achievements(self) -> None:
        cards = [
            AchievementCard(
                key, info, float(self._unlocked.get(key) or 0), self._showcase,
                self.grid_holder,
            )
            for key, info in ACHIEVEMENTS.items()
        ]
        for card in cards:
            card.showcase_requested.connect(self.medal_showcase_requested.emit)
        self._fill_item_grid(cards)

    def _render_notes(self) -> None:
        if not self._notes:
            self.scroll.hide()
            self.empty_label.setText("还没有设定文档\n右键空白处选择「新建设定」")
            self.empty_label.show()
            return
        cards: list[ItemCard] = []
        for item in sorted(
            self._notes, key=lambda n: float(n.get("updated_at") or 0), reverse=True
        ):
            content = " ".join(str(item.get("content") or "").split())[:40]
            subtitle = content or f"更新于 {_date_text(float(item.get('updated_at') or 0))}"
            card = ItemCard(
                str(item.get("id")),
                str(item.get("title") or "未命名设定"),
                subtitle,
                "#5B8DEF",
                self.grid_holder,
            )
            card.clicked.connect(self.open_note_requested.emit)
            card.open_requested.connect(self.open_note_requested.emit)
            card.rename_requested.connect(self.rename_note_requested.emit)
            card.delete_requested.connect(self.delete_note_requested.emit)
            cards.append(card)
        self._fill_item_grid(cards)

    def _connect_card(self, card) -> None:
        card.open_requested.connect(self.open_book_requested.emit)
        card.clicked.connect(self.open_book_requested.emit)
        card.rename_requested.connect(self.rename_book_requested.emit)
        card.cover_change_requested.connect(self.cover_change_book_requested.emit)
        card.trash_requested.connect(self.trash_book_requested.emit)
        card.move_group_requested.connect(self.move_group_requested.emit)
        card.restore_requested.connect(self.restore_book_requested.emit)
        card.delete_forever_requested.connect(self.delete_forever_requested.emit)
        card.export_requested.connect(self.export_book_requested.emit)

    # ------------------------------------------------------------------
    # 右键菜单
    # ------------------------------------------------------------------
    def contextMenuEvent(self, event) -> None:  # noqa: N802
        menu = QMenu(self)
        menu.addAction("新建作品", self.new_book_requested.emit)
        menu.addAction("新建设定", self.new_note_requested.emit)
        menu.addAction("新建时间轴", self.new_timeline_requested.emit)
        menu.addAction("新建大纲", self.new_outline_requested.emit)
        menu.addAction("新建世界地图", self.new_map_requested.emit)
        menu.addSeparator()
        import_menu = menu.addMenu("导入文件…")
        import_menu.addAction("导入小说 TXT…", self.import_book_requested.emit)
        import_menu.addAction("导入设定 TXT…", self.import_note_requested.emit)
        import_menu.addAction("导入时间轴 JSON…", self.import_timeline_requested.emit)
        import_menu.addAction("导入大纲 JSON…", self.import_outline_requested.emit)
        import_menu.addAction("导入地图底图…", self.import_map_requested.emit)
        menu.addSeparator()
        menu.addAction("新建分组", self.new_group_requested.emit)
        if self._page in {"home", "trash"}:
            menu.addSeparator()
            grid_action = menu.addAction("网格视图", lambda: self.set_view("grid"))
            list_action = menu.addAction("列表视图", lambda: self.set_view("list"))
            grid_action.setCheckable(True)
            list_action.setCheckable(True)
            grid_action.setChecked(self._view == "grid")
            list_action.setChecked(self._view == "list")
        menu.exec(event.globalPos())
