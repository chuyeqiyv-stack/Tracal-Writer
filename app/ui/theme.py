# -*- coding: utf-8 -*-
"""主题系统：现代极简浅色 / 深色模式、写作区配色与全局 QSS。

所有视觉设计常量（圆角、字体、间距、颜色）集中在 DESIGN 与色板字典中，
方便全局统一调整，并保证深色模式下边框、阴影自适应。
"""
from __future__ import annotations

from functools import lru_cache
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
    "starry":     {"name": "星空蓝",   "bg": "#1B2340", "text": "#DCE4FF"},
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


# ======================================================================
# 外观风格预设（与浅 / 深主题正交；差异在质感而非堆色）
# 结构令牌：radius_* 圆角、font_title 标题字体栈、window_bg 背景、
# table_header_* 表头、section_gap 详情区行距、shadow_* 浮层阴影（motion 用）。
# default 不覆盖色板，结构令牌取与 DESIGN 一致的默认值，旧观感保持不变。
# ======================================================================
_SERIF_STACK = '"Noto Serif SC", "Source Han Serif SC", "Songti SC", STSong, SimSun, Georgia, serif'
_SANS_STACK = "Microsoft YaHei, PingFang SC, Segoe UI, sans-serif"

STYLE_PROFILES: dict[str, dict[str, dict[str, str]]] = {
    "default": {"light": {}, "dark": {}},
    # 纸感极简：温和纸白 / 暖墨、发丝分隔、衬线标题、圆角收敛、行距松
    "paper": {
        "light": {
            "window": "#FAF8F2", "sidebar": "#FAF8F2", "panel": "#FFFDF7",
            "panel_alt": "#F5F1E7", "text": "#2B2620", "subtext": "#8F8675",
            "border": "#ECE6D8", "border_strong": "#DED6C2", "divider": "#ECE6D8",
            "hover": "#F3EEE1", "hover_accent": "#F0EAE2", "pressed": "#EAE3D2",
            "accent_soft": "#F0E9F8",
            "topbar_bg": "#FAF8F2", "topbar_text": "#2B2620",
            "statusbar_bg": "#FAF8F2", "statusbar_text": "#8F8675",
            "input_bg": "#FFFDF7", "icon": "#5A5246",
            "radius_sm": "4", "radius": "8", "radius_lg": "10", "radius_pill": "14",
            "font_title": _SERIF_STACK,
            "window_bg": "#FAF8F2",
            "table_header_bg": "transparent", "table_header_border": "#ECE6D8",
            "section_gap": "5",
            "shadow_blur1": "14", "shadow_dy1": "2", "shadow_alpha1": "16",
            "shadow_blur2": "26", "shadow_dy2": "8", "shadow_alpha2": "20",
        },
        "dark": {
            "window": "#221F1A", "sidebar": "#1E1B17", "panel": "#2B2721",
            "panel_alt": "#332E27", "text": "#EAE3D4", "subtext": "#A69B86",
            "border": "#3E382E", "border_strong": "#4D463A", "divider": "#3E382E",
            "hover": "#342F27", "hover_accent": "#38312A", "pressed": "#3F392E",
            "accent_soft": "#332C4A",
            "topbar_bg": "#221F1A", "topbar_text": "#EAE3D4",
            "statusbar_bg": "#1D1A16", "statusbar_text": "#A69B86",
            "input_bg": "#2B2721", "icon": "#BFB49E",
            "radius_sm": "4", "radius": "8", "radius_lg": "10", "radius_pill": "14",
            "font_title": _SERIF_STACK,
            "window_bg": "#221F1A",
            "table_header_bg": "transparent", "table_header_border": "#3E382E",
            "section_gap": "5",
            "shadow_blur1": "14", "shadow_dy1": "2", "shadow_alpha1": "40",
            "shadow_blur2": "26", "shadow_dy2": "8", "shadow_alpha2": "60",
        },
    },
    # 柔光玻璃：冷调灰白竖向微光渐变、圆角更大、浮层扩散软阴影
    "glass": {
        "light": {
            "window": "#F1F4F8", "sidebar": "#F2F4F8", "panel": "#FFFFFF",
            "panel_alt": "#F3F6FA", "text": "#20252E", "subtext": "#8A93A3",
            "border": "#E6EAF1", "border_strong": "#D7DEE9", "divider": "#E6EAF1",
            "hover": "#F6F9FD", "hover_accent": "#EEF2FB", "pressed": "#E9EFF8",
            "accent_soft": "#ECE9FB",
            "topbar_bg": "#F4F7FB", "topbar_text": "#20252E",
            "statusbar_bg": "#F1F4F8", "statusbar_text": "#8A93A3",
            "input_bg": "#FFFFFF", "icon": "#525C6C",
            "radius_sm": "6", "radius": "10", "radius_lg": "14", "radius_pill": "18",
            "font_title": _SANS_STACK,
            "window_bg": "qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #F8FAFD, stop:1 #EAEFF5)",
            "table_header_bg": "rgba(255,255,255,0.55)", "table_header_border": "#E6EAF1",
            "section_gap": "0",
            # 液态玻璃：仅浮层用 0 8 24 rgba(0,0,0,0.10)；卡片本体不加阴影
            "shadow_blur1": "24", "shadow_dy1": "8", "shadow_alpha1": "26",
            "shadow_blur2": "24", "shadow_dy2": "8", "shadow_alpha2": "26",
            "card_shadow_rest_blur": "0", "card_shadow_rest_dy": "0", "card_shadow_rest_alpha": "0",
            "card_shadow_hover_blur": "0", "card_shadow_hover_dy": "0", "card_shadow_hover_alpha": "0",
            "card_shadow_sel_blur": "0", "card_shadow_sel_dy": "0", "card_shadow_sel_alpha": "0",
            # ---- iPhone 液态玻璃（浅色）：QSS 半透明面令牌 ----
            "qss_panel": "rgba(255,255,255,0.70)",
            "qss_panel_alt": "rgba(244,248,253,0.60)",
            "qss_sidebar": "rgba(255,255,255,0.55)",
            "qss_toolbar": ("qlineargradient(x1:0,y1:0,x2:0,y2:1,"
                            "stop:0 rgba(255,255,255,0.66),stop:1 rgba(244,248,253,0.52))"),
            "sidebar_glass": "rgba(255,255,255,0.55)",
            "glass_border": "rgba(255,255,255,0.85)",
            # ---- 液态边缘光学 / 折射（自绘表面共用，禁止散落魔法数字）----
            "liq_top": "rgba(255,255,255,0.90)",       # 上缘细亮线（QSS 侧边栏等 fallback）
            "liq_bottom": "rgba(70,92,128,0.26)",      # 下缘暗边（QSS fallback）
            # ---- 液态玻璃四层参数（浅色）----
            "liq_blur": "16",                          # 背景模糊 σ（1x 计，内部降采样 0.5）
            "liq_body_hi": "rgba(252,253,255,0.70)",   # 本体面板色 70%（顶）
            "liq_body_lo": "rgba(243,246,251,0.58)",   # 本体面板色 58%（底），区间 55–70%
            "liq_shade": "rgba(18,26,42,0.08)",        # 压暗层 8%（保正文对比）
            "liq_glow0": "rgba(255,255,255,0.22)",     # ① 顶部渐隐高光：22%
            "liq_glow1": "rgba(255,255,255,0.08)",     # ① 中段 8%
            "liq_glow2": "rgba(255,255,255,0.00)",     # ① 顶部 45% 处归零
            "liq_glow_h": "0.45",
            "liq_rim_hi": "rgba(255,255,255,0.55)",    # ② 边缘镜面线：左上 55%
            "liq_rim_lo": "rgba(255,255,255,0.10)",    # ② 右下 10%
            "liq_sheen": "rgba(255,255,255,0.18)",     # ③ 高光斑中心 18%
            "liq_sheen_w": "0.60",                     # ③ 椭圆宽 ≈ 卡宽 60%
            "liq_sheen_h": "0.64",
            "liq_sheen_x": "0.30", "liq_sheen_y": "0.16",  # ③ 静止偏左上（中心比例）
            "liq_inner_dark": "rgba(0,0,0,0.08)",      # ④ 底部内暗边 1px 黑 8%
            "liq_cyan": "rgba(110,205,255,0.05)",      # ④ 色散青 1px 5%
            "liq_magenta": "rgba(255,112,220,0.05)",   # ④ 色散品红 1px 5%
            "liq_noise": "0.025",                      # 噪点 2.5%（深色 1.5%）
        },
        "dark": {
            "window": "#1F2228", "sidebar": "#1D2026", "panel": "#292D35",
            "panel_alt": "#31363F", "text": "#E9EDF4", "subtext": "#9AA3B2",
            "border": "#3A3F49", "border_strong": "#484E5A", "divider": "#3A3F49",
            "hover": "#333841", "hover_accent": "#313744", "pressed": "#3A404B",
            "accent_soft": "#2E3246",
            "topbar_bg": "#20232A", "topbar_text": "#E9EDF4",
            "statusbar_bg": "#1B1E24", "statusbar_text": "#9AA3B2",
            "input_bg": "#292D35", "icon": "#C2C9D6",
            "radius_sm": "6", "radius": "10", "radius_lg": "14", "radius_pill": "18",
            "font_title": _SANS_STACK,
            "window_bg": "qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #262932, stop:1 #1B1E24)",
            "table_header_bg": "rgba(255,255,255,0.05)", "table_header_border": "#3A3F49",
            "section_gap": "0",
            # 液态玻璃：仅浮层用 0 8 24 rgba(0,0,0,0.10)；卡片本体不加阴影
            "shadow_blur1": "24", "shadow_dy1": "8", "shadow_alpha1": "26",
            "shadow_blur2": "24", "shadow_dy2": "8", "shadow_alpha2": "26",
            "card_shadow_rest_blur": "0", "card_shadow_rest_dy": "0", "card_shadow_rest_alpha": "0",
            "card_shadow_hover_blur": "0", "card_shadow_hover_dy": "0", "card_shadow_hover_alpha": "0",
            "card_shadow_sel_blur": "0", "card_shadow_sel_dy": "0", "card_shadow_sel_alpha": "0",
            # ---- iPhone 液态玻璃（深色）----
            "qss_panel": "rgba(42,46,55,0.62)",
            "qss_panel_alt": "rgba(49,54,63,0.52)",
            "qss_sidebar": "rgba(28,31,38,0.55)",
            "qss_toolbar": ("qlineargradient(x1:0,y1:0,x2:0,y2:1,"
                            "stop:0 rgba(44,48,57,0.62),stop:1 rgba(28,31,38,0.50))"),
            "sidebar_glass": "rgba(28,31,38,0.55)",
            "glass_border": "rgba(255,255,255,0.16)",
            "liq_top": "rgba(255,255,255,0.30)",
            "liq_bottom": "rgba(0,0,0,0.42)",
            # ---- 液态玻璃四层参数（深色：深色面板底透出，杜绝白板玻璃）----
            "liq_blur": "16",
            "liq_body_hi": "rgba(44,48,57,0.68)",    # 深色面板 68%（顶）
            "liq_body_lo": "rgba(32,36,44,0.56)",    # 56%（底），区间 55–70%
            "liq_shade": "rgba(0,0,0,0.12)",         # 压暗层 12%
            "liq_glow0": "rgba(255,255,255,0.22)",
            "liq_glow1": "rgba(255,255,255,0.08)",
            "liq_glow2": "rgba(255,255,255,0.00)",
            "liq_glow_h": "0.45",
            "liq_rim_hi": "rgba(255,255,255,0.55)",
            "liq_rim_lo": "rgba(255,255,255,0.10)",
            "liq_sheen": "rgba(255,255,255,0.18)",
            "liq_sheen_w": "0.60",
            "liq_sheen_h": "0.64",
            "liq_sheen_x": "0.30", "liq_sheen_y": "0.16",
            "liq_inner_dark": "rgba(0,0,0,0.08)",
            "liq_cyan": "rgba(110,205,255,0.05)",
            "liq_magenta": "rgba(255,112,220,0.05)",
            "liq_noise": "0.015",                     # 深色噪点降到 1.5%
        },
    },
    # 清晰秩序：克制商务中性灰、色块更平、表头实色分明、圆角锐利、信息更密
    "order": {
        "light": {
            "window": "#F7F8FA", "sidebar": "#F7F8FA", "panel": "#FFFFFF",
            "panel_alt": "#F3F5F8", "text": "#1F2329", "subtext": "#868B94",
            "border": "#E4E8EE", "border_strong": "#D2D8E0", "divider": "#E4E8EE",
            "hover": "#F1F4F8", "hover_accent": "#EFF2FA", "pressed": "#E7EBF1",
            "accent_soft": "#EDE9FB",
            "topbar_bg": "#F7F8FA", "topbar_text": "#1F2329",
            "statusbar_bg": "#F7F8FA", "statusbar_text": "#868B94",
            "input_bg": "#FFFFFF", "icon": "#4E5560",
            "radius_sm": "4", "radius": "6", "radius_lg": "8", "radius_pill": "12",
            "font_title": _SANS_STACK,
            "window_bg": "#F7F8FA",
            "table_header_bg": "#F3F5F8", "table_header_border": "#D2D8E0",
            "section_gap": "0",
            "shadow_blur1": "14", "shadow_dy1": "2", "shadow_alpha1": "20",
            "shadow_blur2": "26", "shadow_dy2": "6", "shadow_alpha2": "26",
            # 磨砂秩序：阴影收紧、贴近、克制
            "card_shadow_rest_blur": "10", "card_shadow_rest_dy": "2", "card_shadow_rest_alpha": "14",
            "card_shadow_hover_blur": "16", "card_shadow_hover_dy": "3", "card_shadow_hover_alpha": "24",
            "card_shadow_sel_blur": "22", "card_shadow_sel_dy": "5", "card_shadow_sel_alpha": "36",
            # ---- 磨砂哑光：近实色面 + 极淡预生成噪点（无高光反光）----
            "noise_name": "noise_light.png",
            "sidebar_glass": "rgba(247,248,250,0.96)",
            "glass_border": "rgba(214,219,227,0.90)",
        },
        "dark": {
            "window": "#1D1F23", "sidebar": "#1A1C20", "panel": "#26292F",
            "panel_alt": "#2E323A", "text": "#E6E9EE", "subtext": "#989FAB",
            "border": "#373C45", "border_strong": "#454B56", "divider": "#373C45",
            "hover": "#30353D", "hover_accent": "#2E333D", "pressed": "#383D46",
            "accent_soft": "#2E3246",
            "topbar_bg": "#1D1F23", "topbar_text": "#E6E9EE",
            "statusbar_bg": "#191B1F", "statusbar_text": "#989FAB",
            "input_bg": "#26292F", "icon": "#C5CAD3",
            "radius_sm": "4", "radius": "6", "radius_lg": "8", "radius_pill": "12",
            "font_title": _SANS_STACK,
            "window_bg": "#1D1F23",
            "table_header_bg": "#2E323A", "table_header_border": "#454B56",
            "section_gap": "0",
            "shadow_blur1": "14", "shadow_dy1": "2", "shadow_alpha1": "45",
            "shadow_blur2": "26", "shadow_dy2": "6", "shadow_alpha2": "65",
            "card_shadow_rest_blur": "10", "card_shadow_rest_dy": "2", "card_shadow_rest_alpha": "34",
            "card_shadow_hover_blur": "16", "card_shadow_hover_dy": "3", "card_shadow_hover_alpha": "54",
            "card_shadow_sel_blur": "22", "card_shadow_sel_dy": "5", "card_shadow_sel_alpha": "78",
            # ---- 磨砂哑光（深色）----
            "noise_name": "noise_dark.png",
            "sidebar_glass": "rgba(26,28,32,0.96)",
            "glass_border": "rgba(255,255,255,0.09)",
        },
    },
}

