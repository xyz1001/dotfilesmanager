"""Portable unit tests for the Windows-only setup boundary."""

import sys
from types import SimpleNamespace
from unittest.mock import Mock

from dotfilesmanager import windows


def _privilege_error():
    error = OSError("privilege missing")
    error.winerror = 1314
    return error


def test_create_symlink_classifies_only_1314(monkeypatch):
    calls = []
    monkeypatch.setattr(
        windows.os, "symlink", lambda *args, **kwargs: calls.append(kwargs)
    )
    windows.create_symlink("target", "link", target_is_directory=True)
    assert calls == [{"target_is_directory": True}]

    monkeypatch.setattr(windows.os, "symlink", Mock(side_effect=_privilege_error()))
    try:
        windows.create_symlink("target", "link")
    except windows.SymlinkPrivilegeError as error:
        assert isinstance(error.__cause__, OSError)
    else:  # pragma: no cover - assertion fallback
        raise AssertionError("WinError 1314 was not classified")

    native = OSError("ordinary failure")
    monkeypatch.setattr(windows.os, "symlink", Mock(side_effect=native))
    try:
        windows.create_symlink("target", "link")
    except OSError as error:
        assert error is native
    else:  # pragma: no cover - assertion fallback
        raise AssertionError("ordinary error was swallowed")


def test_setup_rejects_non_windows_without_changes(monkeypatch):
    monkeypatch.setattr(windows.os, "name", "posix")
    probe = Mock()
    monkeypatch.setattr(windows, "_probe_symlinks", probe)
    result = windows.setup_developer_mode()
    assert not result.success
    assert "only available" in result.message
    probe.assert_not_called()


def test_setup_is_idempotent_when_probe_already_works(monkeypatch):
    monkeypatch.setattr(windows.os, "name", "nt")
    monkeypatch.setattr(windows, "_is_elevated", lambda: False)
    monkeypatch.setattr(windows, "_registry_value", lambda: False)
    probe = Mock()
    monkeypatch.setattr(windows, "_probe_symlinks", probe)
    set_value = Mock()
    monkeypatch.setattr(windows, "_set_registry_value", set_value)
    result = windows.setup_developer_mode()
    assert result.success
    assert "no action" in result.message
    set_value.assert_not_called()


def test_setup_elevates_fixed_command_and_requires_readback_and_probe(monkeypatch):
    monkeypatch.setattr(windows.os, "name", "nt")
    probe = Mock(side_effect=[_privilege_error(), None])
    monkeypatch.setattr(windows, "_probe_symlinks", probe)
    monkeypatch.setattr(windows, "_is_elevated", lambda: False)
    elevated = Mock()
    monkeypatch.setattr(windows, "_run_elevated_reg", elevated)
    monkeypatch.setattr(windows, "_registry_value", Mock(side_effect=[False, True]))
    result = windows.setup_developer_mode()
    assert result.success
    elevated.assert_called_once_with()
    assert probe.call_count == 2


def test_setup_elevated_process_does_not_use_its_probe_as_user_evidence(monkeypatch):
    monkeypatch.setattr(windows.os, "name", "nt")
    probe = Mock()
    monkeypatch.setattr(windows, "_probe_symlinks", probe)
    monkeypatch.setattr(windows, "_is_elevated", lambda: True)
    monkeypatch.setattr(windows, "_registry_value", lambda: True)
    result = windows.setup_developer_mode()
    assert result.success
    assert "ordinary user" in result.message
    probe.assert_not_called()


def test_setup_already_enabled_does_not_elevate(monkeypatch):
    monkeypatch.setattr(windows.os, "name", "nt")
    monkeypatch.setattr(windows, "_is_elevated", lambda: False)
    monkeypatch.setattr(windows, "_registry_value", lambda: True)
    monkeypatch.setattr(
        windows, "_probe_symlinks", Mock(side_effect=_privilege_error())
    )
    elevate = Mock()
    monkeypatch.setattr(windows, "_run_elevated_reg", elevate)
    result = windows.setup_developer_mode()
    assert not result.success
    assert "already enabled" in result.message
    elevate.assert_not_called()


def test_elevated_reg_uses_fixed_64_bit_command_and_system_cwd(monkeypatch):
    monkeypatch.setattr(
        windows, "_system_reg_exe", lambda: r"C:\Windows\System32\reg.exe"
    )
    execute = Mock()
    monkeypatch.setattr(windows, "_shell_execute_elevated", execute)
    monkeypatch.setattr(windows.os.path, "dirname", lambda _: r"C:\Windows\System32")

    windows._run_elevated_reg()

    execute.assert_called_once_with(
        r"C:\Windows\System32\reg.exe",
        windows._REG_ARGUMENTS,
        r"C:\Windows\System32",
    )
    assert windows._REG_ARGUMENTS == (
        r'ADD "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\AppModelUnlock" '
        r"/v AllowDevelopmentWithoutDevLicense /t REG_DWORD /d 1 /f /reg:64"
    )
    assert "AllowAllTrustedApps" not in windows._REG_ARGUMENTS


def test_registry_uses_64_bit_view_and_only_developer_mode_value(monkeypatch):
    class Key:
        def __enter__(self):
            return self

        def __exit__(self, *unused):
            pass

    key = Key()
    opened = Mock(return_value=key)
    created = Mock(return_value=key)
    set_value = Mock()
    registry = SimpleNamespace(
        KEY_READ=1,
        KEY_SET_VALUE=2,
        KEY_WOW64_64KEY=4,
        HKEY_LOCAL_MACHINE="HKLM",
        REG_DWORD=7,
        OpenKey=opened,
        CreateKeyEx=created,
        QueryValueEx=Mock(return_value=(1, 7)),
        SetValueEx=set_value,
    )
    monkeypatch.setitem(sys.modules, "winreg", registry)

    assert windows._registry_value()
    windows._set_registry_value()

    assert opened.call_args.args[3] == registry.KEY_READ | registry.KEY_WOW64_64KEY
    assert (
        created.call_args.args[3] == registry.KEY_SET_VALUE | registry.KEY_WOW64_64KEY
    )
    set_value.assert_called_once_with(
        key, windows._VALUE_NAME, 0, registry.REG_DWORD, 1
    )


def test_setup_reports_elevation_or_readback_failure(monkeypatch):
    monkeypatch.setattr(windows.os, "name", "nt")
    monkeypatch.setattr(
        windows, "_probe_symlinks", Mock(side_effect=_privilege_error())
    )
    monkeypatch.setattr(windows, "_is_elevated", lambda: False)
    monkeypatch.setattr(windows, "_registry_value", lambda: False)
    monkeypatch.setattr(
        windows, "_run_elevated_reg", Mock(side_effect=OSError("cancelled"))
    )
    assert not windows.setup_developer_mode().success

    monkeypatch.setattr(windows, "_run_elevated_reg", Mock())
    monkeypatch.setattr(windows, "_registry_value", lambda: False)
    assert not windows.setup_developer_mode().success
