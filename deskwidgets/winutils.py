"""Обёртки над Windows API через ctypes.

ctypes — стандартный модуль Python, позволяющий вызывать функции из системных
DLL (user32.dll, kernel32.dll и т. д.) без компиляции. Для каждой функции
указываем типы аргументов (argtypes) и результата (restype) — иначе на 64-битной
системе указатели и дескрипторы окон могут «обрезаться» до 32 бит.

Важно: Qt 6 включает для процесса режим Per-Monitor DPI Aware, поэтому все
координаты от WinAPI здесь — в физических пикселях. Мы сравниваем их только
между собой (окно ↔ монитор), с координатами Qt не смешиваем.
"""

import ctypes
import os
import winreg
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
dwmapi = ctypes.WinDLL("dwmapi")

# ---------- константы ----------
GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020   # окно «прозрачно» для мыши — клики уходят в окно под ним
WS_EX_LAYERED = 0x00080000       # окно с поддержкой прозрачности
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
MONITOR_DEFAULTTONEAREST = 2
DWMWA_CLOAKED = 14               # «спрятанные» окна (фоновые UWP-приложения, другие рабочие столы)
VK_CONTROL = 0x11
VK_VOLUME_DOWN = 0xAE
VK_VOLUME_UP = 0xAF
KEYEVENTF_KEYUP = 0x0002
ERROR_ALREADY_EXISTS = 183
ERROR_ACCESS_DENIED = 5

# Окна оболочки Windows, которые не считаем «полноэкранными приложениями»
_SHELL_CLASSES = {
    "Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd",
    "Windows.UI.Core.CoreWindow", "XamlExplorerHostIslandWindow",
}


class RECT(ctypes.Structure):
    _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                ("right", wintypes.LONG), ("bottom", wintypes.LONG)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", RECT),
                ("rcWork", RECT), ("dwFlags", wintypes.DWORD)]


class SHELLEXECUTEINFOW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD), ("fMask", wintypes.ULONG), ("hwnd", wintypes.HWND),
        ("lpVerb", wintypes.LPCWSTR), ("lpFile", wintypes.LPCWSTR),
        ("lpParameters", wintypes.LPCWSTR), ("lpDirectory", wintypes.LPCWSTR),
        ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE), ("lpIDList", ctypes.c_void_p),
        ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY), ("dwHotKey", wintypes.DWORD),
        ("hIconOrMonitor", wintypes.HANDLE), ("hProcess", wintypes.HANDLE),
    ]


# ---------- прототипы функций ----------
if ctypes.sizeof(ctypes.c_void_p) == 8:
    _GetWindowLongPtr = user32.GetWindowLongPtrW
    _SetWindowLongPtr = user32.SetWindowLongPtrW
else:  # 32-битный Python
    _GetWindowLongPtr = user32.GetWindowLongW
    _SetWindowLongPtr = user32.SetWindowLongW
_GetWindowLongPtr.argtypes = [wintypes.HWND, ctypes.c_int]
_GetWindowLongPtr.restype = ctypes.c_ssize_t
_SetWindowLongPtr.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
_SetWindowLongPtr.restype = ctypes.c_ssize_t

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
user32.EnumWindows.restype = wintypes.BOOL
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsZoomed.argtypes = [wintypes.HWND]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
user32.MonitorFromWindow.restype = ctypes.c_void_p
user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(MONITORINFO)]
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.c_void_p]
dwmapi.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CreateMutexW.restype = wintypes.HANDLE
kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(SHELLEXECUTEINFOW)]
shell32.ShellExecuteExW.restype = wintypes.BOOL


# ---------- окна виджетов ----------

def set_click_through(hwnd: int, enabled: bool) -> None:
    """Включает/выключает «прозрачность для мыши»: с WS_EX_TRANSPARENT клики проходят сквозь окно."""
    ex = _GetWindowLongPtr(hwnd, GWL_EXSTYLE)
    if enabled:
        new = ex | WS_EX_TRANSPARENT | WS_EX_LAYERED
    else:
        new = ex & ~WS_EX_TRANSPARENT
    if new != ex:
        _SetWindowLongPtr(hwnd, GWL_EXSTYLE, new)


def is_key_down(vk: int) -> bool:
    """Нажата ли клавиша прямо сейчас (работает, даже когда наше окно не в фокусе)."""
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


def volume_step(up: bool, steps: int = 1) -> None:
    """Меняет системную громкость, имитируя мультимедийные клавиши (1 нажатие = 2%).
    Плюс: Windows сама покажет свой индикатор громкости."""
    vk = VK_VOLUME_UP if up else VK_VOLUME_DOWN
    for _ in range(max(1, min(steps, 10))):
        user32.keybd_event(vk, 0, 0, None)
        user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, None)


def monitor_of_window(hwnd: int) -> int:
    """Дескриптор монитора (HMONITOR), на котором находится большая часть окна."""
    return user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST) or 0


# ---------- полноэкранные окна ----------

def _enum_windows() -> list[int]:
    """Все окна верхнего уровня в порядке Z (сверху вниз)."""
    result: list[int] = []

    def callback(hwnd, _lparam):
        result.append(hwnd)
        return True

    user32.EnumWindows(WNDENUMPROC(callback), 0)
    return result


def _is_cloaked(hwnd: int) -> bool:
    value = ctypes.c_int(0)
    res = dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(value), ctypes.sizeof(value))
    return res == 0 and value.value != 0