# 结构令牌默认值，风格未显式给出时回退
_DEFAULT_STRUCTURE = {
    "radius_sm": "6", "radius": "8", "radius_lg": "12", "radius_pill": "16",
    "font_title": _SANS_STACK,
    "window_bg": None,
    "table_header_bg": "$panel", "table_header_border": "$border",
    "section_gap": "0",
    # 浮层阴影（对话框近 / 远两档，保持既有观感）
    "shadow_blur1": "18", "shadow_dy1": "3", "shadow_alpha1": "22",
    "shadow_blur2": "34", "shadow_dy2": "10", "shadow_alpha2": "28",
    # 卡片/控件五态阴影梯级：rest < hover < selected（柔和扩散，非硬投影）
    "card_shadow_rest_blur": "14", "card_shadow_rest_dy": "3", "card_shadow_rest_alpha": "16",
    "card_shadow_hover_blur": "24", "card_shadow_hover_dy": "5", "card_shadow_hover_alpha": "28",
    "card_shadow_sel_blur": "32", "card_shadow_sel_dy": "8", "card_shadow_sel_alpha": "42",
}
_DEFAULT_STRUCTURE_DARK = {
    **_DEFAULT_STRUCTURE,
    "shadow_alpha1": "48", "shadow_alpha2": "72",
    "card_shadow_rest_alpha": "40",
    "card_shadow_hover_alpha": "64",
    "card_shadow_sel_alpha": "92",
}


