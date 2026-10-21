"""Работа с медиа-сессиями Windows (то, что показывается в плашке громкости Win+…).

API: Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager.
Браузеры (Chrome, Edge, Яндекс) регистрируют сюда вкладки с YouTube, ВК Музыкой
и т. д. через Media Session API — поэтому мы видим трек, обложку и можем
управлять воспроизведением.

Из Python к WinRT ходим через пакеты pywinrt (winrt-Windows.Media.Control и др.).
Старый пакет winsdk тоже поддерживается — API у них одинаковый.

WinRT-методы асинхронные (await ...), поэтому работаем в отдельном потоке со
своим циклом asyncio, а результаты передаём в интерфейс сигналом Qt.
Сигналы между потоками в Qt безопасны: слот выполнится в главном потоке.
"""

import asyncio
import ctypes
import threading
import time
from datetime import datetime, timezone

from PySide6.QtCore import QObject, Signal

from .. import winutils
from ..logger import log, log_throttled

AVAILABLE = False
IMPORT_ERROR = ""
try:  # современный pywinrt (pip install winrt-Windows.Media.Control ...)
    from winrt.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager as MediaManager,
        GlobalSystemMediaTransportControlsSessionPlaybackStatus as PlaybackStatus,
    )
    from winrt.windows.storage.streams import Buffer, DataReader, InputStreamOptions
    AVAILABLE = True
except Exception as e1:  # noqa: BLE001
    try:  # старый пакет winsdk
        from winsdk.windows.media.control import (
            GlobalSystemMediaTransportControlsSessionManager as MediaManager,
            GlobalSystemMediaTransportControlsSessionPlaybackStatus as PlaybackStatus,
        )
        from winsdk.windows.storage.streams import Buffer, DataReader, InputStreamOptions
        AVAILABLE = True
    except Exception as e2:  # noqa: BLE001
        IMPORT_ERROR = f"winrt: {e1}; winsdk: {e2}"


def _buffer_to_bytes(buf) -> bytes:
    """IBuffer (буфер WinRT) → bytes. Пробуем несколько способов: они отличаются в версиях пакетов."""
    n = int(buf.length)
    try:
        return bytes(memoryview(buf))[:n]   # новые версии: буфер поддерживает buffer protocol
    except TypeError:
        pass
    reader = DataReader.from_buffer(buf)
    data = bytearray(n)
    reader.read_bytes(data)
    return bytes(data)


def _guess_site(title: str, artist: str) -> str:
    """Пытаемся угадать сайт по заголовкам окон браузера.

    Сам Windows сайт не сообщает, но заголовок активной вкладки обычно
    «Название видео - YouTube - Google Chrome». Работает, если вкладка с
    музыкой — активная в своём окне браузера.
    """
    if not title:
        return ""
    t_low, a_low = title.lower(), (artist or "").lower()
    for wt in winutils.window_titles():
        low = wt.lower()
        if t_low not in low and (not a_low or a_low not in low):
            continue
        if "youtube music" in low:
            return "YouTube Music"
        if "youtube" in low:
            return "YouTube"
        if "vk" in low or "вконтакте" in low or "вк музыка" in low:
            return "ВК Музыка"
        if "яндекс музык" in low or "yandex music" in low:
            return "Яндекс Музыка"
    return ""


