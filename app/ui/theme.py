# -*- coding: utf-8 -*-
"""主题系统：现代极简浅色 / 深色模式、写作区配色与全局 QSS。

所有视觉设计常量（圆角、字体、间距、颜色）集中在 DESIGN 与色板字典中，
方便全局统一调整，并保证深色模式下边框、阴影自适应。
"""
from __future__ import annotations

from string import Template

from PySide6.QtGui import QColor, QPalette

from app.config import DEFAULT_FONT_FAMILY
from app.models import Settings


# ======================================================================
# 集中设计配置：全局统一管理圆角 / 字体 / 尺寸
# ======================================================================
DESIGN: dict[str, object] = {
    "font_family": DEFAULT_FONT_FAMILY,   # 全局默认字体（微软雅黑）
    "font_size": 13,                       # 全局基础字号
    "radius_sm": 6,                        # 小控件（菜单项、页签）
    "radius": 8,                           # 按钮 / 输入框
    "radius_lg": 12,                       # 卡片 / 纸张
    "radius_pill": 16,                     # 胶囊（浮球、Toast）
    "btn_pad_x": 18,
    "btn_pad_y": 8,
}


LIGHT: dict[str, str] = {
    "name": "极简白",
    "window": "#F5F6F8",          # 页面底色：极浅灰
    "sidebar": "#F5F6F8",
    "panel": "#FFFFFF",           # 卡片色：纯白
    "panel_alt": "#F7F8F9",
    "editor": "#FFFFFF",
    "text": "#1F2329",            # 标题/数值深灰
    "subtext": "#8F959E",         # 说明文字浅灰
    "accent": "#7C5CE6",          # 主题紫（呼应应用图标）
    "accent_hover": "#6A49DB",
    "on_accent": "#FFFFFF",
    "border": "#EDEEF1",          # 极淡边框（尽量少用）
    "border_strong": "#DFE3E9",
    "divider": "#E5E6EB",         # 面板分隔细线
    "hover": "#F2F3F5",           # 悬停浅灰
    "hover_accent": "#F1EEFB",    # 悬停浅紫
    "pressed": "#E7E8EB",
    "accent_soft": "#EDE9FB",
    "focus_ring": "rgba(124,92,230,0.18)",
    "icon": "#4E5560",
    "topbar_bg": "#F5F6F8",       # 顶栏融入浅灰背景
    "topbar_text": "#333333",
    "statusbar_bg": "#F5F6F8",
    "statusbar_text": "#8F959E",
    "input_bg": "#FFFFFF",
    "danger": "#E5484D",
}

DARK: dict[str, str] = {
    "name": "深色",
    "window": "#1E1E1E",
    "sidebar": "#181920",
    "panel": "#26282C",
    "panel_alt": "#2C2E33",
    "editor": "#1E1E1E",
    "text": "#E6E8EB",
    "subtext": "#9AA0A6",
    "accent": "#9B7BFF",
    "accent_hover": "#AC8FFF",
    "on_accent": "#171233",
    "border": "#33363B",
    "border_strong": "#45494F",
    "divider": "#33363B",
    "hover": "#2E3134",
    "hover_accent": "#2B2545",
    "pressed": "#3A3D42",
    "accent_soft": "#2E2850",
    "focus_ring": "rgba(155,123,255,0.32)",
    "icon": "#C9CDD4",
    "topbar_bg": "#1E1E1E",
    "topbar_text": "#E6E8EB",
    "statusbar_bg": "#191A1D",
    "statusbar_text": "#9AA0A6",
    "input_bg": "#26282C",
    "danger": "#FF6B6B",
}

# ======================================================================
# 写作区预设：键 -> (名称, 背景, 文字)  —— 与参考图「编辑器主题」一致
# ======================================================================
WRITING_THEMES: dict[str, dict[str, str]] = {
    "auto":       {"name": "跟随主题", "bg": "", "text": ""},
    "white":      {"name": "默认白",   "bg": "#FFFFFF", "text": "#333333"},
    "green":      {"name": "护眼绿",   "bg": "#CDE8D0", "text": "#22402B"},
    "lilac":      {"name": "浅紫",     "bg": "#EDE9F7", "text": "#3F3654"},
    "sakura":     {"name": "樱粉",     "bg": "#FBEAEE", "text": "#54333D"},
    "skyblue":    {"name": "浅蓝",     "bg": "#E7F0FA", "text": "#2C3E52"},
    "cream":      {"name": "淡黄",     "bg": "#FBF3DC", "text": "#4C442B"},
    "oldgold":    {"name": "古黄",     "bg": "#E8D5A3", "text": "#4A3A1C"},
    "yellowrock": {"name": "黄石纹",   "bg": "#D9C48E", "text": "#42351A"},
    "parchment1": {"name": "羊皮纸1",  "bg": "#F1E4C3", "text": "#4A3926"},
    "parchment2": {"name": "羊皮纸2",  "bg": "#E6D3AC", "text": "#43331E"},
    "stripe":     {"name": "条纹",     "bg": "#EEF0E4", "text": "#3C4033"},
    "starry":     {"name": "星空蓝",   "bg": "#1B2340", "text": "#DCE4FF"},
    "blueblack":  {"name": "蓝黑",     "bg": "#14181F", "text": "#C7D3E3"},
    "drunkblack": {"name": "醉黑",     "bg": "#1A1418", "text": "#D8CBD3"},
    "frosted":    {"name": "磨砂黑",   "bg": "#232323", "text": "#DADADA"},
    "frostwhite": {"name": "磨砂白",   "bg": "#F2F3F1", "text": "#3A3D3A"},
}