def build_theme(settings: Settings) -> dict[str, str]:
    """在基础模式上叠加外观风格、写作区预设与用户自定义颜色。"""
    theme = dict(base_theme(settings.mode))

    # 1) 外观风格覆盖（色板 + 结构令牌），先于派生色计算
    profile = STYLE_PROFILES.get(getattr(settings, "ui_style", "default"))
    if profile:
        theme.update(profile.get(settings.mode, {}))

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

    # ---- 五态交互令牌（rest / hover / selected / focus / disabled 统一来源）----
    is_dark = settings.mode == "dark"
    # selected 用强调色低透明底 + 强调色描边，明显强于 hover 的中性灰
    theme["selected_bg"] = rgba(theme["accent"], 0.14 if is_dark else 0.09)
    theme["selected_bg_strong"] = rgba(theme["accent"], 0.24 if is_dark else 0.15)
    theme["selected_border"] = rgba(theme["accent"], 0.60 if is_dark else 0.50)
    # hover 边框提亮（中性，不与 selected 的强调色混淆）
    theme["hover_border"] = "rgba(255,255,255,0.16)" if is_dark else "rgba(30,40,60,0.16)"
    # disabled 弱化文字
    _tc = QColor(theme["text"])
    theme["disabled_text"] = QColor(_tc.red(), _tc.green(), _tc.blue(),
                                    110 if is_dark else 120).name(QColor.HexArgb)
    theme["disabled_bg"] = theme["panel_alt"]
    # 柔和阴影颜色（深色模式下更重一些，保证可见但柔和）
    theme["shadow_alpha"] = 0.10 if settings.mode == "light" else 0.38
    theme["shadow_color"] = "#4A5568" if settings.mode == "dark" else "#000000"
    # 表面质感种类：plain（默认/纸感）/ glass（液态玻璃）/ matte（磨砂秩序）
    # 玻璃与秩序在 STYLE_PROFILES 中自带 sidebar_glass/glass_border/liq_*/noise_*；
    # 默认与纸感保持历史观感不动
    style_name = getattr(settings, "ui_style", "default")
    theme["surface_kind"] = {"glass": "glass", "order": "matte"}.get(style_name, "plain")
    if "sidebar_glass" not in theme:
        theme["sidebar_glass"] = (
            "rgba(255, 255, 255, 0.06)" if settings.mode == "dark" else "rgba(255, 255, 255, 0.65)"
        )
    if "glass_border" not in theme:
        theme["glass_border"] = (
            "rgba(255, 255, 255, 0.14)" if settings.mode == "dark" else "rgba(255, 255, 255, 0.80)"
        )

    # 2) 结构令牌补默认（风格未给的键回退到现有观感）
    defaults = _DEFAULT_STRUCTURE_DARK if settings.mode == "dark" else _DEFAULT_STRUCTURE
    for key, value in defaults.items():
        theme.setdefault(key, value)
    # 结构令牌允许用 "$色板键" 引用同名字段（单趟 Template 替换不会递归），这里先解析
    for key, value in list(theme.items()):
        if isinstance(value, str) and value.startswith("$"):
            theme[key] = theme.get(value[1:], theme["panel"])
    if not theme.get("window_bg"):
        theme["window_bg"] = theme["window"]
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




