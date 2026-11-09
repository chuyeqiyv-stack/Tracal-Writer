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
from app.services.secret_store import decrypt_secret, encrypt_secret


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
    writing_theme: str = "auto"         # auto / white / green / lilac / sakura / skyblue / cream / starry / custom
    # 自定义写作区背景：当前选中的文件名（位于 <数据目录>/backgrounds；旧版存绝对路径，启动时迁移）
    writing_image: str = ""
    # 已保存的自定义背景（文件名列表，顺序即面板显示顺序，最多 5 个）
    backgrounds: list[str] = field(default_factory=list)

    # 排版
    indent: str = "two"                 # two 两个字符缩进 / none 无缩进
    blank_line: str = "blank"           # blank 段间空一行 / none 无空行
    clean_spaces: bool = True           # 清除多余空格
    unify_punct: bool = True            # 半角标点转全角（中文段落）
    ellipsis_fix: bool = True           # 规范省略号（。。。→……）与破折号（---→——）

    # 输入
    auto_pair: bool = True              # 成对标点自动补全
    tab_cjk_indent: bool = False        # Tab 插入两个全角空格（中文缩进，默认关）
    inline_hints: bool = True           # 正文内联提示：当前章红波浪线 + 悬停建议字

    # 字数
    exclude_punct: bool = False         # 字数统计不计标点
    speed_unit: str = "per_min"         # （v3 起保留兼容）旧版 字/分 / 字/时
    # v3：时速面板速度单位（只换显示，不影响内部统计）：char 字/分 / hour 字/时
    speed_unit_scale: str = "char"
    speed_tracking: bool = True         # 本次码字统计开关（圆钮右键暂停/继续）

    # 操作
    fast_scroll: bool = True
    float_jump: bool = True
    open_at_end: bool = True
    achievement_sound: bool = True       # 成就解锁提示音（Windows winsound，默认开）
    manual_save_sound: str = "wood"       # 手动保存提示音：off / wood / bell / paper / dingdong / lowhum（默认木质轻叩）

    # 字体
    font_family: str = DEFAULT_FONT_FAMILY   # 中文字体（空 = 默认字体）
    font_latin: str = "Segoe UI"             # 英文字体
    # v1.4.4：默认正文对齐参考「口袋写作」——12pt（96dpi 下 16px）、行距 1.55em
    font_size: float = 12.0
    font_bold: bool = False                  # 加粗
    font_italic: bool = False                # 斜体
    font_underline: bool = False             # 下划线
    line_spacing: float = 1.55
    # 段落排版（“排版”按钮 / 正文右键“段落设置”，借鉴 Word 段落对话框）
    para_align: str = "left"            # left / center / right
    para_indent: int = 0                # 首行缩进字符数（0-8；智能排版已插全角空格时保持 0）
    para_space_before: float = 0.5      # 段前间距（行）
    para_space_after: float = 0.5       # 段后间距（行）
    imported_fonts: list[str] = field(default_factory=list)

    # 书架
    shelf_view: str = "grid"            # grid / list
    notes_view: str = "grid"            # 设定页：grid 卡片 / list 列表
    character_view: str = "list"        # 角色页：list 角色列表 / board 关系图
    snippets_width: int = 320           # 右侧「文笔仿写」片段栏宽度（px，收起时保留）
    snippets_open: bool = False         # 仿写区展开状态（面板右上角箭头 / Esc 收起后记住）
    # v1.4.9：整卷导出上次使用的目录（空 = 系统文档目录）
    export_dir: str = ""
    ui_style: str = "default"           # 外观风格：default/paper/glass/order（与深浅正交）
    # v1.4.4：写作页顶部工具栏收起状态（重开程序保持；书架页不受影响）
    topbar_collapsed: bool = False
    # 玻璃质感三档（ui_style=glass 时生效）：block 立体玻璃块（默认）/ liquid 液态玻璃 / frosted 磨砂雾面
    glass_mode: str = "block"
    glass_blur: bool = True             # 玻璃风：背后内容高斯模糊（关闭后仅半透明本体，低端机可用）
    boot_animation: bool = True         # 启动小动画（黑白线稿莲花绽放，可点击/按任意键跳过）
    nickname: str = "写作者"
    avatar_path: str = ""
    showcase_medal: str = ""            # 首页左下角展示的成就勋章（成就 id，空 = 不展示；成就页右键更换）

    # 坚果云 WebDAV（纯手动触发）
    webdav_url: str = "https://dav.jianguoyun.com/dav/"
    webdav_user: str = ""
    webdav_password: str = ""
    webdav_remote: str = "moji_backup.zip"
    webdav_dir: str = ""          # 空值回退「催客写作」；最多 3 级
    sync_covers_mode: str = "thumb"     # 同步封面：none / thumb（256px WebP，默认） / full（旧行为，带原图）
    auto_sync_enabled: bool = False     # 自动同步开关（默认关闭）
    auto_sync_interval: int = 30        # 自动同步间隔（分钟）：10 / 20 / 30 / 60
    auto_sync_last_time: str = ""       # 上次自动同步 HH:MM
    auto_sync_last_ok: bool = False     # 上次自动同步是否成功
    auto_sync_signature: str = ""       # 上次成功上传时的本地数据指纹，未变化则不发请求

    # 设置窗口尺寸 / 位置（0 = 未记录，-1 = 未记录坐标；打开时夹在屏幕可用区内）
    settings_win_w: int = 0
    settings_win_h: int = 0
    settings_win_x: int = -1
    settings_win_y: int = -1

    def to_dict(self) -> dict:
        data = asdict(self)
        # 应用密码用 DPAPI 加密后落盘（键名保持不变，旧配置仍可读）
        if data.get("webdav_password"):
            data["webdav_password"] = encrypt_secret(str(data["webdav_password"]))
        return data

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
        if not result.writing_theme:
            result.writing_theme = "auto"
        elif result.writing_theme != "custom":
            # 预设色卡可能被精简：旧配置里已删除的键映射回「跟随主题」，不报错
            try:
                from app.ui.theme import WRITING_THEMES
            except ImportError:
                WRITING_THEMES = {}
            if WRITING_THEMES and result.writing_theme not in WRITING_THEMES:
                result.writing_theme = "auto"
        if result.shelf_view not in {"grid", "list"}:
            result.shelf_view = "grid"
        if result.notes_view not in {"grid", "list"}:
            result.notes_view = "grid"
        if result.character_view not in {"list", "board"}:
            result.character_view = "list"
        if result.ui_style not in {"default", "paper", "glass", "order"}:
            result.ui_style = "default"
        if result.glass_mode not in {"block", "liquid", "frosted"}:
            result.glass_mode = "block"
        # 旧配置可能存 kilo/wan（早期三档口径），统一平滑回退到「字/分」
        if result.speed_unit_scale not in {"char", "hour"}:
            result.speed_unit_scale = "char"
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
        if result.para_align not in {"left", "center", "right"}:
            result.para_align = "left"
        result.para_indent = max(0, min(8, int(result.para_indent)))
        result.para_space_before = max(0.0, min(4.0, float(result.para_space_before)))
        result.para_space_after = max(0.0, min(4.0, float(result.para_space_after)))
        if not result.nickname.strip():
            result.nickname = "写作者"
        # 应用密码：旧配置是明文（原样保留）；新配置是 dpapi: 前缀密文
        if result.webdav_password:
            plain = decrypt_secret(result.webdav_password)
            if plain:
                result.webdav_password = plain
            elif result.webdav_password.startswith("dpapi:"):
                # 换了系统用户 / 密文损坏：清空并标记，界面提示重新填写，不崩不静默留错
                result.webdav_password = ""
                result.webdav_password_lost = True
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
    # 锁定后不可拖动（仍可选中 / 编辑内容 / 换色）；color 为语义色键，"" 表示默认主题色
    locked: bool = False
    color: str = ""
    # v3：上/下引线长度（像素），-1 或缺键 = 默认长度；仅由 Ctrl+左键拖动调整
    lead_top: float = -1.0
    lead_bot: float = -1.0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "x": self.x,
            "time": self.time,
            "event": self.event,
            "desc": self.desc,
            "locked": self.locked,
            "color": self.color,
            "lead_top": self.lead_top,
            "lead_bot": self.lead_bot,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TimelineEvent":
        # 兼容旧版书籍内置时间轴的字段名（title / below）
        title = str(data.get("event") or data.get("title") or "")

        def _lead(key: str) -> float:
            try:
                v = float(data.get(key, -1.0))
            except (TypeError, ValueError):
                return -1.0
            return v if v > 0 else -1.0   # 缺键 / 非法 / 非正一律回退默认

        return cls(
            id=str(data.get("id") or new_id()),
            x=float(data.get("x") or 0),
            time=str(data.get("time") or ""),
            event=title,
            desc=str(data.get("desc") or ""),
            # 旧数据缺键时自动回退：未锁定 + 默认色（禁止用 bool("false") 这种误判）
            locked=data.get("locked") is True,
            color=str(data.get("color") or ""),
            lead_top=_lead("lead_top"),
            lead_bot=_lead("lead_bot"),
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


# ======================================================================
# 角色卡与角色关系（保存在 library.json 的 characters / character_relations 中）
# ======================================================================
CHARACTER_ROLES = ["主角", "重要配角", "配角", "反派", "路人"]
RELATION_NAMES = ["师徒", "宿敌", "恋人", "血亲", "主仆", "朋友", "同门"]

# 角色卡方框属性（图二布局）：三列九项，标签可右键改名 / 删除 / 新增
DEFAULT_CARD_FIELDS = ["灵根", "仙脉", "性别", "斗魂", "主修", "功法", "战技", "武器", "法宝"]
GENDER_FIELD = "性别"


@dataclass
class Character:
    """一张角色卡。x / y 为关系图上的卡片坐标（随拖拽持久化）。"""

    id: str
    name: str = "未命名角色"
    alias: str = ""
    role: str = "配角"
    gender: str = ""
    age: str = ""
    appearance: str = ""
    personality: str = ""
    backstory: str = ""
    goals: str = ""
    speech: str = ""
    tags: list[str] = field(default_factory=list)
    first_chapter: str = ""
    note: str = ""
    # 人物短章：角色卡小书本右键编辑的自由文本（旧数据缺键自动补空串）
    memo: str = ""
    # 从书籍正文提取时预填：出场章节列表 / 原文摘录（带章节出处），旧数据缺省不报错
    chapters: list[str] = field(default_factory=list)
    description: str = ""
    # 角色卡方框属性（灵根 / 仙脉 / 性别 …），每项 {"label", "value"}，可改名/增删
    card_fields: list[dict] = field(default_factory=list)
    x: float = 0.0
    y: float = 0.0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "alias": self.alias,
            "role": self.role,
            "gender": self.gender,
            "age": self.age,
            "appearance": self.appearance,
            "personality": self.personality,
            "backstory": self.backstory,
            "goals": self.goals,
            "speech": self.speech,
            "tags": [str(t) for t in self.tags],
            "first_chapter": self.first_chapter,
            "note": self.note,
            "memo": self.memo,
            "chapters": [str(c) for c in self.chapters],
            "description": self.description,
            "card_fields": [
                {"label": str(f.get("label") or ""), "value": str(f.get("value") or "")}
                for f in self.card_fields
            ],
            "x": self.x,
            "y": self.y,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Character":
        tags_raw = data.get("tags")
        if isinstance(tags_raw, str):
            tags = [t.strip() for t in tags_raw.replace("，", ",").split(",") if t.strip()]
        elif isinstance(tags_raw, list):
            tags = [str(t).strip() for t in tags_raw if str(t).strip()]
        else:
            tags = []
        chapters_raw = data.get("chapters")
        if isinstance(chapters_raw, list):
            chapters = [str(c).strip() for c in chapters_raw if str(c).strip()]
        else:
            chapters = []
        # 方框属性：旧数据缺键 -> 用图二默认九项，并把已有的「性别」预填进去
        card_fields = cls._parse_card_fields(
            data.get("card_fields"), str(data.get("gender") or "")
        )
        return cls(
            id=str(data.get("id") or new_id()),
            name=str(data.get("name") or "未命名角色"),
            alias=str(data.get("alias") or ""),
            role=str(data.get("role") or "配角"),
            gender=str(data.get("gender") or ""),
            age=str(data.get("age") or ""),
            appearance=str(data.get("appearance") or ""),
            personality=str(data.get("personality") or ""),
            backstory=str(data.get("backstory") or ""),
            goals=str(data.get("goals") or ""),
            speech=str(data.get("speech") or ""),
            tags=tags,
            first_chapter=str(data.get("first_chapter") or ""),
            note=str(data.get("note") or ""),
            memo=str(data.get("memo") or ""),   # 旧角色缺键 -> 空串，不报错
            chapters=chapters,
            description=str(data.get("description") or ""),
            card_fields=card_fields,
            x=float(data.get("x") or 0),
            y=float(data.get("y") or 0),
            created_at=float(data.get("created_at") or time.time()),
            updated_at=float(data.get("updated_at") or time.time()),
        )

    @staticmethod
    def _parse_card_fields(raw, gender: str) -> list[dict]:
        """容错读取方框属性；旧数据 / 异常数据一律回退默认九项，不报错。"""
        fields: list[dict] = []
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, dict):
                    label = str(item.get("label") or "").strip()
                    if label:
                        fields.append({"label": label, "value": str(item.get("value") or "").strip()})
        if not fields:
            fields = [{"label": name, "value": ""} for name in DEFAULT_CARD_FIELDS]
        # 性别项与顶层 gender 同步（旧数据只有 gender）
        for item in fields:
            if item["label"] == GENDER_FIELD and not item["value"] and gender:
                item["value"] = gender
        return fields

    def touch(self) -> None:
        self.updated_at = time.time()


