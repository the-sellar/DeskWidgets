"""Автозапуск вместе с Windows и запуск от администратора без окна UAC.

Два способа:
1. Обычный автозапуск — строка в реестре HKCU\\...\\Run. Права администратора не нужны.
2. «Запускать от администратора» — задача в Планировщике заданий с флагом
   «Выполнить с наивысшими правами». Задачу, созданную один раз (это требует
   одного подтверждения UAC), потом можно запускать без запроса UAC.
   Поэтому при обычном запуске программа без прав сначала просит Планировщик
   запустить эту задачу и сама закрывается — новая копия уже с правами.
"""

import html
import os
import subprocess
import tempfile
import winreg

from . import winutils
from .logger import log
from .paths import APP_NAME, app_dir, launch_command

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
TASK_NAME = "DeskWidgets"
CREATE_NO_WINDOW = 0x08000000  # не показывать чёрное окно консоли при запуске schtasks


def _command_line() -> str:
    exe, args = launch_command()
    return f'"{exe}" {args}'.strip()


# ---------- реестр ----------

def set_registry_autostart(enabled: bool) -> None:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, _command_line())
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except FileNotFoundError:
                pass


# ---------- планировщик заданий ----------

def task_exists() -> bool:
    try:
        r = subprocess.run(["schtasks", "/Query", "/TN", TASK_NAME],
                           capture_output=True, creationflags=CREATE_NO_WINDOW, timeout=10)
        return r.returncode == 0
    except Exception:
        return False


def run_task() -> bool:
    """Запустить задачу (программа стартует с правами администратора, без UAC)."""
    try:
        r = subprocess.run(["schtasks", "/Run", "/TN", TASK_NAME],
                           capture_output=True, creationflags=CREATE_NO_WINDOW, timeout=10)
        return r.returncode == 0
    except Exception:
        return False


def _task_xml(with_logon_trigger: bool) -> str:
    """Описание задачи в формате XML. Через XML (а не параметры schtasks) можно
    отключить ограничения по умолчанию: «остановить через 3 дня» и «не запускать от батареи»."""
    exe, args = launch_command()
    user = f"{os.environ.get('USERDOMAIN', '')}\\{os.environ.get('USERNAME', '')}"
    e = html.escape  # экранируем &, <, > и кавычки в путях
    trigger = ""
    if with_logon_trigger:
        trigger = (f"<Triggers><LogonTrigger><Enabled>true</Enabled><UserId>{e(user)}</UserId>"
                   f"<Delay>PT5S</Delay></LogonTrigger></Triggers>")
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>DeskWidgets: запуск с правами администратора без запроса UAC</Description></RegistrationInfo>
  {trigger}
  <Principals>
    <Principal id="Author">
      <UserId>{e(user)}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>false</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{e(exe)}</Command>
      <Arguments>{e(args)}</Arguments>
      <WorkingDirectory>{e(app_dir())}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def _create_task(with_logon_trigger: bool) -> tuple[bool, str]:
    path = os.path.join(tempfile.gettempdir(), "deskwidgets_task.xml")
    with open(path, "w", encoding="utf-16") as f:
        f.write(_task_xml(with_logon_trigger))
    code = winutils.run_elevated("schtasks.exe", f'/Create /TN "{TASK_NAME}" /XML "{path}" /F')
    if code is None:
        return False, "Запрос прав администратора отклонён."
    if code != 0:
        return False, f"Планировщик заданий вернул ошибку (код {code})."
    return True, ""


def _delete_task() -> tuple[bool, str]:
    code = winutils.run_elevated("schtasks.exe", f'/Delete /TN "{TASK_NAME}" /F')
    if code is None:
        return False, "Запрос прав администратора отклонён — задача в Планировщике осталась."
    return True, ""


def apply_startup(autostart: bool, as_admin: bool) -> tuple[bool, str]:
    """Приводит систему в соответствие с двумя галочками. Возвращает (успех, текст ошибки)."""
    try:
        if as_admin:
            set_registry_autostart(False)          # чтобы не запускалось дважды
            return _create_task(with_logon_trigger=autostart)
        ok, msg = True, ""
        if task_exists():
            ok, msg = _delete_task()
        set_registry_autostart(autostart)
        return ok, msg
    except Exception as e:
        log.exception("Ошибка настройки автозапуска")
        return False, str(e)


def refresh_registry_path(autostart: bool, as_admin: bool) -> None:
    """При старте обновляем путь в реестре (если папку с программой перенесли)."""
    if autostart and not as_admin:
        try:
            set_registry_autostart(True)
        except OSError:
            log.exception("Не удалось обновить автозапуск в реестре")
