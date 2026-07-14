"""Focused durability and recovery tests."""

import errno
import os
import shutil
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml

from dotfilesmanager import config, transaction, windows


def _privilege_error():
    error = OSError("privilege missing")
    error.winerror = 1314
    return error


def test_recover_restores_paths_and_config_after_interrupted_mutation(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    target = root / "managed-target"
    target.write_text("before")
    config.save_config(str(root), {"dotfiles": {"before": {}}})
    tx = transaction.Transaction(str(root), [str(target)])
    tx.begin()
    target.unlink()
    target.write_text("after")
    config.save_config(str(root), {"dotfiles": {"after": {}}})

    assert transaction.recover(str(root)) is True
    assert target.read_text() == "before"
    assert config.load_config(str(root)) == {"dotfiles": {"before": {}}}
    assert not (root / transaction.JOURNAL).exists()


def test_v1_journal_recovers_with_best_effort_backup_description(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    target = root / "target"
    target.write_text("before")
    tx = transaction.Transaction(str(root), [str(target)])
    tx.begin()
    record = yaml.safe_load((root / transaction.JOURNAL).read_text())
    record["version"] = 1
    for entry in record["paths"]:
        entry.pop("snapshot")
    (root / transaction.JOURNAL).write_text(yaml.safe_dump(record))
    (root / transaction.BACKUPS / tx.identifier / "manifest.yaml").write_text(
        yaml.safe_dump(record)
    )
    target.write_text("after")

    assert transaction.recover(str(root))
    assert target.read_text() == "before"


def test_invalid_v1_backup_is_rejected_before_destination_mutation(
    tmp_path, monkeypatch
):
    root = tmp_path / "repo"
    root.mkdir()
    target = root / "target"
    target.write_text("before")
    tx = transaction.Transaction(str(root), [str(target)])
    tx.begin()
    record = yaml.safe_load((root / transaction.JOURNAL).read_text())
    record["version"] = 1
    for entry in record["paths"]:
        entry.pop("snapshot")
    (root / transaction.JOURNAL).write_text(yaml.safe_dump(record))
    (root / transaction.BACKUPS / tx.identifier / "manifest.yaml").write_text(
        yaml.safe_dump(record)
    )
    target.write_text("changed")
    backup = str(root / transaction.BACKUPS / tx.identifier / "0")
    original = transaction._describe_snapshot

    def reject_bad_backup(path, *args, **kwargs):
        if path == backup:
            raise transaction.JournalError("unsupported legacy backup reparse point")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(transaction, "_describe_snapshot", reject_bad_backup)
    with pytest.raises(transaction.JournalError):
        transaction.recover(str(root))
    assert target.read_text() == "changed"


@pytest.mark.parametrize(
    "key", ["../link", "C:link", "C:/link", "\\\\host\\link", "a\\b", "a:b"]
)
def test_v2_invalid_nested_metadata_fails_before_destination_mutation(tmp_path, key):
    root = tmp_path / "repo"
    root.mkdir()
    target = root / "target"
    target.write_text("before")
    tx = transaction.Transaction(str(root), [str(target)])
    tx.begin()
    record = yaml.safe_load((root / transaction.JOURNAL).read_text())
    record["paths"][0]["snapshot"] = {
        "kind": "directory",
        "nested_symlinks": {key: True},
    }
    (root / transaction.JOURNAL).write_text(yaml.safe_dump(record))
    (root / transaction.BACKUPS / tx.identifier / "manifest.yaml").write_text(
        yaml.safe_dump(record)
    )
    target.write_text("changed")

    with pytest.raises(transaction.JournalError):
        transaction.recover(str(root))
    assert target.read_text() == "changed"


def test_transaction_journal_survives_failure_and_can_be_recovered(
    tmp_path, monkeypatch
):
    root = tmp_path / "repo"
    root.mkdir()
    target = root / "target"
    target.write_text("before")
    tx = transaction.Transaction(str(root), [str(target)])
    tx.begin()
    target.write_text("changed")
    monkeypatch.setattr(
        transaction,
        "_remove",
        lambda path: (_ for _ in ()).throw(OSError("boom")),
    )

    with pytest.raises(OSError):
        tx.rollback()
    assert (root / transaction.JOURNAL).exists()


def test_recovery_is_retryable_after_a_mid_restore_fault(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    target = root / "target"
    target.write_text("before")
    tx = transaction.Transaction(str(root), [str(target)])
    tx.begin()
    target.write_text("after")
    original = transaction._copy_snapshot
    calls = {"count": 0}

    def fail_once(source, destination, descriptor=None):
        calls["count"] += 1
        if calls["count"] == 1:
            raise OSError("injected restore failure")
        return original(source, destination, descriptor)

    monkeypatch.setattr(transaction, "_copy_snapshot", fail_once)
    with pytest.raises(OSError):
        transaction.recover(str(root))
    assert (root / transaction.JOURNAL).exists()
    assert (root / transaction.BACKUPS / tx.identifier / "0").exists()
    monkeypatch.setattr(transaction, "_copy_snapshot", original)
    assert transaction.recover(str(root)) is True
    assert target.read_text() == "before"


def test_snapshot_symlink_privilege_failure_is_classified(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.write_text("value")
    link = tmp_path / "link"
    link.symlink_to(source)
    monkeypatch.setattr(transaction.os, "symlink", Mock(side_effect=_privilege_error()))

    with pytest.raises(windows.SymlinkPrivilegeError) as caught:
        transaction._copy_snapshot(str(link), str(tmp_path / "backup"))

    assert isinstance(caught.value.__cause__, OSError)


@pytest.mark.skipif(os.name == "nt", reason="exercises the POSIX copytree branch")
def test_nested_snapshot_symlink_privilege_failure_is_classified(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    monkeypatch.setattr(
        transaction.shutil, "copytree", Mock(side_effect=_privilege_error())
    )

    with pytest.raises(windows.SymlinkPrivilegeError) as caught:
        transaction._copy_snapshot(str(source), str(tmp_path / "backup"))

    assert isinstance(caught.value.__cause__, OSError)


@pytest.mark.skipif(os.name == "nt", reason="exercises the POSIX copytree branch")
def test_nested_copytree_text_1314_remains_native(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    aggregate = shutil.Error(
        [("source/link", "backup/link", "[WinError 1314] privilege missing")]
    )
    monkeypatch.setattr(transaction.shutil, "copytree", Mock(side_effect=aggregate))

    with pytest.raises(shutil.Error) as caught:
        transaction._copy_snapshot(str(source), str(tmp_path / "backup"))

    assert caught.value is aggregate


def test_snapshot_preserves_directory_link_type(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    source = tmp_path / "directory-link"
    source.symlink_to(target, target_is_directory=True)
    destination = tmp_path / "backup-link"

    transaction._copy_snapshot(str(source), str(destination))

    assert destination.is_symlink()
    assert destination.is_dir()


def test_windows_relative_dangling_directory_link_metadata_round_trips(
    tmp_path, monkeypatch
):
    root = tmp_path / "repo"
    root.mkdir()
    link = root / "directory-link"
    link.symlink_to("missing-directory", target_is_directory=True)
    monkeypatch.setattr(transaction, "_is_windows", lambda: True)
    monkeypatch.setattr(transaction, "_link_is_directory", lambda _: True)
    calls = []

    def create(target, path, *, target_is_directory):
        calls.append((target, target_is_directory))
        os.symlink(target, path, target_is_directory=target_is_directory)

    monkeypatch.setattr(transaction.windows, "create_symlink", create)
    tx = transaction.Transaction(str(root), [str(link)])
    tx.begin()
    journal = yaml.safe_load((root / transaction.JOURNAL).read_text())
    assert journal["version"] == 2
    assert journal["paths"][0]["snapshot"]["link_is_directory"] is True
    link.unlink()

    transaction.recover(str(root))

    assert link.is_symlink()
    assert os.readlink(link) == "missing-directory"
    assert all(is_directory for _, is_directory in calls)


def test_windows_nested_dangling_directory_link_metadata_round_trips(
    tmp_path, monkeypatch
):
    root = tmp_path / "repo"
    managed = root / "managed"
    nested = managed / "nested-directory-link"
    managed.mkdir(parents=True)
    nested.symlink_to("missing-directory", target_is_directory=True)
    monkeypatch.setattr(transaction, "_is_windows", lambda: True)
    monkeypatch.setattr(transaction, "_link_is_directory", lambda _: True)
    calls = []

    def create(target, path, *, target_is_directory):
        calls.append((target, target_is_directory))
        os.symlink(target, path, target_is_directory=target_is_directory)

    monkeypatch.setattr(transaction.windows, "create_symlink", create)
    tx = transaction.Transaction(str(root), [str(managed)])
    tx.begin()
    journal = yaml.safe_load((root / transaction.JOURNAL).read_text())
    assert journal["paths"][0]["snapshot"]["nested_symlinks"] == {
        "nested-directory-link": True
    }
    shutil.rmtree(managed)

    transaction.recover(str(root))

    assert nested.is_symlink()
    assert os.readlink(nested) == "missing-directory"
    assert all(is_directory for _, is_directory in calls)


def test_windows_nested_symlink_1314_is_typed_and_file_error_is_native(
    tmp_path, monkeypatch
):
    source = tmp_path / "source"
    source.mkdir()
    target = source / "target"
    target.write_text("value")
    (source / "link").symlink_to("target")
    monkeypatch.setattr(transaction, "_is_windows", lambda: True)
    monkeypatch.setattr(transaction, "_link_is_directory", lambda _: False)
    monkeypatch.setattr(transaction.os, "symlink", Mock(side_effect=_privilege_error()))

    with pytest.raises(windows.SymlinkPrivilegeError):
        transaction._copy_snapshot(str(source), str(tmp_path / "backup"))

    file_source = tmp_path / "file"
    file_source.write_text("value")
    native = OSError("copy failure")
    monkeypatch.setattr(transaction.shutil, "copy2", Mock(side_effect=native))
    with pytest.raises(OSError) as caught:
        transaction._copy_snapshot(str(file_source), str(tmp_path / "file-backup"))
    assert caught.value is native


def test_windows_symlink_directory_bit_mismatch_fails_before_link_creation(
    tmp_path, monkeypatch
):
    source = tmp_path / "source"
    source.write_text("value")
    link = tmp_path / "link"
    link.symlink_to(source)
    monkeypatch.setattr(transaction, "_is_windows", lambda: True)
    monkeypatch.setattr(transaction, "_link_is_directory", lambda _: False)
    create = Mock()
    monkeypatch.setattr(transaction.windows, "create_symlink", create)

    with pytest.raises(transaction.JournalError, match="type changed"):
        transaction._copy_snapshot(
            str(link),
            str(tmp_path / "backup"),
            {"kind": "symlink", "link_is_directory": True, "nested_symlinks": {}},
        )
    create.assert_not_called()


@pytest.mark.parametrize("kind", ["file", "directory"])
def test_windows_nested_expected_link_cannot_change_to_other_node(
    tmp_path, monkeypatch, kind
):
    source = tmp_path / "source"
    source.write_text("value")
    destination = tmp_path / "destination"
    monkeypatch.setattr(transaction, "_windows_kind", lambda _: kind)
    monkeypatch.setattr(transaction.windows, "create_symlink", Mock())

    with pytest.raises(transaction.JournalError, match="link changed"):
        transaction._copy_windows_child(
            str(source), str(destination), "link", {"link": False}
        )
    assert not destination.exists()


def test_windows_nested_descendant_link_cannot_be_replaced_by_file(
    tmp_path, monkeypatch
):
    source = tmp_path / "source"
    source.write_text("value")
    monkeypatch.setattr(transaction, "_windows_kind", lambda _: "file")

    with pytest.raises(transaction.JournalError, match="link changed"):
        transaction._copy_windows_child(
            str(source), str(tmp_path / "destination"), "parent", {"parent/link": False}
        )


def test_windows_recovery_rejects_nested_link_disappearing_after_prevalidation(
    tmp_path, monkeypatch
):
    root = tmp_path / "repo"
    managed = root / "managed"
    managed.mkdir(parents=True)
    (managed / "target").write_text("value")
    (managed / "link").symlink_to("target")
    tx = transaction.Transaction(str(root), [str(managed)])
    tx.begin()
    shutil.rmtree(managed)
    managed.mkdir()
    (managed / "changed").write_text("changed")
    monkeypatch.setattr(transaction, "_is_windows", lambda: True)
    original = transaction._validate_backup_descriptor

    def validate_then_remove(backup, descriptor):
        original(backup, descriptor)
        os.unlink(os.path.join(backup, "link"))

    monkeypatch.setattr(
        transaction, "_validate_backup_descriptor", validate_then_remove
    )
    with pytest.raises(transaction.JournalError, match="link disappeared"):
        transaction.recover(str(root))

    assert (root / transaction.JOURNAL).exists()
    assert (root / transaction.BACKUPS / tx.identifier).exists()


def test_windows_source_junction_materializes_but_backup_junction_is_rejected(
    tmp_path, monkeypatch
):
    root = tmp_path / "repo"
    source = root / "managed"
    source.mkdir(parents=True)
    (source / "value").write_text("before")
    original_lstat = transaction.os.lstat
    monkeypatch.setattr(transaction, "_is_windows", lambda: True)

    def source_junction(path):
        status = original_lstat(path)
        if str(path) == str(source):
            return SimpleNamespace(
                st_mode=status.st_mode,
                st_file_attributes=transaction._REPARSE_POINT,
                st_reparse_tag=transaction._REPARSE_TAG_MOUNT_POINT,
            )
        return status

    monkeypatch.setattr(transaction.os, "lstat", source_junction)
    backup = tmp_path / "materialized"
    transaction._copy_snapshot(str(source), str(backup))
    assert (backup / "value").read_text() == "before"
    assert not backup.is_symlink()

    monkeypatch.setattr(transaction.os, "lstat", original_lstat)
    tx = transaction.Transaction(str(root), [str(source)])
    tx.begin()
    injected = root / transaction.BACKUPS / tx.identifier / "0" / "injected"
    injected.mkdir()
    journal = yaml.safe_load((root / transaction.JOURNAL).read_text())
    (root / transaction.BACKUPS / tx.identifier / "manifest.yaml").write_text(
        yaml.safe_dump(journal)
    )
    source.mkdir(exist_ok=True)
    (source / "changed").write_text("changed")

    def backup_junction(path):
        status = original_lstat(path)
        if str(path) == str(injected):
            return SimpleNamespace(
                st_mode=status.st_mode,
                st_file_attributes=transaction._REPARSE_POINT,
                st_reparse_tag=transaction._REPARSE_TAG_MOUNT_POINT,
            )
        return status

    monkeypatch.setattr(transaction.os, "lstat", backup_junction)
    with pytest.raises(transaction.JournalError, match="junction"):
        transaction.recover(str(root))
    assert (source / "changed").read_text() == "changed"


def test_recovery_rejects_valid_escape_journal_without_touching_path(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    victim = tmp_path / "victim"
    victim.write_text("do not remove")
    target = root / "target"
    target.write_text("before")
    tx = transaction.Transaction(str(root), [str(target)])
    tx.begin()
    journal_path = root / transaction.JOURNAL
    journal = yaml.safe_load(journal_path.read_text())
    journal["paths"][0]["path"] = str(victim)
    journal_path.write_text(yaml.safe_dump(journal))
    (root / transaction.BACKUPS / tx.identifier / "manifest.yaml").write_text(
        yaml.safe_dump(journal)
    )

    with pytest.raises(transaction.JournalError):
        transaction.recover(str(root))
    assert victim.read_text() == "do not remove"
    assert journal_path.exists()


def test_atomic_save_leaves_complete_yaml(tmp_path, monkeypatch):
    config.save_config(str(tmp_path), {"dotfiles": {"old": {}}})
    monkeypatch.setattr(
        config.os, "replace", lambda *args: (_ for _ in ()).throw(OSError("boom"))
    )

    with pytest.raises(OSError):
        config.save_config(str(tmp_path), {"dotfiles": {"new": {}}})
    assert config.load_config(str(tmp_path)) == {"dotfiles": {"old": {}}}
    assert not any(name.startswith(".dfm.yaml.") for name in os.listdir(tmp_path))


def test_recovery_keeps_journal_when_atomic_config_restore_fails(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    target = root / "target"
    target.write_text("before")
    config.save_config(str(root), {"dotfiles": {"before": {}}})
    tx = transaction.Transaction(str(root), [str(target)])
    tx.begin()
    target.write_text("after")
    config.save_config(str(root), {"dotfiles": {"after": {}}})
    monkeypatch.setattr(
        config,
        "restore_config_bytes",
        lambda *args: (_ for _ in ()).throw(OSError("config restore failure")),
    )

    with pytest.raises(OSError):
        transaction.recover(str(root))
    assert (root / transaction.JOURNAL).exists()
    assert (root / transaction.BACKUPS / tx.identifier / "config").exists()


def test_valid_matching_journal_cannot_target_transaction_state(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    target = root / "target"
    target.write_text("before")
    tx = transaction.Transaction(str(root), [str(target)])
    tx.begin()
    journal_path = root / transaction.JOURNAL
    record = yaml.safe_load(journal_path.read_text())
    record["paths"][0]["path"] = str(root / transaction.LOCK)
    journal_path.write_text(yaml.safe_dump(record))
    (root / transaction.BACKUPS / tx.identifier / "manifest.yaml").write_text(
        yaml.safe_dump(record)
    )

    with pytest.raises(transaction.JournalError, match="protected"):
        transaction.recover(str(root))
    assert target.read_text() == "before"
    assert journal_path.exists()


def test_commit_syncs_destination_parents_before_journal_removal(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    target = root / "target"
    target.write_text("value")
    tx = transaction.Transaction(str(root), [str(target)])
    tx.begin()
    calls = []
    monkeypatch.setattr(
        transaction,
        "_sync_destination_parents",
        lambda paths: calls.append(list(paths)),
    )

    tx.commit()
    assert calls == [[str(target)]]
    assert not (root / transaction.JOURNAL).exists()


def test_begin_rejects_protected_and_symlink_parent_paths_before_journal(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    with pytest.raises(transaction.JournalError, match="protected"):
        transaction.Transaction(str(root), [str(root / "dfm.yaml")]).begin()
    parent = root / "parent"
    parent.symlink_to(tmp_path)
    with pytest.raises(transaction.JournalError, match="parent"):
        transaction.Transaction(str(root), [str(parent / "target")]).begin()
    assert not (root / transaction.JOURNAL).exists()


def test_commit_syncs_file_content_before_removing_journal(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    target = root / "target"
    target.write_text("value")
    tx = transaction.Transaction(str(root), [str(target)])
    tx.begin()
    synced = []
    monkeypatch.setattr(transaction, "_sync_file", lambda path: synced.append(path))

    tx.commit()
    assert str(target) in synced


def test_commit_fsync_failure_keeps_journal_and_snapshot(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    target = root / "target"
    target.write_text("value")
    tx = transaction.Transaction(str(root), [str(target)])
    tx.begin()
    monkeypatch.setattr(
        transaction,
        "_sync_file",
        lambda path: (_ for _ in ()).throw(OSError("fsync failure")),
    )

    with pytest.raises(OSError, match="fsync failure"):
        tx.commit()
    assert (root / transaction.JOURNAL).exists()
    assert (root / transaction.BACKUPS / tx.identifier / "0").exists()


def test_begin_rejects_symlinked_backup_state_before_snapshots(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / transaction.BACKUPS).symlink_to(outside, target_is_directory=True)
    target = root / "target"
    target.write_text("value")

    with pytest.raises(transaction.JournalError, match="backup root"):
        transaction.Transaction(str(root), [str(target)]).begin()
    assert not any(outside.iterdir())


def test_recovery_rejects_symlinked_backup_root_before_reading_manifest(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    target = root / "target"
    target.write_text("before")
    tx = transaction.Transaction(str(root), [str(target)])
    tx.begin()
    outside = tmp_path / "outside"
    (root / transaction.BACKUPS).rename(outside)
    (root / transaction.BACKUPS).symlink_to(outside, target_is_directory=True)

    with pytest.raises(transaction.JournalError, match="backup root"):
        transaction.recover(str(root))
    assert target.read_text() == "before"
    assert (root / transaction.JOURNAL).exists()


@pytest.mark.parametrize("module", [config, transaction])
def test_directory_sync_tolerates_windows_unsupported_open(
    module, monkeypatch, tmp_path
):
    error = OSError(errno.EINVAL, "unsupported")
    monkeypatch.setattr(module.os, "open", lambda *args: (_ for _ in ()).throw(error))

    module._sync_directory(str(tmp_path))


@pytest.mark.parametrize("module", [config, transaction])
def test_directory_sync_propagates_supported_platform_open_failure(
    module, monkeypatch, tmp_path
):
    error = OSError(errno.EIO, "disk failure")
    monkeypatch.setattr(module.os, "open", lambda *args: (_ for _ in ()).throw(error))

    with pytest.raises(OSError, match="disk failure"):
        module._sync_directory(str(tmp_path))


@pytest.mark.parametrize("module", [config, transaction])
def test_directory_sync_tolerates_windows_access_denied_open(
    module, monkeypatch, tmp_path
):
    error = OSError(errno.EACCES, "directory access denied")
    monkeypatch.setattr(module.os, "name", "nt")
    monkeypatch.setattr(module.os, "open", lambda *args: (_ for _ in ()).throw(error))

    module._sync_directory(str(tmp_path))


def test_sync_tree_skips_live_and_dangling_symlink_targets(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    directory = root / "tree"
    directory.mkdir()
    regular = directory / "regular"
    regular.write_text("value")
    outside = tmp_path / "outside"
    outside.write_text("outside")
    (directory / "live-link").symlink_to(outside)
    (directory / "dangling-link").symlink_to(tmp_path / "missing")
    tx = transaction.Transaction(str(root), [str(directory)])
    tx.begin()
    synced = []
    original = transaction._sync_file

    def record_sync(path):
        synced.append(path)
        original(path)

    monkeypatch.setattr(transaction, "_sync_file", record_sync)

    tx.commit()
    assert str(regular) in synced
    assert str(directory / "live-link") not in synced
    assert str(directory / "dangling-link") not in synced
    assert str(outside) not in synced
