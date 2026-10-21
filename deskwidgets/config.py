"""Менеджер настроек: JSON-файл %APPDATA%\\DeskWidgets\\config.json.

- Ключи адресуются строкой через точку: cfg.get("appearance.radius").
- При каждом изменении испускается сигнал changed(ключ) — виджеты на него
  подписаны и сразу перерисовываются (поэтому перезапуск не нужен).
- Запись на диск откладывается на 0.5 с: если двигать ползунок, файл
  не будет перезаписываться 50 раз в секунду.
- Если файл испорчен — он переименовывается в config.json.bad, а программа
  стартует с настройками по умолчанию.
"""

import copy
import json
import os

from PySide6.QtCore import QObject, QTimer, Signal

from .logger import log
from .paths import config_path

# Порядок дней в списках: 0 = понедельник ... 6 = воскресенье (как datetime.weekday())
DEFAULTS: dict = {
    "version": 1,
    "general": {
        "autostart": False,
        "run_as_admin": False,
        "widgets_visible": True,
        "layer": "top",               # "top" — поверх окон, "bottom" — на рабочем столе (под окнами)
        "hide_on_fullscreen": True,
        "hover_fade": True,           # прозрачность при наведении + пропуск кликов
    },
    "modules": {
        "music": True,
        "monitor": True,
        "shutdown": True,
        "macros": True,
    },
    "appearance": {
        "theme": "dark",              # system / light / dark / custom
        "custom_bg": "#1F2A36",
        "bg_opacity": 82,             # %
        "hover_opacity": 15,          # % — насколько видим виджет под курсором
        "radius": 12,
        "border": True,
        "font_family": "Segoe UI",
        "font_size": 10,
        "text_color": "",             # "" — взять из темы
        "accent_color": "#4FC3F7",
    },
    "user_presets": {},               # {"Моё": {...ключи appearance...}}
    "widgets": {
        # pos = None — «авто»: правый нижний угол второго монитора
        "music": {"pos": None, "scale": 1.0, "pinned": False},
        "monitor": {"pos": None, "scale": 1.0, "pinned": False},
        # У кнопок прозрачность при наведении по умолчанию выключена — иначе их не нажать без Ctrl
        "macros": {"pos": None, "scale": 1.0, "pinned": False, "hover_fade": False},
    },
    "music": {
        "when_idle": "hide",          # hide / text
        "adaptive_bg": False,
        "show_cover": True,
        "show_progress": True,
        "show_source": True,
        "show_volume": True,
    },
    "monitor": {
        "show_net": True,
        "show_cpu": True,
        "show_gpu": True,
        "show_vpn": True,
        "show_timer": True,           # строка с отсчётом, когда запланировано выключение
        "warn_temp": 70,
        "crit_temp": 85,
        "cpu_source": "auto",         # auto / dll / wmi
        "lhm_dll": "",                # путь к LibreHardwareMonitorLib.dll ("" — искать в папке lib)
    },
    "vpn": {
        "check_process": True,
        "process_name": "vpn",
        "check_adapter": True,
        "adapter_name": "",
        "check_ip": False,
        "home_country": "RU",
        "exe_path": "",
    },
    "shutdown": {
        "schedule_enabled": False,
        "days": [True, True, True, True, True, True, True],
        "weekday_time": "23:30",
        "weekend_time": "01:00",
        "action": "shutdown",         # shutdown / restart / sleep / hibernate
        "warn_minutes": 5,
        "skip_fullscreen": True,
        "skip_keys": [],              # отменённые «на сегодня» запуски (служебное)
    },
    "macros": {
        # Ровно 4 кнопки. Шаги: url (сайт), app (программа/игра), wait (пауза) — см. macro_engine.py
        "buttons": [
            # Пример: YouTube в новом окне на правом мониторе (если монитор один — просто развернуть)
            {"name": "YouTube", "steps": [
                {"type": "url", "target": "https://www.youtube.com", "browser": "",
                 "monitor": "right", "state": "maximize"},
            ]},
            {"name": "", "steps": []},
            {"name": "", "steps": []},
            {"name": "", "steps": []},
        ],
    },
}


def _merge(default, loaded):
    """Накладывает загруженные значения на значения по умолчанию.

    Защита от «битого» файла: неизвестные ключи отбрасываются, значение
    неправильного типа заменяется умолчанием, недостающие ключи добавляются.
    """
    if isinstance(default, dict):
        if not isinstance(loaded, dict):
            return copy.deepcopy(default)
        if not default:  # пустой словарь по умолчанию (user_presets) — принимаем как есть
            return copy.deepcopy(loaded)
        return {k: (_merge(dv, loaded[k]) if k in loaded else copy.deepcopy(dv)) for k, dv in default.items()}
    if default is None:  # например pos: None или словарь
        return loaded if (loaded is None or isinstance(loaded, dict)) else None
    if isinstance(default, bool):  # bool проверяем раньше int: в Python bool — подкласс int
        return loaded if isinstance(loaded, bool) else default
    if isinstance(default, (int, float)):
        if isinstance(loaded, (int, float)) and not isinstance(loaded, bool):
            return type(default)(loaded)
        return default
    if isinstance(default, list):
        if not isinstance(loaded, list):
            return copy.deepcopy(default)
        if default and len(loaded) != len(default):
            return copy.deepcopy(default)
        return loaded
    if isinstance(default, str):
        return loaded if isinstance(loaded, str) else default
    return loaded


class Config(QObject):
    changed = Signal(str)  # полный ключ, например "appearance.radius"

    def __init__(self):
        super().__init__()
        self.path = config_path()
        self.data = self._load()
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(500)
        self._save_timer.timeout.connect(self.save)

    # ---------- загрузка / сохранение ----------

    def _load(self) -> dict:
        # Первый запуск = файла настроек ещё нет. Тогда покажем окно-приветствие.
        self.first_run = not os.path.exists(self.path)
        if not os.path.exists(self.path):
            log.info("Файл настроек не найден, используются значения по умолчанию")
            return copy.deepcopy(DEFAULTS)
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            return _merge(DEFAULTS, raw)
        except Exception:
            log.exception("Файл настроек повреждён — сброс к значениям по умолчанию")
            try:
                os.replace(self.path, self.path + ".bad")
            except OSError:
                pass
            return copy.deepcopy(DEFAULTS)

    def save(self) -> None:
        """Атомарная запись: сначала во временный файл, потом подмена.
        Если компьютер выключится посреди записи, старый файл останется целым."""
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except Exception:
            log.exception("Не удалось сохранить настройки")

    # ---------- доступ к значениям ----------

    def get(self, key: str, default=None):
        node = self.data
        for part in key.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set(self, key: str, value, notify: bool = True) -> None:
        parts = key.split(".")
        node = self.data
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        if node.get(parts[-1]) == value:
            return  # ничего не поменялось — не дёргаем подписчиков
        node[parts[-1]] = copy.deepcopy(value)
        self._save_timer.start()
        if notify:
            self.changed.emit(key)

    def set_many(self, values: dict, notify_key: str) -> None:
        """Меняет сразу несколько ключей и шлёт один сигнал (например, при применении пресета)."""
        for k, v in values.items():
            self.set(k, v, notify=False)
        self._save_timer.start()
        self.changed.emit(notify_key)

    def default(self, key: str):
        node = DEFAULTS
        for part in key.split("."):
            node = node[part]
        return copy.deepcopy(node)