def _class_name(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _window_pid(hwnd: int) -> int:
    pid = wintypes.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def _monitor_info(hmon) -> MONITORINFO | None:
    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(MONITORINFO)
    if not user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
        return None
    return mi


def fullscreen_monitors(exclude_pid: int | None = None) -> set[int]:
    """Возвращает множество мониторов (HMONITOR), на которых сейчас открыто полноэкранное окно.

    Алгоритм: идём по окнам сверху вниз (EnumWindows отдаёт их в Z-порядке).
    Для каждого монитора смотрим на первое «большое» окно на нём:
      - окно ровно размером с монитор (или больше, но не «развёрнутое») → полноэкранный режим;
      - обычное развёрнутое окно → не полноэкранный, монитор дальше не проверяем.
    Мелкие окна поверх (например, уведомление) игнорируем. Прозрачные оверлеи
    (NVIDIA, Discord) имеют WS_EX_TRANSPARENT/WS_EX_TOOLWINDOW — тоже пропускаем.
    """
    exclude_pid = exclude_pid if exclude_pid is not None else os.getpid()
    decided: set[int] = set()
    result: set[int] = set()
    for hwnd in _enum_windows():
        if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
            continue
        ex = _GetWindowLongPtr(hwnd, GWL_EXSTYLE)
        if ex & (WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE):
            continue
        if _is_cloaked(hwnd) or _class_name(hwnd) in _SHELL_CLASSES:
            continue
        if _window_pid(hwnd) == exclude_pid:
            continue
        r = RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
            continue
        if r.right - r.left <= 0 or r.bottom - r.top <= 0:
            continue
        hmon = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
        if not hmon or hmon in decided:
            continue
        mi = _monitor_info(hmon)
        if mi is None:
            continue
        m, wk = mi.rcMonitor, mi.rcWork
        exact = (abs(r.left - m.left) <= 1 and abs(r.top - m.top) <= 1
                 and abs(r.right - m.right) <= 1 and abs(r.bottom - m.bottom) <= 1)
        covers = r.left <= m.left and r.top <= m.top and r.right >= m.right and r.bottom >= m.bottom
        if exact or (covers and not user32.IsZoomed(hwnd)):
            result.add(hmon)
            decided.add(hmon)
            continue
        # Большое обычное окно (≥ 90% рабочей области) закрывает всё, что под ним
        ix = max(0, min(r.right, wk.right) - max(r.left, wk.left))
        iy = max(0, min(r.bottom, wk.bottom) - max(r.top, wk.top))
        work_area = max(1, (wk.right - wk.left) * (wk.bottom - wk.top))
        if ix * iy >= 0.9 * work_area:
            decided.add(hmon)
    return result


def any_fullscreen() -> bool:
    return bool(fullscreen_monitors())


def window_titles() -> list[str]:
    """Заголовки всех видимых окон (используем, чтобы угадать сайт: «… - YouTube - Google Chrome»)."""
    titles = []
    for hwnd in _enum_windows():
        if not user32.IsWindowVisible(hwnd):
            continue
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            continue
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        titles.append(buf.value)
    return titles


# ---------- права и процессы ----------

def is_admin() -> bool:
    try:
        return bool(shell32.IsUserAnAdmin())
    except Exception:
        return False


def run_elevated(exe: str, params: str, timeout_ms: int = 60000) -> int | None:
    """Запускает программу с правами администратора (появится окно UAC) и ждёт её завершения.

    Возвращает код выхода или None, если пользователь нажал «Нет» в окне UAC.
    """
    SEE_MASK_NOCLOSEPROCESS = 0x00000040
    SEE_MASK_NO_CONSOLE = 0x00008000
    sei = SHELLEXECUTEINFOW()
    sei.cbSize = ctypes.sizeof(SHELLEXECUTEINFOW)
    sei.fMask = SEE_MASK_NOCLOSEPROCESS | SEE_MASK_NO_CONSOLE
    sei.lpVerb = "runas"   # «запустить от имени администратора»
    sei.lpFile = exe
    sei.lpParameters = params
    sei.nShow = 0          # SW_HIDE — без окна консоли
    if not shell32.ShellExecuteExW(ctypes.byref(sei)):
        return None
    if not sei.hProcess:
        return 0
    kernel32.WaitForSingleObject(sei.hProcess, timeout_ms)
    code = wintypes.DWORD(0)
    kernel32.GetExitCodeProcess(sei.hProcess, ctypes.byref(code))
    kernel32.CloseHandle(sei.hProcess)
    return code.value


_mutex_handle = None


def acquire_single_instance(name: str = "Local\\DeskWidgets_SingleInstance") -> bool:
    """Именованный мьютекс не даёт запустить вторую копию программы.
    Возвращает False, если программа уже запущена."""
    global _mutex_handle
    handle = kernel32.CreateMutexW(None, False, name)
    err = ctypes.get_last_error()
    if not handle:
        # ACCESS_DENIED — мьютекс уже создан копией, запущенной от администратора
        return err != ERROR_ACCESS_DENIED
    if err == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        return False
    _mutex_handle = handle  # держим дескриптор до конца работы программы
    return True


def release_single_instance() -> None:
    """Освободить мьютекс заранее (перед перезапуском программы с правами администратора)."""
    global _mutex_handle
    if _mutex_handle:
        kernel32.CloseHandle(_mutex_handle)
        _mutex_handle = None


def system_uses_dark_theme() -> bool:
    """Тёмная ли тема приложений в параметрах Windows."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return value == 0
    except OSError:
        return False