# 书籍默认封面底纹（渐变）
COVER_GRADIENTS = [
    ("#7C9CF8", "#4B66C9"),
    ("#E0A458", "#B5713A"),
    ("#7FB89A", "#3F7D63"),
    ("#D98BA0", "#A6526E"),
    ("#9B8BE0", "#6353B8"),
    ("#5FB8C9", "#2F7C8F"),
]


def base_theme(mode: str) -> dict[str, str]:
    return DARK if mode == "dark" else LIGHT


def build_theme(settings: Settings) -> dict[str, str]:
    """在基础模式上叠加写作区预设与用户自定义颜色。"""
    theme = dict(base_theme(settings.mode))

    preset = WRITING_THEMES.get(settings.writing_theme, WRITING_THEMES["auto"])
    if preset["bg"]:
        theme["editor"] = preset["bg"]
        theme["editor_text"] = preset["text"]
    else:
        theme["editor_text"] = theme["text"]

    # 独立自定义：工具栏 / 状态栏 / 写作区
    if settings.toolbar_bg:
        theme["topbar_bg"] = settings.toolbar_bg
    if settings.toolbar_text:
        theme["topbar_text"] = settings.toolbar_text
    if settings.statusbar_bg:
        theme["statusbar_bg"] = settings.statusbar_bg
    if settings.statusbar_text:
        theme["statusbar_text"] = settings.statusbar_text
    if settings.editor_bg:
        theme["editor"] = settings.editor_bg
    if settings.editor_text:
        theme["editor_text"] = settings.editor_text

    # 品牌主色调：用户选色后全局派生（按钮 / 选中项 / 焦点边框等）
    if getattr(settings, "accent_color", ""):
        acc = QColor(settings.accent_color)
        dark = settings.mode == "dark"
        theme["accent"] = settings.accent_color.upper()
        theme["accent_hover"] = (acc.lighter(118) if dark else acc.darker(112)).name().upper()
        luma = (0.299 * acc.red() + 0.587 * acc.green() + 0.114 * acc.blue()) / 255.0
        theme["on_accent"] = "#1F2329" if luma > 0.72 else "#FFFFFF"
        theme["accent_soft"] = rgba(theme["accent"], 0.24 if dark else 0.12)
        theme["hover_accent"] = rgba(theme["accent"], 0.22 if dark else 0.09)
        theme["focus_ring"] = rgba(theme["accent"], 0.32 if dark else 0.18)
        theme["selection"] = rgba(theme["accent"], 0.26)

    # 派生色
    theme["input_bg"] = theme["panel"]
    theme["selection"] = rgba(theme["accent"], 0.26)
    theme["scroll"] = rgba(theme["subtext"], 0.26)
    theme["scroll_hover"] = rgba(theme["subtext"], 0.5)
    # 柔和阴影颜色（深色模式下更重一些，保证可见但柔和）
    theme["shadow_alpha"] = 0.10 if settings.mode == "light" else 0.38
    theme["shadow_color"] = "#4A5568" if settings.mode == "dark" else "#000000"
    # 水晶透明质感：左右侧栏半透明底 + 1px 浅色高光边框
    if settings.mode == "dark":
        theme["sidebar_glass"] = "rgba(255, 255, 255, 0.06)"
        theme["glass_border"] = "rgba(255, 255, 255, 0.14)"
    else:
        theme["sidebar_glass"] = "rgba(255, 255, 255, 0.65)"
        theme["glass_border"] = "rgba(255, 255, 255, 0.80)"
    return theme


def rgba(hex_color: str, alpha: float) -> str:
    """把 #RRGGBB 转成 QSS 的 rgba() 字符串。"""
    c = QColor(hex_color)
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {alpha:.2f})"


