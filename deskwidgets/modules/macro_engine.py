"""Выполнение макросов: запуск сайтов и программ, поиск их окна и перенос на нужный монитор.

Макрос — список шагов (словарей) из настроек:
  {"type": "url",  "target": "https://youtube.com", "browser": "", "monitor": "right", "state": "maximize"}
  {"type": "app",  "target": "steam://rungameid/570", "args": "", "window": "", "monitor": "none", "state": "none"}
  {"type": "wait", "seconds": 3}

Как найти окно, которое открылось после запуска: перед запуском запоминаем
все окна, а потом несколько секунд ищем НОВОЕ окно нужного процесса
(для сайта — процесса браузера) или с нужным текстом в заголовке.
"""

import ctypes
import os
import shlex
import subprocess
import time
import winreg
from ctypes import wintypes

import psutil

from .. import winutils
from ..logger import log
from ..winutils import RECT, MONITORINFO, user32

WINDOW_TIMEOUT = 20  # сколько секунд ждать появления окна

MONITORS = [("none", "Не перемещать"), ("primary", "Основной"), ("right", "Правый"),
            ("left", "Левый"), ("second", "Второй (не основной)")]
STATES = [("maximize", "Развернуть"), ("fullscreen", "На весь экран (F11, для браузера)"), ("none", "Как есть")]

SW_RESTORE, SW_MAXIMIZE = 9, 3
SWP_NOZORDER, SWP_NOACTIVATE = 0x0004, 0x0010
VK_F11 = 0x7A
MONITORINFOF_PRIMARY = 1

MONITORENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, ctypes.c_void_p, ctypes.c_void_p,
                                     ctypes.POINTER(RECT), wintypes.LPARAM)
user32.EnumDisplayMonitors.argtypes = [ctypes.c_void_p, ctypes.c_void_p, MONITORENUMPROC, wintypes.LPARAM]
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]


# ---------------- браузеры ----------------

def known_browsers() -> list[tuple[str, str]]:
    """Установленные браузеры: [(название, путь к exe)]."""
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    local = os.environ.get("LOCALAPPDATA", "")
    candidates = [
        ("Chrome", os.path.join(pf, r"Google\Chrome\Application\chrome.exe")),
        ("Chrome", os.path.join(pf86, r"Google\Chrome\Application\chrome.exe")),
        ("Chrome", os.path.join(local, r"Google\Chrome\Application\chrome.exe")),
        ("Яндекс Браузер", os.path.join(local, r"Yandex\YandexBrowser\Application\browser.exe")),
        ("Edge", os.path.join(pf86, r"Microsoft\Edge\Application\msedge.exe")),
        ("Edge", os.path.join(pf, r"Microsoft\Edge\Application\msedge.exe")),
        ("Firefox", os.path.join(pf, r"Mozilla Firefox\firefox.exe")),
    ]
    result, seen = [], set()
    for name, path in candidates:
        if name not in seen and os.path.isfile(path):
            result.append((name, path))
            seen.add(name)
    return result


def default_browser_exe() -> str | None:
    """Браузер по умолчанию из реестра: ProgId для https → команда открытия → путь к exe."""
    try:
        key = r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            prog_id = winreg.QueryValueEx(k, "ProgId")[0]
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, prog_id + r"\shell\open\command") as k:
            command = winreg.QueryValueEx(k, "")[0]
        exe = command[1:command.index('"', 1)] if command.startswith('"') else command.split()[0]
        return exe if os.path.isfile(exe) else None
    except (OSError, ValueError, IndexError):
        return None


# ---------------- окна и мониторы ----------------

def _visible_windows() -> list[int]:
    """Видимые «обычные» окна верхнего уровня (без служебных и окон-инструментов)."""
    result = []
    for hwnd in winutils._enum_windows():
        if not hwnd or not user32.IsWindowVisible(hwnd):
            continue
        ex = winutils._GetWindowLongPtr(hwnd, winutils.GWL_EXSTYLE)
        if ex & winutils.WS_EX_TOOLWINDOW or winutils._is_cloaked(hwnd):
            continue
        result.append(hwnd)
    return result


def _title(hwnd: int) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def _process_name(hwnd: int) -> str:
    try:
        return psutil.Process(winutils._window_pid(hwnd)).name().lower()
    except (psutil.Error, OSError):
        return ""


def wait_new_window(before: set, process: str, title_part: str, should_stop) -> int | None:
    """Ждёт появления нового окна: процесса process (имя exe) и/или с title_part в заголовке."""
    process, title_part = process.lower(), title_part.lower()
    deadline = time.monotonic() + WINDOW_TIMEOUT
    while time.monotonic() < deadline and not should_stop():
        for hwnd in _visible_windows():
            if hwnd in before:
                continue
            r = RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(r))
            if r.right - r.left < 200 or r.bottom - r.top < 150:
                continue  # всплывающие мелкие окна (заставки, подсказки) пропускаем
            if title_part and title_part not in _title(hwnd).lower():
                continue
            if process and _process_name(hwnd) != process:
                continue
            return hwnd
        time.sleep(0.3)
    return None


