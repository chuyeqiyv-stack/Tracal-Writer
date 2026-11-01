# -*- coding: utf-8 -*-
"""数据模型：书籍、卷、章节、相关设定以及用户设置。

所有模型都可以和 JSON 相互转换，方便本地持久化。
"""
from __future__ import annotations

import random
import time
import uuid
from dataclasses import dataclass, field, fields, asdict

from app.config import DEFAULT_FONT_FAMILY


def new_id() -> str:
    """生成本地唯一 ID。"""
    return uuid.uuid4().hex[:12]


_CN_DIGITS = "零一二三四五六七八九"


def to_cn_number(n: int) -> str:
    """把 1~99 的整数转成中文数字，用于“第一章”“第二卷”这样的标题。"""
    if n <= 0:
        return str(n)
    if n < 10:
        return _CN_DIGITS[n]
    if n == 10:
        return "十"
    if n < 20:
        return "十" + _CN_DIGITS[n % 10]
    if n < 100:
        tens, ones = divmod(n, 10)
        return _CN_DIGITS[tens] + "十" + (_CN_DIGITS[ones] if ones else "")
    return str(n)


def _clean_color(value) -> str:
    """校验 #RRGGBB 颜色，非法值时返回空字符串（表示跟随主题）。"""
    text = str(value or "").strip()
    if text.startswith("#") and len(text) == 7:
        try:
            int(text[1:], 16)
            return text.upper()
        except ValueError:
            return ""
    return ""


def _string_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


@dataclass
class Settings:
    """用户设置（全局，保存在 settings.json）。"""

    # 外观
    mode: str = "light"                 # light 浅色 / dark 深色
    accent_color: str = ""              # 品牌主色调（空 = 跟随默认主题紫）
    toolbar_bg: str = ""                # 空 = 跟随模式
    toolbar_text: str = ""
    statusbar_bg: str = ""
    statusbar_text: str = ""
    editor_bg: str = ""                 # 手动覆盖写作区背景
    editor_text: str = ""
    writing_theme: str = "auto"         # auto / white / green / parchment / starry / black / custom
    writing_image: str = ""             # 自定义写作区背景图片（绝对路径）

    # 排版
    indent: str = "two"                 # two 两个字符缩进 / none 无缩进
    blank_line: str = "blank"           # blank 段间空一行 / none 无空行
    clean_spaces: bool = True           # 清除多余空格
    unify_punct: bool = True            # 半角标点转全角（中文段落）
    ellipsis_fix: bool = True           # 规范省略号（。。。→……）与破折号（---→——）

    # 输入
    auto_pair: bool = True              # 成对标点自动补全

    # 字数
    exclude_punct: bool = False         # 字数统计不计标点
    speed_unit: str = "per_min"         # 码字速度单位：per_min 字/分 / per_hour 字/时

    # 操作
    fast_scroll: bool = True
    float_jump: bool = True
    open_at_end: bool = True

    # 字体
    font_family: str = DEFAULT_FONT_FAMILY   # 中文字体（空 = 默认字体）
    font_latin: str = "Segoe UI"             # 英文字体
    font_size: float = 16.0
    font_bold: bool = False                  # 加粗
    font_italic: bool = False                # 斜体
    font_underline: bool = False             # 下划线
    line_spacing: float = 1.8
    imported_fonts: list[str] = field(default_factory=list)

    # 书架
    shelf_view: str = "grid"            # grid / list
    nickname: str = "写作者"
    avatar_path: str = ""
    showcase_medal: str = ""            # 首页左下角展示的成就勋章（成就 id，空 = 不展示；成就页右键更换）

    # 坚果云 WebDAV（纯手动触发）
    webdav_url: str = "https://dav.jianguoyun.com/dav/"
    webdav_user: str = ""
    webdav_password: str = ""
    webdav_remote: str = "moji_backup.zip"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict | None) -> "Settings":
        """容错地从字典恢复设置，字段缺失或类型不符时使用默认值。"""
        result = cls()
        if not isinstance(data, dict):
            return result

        for f in fields(cls):
            if f.name not in data or data[f.name] is None:
                continue
            value = data[f.name]
            default = getattr(result, f.name)
            try:
                if isinstance(default, bool):
                    value = bool(value)
                elif isinstance(default, int):
                    value = int(value)
                elif isinstance(default, float):
                    value = float(value)
                elif isinstance(default, list):
                    value = _string_list(value)
                else:
                    value = str(value)
            except (TypeError, ValueError):
                continue
            setattr(result, f.name, value)

        if result.mode not in {"light", "dark"}:
            result.mode = "light"
        # writing_theme 的合法键集合由 theme.WRITING_THEMES 决定，
        # 这里不做严格校验，未知键在使用处会自动回退为跟随主题。
        if not result.writing_theme:
            result.writing_theme = "auto"
        if result.shelf_view not in {"grid", "list"}:
            result.shelf_view = "grid"
        if result.indent not in {"two", "none"}:
            result.indent = "two"
        if result.blank_line not in {"blank", "none"}:
            result.blank_line = "blank"
        for name in (
            "toolbar_bg", "toolbar_text", "statusbar_bg", "statusbar_text",
            "editor_bg", "editor_text",
        ):
            setattr(result, name, _clean_color(getattr(result, name)))
        result.font_size = max(5.0, min(72.0, float(result.font_size)))
        result.line_spacing = max(1.0, min(3.5, result.line_spacing))
        if not result.nickname.strip():
            result.nickname = "写作者"
        return result