class MediaBackend(QObject):
    updated = Signal(object)   # list[dict] — по одному словарю на сессию
    failed = Signal(str)       # текст ошибки (например, пакет не установлен)

    def __init__(self):
        super().__init__()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._wake: asyncio.Event | None = None
        self._stopping = False
        self._sessions: list = []          # объекты сессий WinRT — трогаем только в рабочем потоке
        self._thumbs: dict[str, dict] = {}  # ключ трека → {"data": bytes|None, "tries": n}
        self._sites: dict[str, tuple[str, float]] = {}

    # ---------- управление потоком ----------

    def start(self) -> None:
        if not AVAILABLE:
            log.error("Медиа-сессии недоступны: %s", IMPORT_ERROR)
            self.failed.emit("Не установлен пакет winrt (см. README)")
            return
        self._thread = threading.Thread(target=self._run, name="media", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stopping = True
        loop, wake = self._loop, self._wake
        if loop and wake and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(wake.set)
            except RuntimeError:
                pass

    def command(self, index: int, action: str) -> None:
        """Отправить команду (toggle/next/prev) сессии с номером index. Вызывается из главного потока."""
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        asyncio.run_coroutine_threadsafe(self._do_command(index, action), loop)

    # ---------- рабочий поток ----------

    def _run(self) -> None:
        # Инициализация COM в многопоточном режиме — WinRT это требуется в новом потоке
        try:
            ctypes.windll.ole32.CoInitializeEx(None, 0)  # 0 = COINIT_MULTITHREADED
        except Exception:
            pass
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            loop.run_until_complete(self._main())
        except Exception:
            log.exception("Поток медиа-сессий завершился с ошибкой")
        finally:
            loop.close()

    async def _main(self) -> None:
        self._wake = asyncio.Event()
        manager = None
        while not self._stopping:
            try:
                if manager is None:
                    manager = await MediaManager.request_async()
                data = await self._poll(manager)
                self.updated.emit(data)
            except Exception as e:  # noqa: BLE001
                log_throttled("media-poll", "Ошибка опроса медиа-сессий", e)
                manager = None  # на следующей итерации попробуем получить менеджер заново
            # Спим 1 секунду или пока нас не разбудят (команда/остановка)
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()

    async def _poll(self, manager) -> list[dict]:
        sessions = list(manager.get_sessions())
        self._sessions = sessions
        current = manager.get_current_session()
        current_id = current.source_app_user_model_id if current else None
        result = []
        for index, session in enumerate(sessions):
            try:
                item = await self._read_session(session)
            except Exception as e:  # noqa: BLE001
                log_throttled("media-read", "Не удалось прочитать медиа-сессию", e)
                continue
            item["index"] = index
            item["is_current"] = item["aumid"] == current_id
            result.append(item)
        return result

    async def _read_session(self, s) -> dict:
        aumid = s.source_app_user_model_id or ""
        props = await s.try_get_media_properties_async()
        title = (props.title or "") if props else ""
        artist = (props.artist or "") if props else ""
        album = (props.album_title or "") if props else ""

        info = s.get_playback_info()
        playing = bool(info) and info.playback_status == PlaybackStatus.PLAYING
        can_prev = can_next = can_toggle = True
        if info and info.controls:
            c = info.controls
            can_prev, can_next = c.is_previous_enabled, c.is_next_enabled
            can_toggle = c.is_play_pause_toggle_enabled or c.is_play_enabled or c.is_pause_enabled

        # --- позиция ---
        position = duration = 0.0
        try:
            tl = s.get_timeline_properties()
            start = tl.start_time.total_seconds()
            duration = max(0.0, tl.end_time.total_seconds() - start)
            position = tl.position.total_seconds() - start
            # Позиция сообщается «на момент last_updated_time» — добавляем прошедшее время
            lu = tl.last_updated_time
            if playing and lu is not None and lu.year > 1970:
                if lu.tzinfo is None:
                    lu = lu.replace(tzinfo=timezone.utc)
                elapsed = (datetime.now(timezone.utc) - lu).total_seconds()
                if 0 < elapsed < 24 * 3600:
                    position += elapsed
            if duration > 0:
                position = max(0.0, min(position, duration))
        except Exception:  # noqa: BLE001
            pass

        # --- обложка: читаем при смене трека, плюс пару повторов (браузер обновляет её с задержкой) ---
        key = f"{aumid}|{title}|{artist}"
        entry = self._thumbs.get(key)
        if entry is None:
            if len(self._thumbs) > 30:
                self._thumbs.clear()
            entry = self._thumbs[key] = {"data": None, "tries": 0}
        if entry["tries"] < 3 and props is not None and props.thumbnail is not None:
            entry["tries"] += 1
            data = await self._read_thumbnail(props.thumbnail)
            if data:
                entry["data"] = data

        # --- сайт (по заголовку окна), обновляем не чаще раза в 10 секунд ---
        site, checked = self._sites.get(key, ("", 0.0))
        if not site and time.monotonic() - checked > 10:
            site = _guess_site(title, artist)
            if len(self._sites) > 50:
                self._sites.clear()
            self._sites[key] = (site, time.monotonic())

        return {
            "aumid": aumid, "title": title, "artist": artist, "album": album, "site": site,
            "playing": playing, "position": position, "duration": duration,
            "snapshot": time.monotonic(),  # когда измерили позицию — интерфейс сам «докрутит» её
            "can_prev": bool(can_prev), "can_next": bool(can_next), "can_toggle": bool(can_toggle),
            "thumb_id": f"{key}#{entry['tries']}" if entry["data"] else "",
            "thumb": entry["data"],
        }

    async def _read_thumbnail(self, ref) -> bytes | None:
        try:
            stream = await ref.open_read_async()
            size = int(stream.size)
            if size <= 0 or size > 8 * 1024 * 1024:
                return None
            buf = Buffer(size)
            result = await stream.read_async(buf, size, InputStreamOptions.READ_AHEAD)
            data = _buffer_to_bytes(result)
            try:
                stream.close()
            except Exception:  # noqa: BLE001
                pass
            return data or None
        except Exception as e:  # noqa: BLE001
            log_throttled("media-thumb", "Не удалось прочитать обложку", e)
            return None

    async def _do_command(self, index: int, action: str) -> None:
        try:
            s = self._sessions[index]
            if action == "toggle":
                await s.try_toggle_play_pause_async()
            elif action == "next":
                await s.try_skip_next_async()
            elif action == "prev":
                await s.try_skip_previous_async()
        except Exception as e:  # noqa: BLE001
            log_throttled("media-cmd", f"Команда {action} не выполнена", e, period=30)
        await asyncio.sleep(0.25)  # даём плееру применить команду
        if self._wake:
            self._wake.set()      # и сразу обновляем данные
