"""Датчики для «Монитора»: скорость сети (psutil), температура GPU (NVML), CPU (LibreHardwareMonitor).

Все классы вызываются из фонового потока монитора, не из интерфейса.
"""

import os
import sys
import time

import psutil

from .. import winutils
from ..logger import log
from ..paths import app_dir, lib_dir

# Адаптеры, трафик которых не считаем (виртуальные и «петля»)
_IGNORED_NICS = ("loopback", "vethernet", "virtualbox", "vmware", "bluetooth", "teredo", "isatap")


def _ignored(name: str, extra: list[str]) -> bool:
    low = name.lower()
    return any(p in low for p in _IGNORED_NICS) or any(e and e.lower() in low for e in extra)


def format_speed(bytes_per_sec: float | None) -> str:
    """Авто-единицы: до 1 МБ/с — КБ/с, дальше — МБ/с."""
    if bytes_per_sec is None:
        return "—"
    kb = bytes_per_sec / 1024
    if kb < 1000:
        return f"{kb:.1f} КБ/с" if kb < 10 else f"{kb:.0f} КБ/с"
    mb = kb / 1024
    return f"{mb:.1f} МБ/с" if mb < 100 else f"{mb:.0f} МБ/с"


class NetSpeed:
    """Скорость = (байты сейчас − байты секунду назад) / прошедшее время. Считаем по каждому адаптеру
    отдельно, чтобы появление нового адаптера не давало скачок."""

    def __init__(self):
        self._last: dict[str, tuple[int, int]] | None = None
        self._last_t = 0.0

    def read(self, exclude: list[str]) -> tuple[float | None, float | None]:
        now = time.monotonic()
        counters = psutil.net_io_counters(pernic=True)
        snap = {n: (c.bytes_recv, c.bytes_sent) for n, c in counters.items() if not _ignored(n, exclude)}
        if self._last is None:
            self._last, self._last_t = snap, now
            return None, None
        dt = max(0.001, now - self._last_t)
        down = up = 0
        for name, (recv, sent) in snap.items():
            if name in self._last:
                pr, ps = self._last[name]
                down += max(0, recv - pr)
                up += max(0, sent - ps)
        self._last, self._last_t = snap, now
        return down / dt, up / dt


class GpuTemp:
    """Температура видеокарты NVIDIA через NVML (библиотека, которая ставится вместе с драйвером)."""

    def __init__(self):
        self._nv = None
        self._handle = None
        self._error = ""
        self._tried = False

    def read(self) -> tuple[float | None, str]:
        if not self._tried:
            self._tried = True
            try:
                import pynvml  # пакет nvidia-ml-py
                pynvml.nvmlInit()
                if pynvml.nvmlDeviceGetCount() == 0:
                    raise RuntimeError("нет GPU")
                self._handle = pynvml.nvmlDeviceGetHandleByIndex(0)
                self._nv = pynvml
            except Exception as e:  # noqa: BLE001
                log.info("NVML недоступен: %s", e)
                self._error = "нет данных (NVIDIA не найдена)"
        if self._handle is None:
            return None, self._error
        try:
            return float(self._nv.nvmlDeviceGetTemperature(self._handle, self._nv.NVML_TEMPERATURE_GPU)), ""
        except Exception as e:  # noqa: BLE001
            return None, f"ошибка: {e}"

    def close(self) -> None:
        if self._nv is not None:
            try:
                self._nv.nvmlShutdown()
            except Exception:  # noqa: BLE001
                pass


# ---------------- CPU ----------------

_clr = None


def _load_clr():
    """Загружает pythonnet — мост между Python и .NET (LibreHardwareMonitor написан на C#)."""
    global _clr
    if _clr is None:
        import pythonnet
        try:
            pythonnet.load("netfx")  # .NET Framework 4.x — есть в любой Windows 10/11
        except Exception:  # noqa: BLE001
            pass  # уже загружен
        import clr
        _clr = clr
    return _clr


def find_lhm_dll(configured: str) -> str | None:
    candidates = [configured] if configured else []
    candidates += [os.path.join(lib_dir(), "LibreHardwareMonitorLib.dll"),
                   os.path.join(app_dir(), "LibreHardwareMonitorLib.dll")]
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return None


# Какие датчики считать «температурой CPU», по убыванию предпочтения
_CPU_SENSOR_PRIORITY = ["CPU Package", "Core (Tctl/Tdie)", "Core (Tdie)", "Core (Tctl)", "Package",
                        "Core Average", "Core Max", "CPU Cores"]


def _pick_cpu_temp(values: dict[str, float]) -> tuple[float | None, str]:
    values = {k: v for k, v in values.items() if v and v > 0}
    if not values:
        return None, "нет данных (нужен драйвер, см. README)"
    for name in _CPU_SENSOR_PRIORITY:
        if name in values:
            return values[name], ""
    return max(values.values()), ""