@dataclass
class Chapter:
    """章节。正文以纯文本形式保存。"""

    id: str
    title: str
    content: str = ""
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "content": self.content,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Chapter":
        return cls(
            id=str(data.get("id") or new_id()),
            title=str(data.get("title") or "未命名章节"),
            content=str(data.get("content") or ""),
            updated_at=float(data.get("updated_at") or time.time()),
        )


@dataclass
class Volume:
    """卷，包含若干章节。"""

    id: str
    title: str
    chapters: list[Chapter] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "chapters": [c.to_dict() for c in self.chapters],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Volume":
        return cls(
            id=str(data.get("id") or new_id()),
            title=str(data.get("title") or "未命名卷"),
            chapters=[Chapter.from_dict(c) for c in data.get("chapters", []) if isinstance(c, dict)],
        )


@dataclass
class RelatedItem:
    """“相关”页签里的设定条目（角色 / 地点 / 物品 / 设定）。"""

    id: str
    kind: str
    name: str
    note: str = ""

    def to_dict(self) -> dict:
        return {"id": self.id, "kind": self.kind, "name": self.name, "note": self.note}

    @classmethod
    def from_dict(cls, data: dict) -> "RelatedItem":
        return cls(
            id=str(data.get("id") or new_id()),
            kind=str(data.get("kind") or "角色"),
            name=str(data.get("name") or ""),
            note=str(data.get("note") or ""),
        )


@dataclass
class TimelineEvent:
    """时间轴上的一个事件节点。

    x 为节点在横线上的自由位置（像素，可任意点击放置，不分年月刻度）；
    time 显示在横线上方，event 显示在横线下方。
    """

    id: str
    x: float = 0.0
    time: str = ""
    event: str = ""
    desc: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "x": self.x,
            "time": self.time,
            "event": self.event,
            "desc": self.desc,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TimelineEvent":
        # 兼容旧版书籍内置时间轴的字段名（title / below）
        title = str(data.get("event") or data.get("title") or "")
        return cls(
            id=str(data.get("id") or new_id()),
            x=float(data.get("x") or 0),
            time=str(data.get("time") or ""),
            event=title,
            desc=str(data.get("desc") or ""),
        )


@dataclass
class TimelineItem:
    """独立的故事时间轴（可创建多个，保存在 library.json 中）。"""

    id: str
    title: str
    events: list[TimelineEvent] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    # 画布尺寸 / 0 点位置（用户扩容后必须持久化，否则重开丢失）
    canvas_width: int = 60000
    canvas_origin: float = 30000.0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "events": [e.to_dict() for e in self.events],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "canvas_width": int(self.canvas_width),
            "canvas_origin": float(self.canvas_origin),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TimelineItem":
        return cls(
            id=str(data.get("id") or new_id()),
            title=str(data.get("title") or "未命名时间轴"),
            events=[
                TimelineEvent.from_dict(e)
                for e in data.get("events", [])
                if isinstance(e, dict)
            ],
            created_at=float(data.get("created_at") or time.time()),
            updated_at=float(data.get("updated_at") or time.time()),
            canvas_width=max(60000, int(float(data.get("canvas_width") or 60000))),
            canvas_origin=max(
                0.0, min(float(data.get("canvas_origin") or 30000.0), float(max(60000, int(float(data.get("canvas_width") or 60000)))))
            ),
        )

    def touch(self) -> None:
        self.updated_at = time.time()


@dataclass
class OutlineNode:
    """思维导图上的一个卡片节点。

    x / y 为卡片左上角在画布上的坐标（像素）；
    text 为卡片文字，color 为卡片底色（#RRGGBB）。
    """

    id: str
    x: float = 0.0
    y: float = 0.0
    text: str = ""
    color: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "x": self.x,
            "y": self.y,
            "text": self.text,
            "color": self.color,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "OutlineNode":
        color = str(data.get("color") or "")
        if not (color.startswith("#") and len(color) == 7):
            color = ""
        return cls(
            id=str(data.get("id") or new_id()),
            x=float(data.get("x") or 0),
            y=float(data.get("y") or 0),
            text=str(data.get("text") or ""),
            color=color,
        )


