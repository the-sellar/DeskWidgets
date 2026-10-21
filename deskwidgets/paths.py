"""Пути к файлам программы: настройки, логи, папка с DLL, команда запуска."""

import os
import sys

APP_NAME = "DeskWidgets"


def appdata_dir() -> str:
    """%APPDATA%\\DeskWidgets — здесь лежат config.json и log.txt. Папка создаётся при первом обращении."""
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    path = os.path.join(base, APP_NAME)
    os.makedirs(path, exist_ok=True)
    return path


def config_path() -> str:
    return os.path.join(appdata_dir(), "config.json")


def log_path() -> str:
    return os.path.join(appdata_dir(), "log.txt")


def is_frozen() -> bool:
    """True, если программа собрана PyInstaller'ом в .exe (у собранной программы есть атрибут sys.frozen)."""
    return bool(getattr(sys, "frozen", False))


def app_dir() -> str:
    """Папка, где лежит программа: рядом с DeskWidgets.exe или корень проекта (там, где main.py)."""
    if is_frozen():
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def lib_dir() -> str:
    """Папка lib рядом с программой — сюда кладут LibreHardwareMonitorLib.dll."""
    return os.path.join(app_dir(), "lib")


def launch_command() -> tuple[str, str]:
    """Чем запускать программу при автозапуске: (путь к exe, аргументы).

    Для собранного .exe — сам exe. Для запуска из исходников — pythonw.exe
    (вариант Python без чёрного окна консоли) + путь к main.py.
    """
    if is_frozen():
        return sys.executable, ""
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.exists(pythonw):
        pythonw = sys.executable
    return pythonw, f'"{os.path.join(app_dir(), "main.py")}"'
