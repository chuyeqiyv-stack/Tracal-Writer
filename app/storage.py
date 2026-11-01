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

import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

from app import config
from app.models import Book, Settings, new_id


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
        self.books_dir = self.base_dir / "books"
        self.covers_dir = self.base_dir / "covers"
        self.history_dir = self.base_dir / "history"
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
        """读取 JSON，文件不存在或损坏时返回默认值。"""
        try:
            if not path.exists():
                return default
            with path.open("r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError, json.JSONDecodeError):
            return default

    def _write_json(self, path: Path, data: Any) -> None:
        """原子写入 JSON，避免中途退出导致文件损坏。"""
        path.parent.mkdir(parents=True, exist_ok=True)
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
        return Settings.from_dict(self._read_json(self.settings_file, {}))

    def save_settings(self, settings: Settings) -> None:
        self._write_json(self.settings_file, settings.to_dict())

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
                    return TimelineItem.from_dict(item).to_dict()
                except (TypeError, ValueError):
                    return None
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
    # 世界地图（可创建多张，统一存为 map_global.json 的 {"maps": [...]}）
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
        return {
            "id": str(raw.get("id") or new_id()),
            "title": str(raw.get("title") or "未命名地图"),
            "created_at": float(raw.get("created_at") or time.time()),
            "updated_at": float(raw.get("updated_at") or time.time()),
            "bg": raw.get("bg") if isinstance(raw.get("bg"), dict) else None,
            "factions": [f for f in (raw.get("factions") or []) if isinstance(f, dict)],
            "pins": [p for p in (raw.get("pins") or []) if isinstance(p, dict)],
            "scene": {"left": left, "top": top, "width": width, "height": height},
        }

    def load_maps(self) -> list[dict]:
        """读取全部世界地图；旧版单图结构自动迁移为一张「世界地图」。"""
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
            migrated = self._normalize_map({"id": new_id(), "title": "世界地图", **data})
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
        """新建一张空白世界地图。"""
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
        for cover in self.covers_dir.glob(f"{book_id}.*"):
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
    def save_cover_image(self, source: str | Path, book_id: str) -> str:
        """把用户选择的封面复制到数据目录，返回相对路径。"""
        src = Path(source)
        if not src.exists():
            return ""
        suffix = src.suffix.lower() or ".png"
        if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
            suffix = ".png"
        for old in self.covers_dir.glob(f"{book_id}.*"):
            try:
                old.unlink()
            except OSError:
                pass
        target = self.covers_dir / f"{book_id}{suffix}"
        shutil.copyfile(src, target)
        return f"covers/{target.name}"

    def cover_abs(self, relative: str) -> Path | None:
        """把封面相对路径转换为绝对路径；文件不存在时返回 None。"""
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
        """保存一个历史快照，并把整本书的历史版本裁剪到上限（默认 config.MAX_SNAPSHOTS）。"""
        folder = self._snapshot_dir(book_id, chapter_id)
        folder.mkdir(parents=True, exist_ok=True)
        ts = time.time()
        name = f"{time.time_ns() // 1_000_000:013d}.json"
        self._write_json(
            folder / name,
            {"time": ts, "content": content, "chars": chars, "id": new_id()},
        )
        self.trim_snapshots(book_id, cap if cap is not None else config.MAX_SNAPSHOTS)

    def trim_snapshots(self, book_id: str, cap: int) -> None:
        """把一本书全部章节的历史快照裁剪到最近 cap 个（按时间从旧到新删除）。"""
        root = self.history_dir / book_id
        if not root.exists():
            return
        files = sorted(root.glob("*/*.json"))
        limit = max(0, int(cap))
        excess = len(files) - limit
        for old in files[: max(0, excess)]:
            try:
                old.unlink()
            except OSError:
                pass

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

    def latest_snapshot(self, book_id: str, chapter_id: str) -> dict | None:
        snapshots = self.list_snapshots(book_id, chapter_id)
        return snapshots[0] if snapshots else None

    @staticmethod
    def snapshot_content(snapshot: dict) -> str:
        return str(snapshot.get("content") or "")

    def snapshot_time_text(self, snapshot: dict) -> str:
        ts = float(snapshot.get("time") or 0)
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
