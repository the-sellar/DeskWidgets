"""Меню «Тема»: используется и в трее, и в меню правого клика по виджету.

Меню заполняется заново при каждом открытии — поэтому в нём всегда видны
свои сохранённые пресеты, а галочка стоит на теме, которая сейчас применена.
"""

from PySide6.QtGui import QActionGroup, QColor, QIcon, QPainter, QPixmap
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMenu

from . import theme

MODES = [("system", "Как в Windows"), ("light", "Светлая"), ("dark", "Тёмная")]


def _swatch(bg: str, accent: str) -> QIcon:
    """Маленький значок-образец: кружок цвета фона с точкой акцентного цвета."""
    pm = QPixmap(16, 16)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QColor(128, 128, 128))
    p.setBrush(QColor(bg))
    p.drawEllipse(1, 1, 14, 14)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(accent))
    p.drawEllipse(5, 5, 6, 6)
    p.end()
    return QIcon(pm)


def _preset_bg(data: dict) -> str:
    mode = data.get("theme")
    if mode == "custom":
        return data.get("custom_bg", "#333333")
    return "#F4F4F4" if mode == "light" else "#1C1C1E"


def _matches(cfg, data: dict) -> bool:
    return all(cfg.get(f"appearance.{k}") == data[k] for k in theme.PRESET_KEYS if k in data)


def apply_preset(cfg, data: dict) -> None:
    cfg.set_many({f"appearance.{k}": data[k] for k in theme.PRESET_KEYS if k in data}, "appearance")


def fill_theme_menu(menu: QMenu, app) -> None:
    cfg = app.cfg
    menu.clear()
    group = QActionGroup(menu)  # группа делает галочки «одна из многих»

    menu.addSection("Режим")
    current_mode = cfg.get("appearance.theme")
    for mode, title in MODES:
        act = menu.addAction(title)
        act.setCheckable(True)
        act.setChecked(current_mode == mode)
        act.setActionGroup(group)
        act.triggered.connect(lambda _=False, m=mode: cfg.set("appearance.theme", m))

    menu.addSection("Темы")
    presets = list(theme.BUILTIN_PRESETS.items())
    presets += [(f"★ {n}", d) for n, d in cfg.get("user_presets", {}).items()]
    for name, data in presets:
        act = menu.addAction(_swatch(_preset_bg(data), data.get("accent_color", "#4FC3F7")), name)
        act.setCheckable(True)
        act.setChecked(_matches(cfg, data))
        act.setActionGroup(group)
        act.triggered.connect(lambda _=False, d=data: apply_preset(cfg, d))

    menu.addSeparator()
    menu.addAction("Настроить цвета и шрифт…").triggered.connect(lambda: app.show_settings(tab=1))


def make_theme_menu(parent_menu: QMenu, app, title: str = "Тема") -> QMenu:
    sub = parent_menu.addMenu(title)
    sub.aboutToShow.connect(lambda: fill_theme_menu(sub, app))
    fill_theme_menu(sub, app)
    return sub