def _monitors() -> list[dict]:
    result = []

    def callback(hmon, _hdc, _rect, _lparam):
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            w = mi.rcWork
            result.append({"work": (w.left, w.top, w.right, w.bottom),
                           "primary": bool(mi.dwFlags & MONITORINFOF_PRIMARY)})
        return True

    user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(callback), 0)
    return result


def pick_monitor(which: str) -> dict | None:
    mons = _monitors()
    if not mons:
        return None
    primary = next((m for m in mons if m["primary"]), mons[0])
    if which == "primary":
        return primary
    if which == "right":
        return max(mons, key=lambda m: m["work"][0])
    if which == "left":
        return min(mons, key=lambda m: m["work"][0])
    if which == "second":
        return next((m for m in mons if not m["primary"]), primary)
    return None


def place_window(hwnd: int, monitor: str, state: str) -> None:
    """Переносит окно на монитор и разворачивает/делает полноэкранным."""
    mon = pick_monitor(monitor)
    if mon is not None:
        # Развёрнутое окно сначала «восстанавливаем», иначе Windows не даст его сдвинуть
        if user32.IsZoomed(hwnd) or user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)
        left, top, right, bottom = mon["work"]
        user32.SetWindowPos(hwnd, None, left + 40, top + 40, right - left - 80, bottom - top - 80,
                            SWP_NOZORDER | SWP_NOACTIVATE)
    if state in ("maximize", "fullscreen"):
        user32.ShowWindow(hwnd, SW_MAXIMIZE)
    if state == "fullscreen":
        time.sleep(0.8)  # даём странице загрузиться, иначе F11 может «потеряться»
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.2)
        user32.keybd_event(VK_F11, 0, 0, None)
        user32.keybd_event(VK_F11, 0, winutils.KEYEVENTF_KEYUP, None)


# ---------------- шаги ----------------

def describe(step: dict) -> str:
    """Человеческое описание шага для списка в настройках и подсказки на кнопке."""
    t = step.get("type")
    mon = dict(MONITORS).get(step.get("monitor", "none"), "")
    st = dict(STATES).get(step.get("state", "none"), "")
    where = f" → {mon.lower()} монитор, {st.lower()}" if step.get("monitor", "none") != "none" else ""
    if t == "url":
        return f"Сайт {step.get('target', '')}{where}"
    if t == "app":
        return f"Запустить {step.get('target', '')}{where}"
    if t == "wait":
        return f"Подождать {step.get('seconds', 1)} с"
    return "?"


def run_steps(steps: list[dict], should_stop) -> list[str]:
    """Выполняет шаги по порядку. Ошибка в одном шаге не мешает остальным. Возвращает список ошибок."""
    errors = []
    for i, step in enumerate(steps, 1):
        if should_stop():
            break
        try:
            _run_step(step, should_stop)
        except Exception as e:  # noqa: BLE001
            log.exception("Макрос: ошибка в шаге %d (%s)", i, describe(step))
            errors.append(f"Шаг {i}: {e}")
    return errors


def _run_step(step: dict, should_stop) -> None:
    t = step.get("type")
    if t == "wait":
        end = time.monotonic() + float(step.get("seconds", 1))
        while time.monotonic() < end and not should_stop():
            time.sleep(0.1)
        return

    target = (step.get("target") or "").strip()
    if not target:
        raise ValueError("не указан адрес или программа")
    move = step.get("monitor", "none") != "none" or step.get("state", "none") != "none"
    before = set(_visible_windows())
    process = ""

    if t == "url":
        if "://" not in target:
            target = "https://" + target
        browser = step.get("browser") or default_browser_exe()
        if browser and os.path.isfile(browser):
            # --new-window: открыть в НОВОМ окне, а не вкладкой в уже открытом
            flag = "-new-window" if "firefox" in browser.lower() else "--new-window"
            subprocess.Popen([browser, flag, target])
            process = os.path.basename(browser)
        else:
            os.startfile(target)
    elif t == "app":
        if os.path.isfile(target) and target.lower().endswith(".exe"):
            args = shlex.split(step.get("args", ""), posix=False)
            subprocess.Popen([target, *args], cwd=os.path.dirname(target))
            process = os.path.basename(target)
        else:
            os.startfile(target)  # ярлык .lnk, steam://..., документ — как двойной щелчок
    else:
        raise ValueError(f"неизвестный тип шага: {t}")

    if not move:
        return
    title_part = step.get("window", "") if t == "app" else ""
    if not process and not title_part:
        log.info("Макрос: не знаю, какое окно двигать для «%s» — укажите часть заголовка окна", target)
        return
    hwnd = wait_new_window(before, process, title_part, should_stop)
    if hwnd is None:
        raise TimeoutError(f"окно «{target}» не появилось за {WINDOW_TIMEOUT} с")
    time.sleep(0.5)  # окно только что создано — даём ему «встать»
    place_window(hwnd, step.get("monitor", "none"), step.get("state", "none"))
