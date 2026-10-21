"""Модуль 2. «Монитор»: скорость сети, температура CPU/GPU, статус VPN.

Все замеры делаются в фоновом потоке (чтение датчиков и списка процессов может
занимать десятки миллисекунд — интерфейс не должен «подвисать»). Раз в секунду
поток присылает словарь с результатами сигналом Qt.
"""

import ctypes
import os
import threading
import time
from datetime import datetime

from PySide6.QtCore import QObject, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QPainter
from PySide6.QtWidgets import QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget

from ..logger import log_throttled
from ..ui_parts import ElideLabel
from ..widget_base import BaseWidget
from .base_module import BaseModule
from .sensors import CpuTemp, GpuTemp, NetSpeed, format_speed
from .shutdown import ACTIONS
from .vpn import VpnChecker

ROWS = ["net", "cpu", "gpu", "vpn", "timer"]
ROW_H = 22


class MonitorWorker(QObject):
    data = Signal(object)

    def __init__(self):
        super().__init__()
        self._settings: dict = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def set_settings(self, settings: dict) -> None:
        # Присваивание ссылки в Python атомарно — поток просто возьмёт новый словарь на следующем шаге
        self._settings = settings

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def _run(self) -> None:
        try:
            ctypes.windll.ole32.CoInitializeEx(None, 0)  # для WMI в этом потоке
        except Exception:  # noqa: BLE001
            pass
        net, gpu, cpu, vpn = NetSpeed(), GpuTemp(), CpuTemp(), VpnChecker()
        vpn_result = None
        vpn_next = 0.0
        next_t = time.monotonic()
        while not self._stop.is_set():
            s = self._settings
            result: dict = {}
            try:
                if s.get("show_net"):
                    result["net"] = net.read(s.get("exclude", []))
            except Exception as e:  # noqa: BLE001
                log_throttled("mon-net", "Ошибка чтения скорости сети", e)
            try:
                if s.get("show_cpu"):
                    cpu.configure(s.get("cpu_source", "auto"), s.get("lhm_dll", ""))
                    result["cpu"] = cpu.read()
            except Exception as e:  # noqa: BLE001
                log_throttled("mon-cpu", "Ошибка чтения температуры CPU", e)
                result["cpu"] = (None, "ошибка")
            try:
                if s.get("show_gpu"):
                    result["gpu"] = gpu.read()
            except Exception as e:  # noqa: BLE001
                log_throttled("mon-gpu", "Ошибка чтения температуры GPU", e)
                result["gpu"] = (None, "ошибка")
            try:
                # Список процессов — самая «дорогая» операция, поэтому VPN проверяем раз в 2 секунды
                if s.get("show_vpn") and time.monotonic() >= vpn_next:
                    vpn_result = vpn.check(s.get("vpn", {}))
                    vpn_next = time.monotonic() + 2
            except Exception as e:  # noqa: BLE001
                log_throttled("mon-vpn", "Ошибка проверки VPN", e)
            result["vpn"] = vpn_result
            self.data.emit(result)

            next_t += 1.0
            delay = next_t - time.monotonic()
            if delay < 0:  # не успели (например, после сна) — не пытаемся «догонять»
                next_t = time.monotonic()
                delay = 0.05
            self._stop.wait(delay)
        cpu.close()
        gpu.close()


class Dot(QWidget):
    """Цветная точка-индикатор VPN."""

    def __init__(self):
        super().__init__()
        self.color = QColor("#8A8A8A")

    def set_color(self, c: QColor) -> None:
        self.color = QColor(c)
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(self.color)
        d = min(self.width(), self.height()) * 0.55
        p.drawEllipse(QRectF((self.width() - d) / 2, (self.height() - d) / 2, d, d))
        p.end()


