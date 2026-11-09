# -*- coding: utf-8 -*-
"""统一的动效与阴影辅助。

- 缓动与时长常量：全应用反馈动效统一 150–300ms ease-out；
- apply_elevation：两档轻阴影（浮层使用；卡片 / 表格仍以 1px 边框为主）；
- fade_in / fade_out：子控件淡入淡出（基于 QGraphicsOpacityEffect，
  与阴影互斥：已有阴影效果的控件不再做透明度动画）。
"""
from __future__ import annotations

import weakref

try:
    import shiboken6
except ImportError:  # PySide6 正常都带 shiboken6；缺失时退化为只查弱引用
    shiboken6 = None

from PySide6.QtCore import (
    QAbstractAnimation, QEasingCurve, QEvent, QObject, QPropertyAnimation, Qt, QTimer,
)
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsDropShadowEffect,
    QGraphicsOpacityEffect,
    QVBoxLayout,
    QWidget,
)

EASE_OUT = QEasingCurve.OutCubic          # 统一 ease-out 曲线
DUR_FAST = 150                            # 按下 / 勾选等即时反馈
DUR_BASE = 200                            # hover 切换 / 淡入
DUR_SLOW = 280                            # 浮层 / 空状态出现

# 动效统一按 60fps 上限推进：Qt 动画框架本身固定 16ms 一帧（实测 16.1ms，
# 与屏幕刷新率无关），QTimer 驱动的循环不得低于该间隔，否则超频。
FPS_CAP = 60
FRAME_MS = 1000.0 / FPS_CAP
MIN_FRAME_INTERVAL_MS = 17

# level: (模糊半径, y 偏移, alpha 0-255) —— 默认与既有观感一致，
# 可由 set_elevation_tokens 按外观风格覆盖（玻璃风更扩散、纸感更收敛）。
_ELEVATION = {
    1: [18, 3, 22],     # 对话框 / 右键菜单等近距浮层
    2: [34, 10, 28],    # 抽屉 / 全屏大浮层
}

# 卡片 / 列表控件五态阴影梯级：rest < hover < selected（柔和扩散）。
# 由 set_elevation_tokens 按风格 + 深浅写入；键顺序固定勿改。
CARD_ELEVATION = {
    "rest":     [14.0, 3.0, 16],
    "hover":    [24.0, 5.0, 28],
    "selected": [32.0, 8.0, 42],
}


def set_elevation_tokens(theme: dict) -> None:
    """apply_theme 时调用：按当前外观风格与深浅模式更新阴影参数（集中令牌）。"""
    def pick(level: int, blur_key: str, dy_key: str, alpha_key: str,
             d_blur, d_dy, d_alpha) -> None:
        try:
            blur = int(theme.get(blur_key, d_blur))
            dy = int(theme.get(dy_key, d_dy))
            alpha = int(theme.get(alpha_key, d_alpha))
        except (TypeError, ValueError):
            blur, dy, alpha = d_blur, d_dy, d_alpha
        _ELEVATION[level] = [blur, dy, alpha]

    pick(1, "shadow_blur1", "shadow_dy1", "shadow_alpha1", 18, 3, 22)
    pick(2, "shadow_blur2", "shadow_dy2", "shadow_alpha2", 34, 10, 28)

    def card(state: str, blur_key: str, dy_key: str, alpha_key: str,
             d_blur, d_dy, d_alpha) -> None:
        try:
            blur = float(theme.get(blur_key, d_blur))
            dy = float(theme.get(dy_key, d_dy))
            alpha = int(theme.get(alpha_key, d_alpha))
        except (TypeError, ValueError):
            blur, dy, alpha = d_blur, d_dy, d_alpha
        CARD_ELEVATION[state] = [blur, dy, alpha]

    card("rest", "card_shadow_rest_blur", "card_shadow_rest_dy", "card_shadow_rest_alpha", 14, 3, 16)
    card("hover", "card_shadow_hover_blur", "card_shadow_hover_dy", "card_shadow_hover_alpha", 24, 5, 28)
    card("selected", "card_shadow_sel_blur", "card_shadow_sel_dy", "card_shadow_sel_alpha", 32, 8, 42)


def apply_elevation(widget: QWidget, level: int = 1, dark: bool = False):
    """挂一次轻阴影（构造时调用；不要在 enter/leave 里反复挂卸）。"""
    blur, dy, alpha = _ELEVATION.get(level, _ELEVATION[1])
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(blur)
    effect.setOffset(0, dy)
    effect.setColor(QColor(15, 18, 30, alpha))
    widget.setGraphicsEffect(effect)
    return effect


