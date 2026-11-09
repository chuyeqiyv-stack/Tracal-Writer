# -*- coding: utf-8 -*-
"""本地 JSON 存储层。

目录结构：
    <数据目录>/library.json          书架索引（书籍摘要、分组、上次打开的书）
    <数据目录>/settings.json          全局设置
    <数据目录>/books/<book_id>.json   单本书的完整内容
    <数据目录>/covers/<book_id>.<后缀> 自定义封面
    <数据目录>/history/<book_id>/<chapter_id>/<时间戳>.json  历史快照

写入采用“临时文件 + 原子替换”，尽量避免崩溃时损坏数据。
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import shutil
import time
from pathlib import Path
from typing import Any

from app import config
from app.models import Book, Settings, new_id


# v1.4.4：旧版默认正文排版。已存配置四项全部等于这组值时，视为“用户从未自定义”，
# 一次性迁移到新默认（12pt / 1.55em / 段前段后各 0.5 行）；任何一项不等则保留原值。
LEGACY_TYPOGRAPHY_DEFAULTS = {
    "font_size": 16.0,
    "line_spacing": 1.1,
    "para_space_before": 0.0,
    "para_space_after": 0.0,
}
TYPOGRAPHY_MIGRATION_DEFAULTS = {
    "font_size": 12.0,
    "line_spacing": 1.55,
    "para_space_before": 0.5,
    "para_space_after": 0.5,
}


def legacy_typography_matches(raw: Any) -> bool:
    """旧配置是否仍完整停留在旧默认排版上（失败/缺键/类型异常一律视为“自定义”）。"""
    if not isinstance(raw, dict):
        return False
    for key, old_value in LEGACY_TYPOGRAPHY_DEFAULTS.items():
        if key not in raw:
            return False
        try:
            value = float(raw[key])
        except (TypeError, ValueError):
            return False
        if abs(value - old_value) > 1e-6:
            return False
    return True


class ImportReport(dict):
    """角色导入报告：支持字典取值，同时兼容旧调用方的双值解包。"""

    def __iter__(self):
        yield self.get("added_characters", 0)
        yield self.get("added_relations", 0)


class Storage:
    """封装所有文件读写操作。"""

    def __init__(self, base_dir: Path | str | None = None) -> None:
        self.base_dir = Path(base_dir) if base_dir else config.DATA_DIR
        self.library_file = self.base_dir / "library.json"
        self.settings_file = self.base_dir / "settings.json"
        self.map_file = self.base_dir / "map_global.json"
        self.words_file = self.base_dir / "words_daily.json"
        self.book_words_file = self.base_dir / "book_words.json"
        self.book_medals_file = self.base_dir / "book_medals.json"
        self.achievements_file = self.base_dir / "achievements.json"
        self.snippets_file = self.base_dir / "snippets.json"
        self.character_templates_file = self.base_dir / "character_templates.json"
        self.books_dir = self.base_dir / "books"
        self.covers_dir = self.base_dir / "covers"
        self.history_dir = self.base_dir / "history"
        self._covers_housekept = False   # v3：封面懒迁移 / 孤儿扫描每实例只跑一次
        self.last_cover_event: dict | None = None   # 最近一次超大图规范记录
        self._ensure_dirs()

    # ------------------------------------------------------------------
    # 基础工具
    # ------------------------------------------------------------------
    def _ensure_dirs(self) -> None:
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.books_dir.mkdir(parents=True, exist_ok=True)
        self.covers_dir.mkdir(parents=True, exist_ok=True)
        self.history_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _read_json(path: Path, default: Any) -> Any:
        """读取 JSON：主文件损坏或不存在时回退到同名 .bak，避免读到空数据。

        损坏的主文件会另存为 `.corrupt` 供人工恢复，绝不静默删除。
        """
        for candidate in (path, path.with_name(path.name + ".bak")):
            try:
                if not candidate.exists():
                    continue
                with candidate.open("r", encoding="utf-8") as f:
                    return json.load(f)
            except (OSError, ValueError, json.JSONDecodeError):
                if candidate == path:
                    try:
                        shutil.copy2(path, path.with_name(path.name + ".corrupt"))
                    except OSError:
                        pass
                continue
        return default

    def _write_json(self, path: Path, data: Any) -> None:
        """原子写入 JSON，并保留一份上一版 .bak。"""
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            try:
                shutil.copy2(path, path.with_name(path.name + ".bak"))
            except OSError:
                pass
        tmp = path.with_name(path.name + ".tmp")
        with tmp.open("w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)

    def _book_path(self, book_id: str) -> Path:
        return self.books_dir / f"{book_id}.json"

    # ------------------------------------------------------------------
    # 设置
    # ------------------------------------------------------------------
    def load_settings(self) -> Settings:
        # v1.4.4：首次读取旧配置时做一次性排版迁移（失败/异常就跳过，绝不阻断启动）
        try:
            if self._migrate_legacy_typography():
                return Settings.from_dict(self._read_json(self.settings_file, {}))
        except Exception:   # noqa: BLE001 - 迁移属附加动作，绝不能影响启动
            pass
        return Settings.from_dict(self._read_json(self.settings_file, {}))

    def _migrate_legacy_typography(self) -> bool:
        """把“仍停留在旧默认排版”的配置迁到新默认；迁移前备份 settings.json。

        可重入：迁移后四项已不等于旧默认，第二次读取不会再写盘；备份只建一次，
        不会用迁移后的文件覆盖掉原始备份。
        """
        path = self.settings_file
        backup = path.with_name(path.name + ".bak_v144")
        try:
            if not path.exists():
                return False
            with path.open("r", encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, ValueError):
            return False
        if not legacy_typography_matches(raw):
            return False
        try:
            if not backup.exists():
                shutil.copy2(path, backup)
            migrated = dict(raw)
            migrated.update(TYPOGRAPHY_MIGRATION_DEFAULTS)
            self._write_json(path, migrated)
        except OSError:
            return False
        return True

    def save_settings(self, settings: Settings) -> None:
        self._write_json(self.settings_file, settings.to_dict())

    # ------------------------------------------------------------------
    # 文笔仿写片段（snippets.json，全书共享；不进 library.json）
    # ------------------------------------------------------------------
    @staticmethod
    def _normalize_snippet(raw: dict, index: int, seen: set[str]) -> dict | None:
        """容错归一化：缺键 / 类型错误 / 重复 id 都兜底，空内容条目丢弃。"""
        if not isinstance(raw, dict):
            return None
        content = raw.get("content")
        if not isinstance(content, str):
            content = "" if content is None else str(content)
        if not content.strip():
            return None
        sid = str(raw.get("id") or "").strip() or new_id()
        if sid in seen:
            sid = new_id()
        seen.add(sid)
        try:
            created = float(raw.get("created_at") or 0.0)
        except (TypeError, ValueError):
            created = 0.0
        if created <= 0:
            created = time.time()
        try:
            updated = float(raw.get("updated_at") or created)
        except (TypeError, ValueError):
            updated = created
        try:
            order = int(raw.get("order"))
        except (TypeError, ValueError):
            order = index
        source = raw.get("source")
        if not isinstance(source, str):
            source = "" if source is None else str(source)
        return {
            "id": sid,
            "content": content,
            "source": source,
            "created_at": created,
            "updated_at": updated,
            "order": order,
        }

    def load_snippets(self) -> list[dict]:
        """读取全书共享片段；文件缺失 / 旧备份无此文件 / 缺键均不报错。"""
        data = self._read_json(self.snippets_file, [])
        raw = data.get("snippets") if isinstance(data, dict) else data
        if not isinstance(raw, list):
            raw = []
        seen: set[str] = set()
        items: list[dict] = []
        for index, entry in enumerate(raw):
            item = self._normalize_snippet(entry, index, seen)
            if item:
                items.append(item)
        items.sort(key=lambda x: (x["order"], x["created_at"], x["id"]))
        for index, item in enumerate(items):
            item["order"] = index
        return items

    def save_snippets(self, items: list[dict]) -> None:
        seen: set[str] = set()
        clean: list[dict] = []
        for index, entry in enumerate(items):
            item = self._normalize_snippet(entry, index, seen)
            if item:
                clean.append(item)
        clean.sort(key=lambda x: (x["order"], x["created_at"], x["id"]))
        for index, item in enumerate(clean):
            item["order"] = index
        self._write_json(self.snippets_file, {"version": 1, "snippets": clean})

    # ------------------------------------------------------------------
    # 新建角色模板（character_templates.json，全局共享；不进 library.json）
    # ------------------------------------------------------------------
    def load_character_templates(self) -> dict:
        from app.services.character_templates import sanitize_document

        return sanitize_document(self._read_json(self.character_templates_file, {}))

    def save_character_templates(self, document: dict) -> None:
        from app.services.character_templates import sanitize_document

        self._write_json(self.character_templates_file, sanitize_document(document))

    # ------------------------------------------------------------------
    # 书架索引
    # ------------------------------------------------------------------
    def load_library(self) -> dict:
        data = self._read_json(self.library_file, None)
        if not isinstance(data, dict):
            data = {}
        data.setdefault("version", 2)
        data.setdefault("books", [])
        data.setdefault("groups", [])
        data.setdefault("timelines", [])
        data.setdefault("notes", [])
        data.setdefault("outlines", [])
        data.setdefault("characters", [])
        data.setdefault("character_relations", [])
        data.setdefault("character_board", {})
        data.setdefault("last_book_id", "")
        data.setdefault("initialized", False)
        # 过滤掉结构异常的条目
        data["books"] = [b for b in data["books"] if isinstance(b, dict) and b.get("id")]
        data["groups"] = [
            g for g in data["groups"] if isinstance(g, dict) and g.get("id") and g.get("name")
        ]
        data["timelines"] = [
            t for t in data["timelines"] if isinstance(t, dict) and t.get("id")
        ]
        data["notes"] = [n for n in data["notes"] if isinstance(n, dict) and n.get("id")]
        data["outlines"] = [o for o in data["outlines"] if isinstance(o, dict) and o.get("id")]
        data["characters"] = [
            c for c in data["characters"] if isinstance(c, dict) and c.get("id")
        ]
        data["character_relations"] = [
            r
            for r in data["character_relations"]
            if isinstance(r, dict) and r.get("id") and r.get("from_id") and r.get("to_id")
        ]
        return data

    def save_library(self, library: dict) -> None:
        self._write_json(self.library_file, library)

    def set_last_book(self, book_id: str) -> None:
        library = self.load_library()
        library["last_book_id"] = book_id
        self.save_library(library)

    def add_group(self, name: str) -> dict:
        """新建书籍分组，返回分组信息。"""
        library = self.load_library()
        group = {"id": new_id(), "name": name.strip()}
        library["groups"].append(group)
        self.save_library(library)
        return group

    def delete_group(self, group_id: str) -> None:
        library = self.load_library()
        library["groups"] = [g for g in library["groups"] if g["id"] != group_id]
        affected: list[str] = []
        for meta in library["books"]:
            if meta.get("group_id") == group_id:
                meta["group_id"] = ""
                affected.append(str(meta.get("id") or ""))
        self.save_library(library)
        # 书籍文件里也存有 group_id，必须一并清除，
        # 否则该书下次保存时已删除的分组会被“复活”
        for book_id in affected:
            if not book_id:
                continue
            book = self.load_book(book_id)
            if book is not None and book.group_id == group_id:
                book.group_id = ""
                self.save_book(book, None)

    # ------------------------------------------------------------------
    # 独立时间轴（可创建多个）
    # ------------------------------------------------------------------
    def add_timeline(self, title: str) -> dict:
        from app.models import TimelineItem

        item = TimelineItem(id=new_id(), title=title.strip() or "未命名时间轴")
        library = self.load_library()
        library["timelines"].append(item.to_dict())
        self.save_library(library)
        return item.to_dict()

    def import_timeline(self, item_dict: dict) -> dict:
        """从导出的 JSON dict 导入一条时间轴（分配新 id，避免冲突）。"""
        from app.models import TimelineItem

        data = dict(item_dict)
        data["id"] = new_id()
        data.pop("created_at", None)
        data.pop("updated_at", None)
        item = TimelineItem.from_dict(data)
        item.touch()
        library = self.load_library()
        library["timelines"].append(item.to_dict())
        self.save_library(library)
        return item.to_dict()

    def save_timeline(self, item_dict: dict) -> None:
        """按 id 覆盖保存一条时间轴。"""
        library = self.load_library()
        for i, item in enumerate(library["timelines"]):
            if item.get("id") == item_dict.get("id"):
                library["timelines"][i] = item_dict
                break
        else:
            library["timelines"].append(item_dict)
        self.save_library(library)

    def delete_timeline(self, timeline_id: str) -> None:
        library = self.load_library()
        library["timelines"] = [
            t for t in library["timelines"] if t.get("id") != timeline_id
        ]
        self.save_library(library)

    def load_timeline(self, timeline_id: str) -> dict | None:
        from app.models import TimelineItem

        library = self.load_library()
        for item in library["timelines"]:
            if item.get("id") == timeline_id:
                try:
                    normalized = TimelineItem.from_dict(item).to_dict()
                except (TypeError, ValueError):
                    return None
                # 旧数据 / 新字段混用时不静默丢字段：规范化结果之外的键原样带回，
                # 事件里未来新增的字段也按 id 合并回去。
                for key, value in item.items():
                    normalized.setdefault(key, value)
                raw_events = [
                    e for e in (item.get("events") or []) if isinstance(e, dict)
                ]
                by_id = {
                    str(e.get("id")): e for e in raw_events if e.get("id")
                }
                for index, event in enumerate(normalized.get("events") or []):
                    source = by_id.get(str(event.get("id")))
                    if source is None and index < len(raw_events):
                        source = raw_events[index]
                    if not isinstance(source, dict):
                        continue
                    for key, value in source.items():
                        event.setdefault(key, value)
                return normalized
        return None

    # ------------------------------------------------------------------
    # 独立设定文档（可创建多个）
    # ------------------------------------------------------------------
    def add_note(self, title: str) -> dict:
        from app.models import SettingDoc

        doc = SettingDoc(id=new_id(), title=title.strip() or "未命名设定")
        library = self.load_library()
        library["notes"].append(doc.to_dict())
        self.save_library(library)
        return doc.to_dict()

    def save_note(self, doc_dict: dict) -> None:
        library = self.load_library()
        for i, item in enumerate(library["notes"]):
            if item.get("id") == doc_dict.get("id"):
                library["notes"][i] = doc_dict
                break
        else:
            library["notes"].append(doc_dict)
        self.save_library(library)

    def import_note_doc(self, title: str, content: str) -> dict:
        """从 TXT 导入一篇设定文档。"""
        from app.models import SettingDoc

        doc = SettingDoc(id=new_id(), title=title.strip() or "导入的设定")
        doc.content = content
        doc.touch()
        library = self.load_library()
        library["notes"].append(doc.to_dict())
        self.save_library(library)
        return doc.to_dict()

    def delete_note(self, note_id: str) -> None:
        library = self.load_library()
        library["notes"] = [n for n in library["notes"] if n.get("id") != note_id]
        self.save_library(library)

    # ------------------------------------------------------------------
    # 独立大纲（思维导图，可创建多个）
    # ------------------------------------------------------------------
    def add_outline(self, title: str) -> dict:
        from app.models import OutlineItem, OutlineNode

        item = OutlineItem(
            id=new_id(),
            title=title.strip() or "未命名大纲",
            nodes=[OutlineNode(id=new_id(), x=2400.0, y=1500.0, text="中心主题", color="")],
        )
        library = self.load_library()
        library["outlines"].append(item.to_dict())
        self.save_library(library)
        return item.to_dict()

    def save_outline(self, item_dict: dict) -> None:
        """按 id 覆盖保存一条大纲。"""
        library = self.load_library()
        for i, item in enumerate(library["outlines"]):
            if item.get("id") == item_dict.get("id"):
                library["outlines"][i] = item_dict
                break
        else:
            library["outlines"].append(item_dict)
        self.save_library(library)

    def import_outline(self, item_dict: dict) -> dict:
        """从导出的 JSON dict 导入一条大纲（分配新 id，避免冲突）。"""
        from app.models import OutlineItem

        data = dict(item_dict)
        data["id"] = new_id()
        data.pop("created_at", None)
        data.pop("updated_at", None)
        item = OutlineItem.from_dict(data)
        item.touch()
        library = self.load_library()
        library["outlines"].append(item.to_dict())
        self.save_library(library)
        return item.to_dict()

    def delete_outline(self, outline_id: str) -> None:
        library = self.load_library()
        library["outlines"] = [
            o for o in library["outlines"] if o.get("id") != outline_id
        ]
        self.save_library(library)

    def load_outline(self, outline_id: str) -> dict | None:
        from app.models import OutlineItem

        library = self.load_library()
        for item in library["outlines"]:
            if item.get("id") == outline_id:
                try:
                    return OutlineItem.from_dict(item).to_dict()
                except (TypeError, ValueError):
                    return None
        return None

    # ------------------------------------------------------------------
    # 角色卡与角色关系（保存在 library.json，随项目备份 / 迁移一并带走）
    # ------------------------------------------------------------------
    @staticmethod
    def _normalize_character(raw: dict) -> dict:
        from app.models import Character

        try:
            return Character.from_dict(raw).to_dict()
        except (TypeError, ValueError):
            return {}

    @staticmethod
    def _normalize_relation(raw: dict) -> dict:
        from app.models import CharacterRelation

        try:
            return CharacterRelation.from_dict(raw).to_dict()
        except (TypeError, ValueError):
            return {}

    def load_characters(self) -> list[dict]:
        """全部角色卡（容错归一化，旧项目文件缺字段也能打开）。"""
        items: list[dict] = []
        for raw in self.load_library().get("characters", []):
            item = self._normalize_character(raw)
            if item:
                items.append(item)
        return items

    def migrate_split_edges(self) -> int:
        """一次性迁移：旧 bidirectional=true 的单线拆成两条反向独立有向边。

        - 拆分出的两条边都带 split_from="bidir:<原id>"，重复执行不再产生副本
          （迁移后库中不再有无标记的 bidirectional 记录）；
        - bidirectional=false / directed=false 的记录原样保留；
        - 返回本次新拆出的边条数（0 表示无需迁移）。
        """
        library = self.load_library()
        raws = library.get("character_relations")
        if not isinstance(raws, list):
            return 0
        out: list[dict] = []
        added = 0
        for raw in raws:
            if (isinstance(raw, dict) and raw.get("bidirectional") is True
                    and raw.get("directed") and not str(raw.get("split_from") or "")):
                fwd = self._normalize_relation(raw)
                if not fwd:
                    out.append(raw)
                    continue
                group = f"bidir:{fwd.get('id') or new_id()}"
                fwd["bidirectional"] = False
                fwd["split_from"] = group
                rev = dict(fwd)
                rev["id"] = new_id()
                rev["from_id"], rev["to_id"] = fwd["to_id"], fwd["from_id"]
                out.extend([fwd, rev])
                added += 1
            else:
                out.append(raw)
        if added:
            library["character_relations"] = out
            self.save_library(library)
        return added

    def load_relations(self) -> list[dict]:
        """全部角色关系（读取前先做旧双向单线拆分迁移），过滤掉指向已删除角色的悬空关系。"""
        self.migrate_split_edges()
        ids = {c["id"] for c in self.load_characters()}
        items: list[dict] = []
        for raw in self.load_library().get("character_relations", []):
            item = self._normalize_relation(raw)
            if item and item["from_id"] in ids and item["to_id"] in ids:
                items.append(item)
        return items

    def load_board_size(self) -> tuple[int, int]:
        """关系图画布尺寸（老数据没有时用默认值）。"""
        raw = self.load_library().get("character_board") or {}
        try:
            width = max(4800, int(float(raw.get("width") or 0)))
            height = max(3000, int(float(raw.get("height") or 0)))
        except (TypeError, ValueError):
            width, height = 4800, 3000
        return width, height

    def load_board(self) -> dict:
        """关系图画布完整信息（尺寸 / 缩放 / 滚动位置），供导出原样带走。"""
        width, height = self.load_board_size()
        raw = self.load_library().get("character_board") or {}
        board = {"width": width, "height": height, "zoom": 1.0,
                 "scroll_x": 0, "scroll_y": 0}
        try:
            board["zoom"] = max(0.2, min(3.0, float(raw.get("zoom") or 1.0)))
        except (TypeError, ValueError):
            pass
        for key in ("scroll_x", "scroll_y"):
            try:
                board[key] = max(0, int(float(raw.get(key) or 0)))
            except (TypeError, ValueError):
                pass
        return board

    def save_character(self, char_dict: dict) -> None:
        """按 id 覆盖保存一张角色卡。"""
        library = self.load_library()
        item = self._normalize_character(char_dict)
        if not item:
            return
        for i, existing in enumerate(library["characters"]):
            if existing.get("id") == item["id"]:
                library["characters"][i] = item
                break
        else:
            library["characters"].append(item)
        self.save_library(library)

    def save_characters(self, items: list[dict]) -> None:
        """整体覆盖保存角色列表（关系图画布批量落坐标时用）。"""
        library = self.load_library()
        normalized = []
        for raw in items:
            item = self._normalize_character(raw)
            if item:
                normalized.append(item)
        library["characters"] = normalized
        self.save_library(library)

    def save_relations(self, items: list[dict]) -> None:
        """整体覆盖保存关系列表（同时清理悬空关系）。"""
        library = self.load_library()
        ids = {str(c.get("id") or "") for c in library["characters"]}
        normalized = []
        for raw in items:
            item = self._normalize_relation(raw)
            if item and item["from_id"] in ids and item["to_id"] in ids:
                normalized.append(item)
        library["character_relations"] = normalized
        self.save_library(library)

    def save_board_size(self, width: int, height: int, extra: dict | None = None) -> None:
        library = self.load_library()
        entry = {"width": int(width), "height": int(height)}
        if isinstance(extra, dict):
            for key in ("zoom", "scroll_x", "scroll_y"):
                if key in extra:
                    try:
                        entry[key] = float(extra[key]) if key == "zoom" else int(extra[key])
                    except (TypeError, ValueError):
                        pass
        library["character_board"] = entry
        self.save_library(library)

    def delete_character(self, character_id: str) -> None:
        """删除角色，并一并清理涉及该角色的全部关系（一致性校验）。"""
        library = self.load_library()
        library["characters"] = [
            c for c in library["characters"] if c.get("id") != character_id
        ]
        library["character_relations"] = [
            r
            for r in library["character_relations"]
            if r.get("from_id") != character_id and r.get("to_id") != character_id
        ]
        self.save_library(library)

    @staticmethod
    def _relation_key(item: dict) -> tuple:
        """关系去重键：端点 + 语义 + 强度/备注/颜色；忽略 id 与时间戳。

        - 双向独立边（A→B 与 B→A）天然因端点不同而保留；
        - split_from 计入键，旧双向拆分出的两条边不会互相顶掉；
        - 同一对角色、同方向但名称/强度/备注不同的两条边仍视为不同关系。
        """
        return (
            str(item.get("from_id") or ""),
            str(item.get("to_id") or ""),
            str(item.get("name") or ""),
            bool(item.get("directed")),
            int(item.get("intensity") or 0),
            str(item.get("note") or ""),
            str(item.get("split_from") or ""),
            str(item.get("color") or ""),
        )

    def analyze_character_import(self, data: dict) -> dict:
        """导入预检：统计可导入条目、坏数据条数、与现有数据的 id / 同名冲突。

        不写盘、不抛异常；坏条目只计数并跳过，交由调用方展示给用户确认。
        """
        result = {
            "version": 0,
            "valid_characters": [],
            "valid_relations": [],
            "skipped_characters": 0,
            "skipped_relations": 0,
            "conflict_id": 0,
            "conflict_name": 0,
            "existing_characters": 0,
        }
        if not isinstance(data, dict):
            return result
        try:
            result["version"] = int(data.get("version") or 1)
        except (TypeError, ValueError):
            result["version"] = 1
        chars_raw = data.get("characters")
        chars_raw = chars_raw if isinstance(chars_raw, list) else []
        rels_raw = data.get("relations")
        rels_raw = rels_raw if isinstance(rels_raw, list) else []
        for raw in chars_raw:
            if not isinstance(raw, dict) or not str(raw.get("name") or "").strip():
                result["skipped_characters"] += 1
                continue
            item = self._normalize_character(raw)
            if item:
                result["valid_characters"].append(item)
            else:
                result["skipped_characters"] += 1
        for raw in rels_raw:
            if not isinstance(raw, dict):
                result["skipped_relations"] += 1
                continue
            item = self._normalize_relation(raw)
            if item and item.get("from_id") and item.get("to_id"):
                result["valid_relations"].append(item)
            else:
                result["skipped_relations"] += 1
        library = self.load_library()
        existing_ids = {str(c.get("id") or "") for c in library["characters"]}
        existing_names = {str(c.get("name") or "") for c in library["characters"]}
        result["existing_characters"] = len(library["characters"])
        result["conflict_id"] = sum(
            1 for c in result["valid_characters"] if c["id"] in existing_ids
        )
        result["conflict_name"] = sum(
            1 for c in result["valid_characters"] if c["name"] in existing_names
        )
        return result

    @staticmethod
    def _reflow_imported(imported: list[dict], characters: list[dict],
                         width: int, height: int) -> int:
        """导入后画布重排：保留不冲突的原坐标，冲突/无坐标的黄金角散开。

        返回被重排的角色数。空库导入同一份导出文件时无冲突，坐标逐项不变。
        """
        node_w, node_h, gap = 156.0, 68.0, 24.0
        imported_ids = {str(c.get("id") or "") for c in imported}
        occupied: list[tuple[float, float]] = []
        for char in characters:
            if str(char.get("id") or "") in imported_ids:
                continue
            try:
                occupied.append((float(char.get("x") or 0), float(char.get("y") or 0)))
            except (TypeError, ValueError):
                continue

        def overlaps(x: float, y: float) -> bool:
            for ox, oy in occupied:
                if abs(x - ox) < node_w + gap and abs(y - oy) < node_h + gap:
                    return True
            return False

        moved = 0
        for index, char in enumerate(imported):
            try:
                x = float(char.get("x") or 0)
                y = float(char.get("y") or 0)
            except (TypeError, ValueError):
                x = y = 0.0
            if (x or y) and not overlaps(x, y):
                occupied.append((x, y))
                continue
            cx, cy = width / 2, height / 2
            for attempt in range(index, index + 400):
                if attempt == 0:
                    nx, ny = cx - node_w / 2, cy - node_h / 2
                else:
                    angle = math.radians(attempt * 137.5)
                    radius = 90 + (attempt // 8) * 110
                    nx = cx + radius * math.cos(angle) - node_w / 2
                    ny = cy + radius * 0.7 * math.sin(angle) - node_h / 2
                if not overlaps(nx, ny):
                    break
            char["x"], char["y"] = nx, ny
            occupied.append((nx, ny))
            moved += 1
        return moved

    def import_character_board(self, data: dict, strategy: str = "skip") -> dict:
        """导入导出的角色 JSON，返回详细报告（不抛异常）。

        strategy：
        - "skip"（默认，兼容旧行为）：同 id / 同名角色跳过并计数，保留现有数据；其余新增。
        - "merge"：同 id / 同名合并更新现有角色（保留现有 id 与创建时间）；
          其余新增；关系按端点去重，不重复造边。

        关系边一律重新分配 id、按导入角色映射端点、去重、剔除自环；
        导入后画布自动扩容并对重叠节点重排，避免叠成一坨。
        """
        if strategy not in ("merge", "skip"):
            strategy = "merge"
        pre = self.analyze_character_import(data)
        library = self.load_library()
        chars: list[dict] = library["characters"]
        by_id = {str(c.get("id") or ""): i for i, c in enumerate(chars)}
        by_name = {str(c.get("name") or ""): i for i, c in enumerate(chars)}
        report = ImportReport({
            "strategy": strategy,
            "added_characters": 0,
            "updated_characters": 0,
            "skipped_characters": pre["skipped_characters"],
            "skipped_conflicts": 0,
            "added_relations": 0,
            "skipped_relations": pre["skipped_relations"],
            "dropped_relations": 0,
            "duplicate_relations": 0,
            "reflowed_characters": 0,
            "board_width": 0,
            "board_height": 0,
        })
        id_map: dict[str, str] = {}
        imported_chars: list[dict] = []
        for item in pre["valid_characters"]:
            old_id = str(item.get("id") or "")
            idx_id = by_id.get(old_id) if old_id else None
            idx_name = by_name.get(item["name"])
            target = idx_id if idx_id is not None else idx_name
            if strategy == "skip" and target is not None:
                target_id = str(chars[target].get("id") or "")
                if old_id:
                    id_map[old_id] = target_id
                report["skipped_conflicts"] += 1
                continue
            if target is not None:
                merged = dict(chars[target])
                keep_x, keep_y = merged.get("x"), merged.get("y")
                merged.update(item)
                merged["id"] = str(chars[target].get("id") or item["id"])
                merged["created_at"] = (
                    chars[target].get("created_at") or item.get("created_at") or time.time()
                )
                merged["updated_at"] = time.time()
                # 导入文件没有坐标（或为 0）时保留现有画布位置
                if not float(merged.get("x") or 0) and not float(merged.get("y") or 0):
                    merged["x"], merged["y"] = keep_x or 0, keep_y or 0
                old_name = str(chars[target].get("name") or "")
                chars[target] = merged
                by_id[merged["id"]] = target
                by_name[merged["name"]] = target
                if old_name and old_name != merged["name"]:
                    by_name.pop(old_name, None)
                if old_id:
                    id_map[old_id] = merged["id"]
                report["updated_characters"] += 1
                continue
            item["id"] = old_id if old_id and old_id not in by_id else new_id()
            now = time.time()
            item["created_at"] = item.get("created_at") or now
            item["updated_at"] = now
            chars.append(item)
            by_id[item["id"]] = len(chars) - 1
            by_name[item["name"]] = len(chars) - 1
            if old_id:
                id_map[old_id] = item["id"]
            imported_chars.append(item)
            report["added_characters"] += 1

        existing_keys = set()
        for raw in library["character_relations"]:
            relation = self._normalize_relation(raw)
            if relation:
                existing_keys.add(self._relation_key(relation))
        for item in pre["valid_relations"]:
            from_id = id_map.get(item["from_id"])
            to_id = id_map.get(item["to_id"])
            if not (from_id and to_id):
                report["dropped_relations"] += 1
                continue
            if from_id == to_id:
                report["dropped_relations"] += 1   # 自环剔除
                continue
            item["from_id"], item["to_id"] = from_id, to_id
            key = self._relation_key(item)
            if key in existing_keys:
                report["duplicate_relations"] += 1
                continue
            item["id"] = new_id()
            item["created_at"] = time.time()
            library["character_relations"].append(item)
            existing_keys.add(key)
            report["added_relations"] += 1

        # 画布尺寸：至少容纳导出尺寸 / 现有尺寸 / 全部节点，导入后不重叠
        board = data.get("board") if isinstance(data.get("board"), dict) else {}
        width, height = self.load_board_size()
        try:
            width = max(width, int(float(board.get("width") or 0)))
            height = max(height, int(float(board.get("height") or 0)))
        except (TypeError, ValueError):
            pass
        if chars:
            try:
                width = max(width, int(max(float(c.get("x") or 0) for c in chars) + 260))
                height = max(height, int(max(float(c.get("y") or 0) for c in chars) + 200))
            except (TypeError, ValueError):
                pass
        report["board_width"], report["board_height"] = width, height
        if imported_chars:
            report["reflowed_characters"] = self._reflow_imported(
                imported_chars, chars, width, height
            )
            self.save_library(library)
            self.save_board_size(width, height, extra=board if board else None)
        elif board:
            self.save_board_size(width, height, extra=board)
        return report

    # ------------------------------------------------------------------
    # 地图（可创建多张，统一存为 map_global.json 的 {"maps": [...]}）
    # ------------------------------------------------------------------
    @staticmethod
    def _normalize_map(raw: dict) -> dict:
        """把单张地图 dict 修正为规范结构（场景边界钳制、列表过滤）。"""
        scene = raw.get("scene") if isinstance(raw.get("scene"), dict) else {}
        try:
            left = float(scene.get("left", 0.0))
            top = float(scene.get("top", 0.0))
            width = float(scene.get("width", 4000.0))
            height = float(scene.get("height", 3000.0))
        except (TypeError, ValueError):
            left, top, width, height = 0.0, 0.0, 4000.0, 3000.0
        width = max(1000.0, min(width, 200000.0))
        height = max(1000.0, min(height, 200000.0))
        # 地形标注：旧地图缺键 -> 空列表；隐藏列表只保留合法类型键
        terrain_keys = {"mountain", "river", "forest", "lake", "desert", "plain", "city", "pass"}
        hidden_raw = raw.get("terrain_hidden")
        terrain_hidden = [str(t) for t in hidden_raw if str(t) in terrain_keys] \
            if isinstance(hidden_raw, list) else []
        ref = Storage._map_ref_rect(raw, (left, top, width, height))
        terrains = [
            Storage._normalize_terrain(t, ref, terrain_keys)
            for t in (raw.get("terrains") or []) if isinstance(t, dict)
        ]
        return {
            "id": str(raw.get("id") or new_id()),
            "title": str(raw.get("title") or "未命名地图"),
            "created_at": float(raw.get("created_at") or time.time()),
            "updated_at": float(raw.get("updated_at") or time.time()),
            "bg": raw.get("bg") if isinstance(raw.get("bg"), dict) else None,
            "factions": [f for f in (raw.get("factions") or []) if isinstance(f, dict)],
            "pins": [p for p in (raw.get("pins") or []) if isinstance(p, dict)],
            "terrains": terrains,
            "terrain_hidden": terrain_hidden,
            "scene": {"left": left, "top": top, "width": width, "height": height},
        }

    @staticmethod
    def _map_ref_rect(raw: dict, scene: tuple) -> tuple[float, float, float, float]:
        """地形归一化坐标的参考矩形：有底图用底图，否则用场景边界。"""
        bg = raw.get("bg")
        if isinstance(bg, dict):
            try:
                bw = float(bg.get("w") or 0)
                bh = float(bg.get("h") or 0)
                cx = float(bg.get("x") or 0)
                cy = float(bg.get("y") or 0)
            except (TypeError, ValueError):
                bw = bh = 0.0
                cx = cy = 0.0
            if bw > 1 and bh > 1:
                return (cx - bw / 2, cy - bh / 2, bw, bh)
        return (float(scene[0]), float(scene[1]), float(scene[2]), float(scene[3]))

    @staticmethod
    def _normalize_terrain(raw: dict, ref: tuple, terrain_keys: set) -> dict:
        """地形路径：优先保留 points；仅有旧版 x/y 时迁移成单点路径，绝不丢标注。"""
        item = dict(raw)
        kind = str(item.get("type") or "mountain")
        item["type"] = kind if kind in terrain_keys else "mountain"
        points: list[list[float]] = []
        raw_points = item.get("points")
        if isinstance(raw_points, list):
            for pair in raw_points:
                if not isinstance(pair, (list, tuple)) or len(pair) < 2:
                    continue
                try:
                    points.append([round(float(pair[0]), 5), round(float(pair[1]), 5)])
                except (TypeError, ValueError):
                    continue
        if not points:
            try:
                x = float(item.get("x") or 0)
                y = float(item.get("y") or 0)
                left, top, width, height = ref
                points = [[round((x - left) / width, 5), round((y - top) / height, 5)]]
            except (TypeError, ValueError, ZeroDivisionError):
                points = []
        item["points"] = points or [[0.5, 0.5]]
        item.pop("x", None)
        item.pop("y", None)
        return item

    def load_maps(self) -> list[dict]:
        """读取全部地图；旧版单图结构自动迁移为一张「地图」。"""
        data = self._read_json(self.map_file, None)
        if not isinstance(data, dict):
            data = {}
        maps_raw = data.get("maps")
        if isinstance(maps_raw, list):
            return [
                self._normalize_map(m)
                for m in maps_raw
                if isinstance(m, dict) and m.get("id")
            ]
        # 旧结构迁移：{bg, factions, pins, scene} → 一张地图
        if (
            data.get("factions") or data.get("pins")
            or isinstance(data.get("bg"), dict) or isinstance(data.get("scene"), dict)
        ):
            migrated = self._normalize_map({"id": new_id(), "title": "地图", **data})
            self._write_json(self.map_file, {"maps": [migrated]})
            return [migrated]
        return []

    def load_map_item(self, map_id: str) -> dict | None:
        for item in self.load_maps():
            if item.get("id") == map_id:
                return item
        return None

    def save_maps(self, maps: list[dict]) -> None:
        self._write_json(self.map_file, {"maps": [self._normalize_map(m) for m in maps]})

    def save_map_item(self, item: dict) -> None:
        """按 id 覆盖保存一张地图（不存在则追加）。"""
        maps = self.load_maps()
        target = self._normalize_map(item)
        for i, existing in enumerate(maps):
            if existing.get("id") == target.get("id"):
                maps[i] = target
                break
        else:
            maps.append(target)
        self.save_maps(maps)

    def add_map(self, title: str) -> dict:
        """新建一张空白地图。"""
        item = self._normalize_map({"id": new_id(), "title": title.strip() or "未命名地图"})
        maps = self.load_maps()
        maps.append(item)
        self.save_maps(maps)
        return item

    def import_map_image(self, source: str | Path, title: str = "") -> dict:
        """把图片文件导入为新地图的底图（图片复制进数据目录 maps/）。"""
        src = Path(source)
        suffix = src.suffix.lower() or ".png"
        if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
            suffix = ".png"
        map_id = new_id()
        maps_dir = self.base_dir / "maps"
        maps_dir.mkdir(parents=True, exist_ok=True)
        target = maps_dir / f"{map_id}{suffix}"
        shutil.copyfile(src, target)
        item = self._normalize_map(
            {
                "id": map_id,
                "title": title.strip() or src.stem or "导入的地图",
                "bg": {"path": str(target), "w": 0, "h": 0},
            }
        )
        maps = self.load_maps()
        maps.append(item)
        self.save_maps(maps)
        return item

    def delete_map(self, map_id: str) -> None:
        maps = [m for m in self.load_maps() if m.get("id") != map_id]
        self.save_maps(maps)

    # ------------------------------------------------------------------
    # 每日打字统计（只统计键盘输入的字，words_daily.json）
    # ------------------------------------------------------------------
    def load_words(self) -> dict:
        """读取每日打字统计；返回 {"total": int, "days": {"YYYY-MM-DD": int}}。"""
        data = self._read_json(self.words_file, None)
        if not isinstance(data, dict):
            data = {}
        try:
            total = int(float(data.get("total") or 0))
        except (TypeError, ValueError):
            total = 0
        days_raw = data.get("days") if isinstance(data.get("days"), dict) else {}
        days: dict[str, int] = {}
        for key, value in days_raw.items():
            try:
                days[str(key)] = int(float(value))
            except (TypeError, ValueError):
                continue
        return {"total": max(0, total), "days": days}

    def add_words(self, count: int, day: str) -> dict:
        """累加某一天（电脑本地日期）的打字字数，返回更新后的完整统计。"""
        if count <= 0:
            return self.load_words()
        data = self.load_words()
        data["total"] = int(data.get("total") or 0) + count
        days = data.setdefault("days", {})
        days[day] = int(days.get(day) or 0) + count
        self._write_json(self.words_file, data)
        return data

    # ------------------------------------------------------------------
    # 单本每日有效码字（book_words.json：{book_id: {"days": {date: n}}}）
    # ------------------------------------------------------------------
    def load_book_words(self) -> dict:
        data = self._read_json(self.book_words_file, None)
        return data if isinstance(data, dict) else {}

    def add_book_words(self, book_id: str, count: int, day: str) -> dict:
        """累加某本书某一天的键盘有效码字（用于断更/救赎成就判定）。"""
        data = self.load_book_words()
        entry = data.get(book_id)
        if not isinstance(entry, dict):
            entry = {}
        days = entry.setdefault("days", {})
        if count > 0:
            days[day] = int(days.get(day) or 0) + count
        data[book_id] = entry
        self._write_json(self.book_words_file, data)
        return data

    # ------------------------------------------------------------------
    # 单本勋章记录（book_medals.json：{book_id: {ach_key: 解锁时间戳}}）
    # ------------------------------------------------------------------
    def load_book_medals(self) -> dict:
        data = self._read_json(self.book_medals_file, None)
        return data if isinstance(data, dict) else {}

    def set_book_medal(self, book_id: str, key: str, ts: float) -> None:
        """记录某本书解锁某枚「单本」成就（断更警示 / 救赎）的时间戳。"""
        data = self.load_book_medals()
        entry = data.get(book_id)
        if not isinstance(entry, dict):
            entry = {}
        entry[key] = float(ts)
        data[book_id] = entry
        self._write_json(self.book_medals_file, data)

    # ------------------------------------------------------------------
    # 成就（achievements.json，value = 解锁时间戳）
    # ------------------------------------------------------------------
    def load_achievements(self) -> dict[str, float]:
        data = self._read_json(self.achievements_file, None)
        if not isinstance(data, dict):
            data = {}
        unlocked_raw = data.get("unlocked") if isinstance(data.get("unlocked"), dict) else data
        unlocked: dict[str, float] = {}
        for key, value in unlocked_raw.items():
            try:
                unlocked[str(key)] = float(value)
            except (TypeError, ValueError):
                continue
        return unlocked

    def save_achievements(self, unlocked: dict[str, float]) -> None:
        self._write_json(self.achievements_file, {"unlocked": dict(unlocked)})

    # ------------------------------------------------------------------
    # 书籍
    # ------------------------------------------------------------------
    def load_book(self, book_id: str) -> Book | None:
        data = self._read_json(self._book_path(book_id), None)
        if not isinstance(data, dict):
            return None
        try:
            return Book.from_dict(data)
        except (TypeError, ValueError, KeyError):
            return None

    def save_book(self, book: Book, word_count: int | None = None) -> None:
        """保存整本书，并同步书架索引中的摘要信息。"""
        self._write_json(self._book_path(book.id), book.to_dict())
        self.sync_book_meta(book, word_count)

    def sync_book_meta(self, book: Book, word_count: int | None = None) -> None:
        library = self.load_library()
        if word_count is None:
            from app.services.word_count import count_chars  # 延迟导入，避免循环依赖

            word_count = sum(count_chars(ch.content, False) for ch in book.all_chapters())
        meta = book.meta(word_count)
        books = library["books"]
        replaced = False
        for i, item in enumerate(books):
            if item.get("id") == book.id:
                books[i] = meta
                replaced = True
                break
        if not replaced:
            books.append(meta)
        if book.last_chapter_id and not book.trashed:
            library["last_book_id"] = book.id
        self.save_library(library)

    def trash_book(self, book_id: str) -> None:
        """把书籍移入回收站（不删除文件）。"""
        book = self.load_book(book_id)
        if book is not None:
            book.trashed = True
            book.touch()
            self.save_book(book, None)
            return
        library = self.load_library()
        for meta in library["books"]:
            if meta.get("id") == book_id:
                meta["trashed"] = True
        if library.get("last_book_id") == book_id:
            library["last_book_id"] = ""
        self.save_library(library)

    def restore_book(self, book_id: str) -> None:
        """从回收站恢复书籍。"""
        book = self.load_book(book_id)
        if book is not None:
            book.trashed = False
            book.touch()
            self.save_book(book, None)
            return
        library = self.load_library()
        for meta in library["books"]:
            if meta.get("id") == book_id:
                meta["trashed"] = False
        self.save_library(library)

    def empty_trash(self) -> int:
        """清空回收站：彻底删除所有被标记为 trashed 的书籍。返回删除数量。"""
        library = self.load_library()
        trashed_ids = [m.get("id") for m in library["books"] if m.get("trashed")]
        for book_id in trashed_ids:
            if book_id:
                self.delete_book(str(book_id))
        return len(trashed_ids)

    def delete_book(self, book_id: str) -> None:
        """彻底删除书籍（正文、封面、历史快照一并移除）。"""
        path = self._book_path(book_id)
        if path.exists():
            path.unlink()
        history = self.history_dir / book_id
        if history.exists():
            shutil.rmtree(history, ignore_errors=True)
        # v3：封面可能是旧式 <bid>.<ext> 或新式 <bid>_<hash>.<ext>
        for cover in self._iter_book_covers(book_id):
            try:
                cover.unlink()
            except OSError:
                pass

        library = self.load_library()
        library["books"] = [b for b in library["books"] if b.get("id") != book_id]
        if library.get("last_book_id") == book_id:
            library["last_book_id"] = ""
        self.save_library(library)

    # ------------------------------------------------------------------
    # 封面图片
    # ------------------------------------------------------------------
    COVER_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
    COVER_MAX_DIM = 4000          # 任务G：仅超大图才等比收边，其余原图原样落盘

    def _iter_book_covers(self, book_id: str):
        """枚举某本书的全部封面（旧式 <bid>.<ext> 与新式 <bid>_<hash>.<ext>）。"""
        for f in self.covers_dir.iterdir():
            if f.is_file() and (f.stem == book_id or f.stem.startswith(book_id + "_")):
                yield f

    @staticmethod
    def _hash10(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()[:10]

    def _write_cover_bytes(self, book_id: str, data: bytes, suffix: str) -> str:
        """按 <bookid>_<hash10>.<ext> 原子落盘并返回相对路径；清掉同书旧封面。"""
        name = f"{book_id}_{self._hash10(data)}{suffix}"
        target = self.covers_dir / name
        if not target.exists():
            tmp = target.with_name(name + ".tmp")
            tmp.write_bytes(data)
            os.replace(tmp, target)
        for f in self._iter_book_covers(book_id):
            if f != target:
                try:
                    f.unlink()
                except OSError:
                    pass
        return f"covers/{name}"

    def _normalize_cover_bytes(self, src: Path, suffix: str) -> tuple[bytes, str, dict | None]:
        """格式规范：仅超大图（任一边 >4000px）才等比收边重编码，其余原样返回。"""
        from PySide6.QtCore import QBuffer, QByteArray, Qt
        from PySide6.QtGui import QImageReader

        reader = QImageReader(str(src))
        reader.setAutoTransform(True)          # 尊重 EXIF 方向
        size = reader.size()
        orig_w, orig_h = size.width(), size.height()
        if orig_w <= 0 or orig_h <= 0 or max(orig_w, orig_h) <= self.COVER_MAX_DIM:
            return src.read_bytes(), suffix, None
        img = reader.read()
        if img.isNull():
            return src.read_bytes(), suffix, None
        # 长边精确收到 4000，短边四舍五入（避免浮点截断导致长边 3998/3999）
        if orig_w >= orig_h:
            new_w, new_h = self.COVER_MAX_DIM, max(1, round(self.COVER_MAX_DIM * orig_h / orig_w))
        else:
            new_h, new_w = self.COVER_MAX_DIM, max(1, round(self.COVER_MAX_DIM * orig_w / orig_h))
        img = img.scaled(new_w, new_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        ba = QByteArray()
        bufq = QBuffer(ba)
        bufq.open(QBuffer.WriteOnly)
        if suffix in (".jpg", ".jpeg"):
            ok, out_suffix = img.save(bufq, "JPEG", 92), ".jpg"
        else:
            ok, out_suffix = img.save(bufq, "PNG"), ".png"
        bufq.close()
        if not ok or ba.isEmpty():
            return src.read_bytes(), suffix, None
        event = {"src": src.name, "orig": [orig_w, orig_h], "norm": [new_w, new_h],
                 "ext": out_suffix, "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
        return bytes(ba.data()), out_suffix, event

    def _append_cover_log(self, line: str) -> None:
        try:
            with (self.covers_dir / "normalize.log").open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass

    def save_cover_image(self, source: str | Path, book_id: str) -> str:
        """用户封面原图落 covers\\（不降采样不重压缩）；仅 >4000px 等比收边并记录。"""
        src = Path(source)
        if not src.exists():
            return ""
        suffix = src.suffix.lower() or ".png"
        if suffix not in self.COVER_EXTS:
            suffix = ".png"
        data, out_suffix, event = self._normalize_cover_bytes(src, suffix)
        if event is not None:
            self.last_cover_event = event
            self._append_cover_log(
                f"{event['ts']} 规范超大图 {event['src']} "
                f"{event['orig'][0]}x{event['orig'][1]} -> "
                f"{event['norm'][0]}x{event['norm'][1]} {event['ext']}"
            )
        return self._write_cover_bytes(book_id, data, out_suffix)

    _DATA_URI_MIME = {"image/png": ".png", "image/jpeg": ".jpg", "image/jpg": ".jpg",
                      "image/webp": ".webp", "image/bmp": ".bmp"}

    def _save_data_uri_cover(self, raw: str, book_id: str) -> str:
        """历史内嵌 data:image/...;base64 封面解码外置，返回相对路径。"""
        try:
            head, b64 = raw.split(",", 1)
            mime = head.split(";", 1)[0].removeprefix("data:").strip().lower()
            suffix = self._DATA_URI_MIME.get(mime, ".png")
            data = base64.b64decode(b64, validate=True)
        except (ValueError, TypeError):
            return ""
        return self._write_cover_bytes(book_id, data, suffix) if data else ""

    def materialize_covers(self, library: dict) -> dict:
        """懒迁移（幂等）：data-URI 外置 + 旧式无哈希命名补哈希；有改动回写索引。"""
        stats = {"data_uri": 0, "legacy_renamed": 0}
        changed = False
        for meta in library.get("books") or []:
            raw = str(meta.get("cover") or "")
            bid = str(meta.get("id") or "")
            if not raw or not bid:
                continue
            if raw.startswith("data:"):
                rel = self._save_data_uri_cover(raw, bid)
                if rel:
                    meta["cover"] = rel
                    stats["data_uri"] += 1
                    changed = True
                continue
            parts = Path(raw.replace("\\", "/"))
            if parts.parent.as_posix() != "covers" or "_" in parts.name:
                continue
            absf = self.base_dir / parts
            if not absf.is_file():
                continue       # 引用缺失：绘制端回退默认封面，字段保留不动
            try:
                data = absf.read_bytes()
            except OSError:
                continue
            meta["cover"] = self._write_cover_bytes(
                bid, data, absf.suffix.lower() or ".png")
            stats["legacy_renamed"] += 1
            changed = True
        if changed:
            self.save_library(library)
        return stats

    def cleanup_orphan_covers(self, library: dict) -> dict:
        """删除 covers\\ 下不被任何书籍引用的图片，返回条数与释放字节。"""
        referenced = {
            Path(str(m.get("cover") or "").replace("\\", "/")).name
            for m in (library.get("books") or [])
            if str(m.get("cover") or "").replace("\\", "/").startswith("covers/")
        }
        removed, freed = 0, 0
        for f in self.covers_dir.iterdir():
            if not f.is_file() or f.name == "normalize.log":
                continue
            if f.suffix.lower() not in self.COVER_EXTS or f.name in referenced:
                continue
            try:
                size = f.stat().st_size
                f.unlink()
                removed += 1
                freed += size
            except OSError:
                pass
        return {"removed": removed, "freed_bytes": freed}

    def housekeep_covers_once(self, force: bool = False) -> dict:
        """启动 / 云恢复后调用：懒迁移 + 孤儿扫描（每实例仅一次，force 可重跑）。

        稳态快速路径：每次启动都进这里，绝大多数库没有 data:URI、没有旧式命名、
        没有孤儿；此时只做一次 iterdir + 书名字符串比对（不 stat、不读内容、
        不写盘），确认干净即返回。只有发现待处理项才落到完整的迁移 / 清理流程。
        """
        if self._covers_housekept and not force:
            return {"skipped": True}
        self._covers_housekept = True
        try:
            library = self.load_library()
            books = library.get("books") or []
            covers_dir = self.covers_dir
            files = list(covers_dir.iterdir()) if covers_dir.is_dir() else []

            need_migrate = any(
                isinstance(m.get("cover"), str) and m["cover"].startswith("data:")
                for m in books)
            referenced = {
                Path(str(m["cover"]).replace("\\", "/")).name
                for m in books
                if str(m.get("cover") or "").startswith("covers/")
            }
            images = [f for f in files
                      if f.is_file() and f.suffix.lower() in self.COVER_EXTS]
            orphan = [f for f in images if f.name not in referenced]
            # 孤儿里若是旧式 <bid>.<ext>（无哈希段），需要走迁移重命名；
            # 其余非图片（normalize.log / 子目录）清理流程本就跳过，不影响稳态判定
            book_ids = {str(m.get("id")) for m in books}
            need_legacy = any("_" not in f.stem and f.stem in book_ids for f in orphan)

            if not need_migrate and not orphan:
                return {"data_uri": 0, "legacy_renamed": 0,
                        "removed": 0, "freed_bytes": 0, "fast": True}

            migrated = {"data_uri": 0, "legacy_renamed": 0}
            if need_migrate or need_legacy:
                migrated = self.materialize_covers(library)
                if migrated["data_uri"] or migrated["legacy_renamed"]:
                    library = self.load_library()
            return {**migrated, **self.cleanup_orphan_covers(library)}
        except OSError:
            return {"error": True}

    def cover_abs(self, relative: str) -> Path | None:
        """封面相对路径转绝对路径；不存在返回 None（绘制端回退默认封面）。"""
        if not relative:
            return None
        path = self.base_dir / relative
        return path if path.exists() else None

    def save_avatar(self, source: str | Path) -> str:
        """保存用户头像到数据目录。"""
        src = Path(source)
        if not src.exists():
            return ""
        suffix = src.suffix.lower() if src.suffix else ".png"
        target = self.base_dir / f"avatar{suffix}"
        try:
            shutil.copyfile(src, target)
        except OSError:
            return ""
        return str(target)

    # ------------------------------------------------------------------
    # 历史快照
    # ------------------------------------------------------------------
    def _snapshot_dir(self, book_id: str, chapter_id: str) -> Path:
        return self.history_dir / book_id / chapter_id

    def save_snapshot(
        self,
        book_id: str,
        chapter_id: str,
        content: str,
        chars: int = 0,
        cap: int | None = None,
    ) -> None:
        """保存一个历史快照，并按「每章份数 + 单本书总字节」双上限淘汰最旧。"""
        folder = self._snapshot_dir(book_id, chapter_id)
        folder.mkdir(parents=True, exist_ok=True)
        ts = time.time()
        name = f"{time.time_ns() // 1_000_000:013d}.json"
        self._write_json(
            folder / name,
            {"time": ts, "content": content, "chars": chars, "id": new_id()},
        )
        self.trim_snapshots(
            book_id,
            cap if cap is not None else config.MAX_SNAPSHOTS,
            config.HISTORY_MAX_BYTES,
        )

    def trim_snapshots(self, book_id: str, cap: int, max_bytes: int | None = None) -> None:
        """清理历史快照：每章只保留最近 cap 个；整本书总字节超限时按最旧淘汰。"""
        root = self.history_dir / book_id
        if not root.exists():
            return
        limit = max(1, int(cap))
        for folder in root.iterdir():
            if not folder.is_dir():
                continue
            files = sorted(folder.glob("*.json"), reverse=True)
            for old in files[limit:]:
                try:
                    old.unlink()
                except OSError:
                    pass
        if max_bytes is None or int(max_bytes) <= 0:
            return
        limit_bytes = int(max_bytes)
        running = 0
        for path in sorted(root.glob("*/*.json"), reverse=True):
            try:
                size = path.stat().st_size
            except OSError:
                continue
            running += size
            if running <= limit_bytes:
                continue
            try:
                path.unlink()
            except OSError:
                continue
            running -= size

    def list_snapshots(self, book_id: str, chapter_id: str) -> list[dict]:
        """按时间倒序返回快照列表。"""
        folder = self._snapshot_dir(book_id, chapter_id)
        if not folder.exists():
            return []
        result: list[dict] = []
        for path in sorted(folder.glob("*.json"), reverse=True):
            data = self._read_json(path, None)
            if isinstance(data, dict):
                data["_path"] = str(path)
                data["chars"] = int(data.get("chars") or 0)
                result.append(data)
        return result

    def delete_snapshots(self, book_id: str, chapter_id: str) -> None:
        """删除某章全部历史快照（删除章节时调用）。"""
        folder = self._snapshot_dir(book_id, chapter_id)
        if folder.exists():
            shutil.rmtree(folder, ignore_errors=True)

    @staticmethod
    def snapshot_content(snapshot: dict) -> str:
        return str(snapshot.get("content") or "")

    def snapshot_time_text(self, snapshot: dict) -> str:
        ts = float(snapshot.get("time") or 0)
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
