"""Optional Windows start-at-login support (HKCU Run key).

Default is OFF.  Nothing is written to the registry unless the user enables the
setting.  No administrator privileges are required.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "PhotoDesktopPet"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def is_supported() -> bool:
    return os.name == "nt"


def launch_command() -> str:
    """Command line suitable for the Run registry value."""

    if getattr(sys, "frozen", False):
        return f'"{Path(sys.executable).resolve()}"'
    python = Path(sys.executable)
    pythonw = python.with_name("pythonw.exe")
    interpreter = pythonw if pythonw.is_file() else python
    # src/autostart.py -> project root
    main_py = Path(__file__).resolve().parent.parent / "main.py"
    return f'"{interpreter}" "{main_py}"'


def _open_run_key(write: bool):
    import winreg  # type: ignore[import-not-found]

    access = winreg.KEY_READ
    if write:
        access = winreg.KEY_READ | winreg.KEY_SET_VALUE
    return winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, access)


def is_enabled() -> bool:
    if not is_supported():
        return False
    try:
        import winreg  # type: ignore[import-not-found]

        with _open_run_key(False) as key:
            try:
                value, _kind = winreg.QueryValueEx(key, APP_NAME)
            except FileNotFoundError:
                return False
            return bool(value)
    except OSError:
        return False


def set_enabled(enabled: bool) -> tuple[bool, str]:
    """Enable/disable autostart.  Returns ``(ok, message)``."""

    if not is_supported():
        return (False, "当前系统不是 Windows，无法设置开机启动。")
    try:
        import winreg  # type: ignore[import-not-found]

        if enabled:
            command = launch_command()
            with winreg.CreateKeyEx(
                winreg.HKEY_CURRENT_USER,
                RUN_KEY,
                0,
                winreg.KEY_SET_VALUE,
            ) as key:
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, command)
            return (True, f"已启用开机启动：{command}")
        try:
            with _open_run_key(True) as key:
                try:
                    winreg.DeleteValue(key, APP_NAME)
                except FileNotFoundError:
                    pass
        except FileNotFoundError:
            pass
        return (True, "已关闭开机启动。")
    except OSError as exc:
        return (False, f"无法修改开机启动项：{exc}")
    except Exception as exc:  # defensive: winreg can raise strange errors
        return (False, f"设置开机启动失败：{exc}")


def status_text() -> str:
    if not is_supported():
        return "当前系统不支持开机启动"
    return "已启用" if is_enabled() else "未启用"