def card_surface(
    dialog,
    gutter: int = 18,
    content: tuple[int, int, int, int] = (24, 20, 24, 16),
    spacing: int = 12,
    level: int = 1,
    readable: bool = False,
    force_readable: bool = False,
) -> QVBoxLayout:
    """把 QDialog 变成「透明无边框 + 圆角卡片 + 分层阴影」浮层，返回卡片内布局。

    gutter：dialog 边缘留给阴影的透明留白；content：卡片内部内容边距。
    与 dialogs.MessageBox 同一套视觉；addWidget 到返回布局的控件会自动 reparent。
    readable=True：字体选择 / 起名助手等文字密集浮层。玻璃风下仍用原值外壳
    （本体膜 0.55–0.72、侧壁、rim、sheen、噪点、色散），只在内部嵌入一层
    内缩 10px 的内容板（90–94% + 6% 顶部高光），不是整体提膜。
    """
    dialog.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
    dialog.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
    outer = QVBoxLayout(dialog)
    outer.setContentsMargins(gutter, gutter, gutter, gutter)
    # 液态玻璃风：卡片自绘缓存模糊背景（QSS 已让 #MessageBox 背景透明）；
    # 其它风格保持 QFrame + QSS 实色 / 磨砂噪点
    from app.ui import surface

    if surface.is_glass() or force_readable:
        card = (surface.ReadableCard(14.0, dialog) if (readable or force_readable)
                else surface.LiquidCard(14.0, dialog))
    else:
        card = QFrame(dialog)
        card.setObjectName("MessageBox")
    apply_elevation(card, level, app_is_dark())
    outer.addWidget(card)
    card_layout = QVBoxLayout(card)
    card_layout.setContentsMargins(*content)
    card_layout.setSpacing(spacing)
    dialog._surface_card = card   # 保活
    return card_layout


def app_is_dark() -> bool:
    """从当前应用调色板推断深浅（对话框 / 菜单构造时拿不到 theme 字典时用）。"""
    from PySide6.QtGui import QPalette
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        return False
    return app.palette().color(QPalette.ColorRole.Window).lightness() < 128


def _opacity_effect(widget: QWidget) -> QGraphicsOpacityEffect | None:
    existing = widget.graphicsEffect()
    if isinstance(existing, QGraphicsOpacityEffect):
        return existing
    if existing is not None:
        return None    # 已挂阴影等其他效果，不抢占
    effect = QGraphicsOpacityEffect(widget)
    widget.setGraphicsEffect(effect)
    return effect


def fade_in(widget: QWidget, duration: int = DUR_BASE) -> QPropertyAnimation | None:
    """淡入（控件需先 show / setVisible(True)；内部自动预挂并复位透明度）。"""
    effect = _opacity_effect(widget)
    if effect is None:
        return None
    effect.setOpacity(0.0)
    anim = QPropertyAnimation(effect, b"opacity", widget)
    anim.setDuration(duration)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.setEasingCurve(EASE_OUT)
    anim.start()
    widget._fade_anim = anim      # 保活，防止被 GC
    return anim


def fade_out(widget: QWidget, duration: int = DUR_FAST, on_finished=None) -> QPropertyAnimation | None:
    """淡出，结束后回调（通常在回调里 hide）。"""
    effect = _opacity_effect(widget)
    if effect is None:
        if on_finished is not None:
            on_finished()
        return None
    anim = QPropertyAnimation(effect, b"opacity", widget)
    anim.setDuration(duration)
    anim.setStartValue(effect.opacity())
    anim.setEndValue(0.0)
    anim.setEasingCurve(EASE_OUT)
    if on_finished is not None:
        anim.finished.connect(on_finished)
    anim.start()
    widget._fade_anim = anim
    return anim


# ----------------------------------------------------------------------
# 动效闸门：不可见时停表，恢复后原地续播
# ----------------------------------------------------------------------
# 窗口最小化 / 切到其它程序 / Hide 时，动画定时器不再推进：
# - QAbstractAnimation（淡入淡出、卡片 hover、抽屉、圆环…）统一 pause()，
#   恢复时 resume()，currentTime 原样保留，不会跳帧也不会停在半路；
# - QTimer 驱动的循环（保存圆环、码字时速条）stop() 并记住运行态，恢复再 start()。
# 多个挂起原因（应用失焦 + 最小化 + 隐藏）按引用计数合并，任一解除才续播。


def _alive(obj) -> bool:
    """包装对象是否仍可用：弱引用未失效，且 C++ 侧尚未析构。

    Qt 父对象销毁动画后，Python 包装可能还活着（弱引用不为 None），
    此时调用 state()/pause() 会访问已释放的 C++ 对象，必须先剪掉。
    """
    if obj is None:
        return False
    return shiboken6 is None or shiboken6.isValid(obj)


