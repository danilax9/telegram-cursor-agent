"""Install script smoke tests."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_install_scripts_exist_and_are_executable() -> None:
    for relative in ("install.sh", "scripts/install.sh"):
        path = REPO_ROOT / relative
        assert path.is_file(), relative
        assert path.stat().st_mode & 0o111, f"{relative} must be executable"


def test_install_script_contains_required_prompts() -> None:
    content = (REPO_ROOT / "scripts/install.sh").read_text(encoding="utf-8")
    assert "Токен Telegram-бота" in content
    assert "Telegram user ID" in content
    assert "loginDeepControl" in content
    assert "write_env_file" in content
    assert "install_worker_service" in content
    assert "read_prompt" in content
    assert "/dev/tty" in content
    assert "pull --ff-only >&2" in content
    assert 'install_root="$(clone_or_update_repo' not in content
    assert "ensure_docker_running" in content
    assert "ensure_disk_space" in content
    assert "UV_BIN=" in content
    assert "wait_until_ready" in content
    assert "Нужен root" in content


def test_bootstrap_script_documents_one_liner() -> None:
    content = (REPO_ROOT / "install.sh").read_text(encoding="utf-8")
    assert "raw.githubusercontent.com" in content
    assert "danilax9/telegram-cursor-agent" in content


def test_uninstall_scripts_exist() -> None:
    for relative in ("uninstall.sh", "scripts/uninstall.sh"):
        path = REPO_ROOT / relative
        assert path.is_file(), relative
        assert path.stat().st_mode & 0o111, f"{relative} must be executable"
    content = (REPO_ROOT / "scripts/uninstall.sh").read_text(encoding="utf-8")
    assert "docker compose down" in content
    assert "systemctl" in content
