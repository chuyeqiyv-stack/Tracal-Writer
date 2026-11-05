# -*- coding: utf-8 -*-
"""新建角色模板：把「建角色时填的那批标签类字段」存成快照，全局共享一份。

- 文件：`character_templates.json`（与 snippets.json 同目录，走 Storage 原子写）；
- 上限 5 个，满了要显式覆盖或取消，不静默丢弃；
- 默认模板 = 最近一次「保存为模板」或「用某模板新建成功」的那个；
- 容错：坏 JSON / 字段缺失 / 超量 / 空名 / 超长名都不崩，也不影响已有角色。
"""
from __future__ import annotations

import time

from app.models import new_id

MAX_TEMPLATES = 5
MAX_NAME_LEN = 40
# 模板只存「标签类」字段；姓名/道号/生平/目标/备注/关系/坐标等一律不进模板
SNAPSHOT_KEYS = (
    "role",
    "gender",
    "age",
    "appearance",
    "personality",
    "speech",
    "tags",
    "card_fields",
)
TEXT_KEYS = ("role", "gender", "age", "appearance", "personality", "speech")


class TemplateLimitError(Exception):
    """模板已满且调用方没有指定要覆盖的旧模板。"""


def _clean_text(value: object, limit: int = 200) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return text[:limit]


def snapshot_from_values(values: dict) -> dict:
    """从角色 values（CharacterDialog.values()）截取可持久化的标签类字段。"""
    fields: dict = {}
    for key in TEXT_KEYS:
        fields[key] = _clean_text(values.get(key))
    tags_raw = values.get("tags")
    tags: list[str] = []
    if isinstance(tags_raw, list):
        for item in tags_raw:
            text = _clean_text(item, 24)
            if text and text not in tags:
                tags.append(text)
    fields["tags"] = tags[:12]
    fields["card_fields"] = _sanitize_card_fields(values.get("card_fields"))
    return fields


def _sanitize_card_fields(raw: object) -> list[dict]:
    """方框属性容错：只留 label 非空的项，值转字符串。"""
    out: list[dict] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if not isinstance(item, dict):
            continue
        label = _clean_text(item.get("label"), 20)
        if not label:
            continue
        out.append({"label": label, "value": _clean_text(item.get("value"), 200)})
    return out[:24]


def sanitize_template(raw: object) -> dict | None:
    """单条模板容错归一化；彻底无意义的数据返回 None（不写入、不报错）。"""
    if not isinstance(raw, dict):
        return None
    name = _clean_text(raw.get("name"), MAX_NAME_LEN) or "未命名模板"
    fields_raw = raw.get("fields")
    fields = snapshot_from_values(fields_raw if isinstance(fields_raw, dict) else {})
    tid = _clean_text(raw.get("id"), 40) or new_id()
    now = time.time()
    try:
        created = float(raw.get("created_at") or now)
    except (TypeError, ValueError):
        created = now
    try:
        updated = float(raw.get("updated_at") or created)
    except (TypeError, ValueError):
        updated = created
    return {"id": tid, "name": name, "fields": fields, "created_at": created, "updated_at": updated}


def sanitize_document(raw: object) -> dict:
    """整份文件容错归一化：最多 5 个、id 去重、default 不存在则清空。"""
    data = raw if isinstance(raw, dict) else {}
    templates: list[dict] = []
    seen: set[str] = set()
    for item in data.get("templates") or []:
        tpl = sanitize_template(item)
        if tpl is None:
            continue
        if tpl["id"] in seen:
            tpl["id"] = new_id()
        seen.add(tpl["id"])
        templates.append(tpl)
        if len(templates) >= MAX_TEMPLATES:
            break
    default_id = _clean_text(data.get("default_id"), 40)
    if default_id and default_id not in seen:
        default_id = ""
    return {"version": 1, "default_id": default_id, "templates": templates}


