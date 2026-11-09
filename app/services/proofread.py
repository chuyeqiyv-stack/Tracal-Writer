# -*- coding: utf-8 -*-
"""文本体检：只报告高置信度的「的 / 地 / 得」等结构性问题。

设计原则：
- 规则基于有限词表 + 结构约束，宁可漏报，不做模糊猜测；
- 所有问题只给出位置与建议，不自动改写；
- fixable=False 的问题（引号/括号不配对）只提示，交给用户手工处理。
"""
import re

RULES = {
    "adj_de_noun": "形容词 + 地 + 名词（应为「的」）",
    "adv_de_verb": "状语 + 的 + 动词（应为「地」）",
    "verb_de_degree": "动词 + 的 + 很/太/不（应为「得」）",
    "repeat_particle": "重复的「的的」「了了」",
    "unpaired": "引号 / 括号不配对",
    "width": "中文里的半角标点",
}

# 规则 A：明确是形容词、后面跟明确是名词的词
ADJ_WORDS = (
    "美丽", "漂亮", "干净", "整洁", "安静", "宁静", "温柔", "温暖", "巨大",
    "明亮", "灿烂", "清新", "柔软", "坚硬", "冰冷", "炎热", "快乐", "悲伤",
    "沉重", "轻松", "认真", "仔细", "小心", "愤怒", "激动", "平静",
)
NOUN_WORDS = (
    "花朵", "眼睛", "天空", "笑容", "声音", "房子", "城市", "世界", "风景",
    "女孩", "男孩", "脸庞", "双手", "衣服", "月光", "阳光", "街道", "房间",
    "桌子", "窗户", "树木", "山峰", "河流", "大海", "云朵", "雨滴", "雪花",
    "灯光", "样子", "模样", "表情", "语气",
)

# 规则 B：明确可作状语的双字词 + 明确是动词的双字词
ADVERB_WORDS = (
    "慢慢", "轻轻", "悄悄", "静静", "缓缓", "渐渐", "深深", "牢牢", "紧紧",
    "匆匆", "飞快", "仔细", "认真", "小心", "努力", "用力", "大声", "小声",
    "轻声", "快速", "迅速", "高高", "远远", "早早", "偷偷", "默默", "好好",
    "狠狠", "呆呆", "愣愣",
)
VERB_WORDS = (
    "走", "跑", "说", "笑", "哭", "看", "听", "坐", "站", "飞", "唱", "跳",
    "读", "写", "吃", "喝", "打开", "关上", "抬头", "低头", "转身", "摇头",
    "点头", "挥手", "抓住", "握住", "抱住", "推开", "放下", "落下", "飘落",
    "闪动", "响起", "移动", "升起", "下降", "流下", "望向", "看向", "走近",
    "走过", "走来",
)

# 规则 C：单字动词 + 的 + 程度词（排除「有 / 要 / 是 / 在」等易误判字）
VERB_CHARS = "说跑走笑哭看听吃睡写唱跳做干想学读打拿放坐站来去买长变亮响冷热"

_PAIR = {"（": "）", "(": ")", "【": "】", "《": "》", "「": "」", "『": "』",
         "“": "”", "‘": "’"}
_PAIR_OPEN = {op: cl for op, cl in _PAIR.items()}
_PAIR_CLOSE = {cl: op for op, cl in _PAIR.items()}

_WIDTH_MAP = {",": "，", ".": "。", "?": "？", "!": "！", ":": "：", ";": "；"}
_CJK = "\u4e00-\u9fff"
_WIDTH_RE = re.compile(rf"(?<=[{_CJK}])([,\.?!:;])(?=[{_CJK}\s]|$)")


def _word_group(words: tuple[str, ...]) -> str:
    return "|".join(sorted((re.escape(w) for w in words), key=len, reverse=True))


_RULE_A = re.compile(f"({_word_group(ADJ_WORDS)})地({_word_group(NOUN_WORDS)})")
_RULE_B = re.compile(f"({_word_group(ADVERB_WORDS)})的({_word_group(VERB_WORDS)})")
_RULE_C = re.compile(f"([{VERB_CHARS}])的([很太不])")
_RULE_D = re.compile("的的|了了")


def _line_of(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def _snippet(text: str, index: int, radius: int = 14) -> str:
    begin = max(0, index - radius)
    end = min(len(text), index + radius)
    return text[begin:end].replace("\n", " ").strip()


def _issue(text: str, rule: str, start: int, length: int, replacement: str,
           fixable: bool = True) -> dict:
    return {
        "rule": rule,
        "title": RULES[rule],
        "start": start,
        "length": length,
        "replacement": replacement,
        "fixable": fixable,
        "line": _line_of(text, start),
        "snippet": _snippet(text, start),
        "original": text[start:start + length],
    }


def scan_text(text: str) -> list[dict]:
    """扫描一段正文，返回按位置排序的问题列表（只提示，不修改）。"""
    issues: list[dict] = []

    for m in _RULE_A.finditer(text):
        start = m.start(0) + len(m.group(1))
        issues.append(_issue(text, "adj_de_noun", start, 1, "的"))

    for m in _RULE_B.finditer(text):
        start = m.start(0) + len(m.group(1))
        issues.append(_issue(text, "adv_de_verb", start, 1, "地"))

    for m in _RULE_C.finditer(text):
        start = m.start(1) + 1
        issues.append(_issue(text, "verb_de_degree", start, 1, "得"))

    for m in _RULE_D.finditer(text):
        char = m.group(0)[0]
        issues.append(_issue(text, "repeat_particle", m.start(), 2, char))

    for m in _WIDTH_RE.finditer(text):
        issues.append(_issue(text, "width", m.start(1), 1, _WIDTH_MAP[m.group(1)]))

    stack: list[tuple[str, int]] = []
    for index, char in enumerate(text):
        if char in _PAIR_OPEN:
            stack.append((char, index))
        elif char in _PAIR_CLOSE:
            if stack and stack[-1][0] == _PAIR_CLOSE[char]:
                stack.pop()
            else:
                issues.append(_issue(text, "unpaired", index, 1, "", fixable=False))
    for _op, index in stack:
        issues.append(_issue(text, "unpaired", index, 1, "", fixable=False))

    issues.sort(key=lambda item: item["start"])
    return issues