def qc(color: str) -> QColor:
    """解析颜色字符串：支持 #RRGGBB 与 QSS 的 rgba() 写法。

    QColor 原生不认识 "rgba(r, g, b, a)" 字符串，直接传入会得到无效颜色
    （绘制时表现为黑色）。这里统一解析，供 QPainter 自绘控件使用。
    """
    c = QColor(color)
    if c.isValid():
        return c
    s = color.strip()
    if s.startswith("rgba"):
        try:
            parts = s[s.index("(") + 1 : s.rindex(")")].split(",")
            c = QColor(int(parts[0]), int(parts[1]), int(parts[2]))
            c.setAlphaF(float(parts[3]))
        except (ValueError, IndexError):
            c = QColor(0, 0, 0, 0)
    return c


def build_palette(theme: dict[str, str]) -> QPalette:
    """生成应用调色板，让原生控件（复选框、下拉箭头等）跟随主题。"""
    pal = QPalette()
    pal.setColor(QPalette.Window, QColor(theme["window"]))
    pal.setColor(QPalette.WindowText, QColor(theme["text"]))
    pal.setColor(QPalette.Base, QColor(theme["panel"]))
    pal.setColor(QPalette.AlternateBase, QColor(theme["hover"]))
    pal.setColor(QPalette.Text, QColor(theme["text"]))
    pal.setColor(QPalette.Button, QColor(theme["panel"]))
    pal.setColor(QPalette.ButtonText, QColor(theme["text"]))
    pal.setColor(QPalette.Highlight, QColor(theme["accent"]))
    pal.setColor(QPalette.HighlightedText, QColor(theme["on_accent"]))
    pal.setColor(QPalette.ToolTipBase, QColor(theme["panel"]))
    pal.setColor(QPalette.ToolTipText, QColor(theme["text"]))
    pal.setColor(QPalette.PlaceholderText, QColor(theme["subtext"]))
    pal.setColor(QPalette.Mid, QColor(theme["subtext"]))
    pal.setColor(QPalette.Dark, QColor(theme["border"]))
    pal.setColor(QPalette.Light, QColor(theme["hover"]))
    return pal




def _load_qss_template() -> str:
    """从 assets/style.qss 读取 QSS 模板；文件缺失时退回内置极简样式。

    打包（PyInstaller）后 ASSETS_DIR 指向 _MEIPASS/assets，需在 spec 里带上 assets。
    """
    from app.config import ASSETS_DIR

    try:
        return (ASSETS_DIR / "style.qss").read_text(encoding="utf-8")
    except OSError:
        return (
            "* { outline: 0; }"
            "QWidget { color: $text; font-size: ${font_size}px; }"
            "QMainWindow, QDialog { background: $window; }"
            "QPushButton { background: $panel; border: none; border-radius: ${radius}px;"
            " padding: ${btn_pad_y}px ${btn_pad_x}px; color: $text; }"
            "QPushButton#Primary { background: $accent; color: $on_accent; }"
            "QLineEdit, QComboBox { background: $panel_alt; border: none;"
            " border-radius: ${radius}px; padding: 9px 12px; color: $text; }"
            "QLineEdit:focus, QComboBox:focus { border: 1px solid $accent; }"
        )


def build_qss(theme: dict[str, str]) -> str:
    """根据主题生成完整 QSS（设计常量一并注入）。"""
    values = {
        "window": theme["window"],
        "sidebar": theme["sidebar"],
        "panel": theme["panel"],
        "panel_alt": theme["panel_alt"],
        "editor": theme["editor"],
        "editor_text": theme["editor_text"],
        "text": theme["text"],
        "subtext": theme["subtext"],
        "accent": theme["accent"],
        "accent_hover": theme["accent_hover"],
        "on_accent": theme["on_accent"],
        "border": theme["border"],
        "border_strong": theme["border_strong"],
        "divider": theme["divider"],
        "hover": theme["hover"],
        "hover_accent": theme["hover_accent"],
        "pressed": theme["pressed"],
        "accent_soft": theme["accent_soft"],
        "focus_ring": theme["focus_ring"],
        "selection": theme["selection"],
        "scroll": theme["scroll"],
        "scroll_hover": theme["scroll_hover"],
        "toolbar_bg": theme["topbar_bg"],
        "toolbar_text": theme["topbar_text"],
        "statusbar_bg": theme["statusbar_bg"],
        "statusbar_text": theme["statusbar_text"],
        "input_bg": theme["input_bg"],
        "danger": theme["danger"],
        "sidebar_glass": theme["sidebar_glass"],
        "glass_border": theme["glass_border"],
        "font_family": str(DESIGN["font_family"]),
        "font_size": int(DESIGN["font_size"]),
        "radius_sm": int(DESIGN["radius_sm"]),
        "radius": int(DESIGN["radius"]),
        "radius_lg": int(DESIGN["radius_lg"]),
        "radius_pill": int(DESIGN["radius_pill"]),
        "btn_pad_x": int(DESIGN["btn_pad_x"]),
        "btn_pad_y": int(DESIGN["btn_pad_y"]),
    }
    return Template(_load_qss_template()).substitute(**values)