class CharacterTemplateStore:
    """读写模板文件；所有写入都走 Storage._write_json（原子写 + .bak）。"""

    def __init__(self, storage) -> None:
        self.storage = storage

    # ---------- 读 ----------
    def document(self) -> dict:
        raw = self.storage._read_json(self.storage.character_templates_file, {})
        return sanitize_document(raw)

    def templates(self) -> list[dict]:
        return self.document()["templates"]

    def get(self, tid: str) -> dict | None:
        for tpl in self.templates():
            if tpl["id"] == tid:
                return tpl
        return None

    def default_id(self) -> str:
        return self.document()["default_id"]

    def default_template(self) -> dict | None:
        return self.get(self.default_id())

    def find_by_name(self, name: str) -> dict | None:
        target = _clean_text(name, MAX_NAME_LEN)
        if not target:
            return None
        for tpl in self.templates():
            if tpl["name"] == target:
                return tpl
        return None

    def oldest(self) -> dict | None:
        items = self.templates()
        return min(items, key=lambda t: t["updated_at"]) if items else None

    # ---------- 写 ----------
    def _save(self, doc: dict, mark_used_id: str = "") -> None:
        doc = sanitize_document(doc)
        if mark_used_id and any(t["id"] == mark_used_id for t in doc["templates"]):
            doc["default_id"] = mark_used_id
        self.storage.save_character_templates(doc)

    def save_template(self, name: str, values: dict, overwrite_id: str = "") -> dict:
        """保存为新模板；重名返回已有模板（由调用方确认是否覆盖）。"""
        clean_name = _clean_text(name, MAX_NAME_LEN) or "未命名模板"
        doc = self.document()
        now = time.time()
        fields = snapshot_from_values(values if isinstance(values, dict) else {})
        if overwrite_id:
            for tpl in doc["templates"]:
                if tpl["id"] == overwrite_id:
                    tpl["name"] = clean_name
                    tpl["fields"] = fields
                    tpl["updated_at"] = now
                    self._save(doc, mark_used_id=overwrite_id)
                    return tpl
        same = next((t for t in doc["templates"] if t["name"] == clean_name), None)
        if same is not None:
            same["fields"] = fields
            same["updated_at"] = now
            self._save(doc, mark_used_id=same["id"])
            return same
        if len(doc["templates"]) >= MAX_TEMPLATES:
            raise TemplateLimitError(f"最多只能保存 {MAX_TEMPLATES} 个模板")
        tpl = {
            "id": new_id(),
            "name": clean_name,
            "fields": fields,
            "created_at": now,
            "updated_at": now,
        }
        doc["templates"].append(tpl)
        self._save(doc, mark_used_id=tpl["id"])
        return tpl

    def rename(self, tid: str, new_name: str, overwrite: bool = False) -> bool:
        clean = _clean_text(new_name, MAX_NAME_LEN)
        if not clean:
            return False
        doc = self.document()
        tpl = next((t for t in doc["templates"] if t["id"] == tid), None)
        if tpl is None:
            return False
        clash = next((t for t in doc["templates"] if t["name"] == clean and t["id"] != tid), None)
        if clash is not None:
            if not overwrite:
                return False
            doc["templates"] = [t for t in doc["templates"] if t["id"] != clash["id"]]
            if doc.get("default_id") == clash["id"]:
                doc["default_id"] = tid
        tpl["name"] = clean
        tpl["updated_at"] = time.time()
        self._save(doc, mark_used_id=tid)
        return True

    def delete(self, tid: str) -> bool:
        doc = self.document()
        before = len(doc["templates"])
        doc["templates"] = [t for t in doc["templates"] if t["id"] != tid]
        if len(doc["templates"]) == before:
            return False
        if doc.get("default_id") == tid:
            doc["default_id"] = ""
        self._save(doc)
        return True

    def mark_used(self, tid: str) -> None:
        self._save(self.document(), mark_used_id=tid)
