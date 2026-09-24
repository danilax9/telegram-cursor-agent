"""Process reload when package sources change after start."""

import time

from telegram_cursor_agent.services.runtime_revision import (
    capture_loaded_revision,
    revision_drifted,
    should_reload_process,
    source_revision,
)


def test_revision_changes_when_package_source_changes(tmp_path) -> None:
    package = tmp_path / "pkg"
    package.mkdir()
    module = package / "a.py"
    module.write_text("x = 1\n", encoding="utf-8")
    first = source_revision(package)
    module.write_text("x = 2\n", encoding="utf-8")
    assert source_revision(package) != first


def test_drift_tracks_captured_snapshot(tmp_path) -> None:
    package = tmp_path / "pkg"
    package.mkdir()
    module = package / "a.py"
    module.write_text("x = 1\n", encoding="utf-8")
    capture_loaded_revision(package)
    try:
        assert revision_drifted(package) is False
        module.write_text("x = 2\n", encoding="utf-8")
        assert revision_drifted(package) is True
    finally:
        capture_loaded_revision()


def test_should_reload_on_drift_redis_or_newer_flag() -> None:
    started = time.time()
    assert should_reload_process(
        drifted=False, flag_mtime=None, started_at=started, redis_pending=False
    ) is False
    assert should_reload_process(
        drifted=True, flag_mtime=None, started_at=started, redis_pending=False
    ) is True
    assert should_reload_process(
        drifted=False, flag_mtime=None, started_at=started, redis_pending=True
    ) is True
    assert should_reload_process(
        drifted=False,
        flag_mtime=started - 10,
        started_at=started,
        redis_pending=False,
    ) is False
    assert should_reload_process(
        drifted=False,
        flag_mtime=started + 5,
        started_at=started,
        redis_pending=False,
    ) is True