class MonitorWidget(BaseWidget):
    BASE_SIZE = (340, 0)

    def __init__(self, app, module):
        super().__init__(app, "monitor", "Монитор")
        self.module = module
        self._last: dict = {}

        root = QVBoxLayout(self)
        self.root = root
        self.row_widgets: dict[str, QWidget] = {}
        self.captions: list[ElideLabel] = []

        def make_row(key: str, caption: str) -> QHBoxLayout:
            w = QWidget()
            lay = QHBoxLayout(w)
            lay.setContentsMargins(0, 0, 0, 0)
            cap = ElideLabel(caption)
            cap.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)  # подпись строго фиксированной ширины
            self.captions.append(cap)
            lay.addWidget(cap)
            root.addWidget(w)
            self.row_widgets[key] = w
            return lay

        lay = make_row("net", "Сеть")
        self.down_lbl = ElideLabel("↓ —")
        self.up_lbl = ElideLabel("↑ —")
        lay.addWidget(self.down_lbl, 1)
        lay.addWidget(self.up_lbl, 1)

        lay = make_row("cpu", "CPU")
        self.cpu_lbl = ElideLabel("—")
        lay.addWidget(self.cpu_lbl, 1)

        lay = make_row("gpu", "GPU")
        self.gpu_lbl = ElideLabel("—")
        lay.addWidget(self.gpu_lbl, 1)

        lay = make_row("vpn", "VPN")
        self.vpn_dot = Dot()
        self.vpn_lbl = ElideLabel("—")
        self.vpn_lbl.clickable = True
        self.vpn_lbl.setCursor(Qt.PointingHandCursor)
        self.vpn_lbl.setToolTip("Открыть приложение VPN")
        self.vpn_lbl.clicked.connect(module.open_vpn)
        lay.addWidget(self.vpn_dot)
        lay.addWidget(self.vpn_lbl, 1)

        # Строка таймера видна, только когда выключение запланировано
        self.timer_active = False
        lay = make_row("timer", "Таймер")
        self.timer_lbl = ElideLabel("")
        self.timer_lbl.clickable = True
        self.timer_lbl.setCursor(Qt.PointingHandCursor)
        self.timer_lbl.setToolTip("Отложить или отменить выключение")
        self.timer_lbl.clicked.connect(module.timer_menu)
        lay.addWidget(self.timer_lbl, 1)

        root.addStretch(1)
        self.set_scale(1.0)

    def visible_rows(self) -> list[str]:
        rows = [r for r in ROWS if self.cfg.get(f"monitor.show_{r}", True)]
        if "timer" in rows and not self.timer_active:
            rows.remove("timer")
        return rows

    def base_size(self) -> QSize:
        n = max(1, len(self.visible_rows()))
        return QSize(self.BASE_SIZE[0], 16 + n * ROW_H)

    def apply_style(self) -> None:
        c = self.palette_colors()
        self.root.setContentsMargins(self.px(12), self.px(8), self.px(12), self.px(8))
        self.root.setSpacing(0)
        visible = self.visible_rows()
        for key, w in self.row_widgets.items():
            w.setVisible(key in visible)
            w.setFixedHeight(self.px(ROW_H))
            w.layout().setSpacing(self.px(8))
        for cap in self.captions:
            cap.setFont(self.make_font(0.85))
            cap.set_color(c["dim"])
            cap.setFixedWidth(self.px(42))
        for lbl in (self.down_lbl, self.up_lbl, self.cpu_lbl, self.gpu_lbl, self.vpn_lbl, self.timer_lbl):
            lbl.setFont(self.make_font(1.0, bold=lbl not in (self.vpn_lbl, self.timer_lbl)))
            lbl.set_color(c["text"])
        self.vpn_dot.setFixedSize(self.px(14), self.px(14))
        if self._last:
            self.show_data(self._last)

    def _temp(self, label: ElideLabel, value) -> None:
        c = self.palette_colors()
        if not value:
            return
        temp, message = value
        if temp is None:
            label.setText(message or "—")
            label.set_color(c["dim"])
            label.setFont(self.make_font(0.9))
            return
        warn = self.cfg.get("monitor.warn_temp", 70)
        crit = self.cfg.get("monitor.crit_temp", 85)
        color = c["crit"] if temp > crit else c["warn"] if temp >= warn else c["text"]
        label.setFont(self.make_font(1.0, bold=True))
        label.setText(f"{temp:.0f} °C")
        label.set_color(color)

    def show_data(self, d: dict) -> None:
        self._last = d
        c = self.palette_colors()
        if "net" in d:
            down, up = d["net"]
            self.down_lbl.setText(f"↓ {format_speed(down)}")
            self.up_lbl.setText(f"↑ {format_speed(up)}")
        self._temp(self.cpu_lbl, d.get("cpu"))
        self._temp(self.gpu_lbl, d.get("gpu"))
        v = d.get("vpn")
        if v:
            dot = {"on": c["ok"], "wait": c["warn"]}.get(v["state"], c["off"])
            self.vpn_dot.set_color(dot)
            text = v["text"]
            if v.get("country"):
                text += f" · {v['country']}"
            self.vpn_lbl.setText(text)