@lru_cache(maxsize=1)
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
    # 液态玻璃：QSS 的面令牌改用半透明值（Python 自绘/调色板仍用实色 theme["panel"]，
    # 输入框/正文保持实色可读）；磨砂与普通风格沿用实色。
    kind = theme.get("surface_kind", "plain")
    panel_qss = theme.get("qss_panel", theme["panel"])
    panel_alt_qss = theme.get("qss_panel_alt", theme["panel_alt"])
    values = {
        "window": theme["window"],
        "sidebar": theme.get("qss_sidebar", theme["sidebar"]),
        "panel": panel_qss,
        "panel_alt": panel_alt_qss,
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
        "toolbar_bg": theme.get("qss_toolbar", theme["topbar_bg"]),
        "toolbar_text": theme["topbar_text"],
        "statusbar_bg": theme["statusbar_bg"],
        "statusbar_text": theme["statusbar_text"],
        "input_bg": theme["panel"],
        "danger": theme["danger"],
        "sidebar_glass": theme["sidebar_glass"],
        "glass_border": theme["glass_border"],
        "liq_top": theme.get("liq_top", "rgba(255,255,255,0)"),
        "liq_bottom": theme.get("liq_bottom", "rgba(0,0,0,0)"),
        "noise_url": "none",
        # 五态令牌
        "selected_bg": theme["selected_bg"],
        "selected_bg_strong": theme["selected_bg_strong"],
        "selected_border": theme["selected_border"],
        "hover_border": theme["hover_border"],
        "disabled_text": theme["disabled_text"],
        "disabled_bg": theme["disabled_bg"],
        "font_family": str(DESIGN["font_family"]),
        "font_size": int(DESIGN["font_size"]),
        "radius_sm": int(theme.get("radius_sm", DESIGN["radius_sm"])),
        "radius": int(theme.get("radius", DESIGN["radius"])),
        "radius_lg": int(theme.get("radius_lg", DESIGN["radius_lg"])),
        "radius_pill": int(theme.get("radius_pill", DESIGN["radius_pill"])),
        "font_title": str(theme.get("font_title") or DESIGN["font_family"]),
        "window_bg": str(theme.get("window_bg") or theme["window"]),
        "table_header_bg": str(theme.get("table_header_bg") or "transparent"),
        "table_header_border": str(theme.get("table_header_border") or "transparent"),
        "section_gap": int(theme.get("section_gap", 0)),
        "btn_pad_x": int(DESIGN["btn_pad_x"]),
        "btn_pad_y": int(DESIGN["btn_pad_y"]),
    }
    qss = Template(_load_qss_template()).substitute(**values)
    qss += _surface_qss(theme, kind)
    return qss


