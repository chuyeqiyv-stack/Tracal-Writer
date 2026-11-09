# -*- coding: utf-8 -*-
"""按页面 / 落点构建右键菜单的公共工具。

各页面只负责给出「本页此刻真能做到的事」清单，本模块负责统一排版：
- 条目为 None 表示分隔线（首尾自动省略）；
- 条目为 (文字, 回调)：普通菜单项；
- 条目为 (文字, 回调, True, checked)：可勾选项；
- 回调查为 None 的条目直接丢弃（能力不存在就不出现，不留灰显空壳）。
"""
from __future__ import annotations

from typing import Iterable

from PySide6.QtWidgets import QMenu


Entry = tuple


def add_action(menu: QMenu, text: str, slot, checked: bool | None = None) -> None:
    """把一项接到真实槽上；slot 为空表示本页没有这个能力，整项不出现。"""
    if slot is None:
        return
    if isinstance(slot, QMenu):
        slot.setTitle(text)
        menu.addMenu(slot)
        return
    action = menu.addAction(text)
    if checked is not None:
        action.setCheckable(True)
        action.setChecked(bool(checked))
    action.triggered.connect(lambda _checked=False, s=slot: s())


def make_menu(parent, entries: Iterable[Entry | None], title: str = "") -> QMenu:
    """按条目清单生成菜单；连续/首尾分隔线自动归并，不会留空段。"""
    menu = QMenu(title, parent) if title else QMenu(parent)
    for entry in entries:
        if entry is None:
            if menu.actions() and not menu.actions()[-1].isSeparator():
                menu.addSeparator()
            continue
        text = str(entry[0])
        slot = entry[1]
        checked = entry[2] if len(entry) > 2 else None
        add_action(menu, text, slot, checked)
    # 去掉尾部可能出现的分隔线
    while menu.actions() and menu.actions()[-1].isSeparator():
        menu.removeAction(menu.actions()[-1])
    return menu


def submenu(parent, title: str, entries: Iterable[Entry | None]) -> QMenu:
    """子菜单：同样按能力清单生成，空子菜单由调用方决定是否挂载。"""
    return make_menu(parent, entries, title)


def popup(menu: QMenu, global_pos) -> None:
    """弹出后立即安排回收，反复右键不会在页面上堆积菜单对象。"""
    try:
        menu.exec(global_pos)
    finally:
        menu.deleteLater()
