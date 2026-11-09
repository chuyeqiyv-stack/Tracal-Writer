# -*- coding: utf-8 -*-
"""书架主页：左侧导航（首页 / 时间轴 / 大纲 / 设定 / 地图 / 成就 / 回收站）+ 右侧内容区。

- 书籍：大尺寸 3:4 封面卡片（网格）与列表行，带新作品角标与悬浮阴影；
- 时间轴 / 设定 / 大纲 / 地图：独立卡片，可创建 / 打开；
- 成就页：展示已解锁 / 未解锁的勋章；
- 右键菜单按当前页面与落点适配：空白处只列本页能力，卡片上只列该条目操作，
  本页没有的能力整项不出现（见 context_menu.make_menu）。
"""
from __future__ import annotations

import time

from app import config

from PySide6.QtCore import Qt, Signal, QSize, QTimer
from PySide6.QtGui import QColor, QCursor, QFontMetrics
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.services.achievements import ACHIEVEMENTS
from app.services.word_count import format_word_count
from app.ui.context_menu import make_menu, popup, submenu
from app.ui.widgets import AvatarView, CardFrame, CoverView, GlassButton

# 大封面卡片尺寸（封面满铺卡片宽度，3:4 书籍比例）
CARD_W = 204
COVER_W = 204
COVER_H = 264
CARD_H = 336


def _elide(text: str, width: int, font) -> str:
    return QFontMetrics(font).elidedText(text, Qt.ElideRight, width)


def _date_text(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(float(ts or 0)))


def _time_text(ts: float) -> str:
    """修改时间（没有时间戳时返回空串，列表视图据此隐藏该列）。"""
    if not ts:
        return ""
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(float(ts)))


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
    export_chapters_requested = Signal(str)

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
        if self.in_trash:
            entries = [
                ("恢复本书", lambda: self.restore_requested.emit(self.book_id)),
                ("彻底删除本书", lambda: self.delete_forever_requested.emit(self.book_id)),
            ]
        else:
            entries = [
                ("打开本书", lambda: self.open_requested.emit(self.book_id)),
                ("重命名本书", lambda: self.rename_requested.emit(self.book_id)),
                ("更换本书封面…", lambda: self.cover_change_requested.emit(self.book_id)),
                ("移动本书到分组…", lambda: self.move_group_requested.emit(self.book_id)),
                None,
                ("导出本书（单文件 TXT）…", lambda: self.export_requested.emit(self.book_id)),
                ("导出整本书（一章一文件）…",
                 lambda: self.export_chapters_requested.emit(self.book_id)),
                None,
                ("删除本书（移入回收站）", lambda: self.trash_requested.emit(self.book_id)),
            ]
        popup(make_menu(self, entries), event.globalPos())


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
    export_chapters_requested = Signal(str)

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
        if self.in_trash:
            entries = [
                ("恢复本书", lambda: self.restore_requested.emit(self.book_id)),
                ("彻底删除本书", lambda: self.delete_forever_requested.emit(self.book_id)),
            ]
        else:
            entries = [
                ("打开本书", lambda: self.open_requested.emit(self.book_id)),
                ("重命名本书", lambda: self.rename_requested.emit(self.book_id)),
                ("更换本书封面…", lambda: self.cover_change_requested.emit(self.book_id)),
                ("移动本书到分组…", lambda: self.move_group_requested.emit(self.book_id)),
                None,
                ("导出本书（单文件 TXT）…", lambda: self.export_requested.emit(self.book_id)),
                ("导出整本书（一章一文件）…",
                 lambda: self.export_chapters_requested.emit(self.book_id)),
                None,
                ("删除本书（移入回收站）", lambda: self.trash_requested.emit(self.book_id)),
            ]
        popup(make_menu(self, entries), event.globalPos())


class ItemCard(CardFrame):
    """时间轴 / 设定的通用卡片：标题 + 日期 + 摘要。"""

    clicked = Signal(str)
    open_requested = Signal(str)
    rename_requested = Signal(str)
    delete_requested = Signal(str)

    def __init__(self, item_id: str, title: str, subtitle: str, accent: str,
                 parent=None, kind: str = "条目") -> None:
        super().__init__(parent)
        self.item_id = item_id
        self.kind = kind
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
        self._title_text = title
        self._sub_text = subtitle
        self.title_label.setToolTip(title)
        self._refresh_elide()

    def _refresh_elide(self) -> None:
        """摘要按卡片实际宽度截断（列数变化 / 缩放变化后都重新计算）。"""
        width = max(60, (self.width() or CARD_W) - 28)
        self.title_label.setText(_elide(getattr(self, "_title_text", ""), width,
                                        self.title_label.font()))
        self.sub_label.setText(_elide(getattr(self, "_sub_text", ""), width,
                                      self.sub_label.font()))

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if hasattr(self, "_title_text"):
            self._refresh_elide()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.item_id)
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        self.open_requested.emit(self.item_id)
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        kind = self.kind
        entries = [(f"打开{kind}", lambda: self.open_requested.emit(self.item_id))]
        if getattr(self, "can_rename", True):
            entries.append((f"重命名{kind}", lambda: self.rename_requested.emit(self.item_id)))
        if getattr(self, "can_delete", True):
            entries.append(None)
            entries.append((f"删除{kind}", lambda: self.delete_requested.emit(self.item_id)))
        popup(make_menu(self, entries), event.globalPos())


