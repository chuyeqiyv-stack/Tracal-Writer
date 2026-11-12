# -*- coding: utf-8 -*-
"""催客写作 —— 程序入口。

纯本地单机小说写作软件：所有数据都保存在本机 JSON 文件中，
不包含云同步、账号登录等任何联网功能。

运行方式：
    python main.py
"""
from __future__ import annotations

import sys

from app import startup_trace

from PySide6.QtCore import QLibraryInfo, Qt, QTimer, QTranslator
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from app import config
from app.single_instance import InstanceLock
from app.storage import Storage
from app.ui.main_window import MainWindow

startup_trace.mark("imports")

# v1.5.1：窗口版没有 stderr，未捕获异常写入 <数据目录>\logs\error.log。
# 安装失败或写日志失败都不能影响启动。
try:
    from app import error_log

    error_log.install(config.DATA_DIR)
except Exception:   # noqa: BLE001
    pass


def main() -> int:
    """创建应用、载入本地数据并显示主窗口。"""
    # 高分屏缩放：保持小数倍缩放，避免界面在 125% / 150% 下变糊
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    app.setApplicationName(config.APP_NAME)
    app.setApplicationDisplayName(config.APP_NAME)
    app.setOrganizationName(config.ORG_NAME)
    app.setApplicationVersion(config.APP_VERSION)
    app.setWindowIcon(QIcon(str(config.ASSETS_DIR / "app_icon.ico")))

    # Fusion 风格在各平台表现一致，也方便自定义 QSS 主题
    app.setStyle("Fusion")
    app.setFont(QFont(config.DEFAULT_FONT_FAMILY, 10))
    startup_trace.mark("qapp")

    # Qt 自带组件（QFileDialog / QInputDialog 等）的按钮与文案翻译为中文
    translations_path = QLibraryInfo.path(QLibraryInfo.TranslationsPath)
    for name in ("qtbase_zh_CN", "qt_zh_CN"):
        translator = QTranslator(app)
        if translator.load(name, translations_path):
            app.installTranslator(translator)

    # 同一数据目录只允许一个写入进程：两个窗口并存时，退出/自动保存的
    # 整份 library.json 回写会把另一进程刚保存的时间轴事件覆盖掉。
    lock = InstanceLock(config.DATA_DIR)
    if not lock.acquire():
        if lock.failure == "unwritable":
            # v1.5.3：只读数据目录同样会让 tryLock 失败，不能误报成“已在运行”
            QMessageBox.warning(
                None,
                "数据目录不可写",
                "无法写入数据目录：\n"
                f"{config.DATA_DIR}\n\n"
                "这不是「程序已在运行」，而是当前目录没有写入权限"
                "（或所在磁盘只读、空间已满）。\n\n"
                "请检查该目录的权限，或在设置中更换到一个可写的数据目录后重试。",
            )
        else:
            pid = lock.holder_pid()
            QMessageBox.warning(
                None,
                "催客写作已在运行",
                "检测到另一个「催客写作」正在使用同一数据目录：\n"
                f"{config.DATA_DIR}\n\n"
                + (f"占用进程 PID：{pid}\n\n" if pid else "")
                + "为避免两个窗口互相覆盖数据，本次启动已退出。\n"
                "请先关闭已打开的窗口，再重新启动。",
            )
        return 1
    app.aboutToQuit.connect(lock.release)
    startup_trace.mark("lock")

    storage = Storage()
    settings = storage.load_settings()
    startup_trace.mark("storage")
    startup_trace.dump("boot")

    if getattr(settings, "boot_animation", True):
        # 启动动画与主窗口初始化并行：先让莲花首帧上屏，下一事件循环节拍再构造主窗口。
        from app.ui.boot_lotus import BootSequence
        from app.ui.theme import build_theme

        boot = BootSequence(build_theme(settings), settings.mode == "dark")
        boot.start()

        def _build_main_window() -> None:
            boot.mark_ready(MainWindow(storage))

        QTimer.singleShot(0, _build_main_window)
    else:
        # 关闭动画时零额外开销
        window = MainWindow(storage)
        window.show()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