@dataclass
class OutlineItem:
    """独立的大纲（思维导图，可创建多个，保存在 library.json 中）。"""

    id: str
    title: str
    nodes: list[OutlineNode] = field(default_factory=list)
    edges: list[dict] = field(default_factory=list)   # {"from": id, "to": id}
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    # 画布尺寸（用户向四方扩容后必须持久化，否则重开丢失）
    canvas_width: int = 4800
    canvas_height: int = 3000
    # 连线风格：ortho 直角折线 / curve 平滑曲线
    edge_style: str = "ortho"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [dict(e) for e in self.edges],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "canvas_width": int(self.canvas_width),
            "canvas_height": int(self.canvas_height),
            "edge_style": self.edge_style,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "OutlineItem":
        # 连线风格逐条独立保存（直线/曲线），读取时必须原样保留，
        # 否则重开大纲后所有连线都会退回默认风格（互相覆盖的 bug）
        edges = []
        for e in data.get("edges", []):
            if not (isinstance(e, dict) and e.get("from") and e.get("to")):
                continue
            edge = {"from": str(e.get("from") or ""), "to": str(e.get("to") or "")}
            style = str(e.get("style") or "")
            if style in {"ortho", "curve"}:
                edge["style"] = style
            edges.append(edge)
        edge_style = str(data.get("edge_style") or "ortho")
        if edge_style not in {"ortho", "curve"}:
            edge_style = "ortho"
        return cls(
            id=str(data.get("id") or new_id()),
            title=str(data.get("title") or "未命名大纲"),
            nodes=[
                OutlineNode.from_dict(n) for n in data.get("nodes", []) if isinstance(n, dict)
            ],
            edges=edges,
            created_at=float(data.get("created_at") or time.time()),
            updated_at=float(data.get("updated_at") or time.time()),
            canvas_width=max(4800, int(float(data.get("canvas_width") or 4800))),
            canvas_height=max(3000, int(float(data.get("canvas_height") or 3000))),
            edge_style=edge_style,
        )

    def touch(self) -> None:
        self.updated_at = time.time()


@dataclass
class SettingDoc:
    """独立的世界观 / 设定文档（可创建多个，保存在 library.json 中）。"""

    id: str
    title: str
    content: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "content": self.content,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SettingDoc":
        return cls(
            id=str(data.get("id") or new_id()),
            title=str(data.get("title") or "未命名设定"),
            content=str(data.get("content") or ""),
            created_at=float(data.get("created_at") or time.time()),
            updated_at=float(data.get("updated_at") or time.time()),
        )

    def touch(self) -> None:
        self.updated_at = time.time()


