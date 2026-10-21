"""Сборка DeskWidgets.exe через PyInstaller.

Запуск (из папки проекта, с активированным venv):
    python build.py

Результат: dist\\DeskWidgets.exe и рядом dist\\lib (если в lib есть DLL).
"""

import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))

# «Сведения» о файле .exe. Диспетчер задач показывает FileDescription как имя программы,
# а Проводник — в свойствах файла на вкладке «Подробно».
VERSION_INFO = """VSVersionInfo(
  ffi=FixedFileInfo(filevers=(1, 1, 0, 0), prodvers=(1, 1, 0, 0), mask=0x3f, flags=0x0,
                    OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('041904B0', [
      StringStruct('FileDescription', 'DeskWidgets'),
      StringStruct('ProductName', 'DeskWidgets'),
      StringStruct('FileVersion', '1.1.0'),
      StringStruct('ProductVersion', '1.1.0'),
      StringStruct('InternalName', 'DeskWidgets'),
      StringStruct('OriginalFilename', 'DeskWidgets.exe'),
      StringStruct('Comments', 'Виджеты рабочего стола: музыка, монитор, таймер, макросы')])]),
    VarFileInfo([VarStruct('Translation', [0x0419, 1200])])
  ]
)
"""


def main() -> int:
    os.chdir(ROOT)

    # 1. Иконка для .exe
    subprocess.run([sys.executable, os.path.join("tools", "make_icon.py")], check=False)

    # 2. PyInstaller
    args = [
        "--noconfirm", "--clean",
        "--onefile",              # всё в один .exe
        "--windowed",             # без окна консоли
        "--name", "DeskWidgets",
        # Пакеты, которые PyInstaller не находит сам: часть модулей грузится динамически
        "--collect-all", "winrt",
        "--collect-all", "pythonnet",
        "--collect-all", "clr_loader",
        "--hidden-import", "clr",
        "--hidden-import", "pynvml",
        "--collect-all", "pycaw",
        # Модули программы импортируются через importlib — PyInstaller их не видит,
        # поэтому забираем весь пакет deskwidgets целиком (и новые модули тоже)
        "--collect-submodules", "deskwidgets",
    ]
    os.makedirs("build", exist_ok=True)
    version_file = os.path.join("build", "version_info.txt")
    with open(version_file, "w", encoding="utf-8") as f:
        f.write(VERSION_INFO)
    args += ["--version-file", version_file]

    icon = os.path.join("assets", "icon.ico")
    if os.path.exists(icon):
        args += ["--icon", icon]
    # python build.py --release → собрать в папку release (для выкладки на GitHub),
    # не трогая dist, где может работать ваша копия программы
    out = "release" if "--release" in sys.argv else "dist"
    args += ["--distpath", out]
    args.append("main.py")

    import PyInstaller.__main__
    PyInstaller.__main__.run(args)

    # 3. Папка lib (LibreHardwareMonitorLib.dll и её зависимости) — рядом с exe, а не внутри:
    #    так DLL можно обновить без пересборки. В релиз DLL не кладём — это чужая программа.
    if out == "dist" and os.path.isdir("lib"):
        shutil.copytree("lib", os.path.join(out, "lib"), dirs_exist_ok=True)

    print("\nГотово:", os.path.join(ROOT, out, "DeskWidgets.exe"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
