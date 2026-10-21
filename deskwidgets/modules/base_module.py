"""Базовый класс модуля. Модуль = логика + (необязательно) окна-виджеты.

Приложение вызывает:
  start()  — при включении модуля;
  stop()   — при выключении или выходе;
  tick()   — раз в секунду (через safe_tick: ошибка не уронит программу);
  on_config_changed(key) — когда пользователь что-то поменял в настройках.
"""

from PySide6.QtCore import QObject

from ..logger import log_throttled


class BaseModule(QObject):
    key = ""
    title = ""

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.cfg = app.cfg

    def start(self) -> None:
        pass

    def stop(self) -> None:
        for w in self.widgets():
            w.hide()
            w.deleteLater()

    def tick(self) -> None:
        pass

    def widgets(self) -> list:
        return []

    def on_config_changed(self, key: str) -> None:
        pass

    # ---------- защита от ошибок ----------

    def safe_tick(self) -> None:
        self.safe(self.tick)

    def safe(self, fn, *args):
        """Вызывает fn и перехватывает любую ошибку — пишем в лог (не чаще раза в 5 минут)."""
        try:
            return fn(*args)
        except Exception as e:
            self.report(e, getattr(fn, "__name__", "?"))
            return None

    def report(self, exc: BaseException, where: str = "") -> None:
        log_throttled(f"{self.key}:{where}:{type(exc).__name__}:{exc}",
                      f"[{self.key}] ошибка в {where}", exc)
