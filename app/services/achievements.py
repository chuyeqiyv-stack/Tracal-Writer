# -*- coding: utf-8 -*-
"""成就系统：成就定义与解锁管理（持久化到 achievements.json）。

成就分四类：
- 首作 / 习惯：first_book 卧龙出山、marathon 挥斥方遒、persistent 坚持不懈；
- 字数里程碑（正向奖励，单本累计字数）：墨积成卷 3 万 → 笔筑千章 10 万
  → 瀚墨连篇 30 万 → 鸿篇铸就 100 万；
- 断更警示（负向提醒，单本连续无有效码字天数）：太监断更 15 天 →
  久搁弃章 30 天 → 旧稿荒坟 180 天 → 文魂永寂 365 天；
- 救赎：英雄重生——达成文魂永寂后首次重新写出 ≥500 字有效码字。

每个成就只有初次满足条件时解锁并弹窗庆祝（含 114514 彩蛋全解锁）。
勋章美术由 app/ui/medal_art.py 统一以 QPainter 绘制（水墨国风、无外部背景）。
"""
from __future__ import annotations

from app import config
from app.storage import Storage

ACHIEVEMENTS: dict[str, dict] = {
    # ---- 首作 / 习惯 ----
    "first_book": {
        "name": "卧龙出山",
        "sub": "首次动笔",
        "cond": "创立你的第一本书",
        "praise": "第一本书已经立项，卧龙今日出山！愿你落笔千言、文思泉涌，笔下自有一方风云天地。",
    },
    "marathon": {
        "name": "挥斥方遒",
        "sub": "笔力迸发",
        "cond": "单次写作超过一小时",
        "praise": "连续伏案一小时，笔力愈发雄浑！文思正盛，请尽情挥斥方遒，书写你的锦绣文章。",
    },
    "persistent": {
        "name": "坚持不懈",
        "sub": "日日不辍",
        "cond": "连续一个月每天都更新 500 字以上",
        "praise": "整整一个月天天动笔、字字耕耘，这份坚持令人敬佩！作为奖励，历史版本容量已扩至 7 个。",
    },
    # ---- 字数里程碑 · 正向奖励（由浅金逐步升级为厚重暗金） ----
    "words_30k": {
        "name": "墨积成卷",
        "sub": "三万字里程碑",
        "cond": "单本作品累计码字达到 30000 字",
        "praise": "点滴落笔，积字成卷。一字一句积攒，书稿初具篇幅。",
    },
    "words_100k": {
        "name": "笔筑千章",
        "sub": "十万字里程碑",
        "cond": "单本作品累计码字达到 100000 字",
        "praise": "挥毫筑章，骨架已成。以笔墨搭建起完整的故事篇章。",
    },
    "words_300k": {
        "name": "瀚墨连篇",
        "sub": "三十万字里程碑",
        "cond": "单本作品累计码字达到 300000 字",
        "praise": "瀚墨滔滔，篇章连绵。笔墨化作瀚海，故事绵延不绝。",
    },
    "words_1m": {
        "name": "鸿篇铸就",
        "sub": "百万字里程碑",
        "cond": "单本作品累计码字达到 1000000 字",
        "praise": "千锤百炼，鸿篇终铸。百万字巨作落成，故事蔚为大观。",
    },
    # ---- 断更警示 · 负向提醒（冷灰暗沉，逐级加深） ----
    "hiatus_15d": {
        "name": "太监断更",
        "sub": "十五日未更",
        "cond": "单本作品连续 15 天没有新增有效码字",
        "praise": "行文半路，笔墨骤停。故事搁置，前路悬而未续。",
    },
    "hiatus_30d": {
        "name": "久搁弃章",
        "sub": "三十日未续",
        "cond": "单本作品连续 30 天没有新增有效码字",
        "praise": "篇章久搁，弃兆已生。故事长久封存，再不动笔或将彻底放弃。",
    },
    "hiatus_180d": {
        "name": "旧稿荒坟",
        "sub": "半载未更",
        "cond": "单本作品连续 180 天没有新增有效码字",
        "praise": "残稿埋尘，稿作成墟。半载搁置，文稿已经埋没在岁月荒雾之中。",
    },
    "hiatus_365d": {
        "name": "文魂永寂",
        "sub": "一载封笔",
        "cond": "单本作品连续 365 天没有新增有效码字",
        "praise": "文星熄灭，笔墨长眠。故事沉寂一载，续写的希望几近消散。",
    },
    # ---- 救赎特殊成就（灰底鎏金星火） ----
    "rebirth": {
        "name": "英雄重生",
        "sub": "沉寂一载，提笔归来",
        "cond": "达成文魂永寂后，首次新增有效码字 500 字",
        "praise": "文星曾寂，久封笔墨；一朝拾卷，英雄重生。于死寂之中重新拾起搁置一年的故事。",
    },
}

# 字数里程碑阈值（键 → 单本累计字数）
WORD_MILESTONES: list[tuple[str, int]] = [
    ("words_30k", 30000),
    ("words_100k", 100000),
    ("words_300k", 300000),
    ("words_1m", 1000000),
]

# 断更警示阈值（键 → 连续无有效码字天数）
HIATUS_THRESHOLDS: list[tuple[str, int]] = [
    ("hiatus_15d", 15),
    ("hiatus_30d", 30),
    ("hiatus_180d", 180),
    ("hiatus_365d", 365),
]

# 「坚持不懈」解锁后，历史版本上限加成
PERSISTENT_HISTORY_BONUS = 2


class AchievementManager:
    """成就解锁状态管理（内存缓存 + 落盘）。"""

    def __init__(self, storage: Storage) -> None:
        self.storage = storage
        self.unlocked: dict[str, float] = storage.load_achievements()

    def has(self, key: str) -> bool:
        return key in self.unlocked

    def unlock(self, key: str) -> bool:
        """解锁成就；只有初次解锁返回 True（用于触发弹窗）。"""
        if key not in ACHIEVEMENTS or key in self.unlocked:
            return False
        import time

        self.unlocked[key] = time.time()
        self.storage.save_achievements(self.unlocked)
        return True

    def history_cap(self) -> int:
        """当前历史版本上限：基础 5 个，「坚持不懈」+2。"""
        return config.MAX_SNAPSHOTS + (
            PERSISTENT_HISTORY_BONUS if self.has("persistent") else 0
        )
