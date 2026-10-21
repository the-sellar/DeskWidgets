"""Иконка в трее (область уведомлений справа на панели задач) и её меню."""

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from . import theme
from .theme_menu import make_theme_menu


class Tray(QObject):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.icon = QSystemTrayIcon(theme.app_icon())
        self.icon.setToolTip("DeskWidgets")

        self.menu = QMenu()
        self.act_show = self.menu.addAction("Показывать виджеты")
        self.act_show.setCheckable(True)
        self.act_show.triggered.connect(lambda checked: app.set_widgets_visible(checked))

        self.act_edit = self.menu.addAction("Режим редактирования")
        self.act_edit.setCheckable(True)
        self.act_edit.triggered.connect(lambda checked: app.set_edit_mode(checked))

        make_theme_menu(self.menu, app)

        # Макросы можно запускать и из трея (удобно, если панель кнопок спрятана)
        self.macros_menu = self.menu.addMenu("Макросы")
        self.macros_menu.aboutToShow.connect(self._fill_macros)

        # --- подменю таймера выключения ---
        self.timer_menu = self.menu.addMenu("Таймер выключения")
        self.act_timer_status = self.timer_menu.addAction("")
        self.act_timer_status.setEnabled(False)
        self.timer_menu.addSeparator()
        for minutes, text in ((30, "Выключить через 30 мин"), (60, "Выключить через 1 ч"),
                              (120, "Выключить через 2 ч")):
            act = self.timer_menu.addAction(text)
            act.triggered.connect(lambda _=False, m=minutes: self._quick(m))
        self.timer_menu.addAction("Своё время…").triggered.connect(self._custom)
        self.timer_menu.addSeparator()
        self.act_cancel = self.timer_menu.addAction("Отменить")
        self.act_cancel.triggered.connect(self._cancel)

        self.menu.addSeparator()
        self.menu.addAction("Настройки…").triggered.connect(app.show_settings)
        self.menu.addSeparator()
        self.menu.addAction("Выход").triggered.connect(app.quit)

        # Меню обновляем прямо перед показом — так в нём всегда актуальное состояние
        self.menu.aboutToShow.connect(self.refresh)
        self.icon.setContextMenu(self.menu)
        self.icon.activated.connect(self._on_activated)

    def show(self) -> None:
        self.refresh()
        self.icon.show()

    def hide(self) -> None:
        self.icon.hide()

    def refresh(self) -> None:
        self.act_show.setChecked(self.app.widgets_visible)
        self.act_edit.setChecked(self.app.edit_mode)
        shutdown = self.app.modules.get("shutdown")
        self.timer_menu.setEnabled(shutdown is not None)
        if shutdown is None:
            self.timer_menu.setTitle("Таймер выключения (модуль выключен)")
            self.icon.setToolTip("DeskWidgets")
            return
        self.timer_menu.setTitle("Таймер выключения")
        status = shutdown.status_text()
        self.act_timer_status.setText(status)
        self.act_cancel.setEnabled(shutdown.has_pending())
        self.icon.setToolTip(f"DeskWidgets\n{status}")

    def notify(self, title: str, message: str) -> None:
        """Всплывающее уведомление Windows от имени иконки в трее."""
        self.icon.showMessage(title, message, QSystemTrayIcon.Information, 6000)

    # ---------- обработчики ----------

    def _on_activated(self, reason) -> None:
        # Двойной щелчок по иконке — открыть настройки
        if reason == QSystemTrayIcon.DoubleClick:
            self.app.show_settings()

    def _fill_macros(self) -> None:
        self.macros_menu.clear()
        module = self.app.modules.get("macros")
        if module is None:
            self.macros_menu.addAction("(модуль выключен)").setEnabled(False)
            return
        for i, m in enumerate(module.macros()):
            if m.get("steps"):
                name = m.get("name") or f"Макрос {i + 1}"
                self.macros_menu.addAction(f"{i + 1}. {name}").triggered.connect(
                    lambda _=False, idx=i: module.run(idx))
        self.macros_menu.addSeparator()
        self.macros_menu.addAction("Настроить кнопки…").triggered.connect(
            lambda: self.app.show_settings(tab=6))

    def _quick(self, minutes: int) -> None:
        m = self.app.modules.get("shutdown")
        if m:
            m.set_quick_in(minutes)

    def _custom(self) -> None:
        m = self.app.modules.get("shutdown")
        if m:
            m.ask_custom_time()

    def _cancel(self) -> None:
        m = self.app.modules.get("shutdown")
        if m:
            m.cancel_from_tray()