class CpuTemp:
    """Температура процессора.

    Источники:
      dll — LibreHardwareMonitorLib.dll напрямую. Нужны права администратора: чтение
            датчиков процессора идёт через драйвер ядра, обычной программе Windows это не разрешает.
      wmi — если запущено приложение LibreHardwareMonitor (оно само работает от администратора),
            оно публикует датчики в WMI, и нам права уже не нужны.
      auto — сначала dll (если мы администратор), иначе WMI.
    """

    def __init__(self):
        self.source = "auto"
        self.dll_path = ""
        self._mode: str | None = None
        self._error = ""
        self._next_retry = 0.0
        self._computer = None
        self._searchers: list = []

    def configure(self, source: str, dll_path: str) -> None:
        if (source, dll_path) != (self.source, self.dll_path):
            self.close()
            self.source, self.dll_path = source, dll_path
            self._mode = None
            self._next_retry = 0.0

    def read(self) -> tuple[float | None, str]:
        if self._mode is None:
            if time.monotonic() < self._next_retry:
                return None, self._error
            self._init()
            if self._mode is None:
                self._next_retry = time.monotonic() + 30  # повторная попытка через 30 с
                return None, self._error
        try:
            return self._read_dll() if self._mode == "dll" else self._read_wmi()
        except Exception as e:  # noqa: BLE001
            log.warning("Ошибка чтения температуры CPU (%s): %s", self._mode, e)
            self.close()
            self._mode = None
            self._error = "ошибка чтения"
            return None, self._error

    def _init(self) -> None:
        admin = winutils.is_admin()
        if self.source in ("auto", "dll"):
            if admin:
                try:
                    self._init_dll()
                    self._mode = "dll"
                    return
                except Exception as e:  # noqa: BLE001
                    log.warning("LibreHardwareMonitorLib.dll не загрузилась: %s", e)
                    self._error = "нет LibreHardwareMonitorLib.dll" if isinstance(e, FileNotFoundError) \
                        else "ошибка загрузки DLL (см. log.txt)"
            elif self.source == "dll":
                self._error = "нужны права администратора"
                return
        if self.source in ("auto", "wmi"):
            try:
                self._init_wmi()
                self._mode = "wmi"
                return
            except Exception as e:  # noqa: BLE001
                log.info("WMI LibreHardwareMonitor недоступен: %s", e)
                if self.source == "wmi":
                    self._error = "запустите LibreHardwareMonitor"
                    return
        if not admin:
            self._error = "нужны права администратора"

    # --- через DLL ---

    def _init_dll(self) -> None:
        path = find_lhm_dll(self.dll_path)
        if not path:
            raise FileNotFoundError("LibreHardwareMonitorLib.dll не найдена (положите её в папку lib)")
        clr = _load_clr()
        folder = os.path.dirname(path)
        # Папка в sys.path — pythonnet найдёт там и саму DLL, и её зависимости (HidSharp.dll и др.)
        if folder not in sys.path:
            sys.path.append(folder)
        clr.AddReference("LibreHardwareMonitorLib")
        from LibreHardwareMonitor.Hardware import Computer, HardwareType, SensorType  # type: ignore
        computer = Computer()
        computer.IsCpuEnabled = True
        computer.Open()
        self._computer = computer
        self._HT, self._ST = HardwareType, SensorType
        log.info("LibreHardwareMonitorLib загружена: %s", path)

    def _read_dll(self) -> tuple[float | None, str]:
        values: dict[str, float] = {}
        for hw in self._computer.Hardware:
            if hw.HardwareType != self._HT.Cpu:
                continue
            hw.Update()
            for s in hw.Sensors:
                if s.SensorType == self._ST.Temperature and s.Value is not None:
                    values[str(s.Name)] = float(s.Value)
        return _pick_cpu_temp(values)

    # --- через WMI запущенного LibreHardwareMonitor ---

    def _init_wmi(self) -> None:
        clr = _load_clr()
        clr.AddReference("System.Management")
        from System.Management import ManagementObjectSearcher  # type: ignore
        query = "SELECT Name, Value, Parent FROM Sensor WHERE SensorType='Temperature'"
        errors = []
        for ns in ("root\\LibreHardwareMonitor", "root\\OpenHardwareMonitor"):
            searcher = ManagementObjectSearcher(ns, query)
            try:
                list(searcher.Get())  # проверка, что пространство имён существует
                self._searchers = [searcher]
                t, err = self._read_wmi()
                if t is None:
                    raise RuntimeError(err)
                return
            except Exception as e:  # noqa: BLE001
                errors.append(f"{ns}: {str(e).splitlines()[0] if str(e) else type(e).__name__}")
        raise RuntimeError("; ".join(errors))

    def _read_wmi(self) -> tuple[float | None, str]:
        values: dict[str, float] = {}
        for obj in self._searchers[0].Get():
            parent = str(obj["Parent"]).lower()
            if "cpu" not in parent:
                continue
            values[str(obj["Name"])] = float(obj["Value"])
        return _pick_cpu_temp(values)

    def close(self) -> None:
        if self._computer is not None:
            try:
                self._computer.Close()
            except Exception:  # noqa: BLE001
                pass
        self._computer = None
        self._searchers = []