class MonitorModule(BaseModule):
    key = "monitor"
    title = "Монитор"

    def __init__(self, app):
        super().__init__(app)
        self.worker = MonitorWorker()
        self.widget = MonitorWidget(app, self)
        self.worker.data.connect(self._on_data)
        self._push_settings()

    def widgets(self) -> list:
        return [self.widget]

    def start(self) -> None:
        self._update_visibility()
        self.worker.start()

    def stop(self) -> None:
        self.worker.stop()
        super().stop()

    def _push_settings(self) -> None:
        s = dict(self.cfg.get("monitor"))
        s["vpn"] = dict(self.cfg.get("vpn"))
        # Трафик VPN-адаптера не суммируем с физическим — иначе скорость удвоится
        s["exclude"] = [p.strip() for p in s["vpn"].get("adapter_name", "").split(",") if p.strip()]
        self.worker.set_settings(s)

    def _update_visibility(self) -> None:
        self.widget.set_content_visible(bool(self.widget.visible_rows()))

    def _on_data(self, d: dict) -> None:
        try:
            self.widget.show_data(d)
        except Exception as e:  # noqa: BLE001
            self.report(e, "_on_data")

    def on_config_changed(self, key: str) -> None:
        if key.startswith(("monitor.", "vpn.")):
            self._push_settings()
            self.widget.refresh_size()
            self._update_visibility()
            if not self.widget.has_saved_pos():
                self.app.place_widgets()

    # ---------- отсчёт таймера выключения ----------

    def tick(self) -> None:
        sm = self.app.modules.get("shutdown")
        job = sm.peek_job() if sm and self.cfg.get("monitor.show_timer", True) else None
        active = job is not None
        if active != self.widget.timer_active:
            self.widget.timer_active = active
            self._resize_keep_bottom()
        if job is None:
            return
        left = max(0, int((job.when - datetime.now()).total_seconds()))
        h, rem = divmod(left, 3600)
        m, s = divmod(rem, 60)
        countdown = f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
        self.widget.timer_lbl.setText(f"{ACTIONS[job.action]} через {countdown} · в {job.when:%H:%M}")
        c = self.widget.palette_colors()
        soon = left <= self.cfg.get("shutdown.warn_minutes", 5) * 60
        self.widget.timer_lbl.set_color(c["warn"] if soon else c["text"])

    def _resize_keep_bottom(self) -> None:
        """Строка появилась/исчезла: меняем высоту, а нижний край оставляем на месте."""
        w = self.widget
        if w.has_saved_pos():
            bottom = w.geometry().bottom()
            w.refresh_size()
            w.move(w.x(), bottom - w.height() + 1)
            w.save_position()
        else:
            w.refresh_size()
            self.app.place_widgets()

    def timer_menu(self) -> None:
        """Клик по строке таймера — то же меню, что в трее."""
        self.app.tray.refresh()
        self.app.tray.timer_menu.exec(QCursor.pos())

    def open_vpn(self) -> None:
        path = self.cfg.get("vpn.exe_path", "")
        if path and os.path.exists(path):
            try:
                os.startfile(path)  # как двойной щелчок в Проводнике
            except OSError as e:
                self.app.tray.notify("VPN", f"Не удалось открыть: {e}")
        else:
            self.app.tray.notify("VPN", "Укажите путь к приложению VPN: Настройки → VPN.")
