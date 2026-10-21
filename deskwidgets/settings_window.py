"""Окно настроек. Вкладки: Общие, Внешний вид, Музыка, Монитор, VPN, Таймер.

Каждый элемент «привязан» к ключу настроек через Binder: при изменении элемента
значение сразу пишется в Config, Config шлёт сигнал changed, и виджеты на
рабочем столе перерисовываются. Поэтому кнопки «Применить» нет — всё сразу.
"""

import copy
import os
from contextlib import contextmanager

from PySide6.QtCore import Qt, QTime, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (QApplication, QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox,
                               QFileDialog,
                               QFontComboBox, QFormLayout, QGroupBox, QHBoxLayout, QInputDialog, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QScrollArea,
                               QSlider, QSpinBox, QTabWidget, QTimeEdit, QVBoxLayout, QWidget)

from . import autostart, theme, winutils
from .app import MODULE_TITLES
from .modules import macro_engine
from .modules import vpn as vpn_tools


@contextmanager
def blocked(widget):
    """Временно отключает сигналы элемента — чтобы загрузка значения не считалась «изменением»."""
    widget.blockSignals(True)
    try:
        yield
    finally:
        widget.blockSignals(False)


def info_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    lbl.setTextFormat(Qt.RichText)
    lbl.setOpenExternalLinks(True)
    lbl.setStyleSheet("color: #8C8C8C;")  # серый читается и на светлой, и на тёмной теме Windows
    return lbl


class ColorPicker(QWidget):
    """Кнопка-образец цвета (+ необязательная кнопка «Авто» = взять из темы)."""

    changed = Signal(str)

    def __init__(self, allow_auto: bool = False):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.btn = QPushButton()
        self.btn.setFixedSize(70, 24)
        self.btn.clicked.connect(self._pick)
        lay.addWidget(self.btn)
        if allow_auto:
            auto = QPushButton("Как в теме")
            auto.clicked.connect(lambda: (self.set_value(""), self.changed.emit("")))
            lay.addWidget(auto)
        lay.addStretch(1)
        self._value = ""

    def set_value(self, value: str) -> None:
        self._value = value or ""
        if self._value:
            text = theme.readable_text(QColor(self._value)).name()
            self.btn.setStyleSheet(f"background: {self._value}; color: {text}; border: 1px solid #888;")
            self.btn.setText(self._value.upper())
        else:
            self.btn.setStyleSheet("")
            self.btn.setText("авто")

    def _pick(self) -> None:
        c = QColorDialog.getColor(QColor(self._value or "#FFFFFF"), self, "Выбор цвета")
        if c.isValid():
            self.set_value(c.name())
            self.changed.emit(c.name())