class _AnimationGate(QObject):
    def __init__(self) -> None:
        super().__init__()
        self._anims: dict[int, weakref.ref] = {}
        self._paused: set[int] = set()
        self._timers: dict[int, weakref.ref] = {}
        self._stopped: set[int] = set()
        self._holds: set[str] = set()
        self._app_hooked = False

    def _ensure_app_hook(self) -> None:
        if self._app_hooked:
            return
        app = QApplication.instance()
        if app is None:
            return
        app.installEventFilter(self)
        self._app_hooked = True

    def register(self, anim: QAbstractAnimation) -> None:
        if not _alive(anim):
            return
        self._ensure_app_hook()
        key = id(anim)
        self._anims[key] = weakref.ref(anim)
        if not self._holds:
            return
        try:
            if anim.state() == QAbstractAnimation.State.Running:
                anim.pause()
                self._paused.add(key)
        except RuntimeError:
            self._anims.pop(key, None)

    def after_start(self, anim: QAbstractAnimation) -> None:
        """start() 之后复查：挂起期间才启动的动画要立刻暂停在 0 帧。"""
        if not _alive(anim):
            return
        key = id(anim)
        self._anims[key] = weakref.ref(anim)
        if not self._holds:
            return
        try:
            if anim.state() == QAbstractAnimation.State.Running:
                anim.pause()
                self._paused.add(key)
        except RuntimeError:
            self._anims.pop(key, None)

    def register_timer(self, timer: QTimer) -> None:
        """登记动画循环定时器；帧间隔低于 60fps 上限时钳到 17ms。"""
        if not _alive(timer):
            return
        self._ensure_app_hook()
        try:
            interval = int(timer.interval())
            if 0 < interval < MIN_FRAME_INTERVAL_MS:
                timer.setInterval(MIN_FRAME_INTERVAL_MS)
        except (RuntimeError, TypeError):
            return
        self._timers[id(timer)] = weakref.ref(timer)

    def hold(self, reason: str) -> None:
        if reason in self._holds:
            return
        self._holds.add(reason)
        if len(self._holds) == 1:
            self._suspend()

    def release(self, reason: str) -> None:
        if reason not in self._holds:
            return
        self._holds.discard(reason)
        if not self._holds:
            self._resume()

    def _prune(self) -> None:
        for key, ref in list(self._anims.items()):
            if not _alive(ref()):
                self._anims.pop(key, None)
                self._paused.discard(key)
        for key, ref in list(self._timers.items()):
            if not _alive(ref()):
                self._timers.pop(key, None)
                self._stopped.discard(key)

    def _suspend(self) -> None:
        self._prune()
        dead: list[int] = []
        for key, ref in list(self._anims.items()):
            anim = ref()
            if not _alive(anim):
                dead.append(key)
                continue
            try:
                if anim.state() == QAbstractAnimation.State.Running:
                    anim.pause()
                    self._paused.add(key)
            except RuntimeError:
                dead.append(key)
        for key in dead:
            self._anims.pop(key, None)
            self._paused.discard(key)
        dead.clear()
        for key, ref in list(self._timers.items()):
            timer = ref()
            if not _alive(timer):
                dead.append(key)
                continue
            try:
                if timer.isActive():
                    timer.stop()
                    self._stopped.add(key)
            except RuntimeError:
                dead.append(key)
        for key in dead:
            self._timers.pop(key, None)
            self._stopped.discard(key)

    def _resume(self) -> None:
        self._prune()
        dead: list[int] = []
        for key in list(self._paused):
            ref = self._anims.get(key)
            anim = ref() if ref is not None else None
            if not _alive(anim):
                dead.append(key)
                continue
            try:
                if anim.state() == QAbstractAnimation.State.Paused:
                    anim.resume()
            except RuntimeError:
                dead.append(key)
        self._paused.clear()
        for key in dead:
            self._anims.pop(key, None)
        dead.clear()
        for key in list(self._stopped):
            ref = self._timers.get(key)
            timer = ref() if ref is not None else None
            if not _alive(timer):
                dead.append(key)
                continue
            try:
                if not timer.isActive():
                    timer.start()
            except RuntimeError:
                dead.append(key)
        self._stopped.clear()
        for key in dead:
            self._timers.pop(key, None)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if event.type() == QEvent.ApplicationStateChange:
            app = QApplication.instance()
            if app is not None and app.applicationState() == Qt.ApplicationState.ApplicationActive:
                self.release("app_inactive")
            else:
                self.hold("app_inactive")
        return False


_GATE: _AnimationGate | None = None
_HOOKED = False


def _gate() -> _AnimationGate:
    global _GATE
    if _GATE is None:
        _GATE = _AnimationGate()
    return _GATE


def _install_animation_hook() -> None:
    """接管所有 QAbstractAnimation.start：登记实例，供不可见时统一暂停。

    只包一层 Python 侧的 start（PySide 子类均继承该方法），不动 C++ 行为，
    异常一律不向调用方抛出，避免动效故障影响交互。
    """
    global _HOOKED
    if _HOOKED:
        return
    _HOOKED = True
    original_start = QAbstractAnimation.start

    def _start(self, *args, **kwargs):
        gate = _gate()
        try:
            gate.register(self)
        except (RuntimeError, TypeError):
            pass
        result = original_start(self, *args, **kwargs)
        try:
            gate.after_start(self)
        except (RuntimeError, TypeError):
            pass
        return result

    QAbstractAnimation.start = _start


def register_timer(timer: QTimer) -> None:
    """登记 QTimer 驱动的动画循环（保存圆环、时速条等）。"""
    _gate().register_timer(timer)


def hold_animations(reason: str) -> None:
    """挂起动效（窗口不可见 / 应用失焦）。"""
    _gate().hold(reason)


def release_animations(reason: str) -> None:
    """解除挂起，从当前帧继续。"""
    _gate().release(reason)


def animations_suspended() -> bool:
    return bool(_GATE is not None and _GATE._holds)


_install_animation_hook()
