"""Системная громкость Windows через pycaw (обёртка над Windows Core Audio API).

Если pycaw не установлен — get() вернёт None, а колесо мыши будет работать
по-старому, через мультимедийные клавиши.
"""

import time

from . import winutils
from .logger import log_throttled

REACQUIRE = 10  # раз в 10 с заново берём устройство — вдруг переключили наушники/колонки


class SystemVolume:
    def __init__(self):
        self._ev = None             # интерфейс IAudioEndpointVolume
        self._taken_at = 0.0
        self._next_try = 0.0

    def _endpoint(self):
        now = time.monotonic()
        if self._ev is not None and now - self._taken_at > REACQUIRE:
            self._ev = None
        if self._ev is None and now >= self._next_try:
            self._next_try = now + 5
            try:
                from pycaw.pycaw import AudioUtilities
                device = AudioUtilities.GetSpeakers()  # устройство вывода по умолчанию
                ev = getattr(device, "EndpointVolume", None)
                if ev is None:  # старые версии pycaw
                    from ctypes import POINTER, cast

                    from comtypes import CLSCTX_ALL
                    from pycaw.pycaw import IAudioEndpointVolume
                    iface = device.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
                    ev = cast(iface, POINTER(IAudioEndpointVolume))
                self._ev = ev
                self._taken_at = now
            except Exception as e:  # noqa: BLE001
                log_throttled("volume-init", "Не удалось получить доступ к громкости", e)
        return self._ev

    def get(self) -> tuple[int | None, bool]:
        """(громкость 0..100 или None, выключен ли звук)."""
        ev = self._endpoint()
        if ev is None:
            return None, False
        try:
            return round(ev.GetMasterVolumeLevelScalar() * 100), bool(ev.GetMute())
        except Exception:  # noqa: BLE001
            self._ev = None
            return None, False

    def set(self, percent: int) -> None:
        percent = max(0, min(100, int(percent)))
        ev = self._endpoint()
        if ev is None:
            return
        try:
            ev.SetMasterVolumeLevelScalar(percent / 100, None)
            if percent > 0 and ev.GetMute():
                ev.SetMute(0, None)
        except Exception:  # noqa: BLE001
            self._ev = None

    def toggle_mute(self) -> None:
        ev = self._endpoint()
        if ev is None:
            return
        try:
            ev.SetMute(0 if ev.GetMute() else 1, None)
        except Exception:  # noqa: BLE001
            self._ev = None

    def step(self, steps: int) -> None:
        """Шаг колесом мыши: ±2% за щелчок."""
        current, _ = self.get()
        if current is None:
            winutils.volume_step(steps > 0, abs(steps))
        else:
            self.set(current + 2 * steps)
