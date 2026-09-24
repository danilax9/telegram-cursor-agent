"""Deploy guard: live health plus rollback on a throwaway copy."""

import importlib.util
import subprocess
from pathlib import Path

import pytest


def _load_guard():
    path = Path(__file__).resolve().parents[1] / "scripts" / "tca_guard.py"
    spec = importlib.util.spec_from_file_location("tca_guard_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _point_at(guard, repo: Path) -> None:
    guard.REPO = repo
    guard.GOOD = repo / ".deploy-good"
    guard.STATE = repo / ".deploy-guard.json"
    guard.LOG = repo / "guard.log"


def test_live_services_are_healthy() -> None:
    guard = _load_guard()
    ok, detail = guard.healthy()
    assert ok, detail
    assert (guard.GOOD / "src").is_dir()


def test_preflight_restores_snapshot_when_import_breaks(tmp_path: Path, monkeypatch) -> None:
    guard = _load_guard()
    repo = tmp_path / "repo"
    broken = repo / "src" / "telegram_cursor_agent"
    broken.mkdir(parents=True)
    (broken / "__init__.py").write_text("raise RuntimeError('deploy broke import')\n")
    saved = repo / ".deploy-good" / "src" / "telegram_cursor_agent"
    saved.mkdir(parents=True)
    (saved / "__init__.py").write_text("# good\n")
    _point_at(guard, repo)

    fixes: list[str] = []
    monkeypatch.setattr(guard, "restart_services", lambda: None)
    monkeypatch.setattr(guard, "queue_fix", fixes.append)
    monkeypatch.setattr(guard, "notify", lambda text: None)

    real_run = subprocess.run

    def skip_pip(command, **kwargs):
        argv = command if isinstance(command, list) else []
        if "pip" in argv:
            return subprocess.CompletedProcess(argv, 0, "", "")
        return real_run(command, **kwargs)

    monkeypatch.setattr(guard.subprocess, "run", skip_pip)

    assert guard.preflight() == 1
    assert (broken / "__init__.py").read_text(encoding="utf-8") == "# good\n"
    assert fixes and "deploy broke import" in fixes[0]


def test_missing_snapshot_does_not_restart(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    guard = _load_guard()
    repo = tmp_path / "empty"
    repo.mkdir()
    _point_at(guard, repo)
    restarted = {"yes": False}
    monkeypatch.setattr(guard, "restart_services", lambda: restarted.__setitem__("yes", True))
    monkeypatch.setattr(guard, "notify", lambda text: None)
    monkeypatch.setattr(guard, "queue_fix", lambda reason: None)
    assert guard.rollback("no snapshot") == 1
    assert restarted["yes"] is False
