"""Windows-specific symbolic-link support, isolated for portable tests."""

import ctypes
import os
import tempfile
from dataclasses import dataclass

ERROR_PRIVILEGE_NOT_HELD = 1314
_KEY_PATH = r"SOFTWARE\Microsoft\Windows\CurrentVersion\AppModelUnlock"
_VALUE_NAME = "AllowDevelopmentWithoutDevLicense"
_REG_ARGUMENTS = (
    r'ADD "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\AppModelUnlock" '
    r"/v AllowDevelopmentWithoutDevLicense /t REG_DWORD /d 1 /f /reg:64"
)
_WAIT_FAILED = 0xFFFFFFFF
_INFINITE = 0xFFFFFFFF


class SymlinkPrivilegeError(OSError):
    """A symlink failed solely because Windows withheld the privilege."""


def is_privilege_not_held(error):
    return getattr(error, "winerror", None) == ERROR_PRIVILEGE_NOT_HELD


def create_symlink(target, link, *, target_is_directory=False):
    """Create a link and classify only Windows' Developer Mode failure."""
    try:
        os.symlink(target, link, target_is_directory=target_is_directory)
    except OSError as error:
        if is_privilege_not_held(error):
            raise SymlinkPrivilegeError(*error.args) from error
        raise


@dataclass(frozen=True)
class SetupResult:
    success: bool
    message: str


def _probe_symlinks():
    """Exercise both Windows link kinds rather than trusting a registry value."""
    with tempfile.TemporaryDirectory(prefix="dfm-symlink-") as directory:
        file_target = os.path.join(directory, "target-file")
        directory_target = os.path.join(directory, "target-directory")
        open(file_target, "w").close()
        os.mkdir(directory_target)
        os.symlink(
            file_target, os.path.join(directory, "file-link"), target_is_directory=False
        )
        os.symlink(
            directory_target,
            os.path.join(directory, "directory-link"),
            target_is_directory=True,
        )


def _registry_value():
    import winreg

    access = winreg.KEY_READ | winreg.KEY_WOW64_64KEY
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _KEY_PATH, 0, access) as key:
            value, kind = winreg.QueryValueEx(key, _VALUE_NAME)
    except FileNotFoundError:
        return False
    return kind == winreg.REG_DWORD and value == 1


def _set_registry_value():
    import winreg

    access = winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY
    with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, _KEY_PATH, 0, access) as key:
        winreg.SetValueEx(key, _VALUE_NAME, 0, winreg.REG_DWORD, 1)


def _is_elevated():
    from ctypes import wintypes

    function = _win_dll("shell32").IsUserAnAdmin
    function.argtypes = []
    function.restype = wintypes.BOOL
    return bool(function())


def _win_dll(name):
    return ctypes.WinDLL(name, use_last_error=True)


def _system_reg_exe():
    """Use an absolute system path; never resolve a caller-controlled reg.exe."""
    buffer = ctypes.create_unicode_buffer(32768)
    from ctypes import wintypes

    function = _win_dll("kernel32").GetSystemDirectoryW
    function.argtypes = [wintypes.LPWSTR, wintypes.DWORD]
    function.restype = wintypes.DWORD
    length = function(buffer, len(buffer))
    if not length or length >= len(buffer):
        raise OSError(ctypes.get_last_error(), "GetSystemDirectoryW failed")
    path = os.path.join(buffer.value, "reg.exe")
    if not os.path.isabs(path):  # Defensive: ShellExecute must never search PATH.
        raise OSError("GetSystemDirectoryW returned a non-absolute path")
    return path