class NoteRow(CardFrame):
    """设定页列表视图的一行：标题 / 摘要 / 修改时间。"""

    clicked = Signal(str)
    open_requested = Signal(str)
    rename_requested = Signal(str)
    delete_requested = Signal(str)

    def __init__(self, item_id: str, title: str, summary: str, updated: str,
                 accent: str, parent=None, kind: str = "条目") -> None:
        super().__init__(parent)
        self.item_id = item_id
        self.kind = kind
        self.setObjectName("ShelfCard")
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(58)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._title_text = title
        self._sub_text = summary

        row = QHBoxLayout(self)
        row.setContentsMargins(16, 10, 16, 10)
        row.setSpacing(14)

        self.strip = QFrame(self)
        self.strip.setFixedSize(4, 24)
        self.strip.setStyleSheet(f"background:{accent};border-radius:2px;")
        row.addWidget(self.strip)

        self.title_label = QLabel(self)
        self.title_label.setObjectName("CardTitle")
        self.title_label.setFixedWidth(180)
        row.addWidget(self.title_label)

        self.sub_label = QLabel(self)
        self.sub_label.setObjectName("CardSummary")
        self.sub_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        row.addWidget(self.sub_label, 1)

        self.time_label = QLabel(updated, self)
        self.time_label.setObjectName("CardSummary")
        self.time_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.time_label.setFixedWidth(124)
        self.time_label.setVisible(bool(updated))
        row.addWidget(self.time_label)

        self._refresh_elide()

    def set_data(self, title: str, summary: str, updated: str) -> None:
        """就地更新一行（保存设定后只改这一条，不重建整页）。"""
        self._title_text = title
        self._sub_text = summary
        self.time_label.setText(updated)
        self.time_label.setVisible(bool(updated))
        self._refresh_elide()

    def _refresh_elide(self) -> None:
        tw = self.title_label.width() or 180
        self.title_label.setText(_elide(self._title_text, max(60, tw - 4),
                                       self.title_label.font()))
        self.title_label.setToolTip(self._title_text)
        sw = self.sub_label.width() or 400
        self.sub_label.setText(_elide(self._sub_text, max(60, sw - 8),
                                      self.sub_label.font()))
        self.sub_label.setToolTip(self._sub_text)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._refresh_elide()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.item_id)
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        self.open_requested.emit(self.item_id)
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        kind = self.kind
        entries = [(f"打开{kind}", lambda: self.open_requested.emit(self.item_id))]
        if getattr(self, "can_rename", True):
            entries.append((f"重命名{kind}", lambda: self.rename_requested.emit(self.item_id)))
        if getattr(self, "can_delete", True):
            entries.append(None)
            entries.append((f"删除{kind}", lambda: self.delete_requested.emit(self.item_id)))
        popup(make_menu(self, entries), event.globalPos())


