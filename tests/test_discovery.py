"""Project discovery tests."""

from pathlib import Path

from telegram_cursor_agent.projects.discovery import (
    discover_projects,
    find_project_by_name,
)


def test_discover_pyproject(tmp_workspace: Path, test_settings) -> None:
    project = tmp_workspace / "my-app"
    project.mkdir()
    (project / "pyproject.toml").write_text("[project]\nname='my-app'\n")

    discovered = discover_projects(test_settings)
    assert len(discovered) == 1
    assert discovered[0].name == "my-app"
    assert discovered[0].signature_file == "pyproject.toml"


def test_discover_nested_project(tmp_workspace: Path, test_settings) -> None:
    nested = tmp_workspace / "org" / "service"
    nested.mkdir(parents=True)
    (nested / "package.json").write_text("{}")

    discovered = discover_projects(test_settings)
    assert any(p.name == "service" for p in discovered)


def test_skips_hidden_dirs(tmp_workspace: Path, test_settings) -> None:
    hidden = tmp_workspace / ".hidden" / "proj"
    hidden.mkdir(parents=True)
    (hidden / "pyproject.toml").write_text("")

    discovered = discover_projects(test_settings)
    assert len(discovered) == 0


def test_find_by_name(tmp_workspace: Path, test_settings) -> None:
    project = tmp_workspace / "findme"
    project.mkdir()
    (project / "go.mod").write_text("module findme")

    found = find_project_by_name("findme", test_settings)
    assert found is not None
    assert found.name == "findme"


def test_find_by_name_missing(test_settings) -> None:
    assert find_project_by_name("nonexistent", test_settings) is None
