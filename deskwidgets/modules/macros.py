"""Модуль 4. «Макросы»: панель из 4 кнопок, каждая запускает свой набор действий.

Макрос выполняется в фоновом потоке (там есть ожидания по несколько секунд),
а о результате поток сообщает сигналом Qt.
"""

import threading

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QPushButton

from .. import theme
from ..widget_base import BaseWidget
from . import macro_engine
from .base_module import BaseModule

BUTTONS = 4


class MacroButton(QPushButton):
    def __init__(self, index: int, module):
        super().__init__()
        self.index = index
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.clicked.connect(lambda: module.button_clicked(index))

    def mousePressEvent(self, e) -> None:
        # В режиме редактирования клик отдаём окну, чтобы панель можно было тащить
        if getattr(self.window(), "edit_mode", False):
            e.ignore()
            return
        super().mousePressEvent(e)


class MacrosWidget(BaseWidget):
    BASE_SIZE = (340, 40)

    def __init__(self, app, module):
        super().__init__(app, "macros", "Макросы")
        self.module = module
        self.root = QHBoxLayout(self)
        self.buttons = [MacroButton(i, module) for i in range(BUTTONS)]
        for b in self.buttons:
            self.root.addWidget(b, 1)
        self.set_scale(1.0)
        self.refresh_labels()

    def base_size(self) -> QSize:
        return QSize(*self.BASE_SIZE)

    def refresh_labels(self) -> None:
        macros = self.module.macros()
        for i, b in enumerate(self.buttons):
            m = macros[i] if i < len(macros) else {}
            name = (m.get("name") or "").strip()
            steps = m.get("steps") or []
            if self.module.running.get(i):
                b.setText("…")
            elif steps:
                b.setText(name or f"Макрос {i + 1}")
            else:
                b.setText("+")
            if steps:
                tip = "\n".join(f"{n}. {macro_engine.describe(s)}" for n, s in enumerate(steps, 1))
                b.setToolTip(f"{name or f'Макрос {i + 1}'}\n{tip}")
            else:
                b.setToolTip("Пустая кнопка — нажмите, чтобы настроить")
            b.setProperty("empty", not steps)
        self.apply_style()

    def apply_style(self) -> None:
        if not hasattr(self, "buttons"):
            return
        c = self.palette_colors()
        m = self.px(6)
        self.root.setContentsMargins(m, m, m, m)
        self.root.setSpacing(self.px(5))
        radius = self.px(6)
        for b in self.buttons:
            b.setFont(self.make_font(0.85, bold=True))
            b.setFixedHeight(self.px(28))
            text = c["dim"] if b.property("empty") else c["text"]
            b.setStyleSheet(
                f"QPushButton {{ background: {theme.css(c['accent'], 40)}; color: {theme.css(text)};"
                f" border: none; border-radius: {radius}px; padding: 0 {self.px(4)}px; }}"
                f"QPushButton:hover {{ background: {theme.css(c['accent'], 95)}; }}"
                f"QPushButton:pressed {{ background: {theme.css(c['accent'], 150)}; }}"
            )


class MacrosModule(BaseModule):
    key = "macros"
    title = "Макросы"

    finished = Signal(int, list)  # номер кнопки, список ошибок (сигнал из фонового потока)

    def __init__(self, app):
        super().__init__(app)
        self.running: dict[int, bool] = {}
        self._stop = threading.Event()
        self.widget = MacrosWidget(app, self)
        self.finished.connect(self._on_finished)

    def widgets(self) -> list:
        return [self.widget]

    def stop(self) -> None:
        self._stop.set()
        super().stop()

    def macros(self) -> list[dict]:
        data = self.cfg.get("macros.buttons", [])
        return [m if isinstance(m, dict) else {} for m in data]

    def button_clicked(self, index: int) -> None:
        macros = self.macros()
        if index >= len(macros) or not macros[index].get("steps"):
            self.app.show_settings(tab=6, macro=index)  # пустая кнопка → сразу в настройки
            return
        self.run(index)

    def run(self, index: int) -> None:
        if self.running.get(index):
            return
        steps = list(self.macros()[index].get("steps") or [])
        self.running[index] = True
        self.widget.refresh_labels()
        threading.Thread(target=self._worker, args=(index, steps), name=f"macro-{index}", daemon=True).start()

    def _worker(self, index: int, steps: list) -> None:
        errors = macro_engine.run_steps(steps, self._stop.is_set)
        self.finished.emit(index, errors)

    def _on_finished(self, index: int, errors: list) -> None:
        self.running[index] = False
        try:
            self.widget.refresh_labels()
        except RuntimeError:
            return  # модуль выключили, пока макрос работал
        if errors:
            self.app.tray.notify("Макрос не выполнился полностью", "\n".join(errors[:3]))

    def on_config_changed(self, key: str) -> None:
        if key.startswith("macros."):
            self.widget.refresh_labels()