@dataclass
class CharacterRelation:
    """一条角色关系（v3：同两个角色可各持一条反向独立边）。

    intensity 取整数区间 [-3, 3]：正数偏亲密、负数偏敌对、0 中性。
    方向语义：
    - directed=False：无向（不画箭头）；
    - directed=True、bidirectional=False：独立有向边，from_id → to_id，
      同一对角色允许再存在一条 to_id → from_id 的独立边（name/intensity/note/color 各自独立）；
    - directed=True、bidirectional=True：旧版「一根线两端箭头」，仅旧数据存在，
      storage 一次性迁移成两条同名独立有向边；split_from 记录迁移批次保证幂等。
    """

    id: str
    from_id: str
    to_id: str
    name: str = "关系"
    directed: bool = False
    bidirectional: bool = False
    intensity: int = 0
    note: str = ""
    # 关系线颜色（#RRGGBB；空 = 跟随强度自动配色）。导出/导入原样保留，
    # 界面暂无取色器时不影响既有观感。
    color: str = ""
    created_at: float = field(default_factory=time.time)
    split_from: str = ""   # 旧 bidirectional 拆分迁移标记（"bidir:<原id>"），重复迁移不产生副本

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "from_id": self.from_id,
            "to_id": self.to_id,
            "name": self.name,
            "directed": self.directed,
            "bidirectional": self.bidirectional,
            "intensity": int(self.intensity),
            "note": self.note,
            "color": self.color,
            "created_at": self.created_at,
            "split_from": self.split_from,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CharacterRelation":
        try:
            intensity = int(float(data.get("intensity") or 0))
        except (TypeError, ValueError):
            intensity = 0
        intensity = max(-3, min(3, intensity))
        return cls(
            id=str(data.get("id") or new_id()),
            from_id=str(data.get("from_id") or data.get("from") or ""),
            to_id=str(data.get("to_id") or data.get("to") or ""),
            name=str(data.get("name") or "关系"),
            directed=bool(data.get("directed")),
            # 旧数据无此键 -> 单向语义不变（只有 directed 为真时该标记才有意义）
            bidirectional=data.get("bidirectional") is True and bool(data.get("directed")),
            intensity=intensity,
            note=str(data.get("note") or ""),
            color=_clean_color(data.get("color")),
            created_at=float(data.get("created_at") or time.time()),
            split_from=str(data.get("split_from") or ""),
        )