class AchievementCard(CardFrame):
    """成就页里的勋章卡片：勋章图 + 名称 + 称号 + 进度说明。

    右键已解锁的成就可以设为展示 / 卸下首页左下角的展示勋章。
    """

    showcase_requested = Signal(str)   # 携带成就 id；空串 = 卸下展示

    def __init__(self, key: str, info: dict, unlocked_ts: float,
                 showcase_key: str = "", parent=None) -> None:
        super().__init__(parent)
        self.ach_key = key
        self.info = dict(info or {})
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

        # 灰度与缩放结果均在 medal_pixmap 内按 (key,size,gray) 缓存，重建页面零重算
        pix = medal_pixmap(key, 84, gray=not self.unlocked)
        if not pix.isNull():
            self.medal_label.setPixmap(pix)
        name = str(info.get("name") or "")
        sub = str(info.get("sub") or "")
        self.name_label.setText(_elide(name, CARD_W - 28, self.name_label.font()))
        self.sub_label.setText(_elide(sub, CARD_W - 28, self.sub_label.font()))
        if self.unlocked:
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

    def _show_details(self) -> None:
        from app.ui.dialogs import MessageBox

        info = self.info
        state = "已解锁" if self.unlocked else "未解锁"
        MessageBox.information(
            self,
            f"成就 · {info.get('name') or self.ach_key}",
            f"{info.get('sub') or ''}\n\n条件：{info.get('cond') or ''}\n状态：{state}",
        )

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        """右键成就：查看详情 + 已解锁时可设为展示 / 卸下展示勋章。"""
        entries = [("查看成就详情", self._show_details)]
        if self.unlocked:
            entries.append(None)
            if self.showcase_key == self.ach_key:
                entries.append(("卸下展示勋章",
                                lambda: self.showcase_requested.emit("")))
            else:
                entries.append(("设为展示勋章",
                                lambda: self.showcase_requested.emit(self.ach_key)))
        popup(make_menu(self, entries), event.globalPos())


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
        # 侧边栏导航图标（全站统一图标集）
        self._nav_icons = {
            "home": "home", "timeline": "timeline", "outline": "mind",
            "notes": "chapter", "characters": "user", "map": "map",
            "achievements": "medal", "stats": "count", "trash": "trash",
        }
        self._icon_factory = None

        def nav_button(key: str, text: str) -> QPushButton:
            button = QPushButton(text, self)
            button.setObjectName("NavButton")
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setIconSize(QSize(16, 16))
            button.clicked.connect(lambda _c=False, k=key: self.set_page(k))
            layout.addWidget(button)
            self.page_buttons[key] = button
            return button

        nav_button("home", "首页")
        nav_button("timeline", "时间轴")
        nav_button("outline", "大纲")
        nav_button("notes", "设定")
        nav_button("characters", "角色")
        nav_button("map", "地图")
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
    def refresh_icons(self, factory) -> None:
        """主题切换时重建导航 / 分组图标；选中页用 accent 单色帧。"""
        self._icon_factory = factory
        current = self._page
        for key, button in self.page_buttons.items():
            icon_name = self._nav_icons.get(key)
            if icon_name:
                button.setIcon(factory.icon(icon_name, 16))
        self.all_button.setIconSize(QSize(16, 16))
        self.all_button.setIcon(factory.icon("shelf", 16))
        for button in self.group_buttons.values():
            button.setIconSize(QSize(16, 16))
            button.setIcon(factory.icon("folder", 16))
        self.set_page(current)

    def _apply_page_icon(self, key: str) -> None:
        """选中页图标切 accent 帧（QSS 不负责换 QIcon 颜色）。"""
        if self._icon_factory is None:
            return
        for name, button in self.page_buttons.items():
            icon_name = self._nav_icons.get(name)
            if not icon_name:
                continue
            from PySide6.QtGui import QIcon as _QI

            frame = self._icon_factory.pixmap(
                icon_name, 16, state=_QI.On if name == key else _QI.Off,
                dpr=button.screen().devicePixelRatio() if button.screen() else 1.0,
            )
            button.setIcon(_QI(frame))

    def set_page(self, key: str) -> None:
        self._page = key
        for name, button in self.page_buttons.items():
            button.setChecked(name == key)
        self._apply_page_icon(key)
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
            button.setIconSize(QSize(16, 16))
            if self._icon_factory is not None:
                button.setIcon(self._icon_factory.icon("folder", 16))
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
    notes_view_changed = Signal(str)
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

    # 角色卡与角色关系
    new_character_requested = Signal()
    character_view_changed = Signal(str)
    characters_changed = Signal()
    import_characters_requested = Signal()

    # 地图（可创建多张）与书籍导出
    open_map_requested = Signal(str)
    new_map_requested = Signal()
    rename_map_requested = Signal(str)
    delete_map_requested = Signal(str)
    export_book_requested = Signal(str)
    export_book_chapters_requested = Signal(str)

    # 导入（软件导出的同类型文件）
    import_book_requested = Signal()
    import_note_requested = Signal()
    import_timeline_requested = Signal()
    import_outline_requested = Signal()
    import_map_requested = Signal()

    # 书架主页「统计」页：打开完整统计窗口
    stats_open_requested = Signal()
    # 书架刷新（重新读取本机书目后重建页面）
    refresh_requested = Signal()

    # 成就勋章展示（成就页右键已解锁成就触发；空串 = 卸下）
    medal_showcase_requested = Signal(str)

    def __init__(self, parent=None, storage=None) -> None:
        super().__init__(parent)
        self._page = "home"
        self._group = ""
        self._view = "grid"
        self._notes_view = "grid"
        self._notes_query = ""
        self._sort_mode = "created"
        self._ach_filter = "all"
        self._notes_timer = QTimer(self)
        self._notes_timer.setSingleShot(True)
        self._notes_timer.setInterval(180)
        self._notes_timer.timeout.connect(self._render)
        self._grid_cards: list = []
        self._grid_cols = 0
        self._grid_rows = 0
        self._reflow_timer = QTimer(self)
        self._reflow_timer.setSingleShot(True)
        self._reflow_timer.setInterval(120)
        self._reflow_timer.timeout.connect(self._reflow_grid)
        self._pending_scroll_frac: float | None = None
        self._batch_depth = 0
        self._batch_dirty = False
        self._selected_note_id = ""
        self._new_note_id = ""
        self._metas: list[dict] = []
        self._groups: list[dict] = []
        self._cover_paths: dict[str, str] = {}
        self._timelines: list[dict] = []
        self._notes: list[dict] = []
        self._outlines: list[dict] = []
        self._maps: list[dict] = []
        self._characters: list[dict] = []
        self._character_relations: list[dict] = []
        self._theme: dict = {}
        self._storage = storage
        self._characters_panel = None
        self._char_view = "list"
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

        self.notes_search = QLineEdit(self)
        self.notes_search.setObjectName("ShelfSearch")
        self.notes_search.setPlaceholderText("搜索设定标题 / 内容")
        self.notes_search.setClearButtonEnabled(True)
        self.notes_search.setFixedWidth(196)
        self.notes_search.hide()
        self.notes_search.textChanged.connect(self._on_notes_search)
        header.addWidget(self.notes_search)
        header.addSpacing(8)

        self.grid_chip = QPushButton("网格", self)
        self.grid_chip.setObjectName("ViewChip")
        self.grid_chip.setCheckable(True)
        self.list_chip = QPushButton("列表", self)
        self.list_chip.setObjectName("ViewChip")
        self.list_chip.setCheckable(True)
        self.grid_chip.clicked.connect(lambda: self._on_view_chip("grid"))
        self.list_chip.clicked.connect(lambda: self._on_view_chip("list"))
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

        self.new_button = GlassButton("新建作品", self)
        self.new_button.setObjectName("GlassPrimary")
        self.new_button.setIconSize(QSize(16, 16))
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
        # 角色面板宿主（与 scroll / empty_label 同级，避免嵌进滚动区）
        self._content_host = right

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

    def begin_batch(self) -> None:
        """开始批量刷新：期间的 set_* 只记脏、不重建页面。"""
        self._batch_depth += 1

    def end_batch(self) -> None:
        """结束批量：计数归零后统一重建一次（调用方需 try/finally 保证执行）。"""
        if self._batch_depth > 0:
            self._batch_depth -= 1
        if self._batch_depth == 0 and self._batch_dirty:
            self._batch_dirty = False
            self._render()

    def set_timelines(self, items: list[dict]) -> None:
        self._timelines = items
        self._render()

    def set_notes(self, items: list[dict]) -> None:
        old_ids = {str(n.get("id")) for n in self._notes}
        self._notes = list(items or [])
        new_ids = [str(n.get("id")) for n in self._notes if str(n.get("id")) not in old_ids]
        if self._page == "notes" and len(new_ids) == 1:
            # 新建后自动定位到新条目（新条目按更新时间排在最前）
            self._new_note_id = new_ids[0]
            self._selected_note_id = self._new_note_id
            self._pending_scroll_frac = 0.0
        self._render()

    def set_outlines(self, items: list[dict]) -> None:
        self._outlines = items
        self._render()

    def set_maps(self, items: list[dict]) -> None:
        """同步地图列表（可创建多张）。"""
        self._maps = list(items or [])
        self._render()

    def set_characters(self, characters: list[dict], relations: list[dict],
                       theme: dict, view: str = "") -> None:
        """同步角色与关系数据（theme 供面板渲染取色，view 为列表/关系图偏好）。"""
        if view in ("list", "board"):
            self._char_view = view
        # 关系图拖拽期间只改坐标、数量不变，跳过整页 _render（面板内部已自行刷新）
        sig = (len(characters or []), len(relations or []))
        unchanged = sig == (len(self._characters), len(self._character_relations))
        self._characters = list(characters or [])
        self._character_relations = list(relations or [])
        self._theme = dict(theme or {})
        if self._page == "characters" and not unchanged:
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
        self._sync_view_chips()
        self.view_changed.emit(self._view)
        self._render()

    # ------------------------------------------------------------------
    # 设定页：卡片 / 列表视图切换
    # ------------------------------------------------------------------
    def set_notes_view(self, view: str) -> None:
        """切换设定页视图；记住偏好并保住当前滚动位置与选中项。"""
        view = view if view in {"grid", "list"} else "grid"
        if view == self._notes_view:
            self._sync_view_chips()
            return
        sb = self.scroll.verticalScrollBar()
        self._pending_scroll_frac = sb.value() / sb.maximum() if sb.maximum() else 0.0
        self._notes_view = view
        self._sync_view_chips()
        if self._page == "notes":
            self._render()
        self.notes_view_changed.emit(view)

    def _on_view_chip(self, view: str) -> None:
        if self._page == "notes":
            self.set_notes_view(view)
        else:
            self.set_view(view)

    def _sync_view_chips(self) -> None:
        current = self._notes_view if self._page == "notes" else self._view
        self.grid_chip.setChecked(current == "grid")
        self.list_chip.setChecked(current == "list")

    def _on_notes_search(self, text: str) -> None:
        self._notes_query = str(text or "")
        self._notes_timer.start()

    def _on_page_changed(self, page: str) -> None:
        self._page = page
        self._pending_scroll_frac = 0.0      # 换页回顶部
        self._selected_note_id = ""
        self._new_note_id = ""
        self._render()
        # 统计页不自动弹窗：由页面里的「打字统计」卡片左键打开完整统计

    def _on_group_changed(self, group_id: str) -> None:
        self._group = group_id
        self._render()

    def reset_group_if_deleted(self, group_id: str) -> None:
        """分组被删除后回到「全部作品」（当前正好选中该分组时）。"""
        if self._group == group_id:
            self.nav.set_group("")

    def refresh_icons(self, factory) -> None:
        """CTA 主按钮：on_accent 色的 + 图标（GlassPrimary 是 accent 底）。"""
        self.new_button.setIcon(factory.icon("add", 16))

    def _on_new_button(self) -> None:
        if self._page == "timeline":
            self.new_timeline_requested.emit()
        elif self._page == "notes":
            self.new_note_requested.emit()
        elif self._page == "outline":
            self.new_outline_requested.emit()
        elif self._page == "characters":
            self.new_character_requested.emit()
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
            if self._sort_mode == "updated":
                items.sort(key=lambda m: float(m.get("updated_at") or 0), reverse=True)
            elif self._sort_mode == "title":
                items.sort(key=lambda m: str(m.get("title") or ""))
            else:
                items.sort(key=lambda m: float(m.get("created_at") or 0), reverse=True)
        return items

    def _render(self) -> None:
        if self._batch_depth > 0:
            self._batch_dirty = True
            return
        # 离开角色页时隐藏角色面板
        if self._characters_panel is not None and self._page != "characters":
            self._characters_panel.hide()
        # 标题与副标题
        titles = {
            "home": "首页",
            "timeline": "时间轴",
            "outline": "大纲",
            "notes": "设定",
            "characters": "角色",
            "map": "地图",
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
            if self._notes_query.strip():
                shown = len(self._notes_for_view())
                self.subtitle_label.setText(
                    f"共 {len(self._notes)} 篇设定文档 · 命中 {shown} 条"
                )
            else:
                self.subtitle_label.setText(f"共 {len(self._notes)} 篇设定文档 · 点击卡片打开")
        elif self._page == "characters":
            self.subtitle_label.setText(
                f"共 {len(self._characters)} 名角色 · {len(self._character_relations)} 条关系 · 顶部可切换「关系图」视图"
            )
        elif self._page == "map":
            count = len(self._maps)
            self.subtitle_label.setText(
                f"共 {count} 张地图 · 点击卡片打开绘制"
                if count
                else "还没有创建地图 · 点击右上角按钮新建"
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

        # 书籍页面与设定页都显示视图切换；设定页额外显示搜索框
        is_book_page = self._page in {"home", "trash", "discover"}
        is_notes_page = self._page == "notes"
        self.grid_chip.setVisible(is_book_page or is_notes_page)
        self.list_chip.setVisible(is_book_page or is_notes_page)
        self.notes_search.setVisible(is_notes_page)
        self._sync_view_chips()
        if self._page != "trash":
            self.empty_trash_button.hide()
        if self._page == "timeline":
            self.new_button.setText("新建时间轴")
        elif self._page == "notes":
            self.new_button.setText("新建设定")
        elif self._page == "outline":
            self.new_button.setText("新建大纲")
        elif self._page == "characters":
            self.new_button.setText("新建角色")
        elif self._page == "map":
            self.new_button.setText("新建地图")
        else:
            self.new_button.setText("新建作品")
        self.new_button.setVisible(self._page not in ("achievements", "stats"))

        # 清空旧卡片（立即隐藏避免删除延迟期间的残影）
        sb = self.scroll.verticalScrollBar()
        frac = (self._pending_scroll_frac if self._pending_scroll_frac is not None
                else (sb.value() / sb.maximum() if sb.maximum() else 0.0))
        for layout in (self.grid, self.list_box):
            while layout.count():
                item = layout.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.hide()
                    widget.deleteLater()
        self._grid_cards = []
        self._grid_cols = 0
        self._grid_rows = 0
        self._note_widgets: dict[str, QWidget] = {}

        if self._page == "timeline":
            self._render_timelines()
            return
        if self._page == "outline":
            self._render_outlines()
            return
        if self._page == "notes":
            self._render_notes()
            self._apply_scroll(frac)
            return
        if self._page == "characters":
            self._render_characters()
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
        self._apply_scroll(frac)

    def _cover_path_for(self, meta: dict) -> str:
        return self._cover_paths.get(str(meta.get("id") or ""), "")

    def _columns(self) -> int:
        """按可用宽度自适应列数：窄窗自动减列，宽窗增加列。"""
        try:
            width = self.scroll.viewport().width()
        except RuntimeError:
            width = 1000
        return max(1, (width - 20) // (CARD_W + 18))

    def _fill_grid(self, items: list[dict]) -> None:
        columns = self._columns()
        in_trash = self._page == "trash"
        cards = []
        for index, meta in enumerate(items):
            card = BookCard(meta, self.grid_holder)
            card.set_meta(meta, self._cover_path_for(meta))
            card.in_trash = in_trash
            self._connect_card(card)
            row, col = divmod(index, columns)
            self.grid.addWidget(card, row, col)
            cards.append(card)
        for col in range(columns):
            self.grid.setColumnStretch(col, 1)
        rows = (len(items) - 1) // columns + 1
        self.grid.setRowStretch(rows, 1)
        self._grid_cards = cards
        self._grid_cols, self._grid_rows = columns, rows

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
        self._grid_cards = list(cards)
        self._grid_cols, self._grid_rows = columns, rows

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
                kind="时间轴",
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
                kind="大纲",
            )
            card.clicked.connect(self.open_outline_requested.emit)
            card.open_requested.connect(self.open_outline_requested.emit)
            card.rename_requested.connect(self.rename_outline_requested.emit)
            card.delete_requested.connect(self.delete_outline_requested.emit)
            cards.append(card)
        self._fill_item_grid(cards)

    def _render_characters(self) -> None:
        """角色页：角色卡管理面板（惰性创建，需要 storage）。"""
        if self._characters_panel is None:
            if self._storage is None:
                self.empty_label.setText("角色功能需要重新打开软件后可用")
                self.scroll.hide()
                self.empty_label.show()
                return
            from app.ui.characters import CharactersPanel

            self._characters_panel = CharactersPanel(
                self._storage, getattr(self, "_char_view", "list"), self
            )
            self._content_host.addWidget(self._characters_panel, 1)
            self._characters_panel.data_changed.connect(self.characters_changed.emit)
            self._characters_panel.view_changed.connect(self.character_view_changed.emit)
            self._characters_panel.import_requested.connect(
                self.import_characters_requested.emit
            )
        panel = self._characters_panel
        self.empty_label.hide()
        self.grid_holder.hide()
        self.list_holder.hide()
        self.scroll.hide()
        panel.set_theme(self._theme)
        panel.set_data(self._characters, self._character_relations)
        panel.show()

    def characters_view(self):
        """角色面板（未创建时先创建）；供主窗口直接调用其增删改方法。"""
        if self._characters_panel is None and self._storage is not None:
            self._render_characters()
        return self._characters_panel

    def characters_panel(self):
        """已创建的角色面板（未创建返回 None），用于同步作品名而不触发创建。"""
        return self._characters_panel

    def _render_map(self) -> None:
        """地图页：多张地图卡片，可新建 / 打开 / 重命名 / 删除。"""
        if not self._maps:
            self.scroll.hide()
            self.empty_label.setText("还没有地图\n点击右上角「新建地图」开始绘制")
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
                kind="地图",
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
            kind="打字统计",
        )
        card.can_rename = False
        card.can_delete = False
        # clicked/open_requested 带 item_id 参数，stats_open_requested 无参——
        # 必须经 lambda 转发（直连会在真实点击时抛 TypeError 导致卡片点不开统计）
        card.clicked.connect(lambda _item_id="": self.stats_open_requested.emit())
        card.open_requested.connect(lambda _item_id="": self.stats_open_requested.emit())
        self._fill_item_grid([card])

    def _render_achievements(self) -> None:
        pairs = [
            (key, info)
            for key, info in ACHIEVEMENTS.items()
            if self._ach_filter == "all"
            or (self._ach_filter == "unlocked") == (key in self._unlocked)
        ]
        if not pairs:
            self.empty_label.setText("没有符合条件的成就\n右键空白处切换筛选条件")
            self.grid_holder.hide()
            self.empty_label.show()
            return
        self.empty_label.hide()
        cards = [
            AchievementCard(
                key, info, float(self._unlocked.get(key) or 0), self._showcase,
                self.grid_holder,
            )
            for key, info in pairs
        ]
        for card in cards:
            card.showcase_requested.connect(self.medal_showcase_requested.emit)
        self._fill_item_grid(cards)

    def _render_notes(self) -> None:
        items = self._notes_for_view()
        if not items:
            self.scroll.hide()
            if self._notes_query.strip():
                self.empty_label.setText("没有匹配的设定\n换个关键词试试，或清空搜索框")
            else:
                self.empty_label.setText("还没有设定文档\n右键空白处选择「新建设定」")
            self.empty_label.show()
            return
        self.empty_label.hide()
        self.scroll.show()
        if self._notes_view == "grid":
            self.grid_holder.setVisible(True)
            self.list_holder.setVisible(False)
            cards: list[ItemCard] = []
            for item in items:
                subtitle = self._note_subtitle(item)
                card = ItemCard(
                    str(item.get("id")),
                    str(item.get("title") or "未命名设定"),
                    subtitle,
                    "#5B8DEF",
                    self.grid_holder,
                    kind="设定",
                )
                card.clicked.connect(self._on_note_clicked)
                card.open_requested.connect(self.open_note_requested.emit)
                card.rename_requested.connect(self.rename_note_requested.emit)
                card.delete_requested.connect(self.delete_note_requested.emit)
                cards.append(card)
                self._note_widgets[card.item_id] = card
            self._fill_item_grid(cards)
            return
        # 列表视图：一行一篇，标题 / 摘要 / 修改时间
        self.grid_holder.setVisible(False)
        self.list_holder.setVisible(True)
        for item in items:
            summary = " ".join(str(item.get("content") or "").split())[:90]
            row = NoteRow(
                str(item.get("id")),
                str(item.get("title") or "未命名设定"),
                summary,
                _time_text(float(item.get("updated_at") or 0)),
                "#5B8DEF",
                self.list_holder,
                kind="设定",
            )
            row.clicked.connect(self._on_note_clicked)
            row.open_requested.connect(self.open_note_requested.emit)
            row.rename_requested.connect(self.rename_note_requested.emit)
            row.delete_requested.connect(self.delete_note_requested.emit)
            self.list_box.addWidget(row)
            self._note_widgets[row.item_id] = row

    def _notes_for_view(self) -> list[dict]:
        items = sorted(self._notes,
                       key=lambda n: float(n.get("updated_at") or 0), reverse=True)
        q = self._notes_query.strip().lower()
        if q:
            items = [n for n in items
                     if q in str(n.get("title") or "").lower()
                     or q in str(n.get("content") or "").lower()]
        return items

    @staticmethod
    def _note_subtitle(item: dict, limit: int = 40) -> str:
        """卡片摘要：正文前 N 字；正文为空时退回更新时间。"""
        content = " ".join(str(item.get("content") or "").split())[:limit]
        return content or f"更新于 {_date_text(float(item.get('updated_at') or 0))}"

    def update_note(self, doc: dict) -> None:
        """保存设定后就地更新该条目，不重建整页。

        排序（或搜索命中）导致位置变化时只移动这一条；筛选结果可能增减时退回整页刷新。
        """
        note_id = str(doc.get("id") or "")
        if not note_id:
            return
        target = None
        for item in self._notes:
            if str(item.get("id")) == note_id:
                target = item
                break
        if target is None:
            target = dict(doc)
            self._notes.append(target)
        else:
            target.update(dict(doc))
        if self._page != "notes":
            return
        if self._notes_query.strip() or note_id not in self._note_widgets:
            # 有搜索词时该条可能从命中集增删，列表长度会变 → 只能整页刷新
            self._render()
            return
        widget = self._note_widgets[note_id]
        title = str(target.get("title") or "未命名设定")
        if self._notes_view == "grid":
            widget.set_data(title, self._note_subtitle(target))
        else:
            summary = " ".join(str(target.get("content") or "").split())[:90]
            widget.set_data(title, summary,
                            _time_text(float(target.get("updated_at") or 0)))
        self._move_note_widget(note_id)

    def _move_note_widget(self, note_id: str) -> None:
        """按当前排序仅移动/重排受影响的这一条，不重建卡片。"""
        order = [str(n.get("id")) for n in self._notes_for_view()]
        if note_id not in order:
            return
        new_index = order.index(note_id)
        widget = self._note_widgets.get(note_id)
        if widget is None:
            return
        if self._notes_view == "list":
            current = self.list_box.indexOf(widget)
            if current >= 0 and current != new_index:
                self.list_box.removeWidget(widget)
                self.list_box.insertWidget(new_index, widget)
            return
        index = self.grid.indexOf(widget)
        if index < 0 or not self._grid_cols:
            return
        row, col, _rs, _cs = self.grid.getItemPosition(index)
        old_index = row * self._grid_cols + col
        if old_index == new_index:
            return
        cards = list(self._grid_cards)
        ids = [str(getattr(c, "item_id", "")) for c in cards]
        if note_id not in ids:
            return
        moved = cards.pop(old_index)
        cards.insert(new_index, moved)
        self._grid_cards = cards
        # 只把 [start:] 的现有控件重新入格（不新建），前面的卡片完全不动
        start = min(old_index, new_index)
        for card in cards[start:]:
            self.grid.removeWidget(card)
        for i in range(start, len(cards)):
            new_row, new_col = divmod(i, self._grid_cols)
            self.grid.addWidget(cards[i], new_row, new_col)

    def _on_note_clicked(self, note_id: str) -> None:
        self._selected_note_id = str(note_id)
        self._new_note_id = ""
        self.open_note_requested.emit(note_id)

    def _apply_scroll(self, frac: float | None) -> None:
        """渲染完成后恢复滚动位置与选中项（等布局刷新后再设值）。"""
        self._pending_scroll_frac = None
        page_at_call = self._page

        def restore() -> None:
            if self._page != page_at_call:
                return
            sb = self.scroll.verticalScrollBar()
            if frac and sb.maximum() > 0:
                sb.setValue(int(round(frac * sb.maximum())))
            self._apply_note_selection()

        QTimer.singleShot(0, restore)

    def _apply_note_selection(self) -> None:
        if self._page != "notes":
            return
        target = self._new_note_id or self._selected_note_id
        widget = self._note_widgets.get(str(target)) if target else None
        if widget is None:
            return
        for w in self._note_widgets.values():
            w.set_selected(w is widget)
        if self._new_note_id:
            self.scroll.ensureWidgetVisible(widget, 8, 8)
            self._new_note_id = ""

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._reflow_timer.start()

    def _reflow_grid(self) -> None:
        """窗口变宽/变窄时按新列数重排现有卡片，不重建、不打断滚动。"""
        if not self._grid_cards or not self.grid_holder.isVisible():
            return
        if self._page == "notes" and self._notes_view != "grid":
            return
        cols = self._columns()
        if cols == self._grid_cols:
            return
        sb = self.scroll.verticalScrollBar()
        frac = sb.value() / sb.maximum() if sb.maximum() else 0.0
        for c in range(self._grid_cols + 1):
            self.grid.setColumnStretch(c, 0)
        for r in range(self._grid_rows + 1):
            self.grid.setRowStretch(r, 0)
        for i, card in enumerate(self._grid_cards):
            row, col = divmod(i, cols)
            self.grid.addWidget(card, row, col)
        for c in range(cols):
            self.grid.setColumnStretch(c, 1)
        rows = (len(self._grid_cards) - 1) // cols + 1
        self.grid.setRowStretch(rows, 1)
        self._grid_cols, self._grid_rows = cols, rows
        self._apply_scroll(frac)

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
        card.export_chapters_requested.connect(self.export_book_chapters_requested.emit)

    # ------------------------------------------------------------------
    # 右键菜单：按当前页面 + 落点给菜单，本页没有的能力整项不出现
    # ------------------------------------------------------------------
    def contextMenuEvent(self, event) -> None:  # noqa: N802
        popup(make_menu(self, self._blank_menu_entries()), event.globalPos())

    def _blank_menu_entries(self) -> list:
        page = self._page
        if page == "home":
            export_menu = self._export_books_submenu()
            entries = [
                ("新建书", self.new_book_requested.emit),
                ("导入书（TXT）…", self.import_book_requested.emit),
            ]
            if export_menu is not None:
                entries.append(("导出整本书", export_menu))
            entries += [
                ("排序方式", self._sort_submenu()),
                None,
                ("刷新书架", self.refresh_requested.emit),
                ("新建分组", self.new_group_requested.emit),
            ]
            return entries
        if page == "trash":
            entries = [("刷新回收站", self.refresh_requested.emit)]
            if any(meta.get("trashed") for meta in self._metas):
                entries.append(("清空回收站", self.empty_trash_requested.emit))
            return entries
        if page == "timeline":
            return [
                ("新建时间轴", self.new_timeline_requested.emit),
                ("导入时间轴（JSON）…", self.import_timeline_requested.emit),
                None,
                ("刷新时间轴列表", self.refresh_requested.emit),
            ]
        if page == "outline":
            return [
                ("新建大纲", self.new_outline_requested.emit),
                ("导入大纲（JSON）…", self.import_outline_requested.emit),
                None,
                ("刷新大纲列表", self.refresh_requested.emit),
            ]
        if page == "notes":
            return [
                ("新建设定文档", self.new_note_requested.emit),
                ("导入设定（TXT）…", self.import_note_requested.emit),
                None,
                ("搜索设定", self._focus_notes_search),
                ("网格视图", lambda: self.set_notes_view("grid"),
                 self._notes_view == "grid"),
                ("列表视图", lambda: self.set_notes_view("list"),
                 self._notes_view == "list"),
            ]
        if page == "characters":
            return [
                ("新建角色卡", self.new_character_requested.emit),
                ("导入角色（JSON）…", self.import_characters_requested.emit),
                None,
                ("角色列表", lambda: self._set_character_view("list")),
                ("角色关系图", lambda: self._set_character_view("board")),
            ]
        if page == "map":
            return [
                ("新建地图", self.new_map_requested.emit),
                ("导入地图图片…", self.import_map_requested.emit),
                None,
                ("刷新地图列表", self.refresh_requested.emit),
            ]
        if page == "achievements":
            return [
                ("查看成就详情…", self._show_achievements_summary),
                ("筛选成就", self._achievement_filter_submenu()),
            ]
        if page == "stats":
            return [("打开打字统计", self.stats_open_requested.emit)]
        return []

    def _export_books_submenu(self):
        """空白处导出整本书：先按书名列出可导出的书，避免没有宾语的「导出」。"""
        books = [meta for meta in self._metas if not meta.get("trashed")]
        if not books:
            return None
        books.sort(key=lambda m: float(m.get("created_at") or 0), reverse=True)
        return submenu(self, "导出整本书", [
            (str(meta.get("title") or "未命名"),
             lambda bid=str(meta.get("id") or ""):
             self.export_book_chapters_requested.emit(bid))
            for meta in books
        ])

    def _sort_submenu(self):
        labels = (("created", "按创建时间"), ("updated", "按更新时间"), ("title", "按书名"))
        return submenu(self, "排序方式", [
            (label, lambda mode=key: self._set_sort_mode(mode),
             self._sort_mode == key)
            for key, label in labels
        ])

    def _achievement_filter_submenu(self):
        return submenu(self, "筛选成就", [
            ("全部成就", lambda: self._set_achievement_filter("all"),
             self._ach_filter == "all"),
            ("只看已解锁", lambda: self._set_achievement_filter("unlocked"),
             self._ach_filter == "unlocked"),
            ("只看未解锁", lambda: self._set_achievement_filter("locked"),
             self._ach_filter == "locked"),
        ])

    def _set_sort_mode(self, mode: str) -> None:
        if mode not in {"created", "updated", "title"} or mode == self._sort_mode:
            return
        self._sort_mode = mode
        self._render()

    def _set_achievement_filter(self, mode: str) -> None:
        if mode not in {"all", "unlocked", "locked"} or mode == self._ach_filter:
            return
        self._ach_filter = mode
        self._render()

    def _set_character_view(self, key: str) -> None:
        panel = self.characters_view()
        if panel is not None:
            panel._set_view(key)

    def _focus_notes_search(self) -> None:
        self.notes_search.setFocus(Qt.OtherFocusReason)
        self.notes_search.selectAll()

    def _show_achievements_summary(self) -> None:
        from app.ui.dialogs import MessageBox

        unlocked = len(self._unlocked)
        lines = [
            f"{'已解锁' if key in self._unlocked else '未解锁'} · "
            f"{info.get('name') or ''}：{info.get('cond') or ''}"
            for key, info in ACHIEVEMENTS.items()
        ]
        MessageBox.information(
            self, "成就详情", f"已解锁 {unlocked} / {len(ACHIEVEMENTS)} 枚\n\n" + "\n".join(lines)
        )