class Binder:
    """Связывает элементы интерфейса с ключами настроек."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.loaders: list = []

    def _add(self, loader) -> None:
        self.loaders.append(loader)
        loader()

    def refresh(self) -> None:
        for load in self.loaders:
            load()

    def check(self, w: QCheckBox, key: str) -> QCheckBox:
        def load():
            with blocked(w):
                w.setChecked(bool(self.cfg.get(key)))
        w.toggled.connect(lambda v: self.cfg.set(key, bool(v)))
        self._add(load)
        return w

    def spin(self, w: QSpinBox, key: str) -> QSpinBox:
        def load():
            with blocked(w):
                w.setValue(int(self.cfg.get(key, 0)))
        w.valueChanged.connect(lambda v: self.cfg.set(key, int(v)))
        self._add(load)
        return w

    def combo(self, w: QComboBox, key: str, values: list) -> QComboBox:
        def load():
            v = self.cfg.get(key)
            with blocked(w):
                w.setCurrentIndex(values.index(v) if v in values else 0)
        w.currentIndexChanged.connect(lambda i: self.cfg.set(key, values[i]) if 0 <= i < len(values) else None)
        self._add(load)
        return w

    def line(self, w: QLineEdit, key: str) -> QLineEdit:
        def load():
            with blocked(w):
                w.setText(str(self.cfg.get(key, "")))
        w.editingFinished.connect(lambda: self.cfg.set(key, w.text().strip()))
        self._add(load)
        return w

    def time(self, w: QTimeEdit, key: str) -> QTimeEdit:
        def load():
            t = QTime.fromString(self.cfg.get(key, "00:00"), "HH:mm")
            with blocked(w):
                w.setTime(t if t.isValid() else QTime(0, 0))
        w.timeChanged.connect(lambda t: self.cfg.set(key, t.toString("HH:mm")))
        self._add(load)
        return w

    def slider(self, w: QSlider, key: str, label: QLabel, suffix: str = "%") -> QSlider:
        def load():
            v = int(self.cfg.get(key, 0))
            with blocked(w):
                w.setValue(v)
            label.setText(f"{v}{suffix}")

        def changed(v):
            label.setText(f"{v}{suffix}")
            self.cfg.set(key, int(v))
        w.valueChanged.connect(changed)
        self._add(load)
        return w

    def color(self, w: ColorPicker, key: str) -> ColorPicker:
        w.changed.connect(lambda v: self.cfg.set(key, v))
        self._add(lambda: w.set_value(self.cfg.get(key, "")))
        return w

    def font(self, w: QFontComboBox, key: str) -> QFontComboBox:
        def load():
            with blocked(w):
                w.setCurrentFont(QFont(self.cfg.get(key, "Segoe UI")))
        w.currentFontChanged.connect(lambda f: self.cfg.set(key, f.family()))
        self._add(load)
        return w


class VpnDetectDialog(QDialog):
    """«Найти автоматически»: списки процессов и сетевых адаптеров, чтобы выбрать нужные."""

    def __init__(self, parent, cfg):
        super().__init__(parent)
        self.cfg = cfg
        self.setWindowTitle("Поиск VPN")
        self.resize(560, 520)
        lay = QVBoxLayout(self)

        lay.addWidget(QLabel("<b>Процессы</b> (с «vpn» или «wireguard» в названии):"))
        self.cb_all = QCheckBox("Показать все процессы")
        self.cb_all.toggled.connect(self.fill)
        lay.addWidget(self.cb_all)
        self.proc_list = QListWidget()
        lay.addWidget(self.proc_list, 1)

        lay.addWidget(QLabel("<b>Сетевые адаптеры</b> (включите VPN и нажмите «Обновить» — "
                             "появится или «поднимется» нужный):"))
        self.nic_list = QListWidget()
        lay.addWidget(self.nic_list, 1)

        row = QHBoxLayout()
        refresh = QPushButton("Обновить")
        refresh.clicked.connect(self.fill)
        use = QPushButton("Использовать выбранное")
        use.setDefault(True)
        use.clicked.connect(self.apply)
        close = QPushButton("Закрыть")
        close.clicked.connect(self.reject)
        row.addWidget(refresh)
        row.addStretch(1)
        row.addWidget(use)
        row.addWidget(close)
        lay.addLayout(row)
        self.fill()

    def fill(self) -> None:
        self.proc_list.clear()
        for name, exe in vpn_tools.list_vpn_processes(self.cb_all.isChecked()):
            item = QListWidgetItem(name)
            item.setToolTip(exe or "путь недоступен (процесс запущен от администратора?)")
            item.setData(Qt.UserRole, (name, exe))
            self.proc_list.addItem(item)
        if self.proc_list.count() == 0:
            self.proc_list.addItem("— ничего не найдено; запустите VPN или включите «Показать все процессы» —")
        self.nic_list.clear()
        for nic, up, speed, ip in vpn_tools.list_adapters():
            status = "подключён" if up else "отключён"
            text = f"{'●' if up else '○'} {nic} — {status}"
            if speed:
                text += f", {speed} Мбит/с"
            if ip:
                text += f", {ip}"
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, nic)
            self.nic_list.addItem(item)

    def apply(self) -> None:
        proc = self.proc_list.currentItem()
        nic = self.nic_list.currentItem()
        changed = False
        if proc and proc.data(Qt.UserRole):
            name, exe = proc.data(Qt.UserRole)
            base = name[:-4] if name.lower().endswith(".exe") else name
            self.cfg.set("vpn.process_name", base)
            self.cfg.set("vpn.check_process", True)
            if exe and not self.cfg.get("vpn.exe_path"):
                self.cfg.set("vpn.exe_path", exe)
            changed = True
        if nic and nic.data(Qt.UserRole):
            self.cfg.set("vpn.adapter_name", nic.data(Qt.UserRole))
            self.cfg.set("vpn.check_adapter", True)
            changed = True
        if not changed:
            QMessageBox.information(self, "Поиск VPN", "Выберите процесс и/или адаптер в списках.")
            return
        self.accept()


class StepDialog(QDialog):
    """Окно редактирования одного шага макроса: сайт, программа/игра или пауза."""

    TITLES = {"url": "Открыть сайт", "app": "Запустить программу или игру", "wait": "Пауза"}

    def __init__(self, parent, step: dict):
        super().__init__(parent)
        self.step = dict(step)
        kind = self.step.get("type", "url")
        self.setWindowTitle(self.TITLES.get(kind, "Шаг"))
        self.setMinimumWidth(480)
        form = QFormLayout(self)

        if kind == "wait":
            self.seconds = QSpinBox()
            self.seconds.setRange(1, 300)
            self.seconds.setSuffix(" с")
            self.seconds.setValue(int(self.step.get("seconds", 3)))
            form.addRow("Подождать:", self.seconds)
        else:
            self.target = QLineEdit(self.step.get("target", ""))
            if kind == "url":
                self.target.setPlaceholderText("https://www.youtube.com")
                form.addRow("Адрес:", self.target)
                self.browser = QComboBox()
                self.browser.addItem("Браузер по умолчанию", "")
                for name, path in macro_engine.known_browsers():
                    self.browser.addItem(name, path)
                current = self.step.get("browser", "")
                if current and self.browser.findData(current) < 0:
                    self.browser.addItem(os.path.basename(current), current)
                self.browser.setCurrentIndex(max(0, self.browser.findData(current)))
                pick = QPushButton("Другой…")
                pick.clicked.connect(self._pick_browser)
                row = QHBoxLayout()
                row.addWidget(self.browser, 1)
                row.addWidget(pick)
                form.addRow("Браузер:", row)
            else:
                self.target.setPlaceholderText("путь к .exe, ярлык или steam://rungameid/570")
                browse = QPushButton("Обзор…")
                browse.clicked.connect(self._pick_app)
                row = QHBoxLayout()
                row.addWidget(self.target, 1)
                row.addWidget(browse)
                form.addRow("Что запустить:", row)
                self.args = QLineEdit(self.step.get("args", ""))
                self.args.setPlaceholderText("необязательно")
                form.addRow("Параметры запуска:", self.args)
                self.window_part = QLineEdit(self.step.get("window", ""))
                self.window_part.setPlaceholderText("например: Dota 2 (нужно, чтобы найти окно игры)")
                form.addRow("Заголовок окна содержит:", self.window_part)
                form.addRow(info_label("Dota 2 через Steam: <b>steam://rungameid/570</b>. "
                                       "Номер другой игры — в адресе её страницы в магазине Steam."))
            self.monitor = QComboBox()
            for key, title in macro_engine.MONITORS:
                self.monitor.addItem(title, key)
            self.monitor.setCurrentIndex(max(0, self.monitor.findData(self.step.get("monitor", "none"))))
            form.addRow("Перенести на монитор:", self.monitor)
            self.state = QComboBox()
            for key, title in macro_engine.STATES:
                self.state.addItem(title, key)
            self.state.setCurrentIndex(max(0, self.state.findData(self.step.get("state", "maximize"))))
            form.addRow("Окно:", self.state)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _pick_browser(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Браузер", "", "Программы (*.exe)")
        if path:
            path = os.path.normpath(path)
            self.browser.addItem(os.path.basename(path), path)
            self.browser.setCurrentIndex(self.browser.count() - 1)

    def _pick_app(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Программа", "", "Программы и ярлыки (*.exe *.lnk *.url);;Все файлы (*)")
        if path:
            self.target.setText(os.path.normpath(path))

    def result_step(self) -> dict:
        kind = self.step.get("type", "url")
        if kind == "wait":
            return {"type": "wait", "seconds": self.seconds.value()}
        step = {"type": kind, "target": self.target.text().strip(),
                "monitor": self.monitor.currentData(), "state": self.state.currentData()}
        if kind == "url":
            step["browser"] = self.browser.currentData() or ""
        else:
            step["args"] = self.args.text().strip()
            step["window"] = self.window_part.text().strip()
        return step


class SettingsWindow(QWidget):
    def __init__(self, app):
        super().__init__(None)
        self.app = app
        self.cfg = app.cfg
        self.b = Binder(app.cfg)
        self.setWindowTitle("DeskWidgets — настройки")
        self.setWindowIcon(theme.app_icon())
        self.resize(620, 600)

        tabs = QTabWidget()
        tabs.addTab(self._scroll(self._tab_general()), "Общие")
        tabs.addTab(self._scroll(self._tab_appearance()), "Внешний вид")
        tabs.addTab(self._scroll(self._tab_music()), "Музыка")
        tabs.addTab(self._scroll(self._tab_monitor()), "Монитор")
        tabs.addTab(self._scroll(self._tab_vpn()), "VPN")
        tabs.addTab(self._scroll(self._tab_timer()), "Таймер")
        tabs.addTab(self._scroll(self._tab_macros()), "Макросы")

        self.tabs = tabs
        lay = QVBoxLayout(self)
        lay.addWidget(tabs)
        bottom = QHBoxLayout()
        hint = QLabel("Изменения применяются сразу.")
        hint.setStyleSheet("color: #8C8C8C;")
        bottom.addWidget(hint)
        bottom.addStretch(1)
        close = QPushButton("Закрыть")
        close.clicked.connect(self.hide)
        bottom.addWidget(close)
        lay.addLayout(bottom)

        app.edit_mode_changed.connect(self._sync_edit_mode)
        self.cfg.changed.connect(self._on_cfg_changed)

    def _on_cfg_changed(self, key: str) -> None:
        if key.startswith(("shutdown.", "modules.")):
            self.lbl_timer_status.setText(self._timer_status())

    @staticmethod
    def _scroll(page: QWidget) -> QScrollArea:
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QScrollArea.NoFrame)
        area.setWidget(page)
        return area

    def open_window(self, tab: int | None = None, macro: int | None = None) -> None:
        # bool — тоже int в Python (сигнал triggered передаёт checked=False), его отсекаем
        if isinstance(tab, int) and not isinstance(tab, bool):
            self.tabs.setCurrentIndex(tab)
        if isinstance(macro, int):
            self.macro_slot.setCurrentIndex(macro)
        self._load_macro()
        self.b.refresh()
        self._refresh_startup_boxes()
        self._refresh_presets()
        self._sync_edit_mode(self.app.edit_mode)
        self._update_status_labels()
        self.show()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, e) -> None:
        e.ignore()   # окно не уничтожаем, только прячем — быстрее открывается повторно
        self.hide()

    # ================= Общие =================

    def _tab_general(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)

        g = QGroupBox("Модули")
        gl = QVBoxLayout(g)
        for key, title in MODULE_TITLES.items():
            gl.addWidget(self.b.check(QCheckBox(title), f"modules.{key}"))
        lay.addWidget(g)

        g = QGroupBox("Запуск")
        gl = QVBoxLayout(g)
        self.cb_autostart = QCheckBox("Запускать вместе с Windows")
        self.cb_admin = QCheckBox("Запускать от администратора (без запроса UAC при каждом старте)")
        self.cb_autostart.toggled.connect(self._on_startup_changed)
        self.cb_admin.toggled.connect(self._on_startup_changed)
        gl.addWidget(self.cb_autostart)
        gl.addWidget(self.cb_admin)
        gl.addWidget(info_label(
            "Права администратора нужны только для температуры CPU через LibreHardwareMonitorLib.dll. "
            "При включении создаётся задача в Планировщике заданий — один раз появится окно UAC, "
            "дальше программа запускается с правами без вопросов."))
        self.lbl_admin_status = QLabel()
        gl.addWidget(self.lbl_admin_status)
        lay.addWidget(g)

        g = QGroupBox("Виджеты")
        form = QFormLayout(g)
        layer = QComboBox()
        layer.addItems(["Поверх всех окон", "На рабочем столе (под окнами)"])
        form.addRow("Слой:", self.b.combo(layer, "general.layer", ["top", "bottom"]))
        form.addRow(self.b.check(QCheckBox("Скрывать, когда на мониторе полноэкранное окно (видео, игра)"),
                                 "general.hide_on_fullscreen"))
        form.addRow(self.b.check(QCheckBox("Прозрачность при наведении: виджет бледнеет и пропускает клики"),
                                 "general.hover_fade"))
        form.addRow(info_label("Чтобы нажать кнопку на виджете или покрутить громкость, "
                               "удерживайте <b>Ctrl</b> — виджет снова станет обычным."))
        self.cb_edit = QCheckBox("Режим редактирования (перетаскивание, размер за углы)")
        self.cb_edit.toggled.connect(self.app.set_edit_mode)
        form.addRow(self.cb_edit)
        form.addRow(self.b.check(QCheckBox("Закрепить «Сейчас играет»"), "widgets.music.pinned"))
        form.addRow(self.b.check(QCheckBox("Закрепить «Монитор»"), "widgets.monitor.pinned"))
        form.addRow(self.b.check(QCheckBox("Закрепить кнопки макросов"), "widgets.macros.pinned"))
        row = QHBoxLayout()
        reset = QPushButton("Сбросить положение виджетов")
        reset.clicked.connect(self.app.reset_positions)
        folder = QPushButton("Открыть папку с настройками и логом")
        folder.clicked.connect(self.app.open_data_folder)
        row.addWidget(reset)
        row.addWidget(folder)
        row.addStretch(1)
        form.addRow(row)
        lay.addWidget(g)
        lay.addStretch(1)
        return page

    def _sync_edit_mode(self, on: bool) -> None:
        with blocked(self.cb_edit):
            self.cb_edit.setChecked(on)

    def _refresh_startup_boxes(self) -> None:
        with blocked(self.cb_autostart):
            self.cb_autostart.setChecked(bool(self.cfg.get("general.autostart")))
        with blocked(self.cb_admin):
            self.cb_admin.setChecked(bool(self.cfg.get("general.run_as_admin")))

    def _update_status_labels(self) -> None:
        text = "Сейчас программа запущена <b>с правами администратора</b>." if winutils.is_admin() \
            else "Сейчас программа запущена <b>без прав администратора</b>."
        self.lbl_admin_status.setText(text)
        self.lbl_mon_admin.setText(text)
        self.lbl_timer_status.setText(self._timer_status())

    def _on_startup_changed(self) -> None:
        want_auto = self.cb_autostart.isChecked()
        want_admin = self.cb_admin.isChecked()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            ok, msg = autostart.apply_startup(want_auto, want_admin)
        finally:
            QApplication.restoreOverrideCursor()
        if not ok:
            self._refresh_startup_boxes()  # вернуть галочки как были
            QMessageBox.warning(self, "DeskWidgets", f"Не удалось изменить автозапуск.\n{msg}")
            return
        became_admin = want_admin and not self.cfg.get("general.run_as_admin")
        self.cfg.set("general.autostart", want_auto)
        self.cfg.set("general.run_as_admin", want_admin)
        if became_admin and not winutils.is_admin():
            answer = QMessageBox.question(
                self, "DeskWidgets",
                "Задача создана. Права администратора появятся после перезапуска программы.\n"
                "Перезапустить сейчас?")
            if answer == QMessageBox.Yes:
                winutils.release_single_instance()
                if autostart.run_task():
                    self.app.quit()

    # ================= Внешний вид =================

    def _tab_appearance(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)

        g = QGroupBox("Пресеты")
        row = QHBoxLayout(g)
        self.preset_combo = QComboBox()
        self.preset_combo.setMinimumWidth(200)
        apply_btn = QPushButton("Применить")
        apply_btn.clicked.connect(self._apply_preset)
        save_btn = QPushButton("Сохранить как…")
        save_btn.clicked.connect(self._save_preset)
        del_btn = QPushButton("Удалить")
        del_btn.clicked.connect(self._delete_preset)
        row.addWidget(self.preset_combo, 1)
        row.addWidget(apply_btn)
        row.addWidget(save_btn)
        row.addWidget(del_btn)
        lay.addWidget(g)

        g = QGroupBox("Оформление")
        form = QFormLayout(g)
        theme_combo = QComboBox()
        theme_combo.addItems(["Системная", "Светлая", "Тёмная", "Свой цвет"])
        form.addRow("Тема:", self.b.combo(theme_combo, "appearance.theme", ["system", "light", "dark", "custom"]))
        form.addRow("Свой цвет фона:", self.b.color(ColorPicker(), "appearance.custom_bg"))

        def slider_row(key: str, lo: int, hi: int, suffix: str = "%") -> QWidget:
            w = QWidget()
            h = QHBoxLayout(w)
            h.setContentsMargins(0, 0, 0, 0)
            s = QSlider(Qt.Horizontal)
            s.setRange(lo, hi)
            value = QLabel()
            value.setFixedWidth(44)
            h.addWidget(s, 1)
            h.addWidget(value)
            self.b.slider(s, key, value, suffix)
            return w

        form.addRow("Непрозрачность фона:", slider_row("appearance.bg_opacity", 0, 100))
        form.addRow("Видимость при наведении:", slider_row("appearance.hover_opacity", 3, 100))
        radius = QSpinBox()
        radius.setRange(0, 40)
        radius.setSuffix(" px")
        form.addRow("Скругление углов:", self.b.spin(radius, "appearance.radius"))
        form.addRow(self.b.check(QCheckBox("Рамка вокруг виджета"), "appearance.border"))
        form.addRow("Шрифт:", self.b.font(QFontComboBox(), "appearance.font_family"))
        size = QSpinBox()
        size.setRange(7, 24)
        size.setSuffix(" pt")
        form.addRow("Размер шрифта:", self.b.spin(size, "appearance.font_size"))
        form.addRow("Цвет текста:", self.b.color(ColorPicker(allow_auto=True), "appearance.text_color"))
        form.addRow("Цвет акцента:", self.b.color(ColorPicker(), "appearance.accent_color"))
        lay.addWidget(g)
        lay.addStretch(1)
        return page

    def _refresh_presets(self) -> None:
        current = self.preset_combo.currentText()
        self.preset_combo.clear()
        self.preset_combo.addItems(list(theme.BUILTIN_PRESETS))
        user = self.cfg.get("user_presets", {})
        for name in user:
            self.preset_combo.addItem(f"★ {name}")
        idx = self.preset_combo.findText(current)
        if idx >= 0:
            self.preset_combo.setCurrentIndex(idx)

    def _preset_by_text(self, text: str) -> tuple[str, dict | None, bool]:
        if text.startswith("★ "):
            name = text[2:]
            return name, self.cfg.get("user_presets", {}).get(name), True
        return text, theme.BUILTIN_PRESETS.get(text), False

    def _apply_preset(self) -> None:
        _name, data, _user = self._preset_by_text(self.preset_combo.currentText())
        if not data:
            return
        values = {f"appearance.{k}": data[k] for k in theme.PRESET_KEYS if k in data}
        self.cfg.set_many(values, "appearance")
        self.b.refresh()

    def _save_preset(self) -> None:
        name, ok = QInputDialog.getText(self, "Сохранить пресет", "Название:")
        name = name.strip()
        if not ok or not name:
            return
        presets = dict(self.cfg.get("user_presets", {}))
        presets[name] = {k: self.cfg.get(f"appearance.{k}") for k in theme.PRESET_KEYS}
        self.cfg.set("user_presets", presets)
        self._refresh_presets()
        self.preset_combo.setCurrentText(f"★ {name}")

    def _delete_preset(self) -> None:
        name, _data, is_user = self._preset_by_text(self.preset_combo.currentText())
        if not is_user:
            QMessageBox.information(self, "Пресеты", "Встроенные пресеты удалить нельзя.")
            return
        presets = dict(self.cfg.get("user_presets", {}))
        presets.pop(name, None)
        self.cfg.set("user_presets", presets)
        self._refresh_presets()

    # ================= Музыка =================

    def _tab_music(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        g = QGroupBox("«Сейчас играет»")
        form = QFormLayout(g)
        idle = QComboBox()
        idle.addItems(["Скрывать виджет", "Показывать «Ничего не играет»"])
        form.addRow("Когда ничего не играет:", self.b.combo(idle, "music.when_idle", ["hide", "text"]))
        form.addRow(self.b.check(QCheckBox("Фон виджета под основной цвет обложки"), "music.adaptive_bg"))
        form.addRow(self.b.check(QCheckBox("Показывать обложку"), "music.show_cover"))
        form.addRow(self.b.check(QCheckBox("Показывать полосу прогресса"), "music.show_progress"))
        form.addRow(self.b.check(QCheckBox("Показывать источник (браузер / сайт)"), "music.show_source"))
        form.addRow(self.b.check(QCheckBox("Показывать громкость (ползунок и проценты)"), "music.show_volume"))
        lay.addWidget(g)
        lay.addWidget(info_label(
            "Трек берётся из системных медиа-сессий Windows — тех же, что в плашке громкости. "
            "ВК Музыка и YouTube в Chrome / Edge / Яндекс Браузере появляются там автоматически.<br>"
            "Колесо мыши над виджетом меняет громкость системы. Если источников несколько — "
            "переключайтесь маленькими стрелками рядом с названием источника."))
        lay.addStretch(1)
        return page

    # ================= Монитор =================

    def _tab_monitor(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)

        g = QGroupBox("Что показывать")
        gl = QVBoxLayout(g)
        gl.addWidget(self.b.check(QCheckBox("Скорость интернета (скачивание / отдача)"), "monitor.show_net"))
        gl.addWidget(self.b.check(QCheckBox("Температура CPU"), "monitor.show_cpu"))
        gl.addWidget(self.b.check(QCheckBox("Температура GPU (NVIDIA)"), "monitor.show_gpu"))
        gl.addWidget(self.b.check(QCheckBox("Статус VPN"), "monitor.show_vpn"))
        gl.addWidget(self.b.check(QCheckBox("Отсчёт до выключения (только когда таймер запущен)"),
                                  "monitor.show_timer"))
        lay.addWidget(g)

        g = QGroupBox("Пороги температуры")
        form = QFormLayout(g)
        warn = QSpinBox()
        warn.setRange(30, 120)
        warn.setSuffix(" °C")
        crit = QSpinBox()
        crit.setRange(30, 120)
        crit.setSuffix(" °C")
        form.addRow("Жёлтый от:", self.b.spin(warn, "monitor.warn_temp"))
        form.addRow("Красный выше:", self.b.spin(crit, "monitor.crit_temp"))
        lay.addWidget(g)

        g = QGroupBox("Температура CPU")
        form = QFormLayout(g)
        src = QComboBox()
        src.addItems(["Автоматически",
                      "LibreHardwareMonitorLib.dll (нужны права администратора)",
                      "WMI запущенного приложения LibreHardwareMonitor"])
        form.addRow("Источник:", self.b.combo(src, "monitor.cpu_source", ["auto", "dll", "wmi"]))
        dll_row = QWidget()
        h = QHBoxLayout(dll_row)
        h.setContentsMargins(0, 0, 0, 0)
        self.dll_edit = self.b.line(QLineEdit(), "monitor.lhm_dll")
        self.dll_edit.setPlaceholderText("по умолчанию: папка lib рядом с программой")
        browse = QPushButton("Обзор…")
        browse.clicked.connect(lambda: self._browse(self.dll_edit, "monitor.lhm_dll", "DLL (*.dll)"))
        h.addWidget(self.dll_edit, 1)
        h.addWidget(browse)
        form.addRow("Путь к DLL:", dll_row)
        self.lbl_mon_admin = QLabel()
        form.addRow(self.lbl_mon_admin)
        form.addRow(info_label(
            "Температуру процессора Windows отдаёт только через драйвер ядра, поэтому "
            "LibreHardwareMonitorLib требует прав администратора. Без прав на виджете будет "
            "«нужны права администратора». Варианты: включить «Запускать от администратора» "
            "на вкладке «Общие» или держать запущенным приложение LibreHardwareMonitor — "
            "тогда температура читается через WMI без прав."))
        lay.addWidget(g)
        lay.addStretch(1)
        return page

    def _browse(self, edit: QLineEdit, key: str, flt: str) -> None:
        start = os.path.dirname(edit.text()) if edit.text() else ""
        path, _ = QFileDialog.getOpenFileName(self, "Выберите файл", start, flt)
        if path:
            path = os.path.normpath(path)
            edit.setText(path)
            self.cfg.set(key, path)

    # ================= VPN =================

    def _tab_vpn(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        g = QGroupBox("Способы проверки (включённые проверки должны пройти все)")
        form = QFormLayout(g)
        form.addRow(self.b.check(QCheckBox("Запущен процесс VPN"), "vpn.check_process"))
        proc = self.b.line(QLineEdit(), "vpn.process_name")
        proc.setPlaceholderText("часть имени, например vpn (можно несколько через запятую)")
        form.addRow("Имя процесса:", proc)
        form.addRow(self.b.check(QCheckBox("Активен сетевой адаптер VPN"), "vpn.check_adapter"))
        nic = self.b.line(QLineEdit(), "vpn.adapter_name")
        nic.setPlaceholderText("часть имени адаптера")
        form.addRow("Имя адаптера:", nic)
        detect = QPushButton("Найти автоматически…")
        detect.clicked.connect(self._detect_vpn)
        form.addRow(detect)
        form.addRow(self.b.check(QCheckBox("Проверять внешний IP и страну (раз в 60 секунд)"), "vpn.check_ip"))
        home = self.b.line(QLineEdit(), "vpn.home_country")
        home.setMaxLength(2)
        home.setPlaceholderText("RU")
        form.addRow("Домашняя страна (код):", home)
        form.addRow(info_label("Если страна по IP отличается от домашней — это тоже считается признаком "
                               "включённого VPN. Оставьте поле пустым, чтобы только показывать страну. "
                               "Используются сервисы ip-api.com и ipinfo.io."))
        lay.addWidget(g)

        g = QGroupBox("Приложение VPN")
        form = QFormLayout(g)
        exe_row = QWidget()
        h = QHBoxLayout(exe_row)
        h.setContentsMargins(0, 0, 0, 0)
        self.vpn_exe = self.b.line(QLineEdit(), "vpn.exe_path")
        browse = QPushButton("Обзор…")
        browse.clicked.connect(lambda: self._browse(self.vpn_exe, "vpn.exe_path", "Программы (*.exe)"))
        h.addWidget(self.vpn_exe, 1)
        h.addWidget(browse)
        form.addRow("Путь к .exe:", exe_row)
        form.addRow(info_label("Клик по строке VPN на виджете открывает это приложение "
                               "(в режиме редактирования клик не срабатывает)."))
        lay.addWidget(g)
        lay.addStretch(1)
        return page

    def _detect_vpn(self) -> None:
        dlg = VpnDetectDialog(self, self.cfg)
        dlg.exec()
        self.b.refresh()

    # ================= Таймер =================

    def _tab_timer(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        g = QGroupBox("Расписание")
        form = QFormLayout(g)
        form.addRow(self.b.check(QCheckBox("Выключать компьютер по расписанию"), "shutdown.schedule_enabled"))

        days_row = QWidget()
        h = QHBoxLayout(days_row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(4)
        self.day_btns: list[QPushButton] = []
        for name in ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"):
            btn = QPushButton(name)
            btn.setCheckable(True)
            btn.setFixedWidth(40)
            btn.setStyleSheet("QPushButton:checked { background: #4FC3F7; color: black; font-weight: bold; }")
            btn.toggled.connect(self._days_changed)
            h.addWidget(btn)
            self.day_btns.append(btn)
        h.addStretch(1)

        def load_days():
            days = self.cfg.get("shutdown.days", [True] * 7)
            for btn, on in zip(self.day_btns, days):
                with blocked(btn):
                    btn.setChecked(bool(on))
        self.b._add(load_days)
        form.addRow("Дни:", days_row)

        wd = QTimeEdit()
        wd.setDisplayFormat("HH:mm")
        we = QTimeEdit()
        we.setDisplayFormat("HH:mm")
        form.addRow("Время в будни (Пн–Пт):", self.b.time(wd, "shutdown.weekday_time"))
        form.addRow("Время в выходные (Сб, Вс):", self.b.time(we, "shutdown.weekend_time"))
        action = QComboBox()
        action.addItems(["Выключение", "Перезагрузка", "Сон", "Гибернация"])
        form.addRow("Действие:", self.b.combo(action, "shutdown.action",
                                              ["shutdown", "restart", "sleep", "hibernate"]))
        warn = QSpinBox()
        warn.setRange(1, 60)
        warn.setSuffix(" мин")
        form.addRow("Предупреждать за:", self.b.spin(warn, "shutdown.warn_minutes"))
        form.addRow(self.b.check(QCheckBox("Не выключать, если открыто полноэкранное приложение "
                                           "(перенести на 30 минут)"), "shutdown.skip_fullscreen"))
        form.addRow(info_label("Время относится к самому дню: «01:00 в субботу» — это ночь с пятницы на субботу. "
                               "Если в момент срабатывания компьютер спал, после пробуждения он не выключится."))
        self.lbl_timer_status = QLabel()
        form.addRow("Ближайшее:", self.lbl_timer_status)
        lay.addWidget(g)
        lay.addWidget(info_label("Быстрый таймер («Выключить через 30 мин / 1 ч / 2 ч / своё время») — "
                                 "в меню иконки в трее."))
        lay.addStretch(1)
        return page

    # ================= Макросы =================

    def _tab_macros(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)

        row = QHBoxLayout()
        row.addWidget(QLabel("Кнопка:"))
        self.macro_slot = QComboBox()
        self.macro_slot.currentIndexChanged.connect(self._load_macro)
        row.addWidget(self.macro_slot, 1)
        lay.addLayout(row)

        g = QGroupBox("Что делает кнопка")
        gl = QVBoxLayout(g)
        form = QFormLayout()
        self.macro_name = QLineEdit()
        self.macro_name.setPlaceholderText("коротко, например: Дота + YouTube")
        self.macro_name.setMaxLength(24)
        self.macro_name.editingFinished.connect(self._save_macro_name)
        form.addRow("Подпись на кнопке:", self.macro_name)
        gl.addLayout(form)

        body = QHBoxLayout()
        self.steps_list = QListWidget()
        self.steps_list.itemDoubleClicked.connect(lambda _item: self._edit_step())
        body.addWidget(self.steps_list, 1)
        col = QVBoxLayout()
        for text, handler in (("+ Сайт", lambda: self._add_step("url")),
                              ("+ Программа / игра", lambda: self._add_step("app")),
                              ("+ Пауза", lambda: self._add_step("wait")),
                              ("Изменить…", self._edit_step),
                              ("Удалить", self._delete_step),
                              ("▲ Выше", lambda: self._move_step(-1)),
                              ("▼ Ниже", lambda: self._move_step(1))):
            btn = QPushButton(text)
            btn.clicked.connect(handler)
            col.addWidget(btn)
        col.addStretch(1)
        body.addLayout(col)
        gl.addLayout(body)

        test_row = QHBoxLayout()
        test = QPushButton("▶ Запустить сейчас (проверить)")
        test.clicked.connect(self._test_macro)
        clear = QPushButton("Очистить кнопку")
        clear.clicked.connect(self._clear_macro)
        test_row.addWidget(test)
        test_row.addWidget(clear)
        test_row.addStretch(1)
        gl.addLayout(test_row)
        lay.addWidget(g)

        g = QGroupBox("Панель кнопок")
        gl = QVBoxLayout(g)
        gl.addWidget(self.b.check(QCheckBox("Прозрачность при наведении для панели кнопок (тогда нажимать с Ctrl)"),
                                  "widgets.macros.hover_fade"))
        gl.addWidget(info_label(
            "Шаги выполняются по порядку. Для сайта окно браузера находится само; для игры укажите часть "
            "заголовка её окна, если хотите её двигать. Игры в полноэкранном режиме сами выбирают монитор — "
            "Dota 2 открывается на основном, монитор меняется в её настройках видео. "
            "Макросы можно запускать и из трея: «Макросы»."))
        lay.addWidget(g)
        lay.addStretch(1)
        return page

    def _macros(self) -> list[dict]:
        data = copy.deepcopy(self.cfg.get("macros.buttons", []))
        while len(data) < 4:
            data.append({"name": "", "steps": []})
        return [m if isinstance(m, dict) else {"name": "", "steps": []} for m in data[:4]]

    def _save_macros(self, data: list[dict]) -> None:
        self.cfg.set("macros.buttons", data)

    def _refresh_slot_names(self) -> None:
        data = self._macros()
        with blocked(self.macro_slot):
            idx = max(0, self.macro_slot.currentIndex())
            self.macro_slot.clear()
            for i, m in enumerate(data):
                label = m.get("name") or ("пусто" if not m.get("steps") else f"Макрос {i + 1}")
                self.macro_slot.addItem(f"{i + 1} — {label}")
            self.macro_slot.setCurrentIndex(idx)

    def _load_macro(self) -> None:
        self._refresh_slot_names()
        m = self._macros()[self.macro_slot.currentIndex()]
        self.macro_name.setText(m.get("name", ""))
        current = self.steps_list.currentRow()
        self.steps_list.clear()
        for n, step in enumerate(m.get("steps") or [], 1):
            self.steps_list.addItem(f"{n}. {macro_engine.describe(step)}")
        if self.steps_list.count() == 0:
            item = QListWidgetItem("Шагов пока нет — добавьте кнопками справа")
            item.setFlags(Qt.NoItemFlags)
            self.steps_list.addItem(item)
        self.steps_list.setCurrentRow(min(current, self.steps_list.count() - 1))

    def _update_current(self, change) -> None:
        """Применяет change(macro_dict) к выбранной кнопке и сохраняет."""
        data = self._macros()
        m = data[self.macro_slot.currentIndex()]
        m.setdefault("steps", [])
        change(m)
        self._save_macros(data)
        self._load_macro()

    def _save_macro_name(self) -> None:
        name = self.macro_name.text().strip()
        self._update_current(lambda m: m.__setitem__("name", name))

    def _step_index(self) -> int:
        steps = self._macros()[self.macro_slot.currentIndex()].get("steps") or []
        row = self.steps_list.currentRow()
        return row if 0 <= row < len(steps) else -1

    def _add_step(self, kind: str) -> None:
        defaults = {"url": {"type": "url", "target": "", "browser": "", "monitor": "right", "state": "maximize"},
                    "app": {"type": "app", "target": "", "args": "", "window": "", "monitor": "none",
                            "state": "none"},
                    "wait": {"type": "wait", "seconds": 3}}
        dlg = StepDialog(self, defaults[kind])
        if dlg.exec() == QDialog.Accepted:
            step = dlg.result_step()
            self._update_current(lambda m: m["steps"].append(step))
            self.steps_list.setCurrentRow(self.steps_list.count() - 1)

    def _edit_step(self) -> None:
        i = self._step_index()
        if i < 0:
            return
        dlg = StepDialog(self, self._macros()[self.macro_slot.currentIndex()]["steps"][i])
        if dlg.exec() == QDialog.Accepted:
            step = dlg.result_step()
            self._update_current(lambda m: m["steps"].__setitem__(i, step))

    def _delete_step(self) -> None:
        i = self._step_index()
        if i >= 0:
            self._update_current(lambda m: m["steps"].pop(i))

    def _move_step(self, delta: int) -> None:
        i = self._step_index()
        steps = self._macros()[self.macro_slot.currentIndex()].get("steps") or []
        j = i + delta
        if i < 0 or not 0 <= j < len(steps):
            return

        def swap(m):
            m["steps"][i], m["steps"][j] = m["steps"][j], m["steps"][i]
        self._update_current(swap)
        self.steps_list.setCurrentRow(j)

    def _clear_macro(self) -> None:
        if QMessageBox.question(self, "Макросы", "Очистить эту кнопку?") == QMessageBox.Yes:
            self._update_current(lambda m: (m.__setitem__("name", ""), m.__setitem__("steps", [])))

    def _test_macro(self) -> None:
        module = self.app.modules.get("macros")
        if module is None:
            QMessageBox.information(self, "Макросы", "Включите модуль «Макросы» на вкладке «Общие».")
            return
        module.run(self.macro_slot.currentIndex())

    def _days_changed(self) -> None:
        self.cfg.set("shutdown.days", [b.isChecked() for b in self.day_btns])
        self.lbl_timer_status.setText(self._timer_status())

    def _timer_status(self) -> str:
        m = self.app.modules.get("shutdown")
        return m.status_text() if m else "модуль выключен"
