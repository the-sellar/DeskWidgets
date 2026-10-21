"""Журнал ошибок в %APPDATA%\\DeskWidgets\\log.txt.

Главная идея: любая необработанная ошибка (в главном потоке, в фоновом потоке
или в обработчике Qt) записывается в лог, а программа продолжает работать.
"""

import logging
import os
import sys
import threading
import time
from logging.handlers import RotatingFileHandler

from .paths import log_path

log = logging.getLogger("DeskWidgets")

# Для log_throttled: когда в последний раз писали каждое сообщение
_last_logged: dict[str, float] = {}


def setup_logging() -> None:
    # pythonw.exe и собранный --windowed .exe запускаются без консоли:
    # sys.stdout/sys.stderr там равны None, и любой print() упал бы с ошибкой.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")

    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(threadName)s: %(message)s")

    # Файл не растёт бесконечно: максимум 1 МБ + две старые копии (log.txt.1, log.txt.2)
    file_handler = RotatingFileHandler(log_path(), maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    file_handler.setFormatter(fmt)
    log.addHandler(file_handler)

    # Если запущено из консоли (python main.py) — дублируем лог в консоль
    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(fmt)
    log.addHandler(console)

    sys.excepthook = _excepthook
    threading.excepthook = _thread_excepthook
    log.info("=" * 60)
    log.info("DeskWidgets запущен, Python %s", sys.version.split()[0])


def _excepthook(exc_type, exc_value, exc_tb) -> None:
    """Сюда попадают ошибки, которые никто не поймал (в том числе из слотов Qt).
    Стандартное поведение — напечатать и (для главного потока) завершиться; мы только пишем в лог."""
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_tb)
        return
    log.error("Необработанная ошибка", exc_info=(exc_type, exc_value, exc_tb))


def _thread_excepthook(args) -> None:
    name = args.thread.name if args.thread else "?"
    log.error("Ошибка в потоке %s", name, exc_info=(args.exc_type, args.exc_value, args.exc_traceback))


def log_throttled(key: str, message: str, exc: BaseException | None = None, period: float = 300.0) -> None:
    """Пишет сообщение не чаще раза в period секунд.

    Нужна для ошибок, которые повторяются каждую секунду (например, датчик отвалился):
    без этого лог за час вырос бы на тысячи одинаковых строк.
    """
    now = time.monotonic()
    if now - _last_logged.get(key, -1e9) < period:
        return
    _last_logged[key] = now
    if exc is not None:
        log.error(message, exc_info=exc)
    else:
        log.warning(message)
