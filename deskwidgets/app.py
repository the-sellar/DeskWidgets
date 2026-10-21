"""Главный объект программы: держит настройки, трей, модули и общие таймеры."""

import importlib
import os
import subprocess

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QMessageBox

from . import autostart, theme, winutils
from .logger import log
from .paths import appdata_dir
from .tray import Tray
from .widget_base import default_screen

# key модуля → (файл, класс). Импортируем лениво: если у модуля не установлена
# зависимость, упадёт только он, а не вся программа.
MODULES = {
    "music": ("deskwidgets.modules.now_playing", "NowPlayingModule"),
    "monitor": ("deskwidgets.modules.monitor", "MonitorModule"),
    "shutdown": ("deskwidgets.modules.shutdown", "ShutdownModule"),
    "macros": ("deskwidgets.modules.macros", "MacrosModule"),
}
MODULE_TITLES = {"music": "Сейчас играет", "monitor": "Монитор", "shutdown": "Таймер выключения",
                 "macros": "Макросы (кнопки)"}
# Порядок виджетов «по умолчанию» сверху вниз
STACK_ORDER = ["music", "monitor", "macros"]


class DeskWidgetsApp(QObject):
    edit_mode_changed = Signal(bool)

    def __init__(self, qapp, cfg):
        super().__init__()
        self.qapp = qapp
        self.cfg = cfg
        self.widgets_visible = bool(cfg.get("general.widgets_visible", True))
        self.edit_mode = False
        self.colors = theme.resolve_colors(cfg.get("appearance"))
        self.modules: dict = {}
        self.settings_window = None

        self.tray = Tray(self)

        # Раз в секунду: обновление модулей и проверка полноэкранных окон
        self.tick_timer = QTimer(self)
        self.tick_timer.setInterval(1000)
        self.tick_timer.timeout.connect(self._tick)
        # ~15 раз в секунду: где курсор (для прозрачности при наведении). Работа крошечная.
        self.hover_timer = QTimer(self)
        self.hover_timer.setInterval(66)
        self.hover_timer.timeout.connect(self._hover_tick)

        cfg.changed.connect(self._on_config_changed)

        # Подключение/отключение мониторов → переставить виджеты
        self._relayout_timer = QTimer(self)
        self._relayout_timer.setSingleShot(True)
        self._relayout_timer.setInterval(700)
        self._relayout_timer.timeout.connect(self.place_widgets)
        self.qapp.screenAdded.connect(self._on_screens_changed)
        self.qapp.screenRemoved.connect(self._on_screens_changed)
        self.qapp.primaryScreenChanged.connect(self._on_screens_changed)
        for s in self.qapp.screens():
            self._watch_screen(s)

        # Смена светлой/тёмной темы Windows (Qt 6.5+)
        try:
            self.qapp.styleHints().colorSchemeChanged.connect(lambda *_: self._apply_theme())
        except AttributeError:
            pass

        for key in MODULES:
            if cfg.get(f"modules.{key}", True):
                self.enable_module(key, relayout=False)
        self.place_widgets()

        autostart.refresh_registry_path(cfg.get("general.autostart"), cfg.get("general.run_as_admin"))

        self.tray.show()
        self.tick_timer.start()
        self.hover_timer.start()
        if getattr(cfg, "first_run", False):
            cfg.save()  # создаём файл сразу, чтобы приветствие больше не показывалось
            QTimer.singleShot(800, self._welcome)
        log.info("Запуск завершён. Права администратора: %s", winutils.is_admin())

    # ================= модули =================

    def enable_module(self, key: str, relayout: bool = True) -> None:
        if key in self.modules:
            return
        try:
            mod_name, cls_name = MODULES[key]
            cls = getattr(importlib.import_module(mod_name), cls_name)
            module = cls(self)
            module.start()
            self.modules[key] = module
            for w in module.widgets():
                w.set_edit_mode(self.edit_mode)
            log.info("Модуль «%s» запущен", MODULE_TITLES[key])
        except Exception:
            log.exception("Модуль «%s» не запустился", MODULE_TITLES.get(key, key))
            self.tray.notify("DeskWidgets", f"Модуль «{MODULE_TITLES.get(key, key)}» не запустился. "
                                            f"Подробности в log.txt")
            return
        if relayout:
            self.place_widgets()

    def disable_module(self, key: str) -> None:
        module = self.modules.pop(key, None)
        if module is None:
            return
        try:
            module.stop()
        except Exception:
            log.exception("Ошибка при остановке модуля %s", key)

    def all_widgets(self) -> list:
        result = []
        for m in self.modules.values():
            try:
                result.extend(m.widgets())
            except Exception:
                pass
        return result

    # ================= расстановка виджетов =================

    def place_widgets(self) -> None:
        """Ставит каждый виджет: либо на сохранённое место, либо (режим «авто») в правый
        нижний угол второго монитора стопкой: сверху музыка, под ней монитор."""
        by_key = {w.key: w for w in self.all_widgets()}
        screen = default_screen()
        area = screen.availableGeometry()  # без панели задач
        margin, gap = 12, 8
        bottom = area.y() + area.height() - margin
        for key in reversed(STACK_ORDER):  # снизу вверх
            w = by_key.get(key)
            if w is None:
                continue
            w.set_scale(self.cfg.get(f"widgets.{key}.scale", 1.0))
            if w.has_saved_pos():
                w.restore_saved_position()
                continue
            x = area.x() + area.width() - margin - w.width()
            y = bottom - w.height()
            w.move(x, y)
            bottom = y - gap
        for w in by_key.values():
            w.refresh_visibility()

    def on_widget_moved(self, _widget) -> None:
        pass  # место для будущих реакций; позиция уже сохранена самим виджетом

    def reset_positions(self) -> None:
        for key in STACK_ORDER:
            self.cfg.set(f"widgets.{key}.pos", None)
            self.cfg.set(f"widgets.{key}.scale", 1.0)
        self.place_widgets()

    def _watch_screen(self, screen) -> None:
        screen.geometryChanged.connect(self._on_screens_changed)
        screen.availableGeometryChanged.connect(self._on_screens_changed)

    def _on_screens_changed(self, *args) -> None:
        for a in args:
            if hasattr(a, "availableGeometryChanged"):
                try:
                    self._watch_screen(a)
                except Exception:
                    pass
        # Ждём немного: Windows присылает несколько событий подряд
        self._relayout_timer.start()

    # ================= режимы =================

    def set_widgets_visible(self, visible: bool) -> None:
        self.widgets_visible = visible
        self.cfg.set("general.widgets_visible", visible)
        for w in self.all_widgets():
            w.refresh_visibility()

    def set_edit_mode(self, on: bool) -> None:
        self.edit_mode = on
        for w in self.all_widgets():
            w.set_edit_mode(on)
        self.tray.refresh()
        self.edit_mode_changed.emit(on)

    # ================= таймеры =================

    def _tick(self) -> None:
        widgets = self.all_widgets()
        hide_fs = self.cfg.get("general.hide_on_fullscreen", True)
        fs_monitors: set = set()
        if hide_fs and widgets:
            try:
                fs_monitors = winutils.fullscreen_monitors(os.getpid())
            except Exception as e:
                log.debug("fullscreen check: %s", e)
        for w in widgets:
            try:
                hidden = hide_fs and winutils.monitor_of_window(int(w.winId())) in fs_monitors
                w.set_fullscreen_hidden(hidden)
            except Exception:
                pass
        for m in list(self.modules.values()):
            m.safe_tick()

    def _hover_tick(self) -> None:
        if not self.widgets_visible:
            return
        widgets = self.all_widgets()
        if not widgets:
            return
        pos = QCursor.pos()
        ctrl = winutils.is_key_down(winutils.VK_CONTROL)
        for w in widgets:
            w.update_hover(pos, ctrl)

    # ================= настройки =================

    def _apply_theme(self) -> None:
        self.colors = theme.resolve_colors(self.cfg.get("appearance"))
        for w in self.all_widgets():
            w.apply_theme(self.colors)

    def _on_config_changed(self, key: str) -> None:
        section = key.split(".")[0]
        if section == "appearance":
            self._apply_theme()
        elif key == "general.layer":
            for w in self.all_widgets():
                w.reapply_layer()
            self.place_widgets()
        elif key == "general.hide_on_fullscreen":
            for w in self.all_widgets():
                w.set_fullscreen_hidden(False)
        elif section == "modules":
            mod_key = key.split(".")[1]
            if self.cfg.get(key):
                self.enable_module(mod_key)
            else:
                self.disable_module(mod_key)
            self.tray.refresh()
        elif key.startswith("widgets.") and key.endswith(".pinned"):
            for w in self.all_widgets():
                w.update()
        for m in list(self.modules.values()):
            m.safe(m.on_config_changed, key)

    def show_settings(self, tab: int | None = None, macro: int | None = None) -> None:
        from .settings_window import SettingsWindow  # импорт здесь — окно создаём только по требованию
        if self.settings_window is None:
            self.settings_window = SettingsWindow(self)
        self.settings_window.open_window(tab, macro)

    def _welcome(self) -> None:
        """Окно-приветствие при первом запуске: где иконка и как пользоваться."""
        box = QMessageBox()
        box.setWindowIcon(theme.app_icon())
        box.setIconPixmap(theme.paint_icon(64))
        box.setWindowTitle("DeskWidgets")
        box.setTextFormat(Qt.RichText)
        box.setText("<b>Привет! DeskWidgets запущен.</b>")
        box.setInformativeText(
            "• Виджеты появились в правом нижнем углу экрана (на втором мониторе, если он есть).<br>"
            "• Программа живёт в <b>трее</b> — справа внизу у часов, возможно под стрелкой «^». "
            "Правый клик по иконке — всё меню.<br>"
            "• Когда наводишь мышь на виджет, он становится прозрачным и не мешает кликать по окнам "
            "под ним. Чтобы нажать кнопку на виджете — <b>держи Ctrl</b>.<br>"
            "• Подвинуть виджеты: меню трея → «Режим редактирования».<br>"
            "• Темы, макросы, таймер выключения — в настройках.")
        open_btn = box.addButton("Открыть настройки", QMessageBox.AcceptRole)
        box.addButton("Понятно", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is open_btn:
            self.show_settings()

    def open_data_folder(self) -> None:
        subprocess.Popen(["explorer", appdata_dir()])

    # ================= выход =================

    def quit(self) -> None:
        log.info("Выход")
        self.tick_timer.stop()
        self.hover_timer.stop()
        for key in list(self.modules):
            self.disable_module(key)
        self.cfg.save()
        self.tray.hide()
        self.qapp.quit()
