"""Скриншоты для README на выдуманных данных (без реальных треков, датчиков и настроек).

Запуск: python tools\\make_screenshots.py  → картинки в docs\\screenshots
"""

import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["APPDATA"] = tempfile.mkdtemp()  # чистые настройки по умолчанию, реальные не трогаем

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, Signal  # noqa: E402
from PySide6.QtGui import QColor, QFont, QImage, QLinearGradient, QPainter, QPixmap, QRadialGradient  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from deskwidgets import theme  # noqa: E402
from deskwidgets.config import Config  # noqa: E402

OUT = os.path.join(ROOT, "docs", "screenshots")


class FakeTray:
    def notify(self, *_):
        pass

    def refresh(self):
        pass


class FakeApp(QObject):
    """Минимальная «замена» приложения: только то, что нужно виджетам для отрисовки."""
    edit_mode_changed = Signal(bool)

    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        self.colors = theme.resolve_colors(cfg.get("appearance"))
        self.widgets_visible = True
        self.edit_mode = False
        self.modules = {}
        self.tray = FakeTray()
        self._widgets = []

    def all_widgets(self):
        return self._widgets

    def set_edit_mode(self, on):
        pass

    def reset_positions(self):
        pass

    def open_data_folder(self):
        pass


def fake_cover() -> bytes:
    img = QImage(300, 300, QImage.Format_ARGB32)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    g = QLinearGradient(0, 0, 300, 300)
    g.setColorAt(0, QColor("#FF7A59"))
    g.setColorAt(1, QColor("#6A3093"))
    p.fillRect(0, 0, 300, 300, g)
    rg = QRadialGradient(QPointF(200, 110), 90)
    rg.setColorAt(0, QColor(255, 230, 150, 230))
    rg.setColorAt(1, QColor(255, 230, 150, 0))
    p.setBrush(rg)
    p.setPen(Qt.NoPen)
    p.drawEllipse(QPointF(200, 110), 90, 90)
    p.setBrush(QColor(20, 10, 40, 200))
    for i, h in enumerate([60, 110, 80, 140, 95, 50]):
        p.drawRoundedRect(QRectF(40 + i * 38, 260 - h, 24, h), 8, 8)
    p.end()
    path = os.path.join(tempfile.gettempdir(), "dw_cover.png")
    img.save(path)
    with open(path, "rb") as f:
        return f.read()


def background(w: int, h: int) -> QPixmap:
    """Фон «как обои»: мягкий градиент."""
    pm = QPixmap(w, h)
    p = QPainter(pm)
    g = QLinearGradient(0, 0, w, h)
    g.setColorAt(0, QColor("#3A6073"))
    g.setColorAt(1, QColor("#16222A"))
    p.fillRect(0, 0, w, h, g)
    p.end()
    return pm


def make_widgets(app):
    from deskwidgets.modules.macros import MacrosModule
    from deskwidgets.modules.monitor import MonitorModule
    from deskwidgets.modules.now_playing import NowPlayingModule

    music = NowPlayingModule(app)
    monitor = MonitorModule(app)
    macros = MacrosModule(app)
    app._widgets = [music.widget, monitor.widget, macros.widget]

    session = {"aumid": "Chrome", "title": "Ночной город", "artist": "Synthwave Radio", "album": "",
               "site": "YouTube", "playing": True, "position": 83.0, "duration": 214.0,
               "snapshot": time.monotonic(), "can_prev": True, "can_next": True, "can_toggle": True,
               "thumb_id": "demo", "thumb": fake_cover(), "index": 0, "is_current": True, "key": ("Chrome", 0)}
    music.widget.show_session(session, 0, 1)
    music.widget.show_volume(42, False)

    # Строка таймера: показываем, будто выключение запланировано через 47 минут
    monitor.widget.timer_active = True
    monitor.widget.refresh_size()
    monitor.widget.show_data({"net": (2.4 * 1024 * 1024, 180 * 1024), "cpu": (54.0, ""), "gpu": (71.0, ""),
                              "vpn": {"state": "on", "text": "VPN включён", "country": "Нидерланды"}})
    monitor.widget.timer_lbl.setText("Выключение через 47:12 · в 23:30")

    macros_list = app.cfg.get("macros.buttons")
    macros_list[1] = {"name": "Игра + стрим", "steps": [{"type": "wait", "seconds": 1}]}
    macros_list[2] = {"name": "Работа", "steps": [{"type": "wait", "seconds": 1}]}
    app.cfg.set("macros.buttons", macros_list)
    macros.widget.refresh_labels()
    return music, monitor, macros


def compose(widgets, pad=28, gap=8) -> QPixmap:
    pms = [w.grab() for w in widgets]
    width = max(p.width() for p in pms) + pad * 2
    height = sum(p.height() for p in pms) + gap * (len(pms) - 1) + pad * 2
    bg = background(width, height)
    p = QPainter(bg)
    y = pad
    for pm in pms:
        p.drawPixmap(width - pad - pm.width(), y, pm)
        y += pm.height() + gap
    p.end()
    return bg


def main():
    qapp = QApplication(sys.argv)
    os.makedirs(OUT, exist_ok=True)
    cfg = Config()
    app = FakeApp(cfg)
    music, monitor, macros = make_widgets(app)
    for w in app.all_widgets():
        w.show()
    qapp.processEvents()

    compose(app.all_widgets()).save(os.path.join(OUT, "widgets.png"))

    # Несколько тем рядом
    shots = []
    for name in ("Тёмный прозрачный", "Стекло", "Неон", "Лес"):
        data = theme.BUILTIN_PRESETS[name]
        cfg.set_many({f"appearance.{k}": v for k, v in data.items()}, "appearance")
        app.colors = theme.resolve_colors(cfg.get("appearance"))
        music.widget.apply_theme(app.colors)
        qapp.processEvents()
        shots.append((name, music.widget.grab()))
    pad, gap, label_h = 24, 20, 26
    w = max(s.width() for _, s in shots)
    h = shots[0][1].height() + label_h
    cols = 2
    rows = (len(shots) + cols - 1) // cols
    canvas = background(pad * 2 + cols * w + (cols - 1) * gap, pad * 2 + rows * h + (rows - 1) * gap)
    p = QPainter(canvas)
    f = QFont("Segoe UI")
    f.setPixelSize(15)
    f.setBold(True)
    p.setFont(f)
    p.setPen(QColor("white"))
    for i, (name, pm) in enumerate(shots):
        x = pad + (i % cols) * (w + gap)
        y = pad + (i // cols) * (h + gap)
        p.drawText(QRectF(x, y, w, label_h - 4), Qt.AlignLeft | Qt.AlignVCenter, name)
        p.drawPixmap(x, y + label_h, pm)
    p.end()
    canvas.save(os.path.join(OUT, "themes.png"))

    # Окно настроек на вкладке «Макросы»
    cfg.set_many({f"appearance.{k}": v for k, v in theme.BUILTIN_PRESETS["Тёмный прозрачный"].items()},
                 "appearance")
    from deskwidgets.settings_window import SettingsWindow
    sw = SettingsWindow(app)
    sw.open_window(tab=6, macro=0)
    qapp.processEvents()
    sw.grab().save(os.path.join(OUT, "settings-macros.png"))
    sw.open_window(tab=5)
    qapp.processEvents()
    sw.grab().save(os.path.join(OUT, "settings-timer.png"))
    print("Готово:", OUT)


if __name__ == "__main__":
    main()
