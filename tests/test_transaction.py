"""Focused durability and recovery tests."""

import errno
import os

import pytest
import yaml

from dotfilesmanager import config, transaction


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

    def fail_once(source, destination):
        calls["count"] += 1
        if calls["count"] == 1:
            raise OSError("injected restore failure")
        return original(source, destination)

    monkeypatch.setattr(transaction, "_copy_snapshot", fail_once)
    with pytest.raises(OSError):
        transaction.recover(str(root))
    assert (root / transaction.JOURNAL).exists()
    assert (root / transaction.BACKUPS / tx.identifier / "0").exists()
    monkeypatch.setattr(transaction, "_copy_snapshot", original)
    assert transaction.recover(str(root)) is True
    assert target.read_text() == "before"


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