def _surface_qss(theme: dict, kind: str) -> str:
    """外观风格专属 QSS 追加块（在通用模板之后，同特异性下后写生效）。

    glass：侧栏边缘高光亮/暗边；抽屉与对话框卡片改由自绘液态表面接管（透明底）。
    matte：主要骨架面平铺预生成极淡噪点（QSS background-image，自动平铺零运行时开销）。
    plain（默认/纸感）：无追加，历史观感保持不动。
    """
    if kind == "glass":
        return (
            "\n/* ---- 液态玻璃：边缘光学 + 自绘表面让位 ---- */\n"
            "#Sidebar { border-top: 1px solid $liq_top;"
            " border-bottom: 1px solid $liq_bottom; }\n"
            "#TopBar { border-bottom: 1px solid $liq_bottom; }\n"
            "#Drawer, QFrame#MessageBox, #SnippetPanel {"
            " background: transparent; border: none; }\n"
            "\n/* 原生弹出层（菜单 / 下拉 / tooltip）不是自绘玻璃面，改为实底+描边，"
            "避免文字被下面正文透上来；外层玻璃由父窗口保持 */\n"
            f"QMenu, QToolTip, QComboBox QAbstractItemView, QFontComboBox QAbstractItemView {{"
            f" background: {theme.get('panel', '#FFFFFF')};"
            f" border: 1px solid {theme.get('border_strong', 'rgba(0,0,0,0.18)')}; }}\n"
        ).replace("$liq_top", theme.get("liq_top", "rgba(255,255,255,0)")).replace(
            "$liq_bottom", theme.get("liq_bottom", "rgba(0,0,0,0)"))
    if kind == "matte":
        from PySide6.QtCore import QUrl

        from app.config import ASSETS_DIR

        noise = ASSETS_DIR / str(theme.get("noise_name", "noise_light.png"))
        url = QUrl.fromLocalFile(str(noise)).toString()
        return (
            "\n/* ---- 磨砂哑光：预生成极淡噪点平铺（无高光、无反光）---- */\n"
            f"#Sidebar, #TopBar, #Drawer, QFrame#MessageBox, #SnippetPanel {{"
            f" background-image: url({url}); background-repeat: repeat; }}\n"
        )
    return ""
