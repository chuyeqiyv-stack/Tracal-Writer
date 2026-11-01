# -*- coding: utf-8 -*-
"""催客写作 —— 程序入口。

纯本地单机小说写作软件：所有数据都保存在本机 JSON 文件中，
不包含云同步、账号登录等任何联网功能。

运行方式：
    python main.py
"""
from __future__ import annotations

import sys

from PySide6.QtCore import QLibraryInfo, Qt, QTranslator
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QApplication

from app import config
from app.storage import Storage
from app.ui.main_window import MainWindow


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

    # Qt 自带组件（QFileDialog / QInputDialog 等）的按钮与文案翻译为中文
    translations_path = QLibraryInfo.path(QLibraryInfo.TranslationsPath)
    for name in ("qtbase_zh_CN", "qt_zh_CN"):
        translator = QTranslator(app)
        if translator.load(name, translations_path):
            app.installTranslator(translator)

    storage = Storage()
    window = MainWindow(storage)
    window.show()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
