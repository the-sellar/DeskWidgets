"""Точка входа DeskWidgets. Запуск: python main.py (или pythonw main.py — без окна консоли)."""

import sys


def main() -> int:
    # Логирование настраиваем первым делом — чтобы поймать даже ошибки запуска
    from deskwidgets.logger import log, setup_logging
    setup_logging()

    from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon

    from deskwidgets import autostart, winutils
    from deskwidgets.config import Config

    qapp = QApplication(sys.argv)
    qapp.setApplicationName("DeskWidgets")
    # Программа живёт в трее: закрытие последнего окна (например, настроек) не должно её завершать
    qapp.setQuitOnLastWindowClosed(False)

    cfg = Config()

    # «Запускать от администратора»: если мы без прав, а задача в Планировщике есть —
    # просим Планировщик запустить программу с правами (без UAC) и выходим.
    if cfg.get("general.run_as_admin") and not winutils.is_admin() and "--no-elevate" not in sys.argv:
        if autostart.task_exists() and autostart.run_task():
            log.info("Перезапуск с правами администратора через Планировщик заданий")
            return 0
        log.warning("Не удалось запустить задачу Планировщика — продолжаю без прав администратора")

    if not winutils.acquire_single_instance():
        log.info("Программа уже запущена — вторая копия закрывается")
        QMessageBox.information(None, "DeskWidgets", "DeskWidgets уже запущен — ищите иконку в трее.")
        return 0

    if not QSystemTrayIcon.isSystemTrayAvailable():
        log.warning("Системный трей недоступен")

    from deskwidgets.app import DeskWidgetsApp
    app = DeskWidgetsApp(qapp, cfg)  # noqa: F841 — держим ссылку, иначе объект удалится сборщиком мусора
    code = qapp.exec()
    cfg.save()
    return code


if __name__ == "__main__":
    sys.exit(main())
