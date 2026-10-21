"""Создаёт ярлыки DeskWidgets на рабочем столе и в меню «Пуск».

Запуск (из папки проекта):  python tools\\make_shortcuts.py
Ярлык ведёт на dist\\DeskWidgets.exe — после пересборки его не нужно создавать заново.
"""

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXE = os.path.join(ROOT, "dist", "DeskWidgets.exe")

# Ярлыки .lnk создаём через COM-объект WScript.Shell — так же, как это делает сама Windows.
# Папки берём через GetFolderPath: рабочий стол может быть перенесён, например, в OneDrive.
PS_SCRIPT = r"""
$exe = $env:DW_EXE
$shell = New-Object -ComObject WScript.Shell
$targets = @(
  [Environment]::GetFolderPath('Desktop'),
  (Join-Path ([Environment]::GetFolderPath('Programs')) '')
)
foreach ($dir in $targets) {
  $lnk = $shell.CreateShortcut((Join-Path $dir 'DeskWidgets.lnk'))
  $lnk.TargetPath = $exe
  $lnk.WorkingDirectory = Split-Path $exe
  $lnk.IconLocation = "$exe,0"
  $lnk.Description = $env:DW_DESC
  $lnk.Save()
  Write-Output (Join-Path $dir 'DeskWidgets.lnk')
}
"""


def main() -> int:
    if not os.path.isfile(EXE):
        print("Сначала соберите программу: python build.py")
        return 1
    env = dict(os.environ, DW_EXE=EXE, DW_DESC="Виджеты рабочего стола: музыка, монитор, таймер, макросы")
    result = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", PS_SCRIPT],
                            env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        print("Не удалось создать ярлыки:\n", result.stderr)
        return 1
    print("Ярлыки созданы:\n" + result.stdout.strip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
