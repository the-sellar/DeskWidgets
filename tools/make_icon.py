"""Создаёт assets/icon.ico для .exe (ту же картинку, что программа рисует в трее)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtGui import QGuiApplication  # noqa: E402

from deskwidgets.theme import paint_icon  # noqa: E402


def main() -> None:
    app = QGuiApplication(sys.argv)  # noqa: F841 — без приложения Qt рисовать нельзя
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out_dir = os.path.join(root, "assets")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "icon.ico")
    if paint_icon(256).save(path, "ICO"):
        print("Готово:", path)
    else:
        print("Не удалось сохранить иконку (сборка пройдёт и без неё)")


if __name__ == "__main__":
    main()