@dataclass
class Book:
    """一本书，包含卷、章节、相关设定。"""

    id: str
    title: str
    author: str = ""
    cover: str = ""                 # 自定义封面图片的相对路径（空 = 使用默认底纹）
    cover_index: int = 0            # 默认底纹 / 渐变的索引
    tags: list[str] = field(default_factory=list)
    group_id: str = ""
    trashed: bool = False
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    volumes: list[Volume] = field(default_factory=list)
    relations: list[RelatedItem] = field(default_factory=list)
    last_chapter_id: str = ""

    # 故事时间轴（数据持久化在书籍 JSON 内）
    timeline_interval: str = "day"  # day / month / year
    timeline_events: list[dict] = field(default_factory=list)

    # ---------------- 查询 ----------------

    def all_chapters(self) -> list[Chapter]:
        """按顺序返回全书所有章节。"""
        return [ch for vol in self.volumes for ch in vol.chapters]

    def chapter_count(self) -> int:
        return len(self.all_chapters())

    def find_volume(self, volume_id: str) -> Volume | None:
        for vol in self.volumes:
            if vol.id == volume_id:
                return vol
        return None

    def find_chapter(self, chapter_id: str) -> tuple[Volume | None, Chapter | None]:
        for vol in self.volumes:
            for ch in vol.chapters:
                if ch.id == chapter_id:
                    return vol, ch
        return None, None

    def first_chapter(self) -> Chapter | None:
        for vol in self.volumes:
            if vol.chapters:
                return vol.chapters[0]
        return None

    def last_chapter(self) -> Chapter | None:
        _volume, chapter = self.find_chapter(self.last_chapter_id)
        if chapter is not None:
            return chapter
        chapters = self.all_chapters()
        return chapters[-1] if chapters else None

    def volume_of(self, chapter_id: str) -> Volume | None:
        return self.find_chapter(chapter_id)[0]

    def previous_chapter(self, chapter_id: str) -> Chapter | None:
        chapters = self.all_chapters()
        for i, ch in enumerate(chapters):
            if ch.id == chapter_id and i > 0:
                return chapters[i - 1]
        return None

    def next_chapter(self, chapter_id: str) -> Chapter | None:
        chapters = self.all_chapters()
        for i, ch in enumerate(chapters):
            if ch.id == chapter_id and i + 1 < len(chapters):
                return chapters[i + 1]
        return None

    # ---------------- 序列化 ----------------

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "author": self.author,
            "cover": self.cover,
            "cover_index": self.cover_index,
            "tags": list(self.tags),
            "group_id": self.group_id,
            "trashed": self.trashed,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "volumes": [v.to_dict() for v in self.volumes],
            "relations": [r.to_dict() for r in self.relations],
            "last_chapter_id": self.last_chapter_id,
            "timeline_interval": self.timeline_interval,
            "timeline_events": list(self.timeline_events),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Book":
        raw_cover = data.get("cover")
        cover = raw_cover.strip() if isinstance(raw_cover, str) else ""
        if isinstance(raw_cover, (int, float)) and not isinstance(raw_cover, bool):
            cover_index = int(raw_cover)
        else:
            cover_index = int(data.get("cover_index") or 0)

        book = cls(
            id=str(data.get("id") or new_id()),
            title=str(data.get("title") or "未命名书籍"),
            author=str(data.get("author") or ""),
            cover=cover,
            cover_index=max(0, cover_index),
            tags=_string_list(data.get("tags")),
            group_id=str(data.get("group_id") or ""),
            trashed=bool(data.get("trashed")),
            created_at=float(data.get("created_at") or time.time()),
            updated_at=float(data.get("updated_at") or time.time()),
            volumes=[Volume.from_dict(v) for v in data.get("volumes", []) if isinstance(v, dict)],
            relations=[RelatedItem.from_dict(r) for r in data.get("relations", []) if isinstance(r, dict)],
            last_chapter_id=str(data.get("last_chapter_id") or ""),
            timeline_interval=str(data.get("timeline_interval") or "day"),
            timeline_events=[
                dict(e) for e in data.get("timeline_events", []) if isinstance(e, dict)
            ],
        )
        if book.timeline_interval not in {"day", "month", "year"}:
            book.timeline_interval = "day"
        # 容错：保证至少有一卷一章，避免界面出现空状态导致的程序异常
        if not book.volumes:
            book.volumes.append(Volume(id=new_id(), title="第一卷"))
        for vol in book.volumes:
            if not vol.chapters:
                vol.chapters.append(Chapter(id=new_id(), title="第一章"))
        if not book.last_chapter_id or not book.find_chapter(book.last_chapter_id)[1]:
            book.last_chapter_id = book.first_chapter().id
        return book

    def meta(self, word_count: int = 0) -> dict:
        """生成书架展示用的简要信息。"""
        last = self.last_chapter()
        summary = ""
        if last is not None:
            summary = " ".join(last.content.split())[:56]
        return {
            "id": self.id,
            "title": self.title,
            "author": self.author,
            "cover": self.cover,
            "cover_index": self.cover_index,
            "tags": list(self.tags),
            "group_id": self.group_id,
            "trashed": self.trashed,
            "updated_at": self.updated_at,
            "created_at": self.created_at,
            "chapter_count": self.chapter_count(),
            "word_count": word_count,
            "last_chapter_id": self.last_chapter_id,
            "last_chapter_title": last.title if last is not None else "",
            "last_summary": summary,
        }

    def touch(self) -> None:
        self.updated_at = time.time()


def create_book(
    title: str,
    author: str = "",
    demo: bool = False,
    cover: str = "",
    tags: list[str] | None = None,
    group_id: str = "",
) -> Book:
    """创建一本新书。

    demo=True 时生成示例章节（楔子、第一章、第二章、结局），
    方便第一次打开软件时直接看到完整目录结构。
    """
    now = time.time()
    book = Book(
        id=new_id(),
        title=title.strip() or "未命名书籍",
        author=author.strip(),
        cover=cover,
        cover_index=random.randint(0, 5),
        tags=list(tags or []),
        group_id=group_id,
        created_at=now,
        updated_at=now,
    )
    volume = Volume(id=new_id(), title="第一卷")
    book.volumes.append(volume)

    if demo:
        titles = ["楔子", "第一章", "第二章", "结局"]
    else:
        titles = ["第一章"]
    for t in titles:
        volume.chapters.append(Chapter(id=new_id(), title=t, content=""))

    book.last_chapter_id = volume.chapters[0].id
    return book


def next_chapter_title(volume: Volume) -> str:
    """根据当前卷内章节数量，生成类似“第三章”的默认标题。"""
    used = {c.title for c in volume.chapters}
    index = 1
    while True:
        title = f"第{to_cn_number(index)}章"
        if title not in used:
            return title
        index += 1