def _shell_execute_elevated(executable, arguments, directory):
    """Run an absolute command with UAC and wait for its real exit status."""
    from ctypes import wintypes

    class SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("fMask", wintypes.ULONG),
            ("hwnd", wintypes.HWND),
            ("lpVerb", wintypes.LPCWSTR),
            ("lpFile", wintypes.LPCWSTR),
            ("lpParameters", wintypes.LPCWSTR),
            ("lpDirectory", wintypes.LPCWSTR),
            ("nShow", ctypes.c_int),
            ("hInstApp", wintypes.HINSTANCE),
            ("lpIDList", ctypes.c_void_p),
            ("lpClass", wintypes.LPCWSTR),
            ("hkeyClass", wintypes.HKEY),
            ("dwHotKey", wintypes.DWORD),
            ("hIcon", wintypes.HANDLE),
            ("hProcess", wintypes.HANDLE),
        ]

    info = SHELLEXECUTEINFOW(
        cbSize=ctypes.sizeof(SHELLEXECUTEINFOW),
        fMask=0x00000040,
        lpVerb="runas",
        lpFile=executable,
        lpParameters=arguments,
        lpDirectory=directory,
        nShow=0,
    )
    shell_execute = _win_dll("shell32").ShellExecuteExW
    shell_execute.argtypes = [ctypes.POINTER(SHELLEXECUTEINFOW)]
    shell_execute.restype = wintypes.BOOL
    if not shell_execute(ctypes.byref(info)):
        raise OSError(ctypes.get_last_error(), "UAC elevation was cancelled or failed")
    kernel32 = _win_dll("kernel32")
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.GetExitCodeProcess.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    try:
        if kernel32.WaitForSingleObject(info.hProcess, _INFINITE) == _WAIT_FAILED:
            raise OSError(ctypes.get_last_error(), "waiting for reg.exe failed")
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code)):
            raise OSError(ctypes.get_last_error(), "cannot read reg.exe exit code")
        if code.value != 0:
            raise OSError(f"reg.exe exited with status {code.value}")
    finally:
        kernel32.CloseHandle(info.hProcess)


def _run_elevated_reg():
    """Elevate only system reg.exe with the fixed 64-bit Developer Mode command."""
    executable = _system_reg_exe()
    _shell_execute_elevated(executable, _REG_ARGUMENTS, os.path.dirname(executable))


def setup_developer_mode():
    """Enable Developer Mode only when an actual link probe needs it."""
    if os.name != "nt":
        return SetupResult(
            False, "dfm setup is only available on Windows; no changes made."
        )
    try:
        elevated = _is_elevated()
        enabled = _registry_value()
    except OSError as error:
        return SetupResult(False, f"Could not read Windows Developer Mode: {error}")

    # An elevated process has a different symlink privilege.  Its probe cannot
    # establish what the ordinary dfm process will be allowed to do.
    if elevated:
        try:
            if not enabled:
                _set_registry_value()
                if not _registry_value():
                    return SetupResult(
                        False, "Developer Mode setting could not be verified."
                    )
        except OSError as error:
            return SetupResult(
                False, f"Could not enable Windows Developer Mode: {error}"
            )
        return SetupResult(
            True,
            "Windows Developer Mode is enabled. Run dfm normally as your ordinary "
            "user and retry the original command.",
        )

    try:
        _probe_symlinks()
    except OSError as error:
        if not is_privilege_not_held(error):
            return SetupResult(False, f"Windows symlink probe failed: {error}")
    else:
        return SetupResult(
            True, "Windows symlink creation is already available; no action needed."
        )

    if enabled:
        return SetupResult(
            False,
            "Windows Developer Mode is already enabled, but this ordinary-user "
            "probe still lacks symlink privilege. Retry dfm normally after Windows "
            "applies the setting.",
        )

    try:
        _run_elevated_reg()
        if not _registry_value():
            return SetupResult(False, "Developer Mode setting could not be verified.")
        _probe_symlinks()
    except OSError as error:
        return SetupResult(False, f"Could not enable Windows Developer Mode: {error}")
    return SetupResult(
        True,
        "Windows denied symbolic links with ERROR_PRIVILEGE_NOT_HELD; "
        "Developer Mode enabled and symbolic links are available.",
    )
