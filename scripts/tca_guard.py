#!/usr/bin/env python3
"""Deploy safety net that does not import the bot package.

Runs from deploy-self.sh (preflight) and from a systemd timer (watch).
On a broken deploy it restores the last good source snapshot, restarts
services, and asks cursor-agent to fix the failure in the same chat.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(os.environ.get("SELF_REPO_ROOT", "/root/telegram-cursor-agent"))
GOOD = REPO / ".deploy-good"
STATE = REPO / ".deploy-guard.json"
LOG = Path(os.environ.get("DEPLOY_LOG_FILE", "/tmp/tca-deploy.log"))
SNAPSHOT_DIRS = ("src", "alembic", "scripts", "deploy")
SNAPSHOT_FILES = ("pyproject.toml", "alembic.ini", "docker-compose.yml")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6380"))
HEARTBEAT_MAX_AGE = 90
WATCH_SECONDS = int(os.environ.get("DEPLOY_GUARD_SECONDS", "150"))
ERROR_LIMIT = 3
FIX_MARKER = ".deploy-fix-queued"


def log(message: str) -> None:
    line = f"[{datetime.now(UTC).isoformat()}] [guard] {message}"
    print(line, flush=True)
    try:
        with LOG.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        pass


def redis_command(*args: str) -> str | None:
    attempts = [
        ["redis-cli", "-p", str(REDIS_PORT), *args],
        ["docker", "compose", "exec", "-T", "redis", "redis-cli", *args],
    ]
    for command in attempts:
        try:
            result = subprocess.run(
                command,
                cwd=str(REPO),
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0 and result.stdout.strip() not in ("", "(nil)"):
            return result.stdout.strip()
    return None


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    path = REPO / ".env"
    if not path.is_file():
        return env
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def read_state() -> dict:
    if not STATE.is_file():
        return {}
    try:
        data = json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def write_state(data: dict) -> None:
    STATE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def snapshot_good() -> None:
    if GOOD.exists():
        shutil.rmtree(GOOD)
    GOOD.mkdir(parents=True)
    for name in SNAPSHOT_DIRS:
        source = REPO / name
        if source.is_dir():
            shutil.copytree(
                source,
                GOOD / name,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
    for name in SNAPSHOT_FILES:
        source = REPO / name
        if source.is_file():
            shutil.copy2(source, GOOD / name)
    (GOOD / "revision.txt").write_text(datetime.now(UTC).isoformat(), encoding="utf-8")
    log(f"saved good snapshot at {GOOD}")


def restore_good() -> bool:
    if not (GOOD / "src").is_dir():
        log("no good snapshot to restore")
        return False
    for name in SNAPSHOT_DIRS:
        target = REPO / name
        saved = GOOD / name
        if target.exists():
            shutil.rmtree(target)
        if saved.is_dir():
            shutil.copytree(saved, target)
    for name in SNAPSHOT_FILES:
        saved = GOOD / name
        if saved.is_file():
            shutil.copy2(saved, REPO / name)
    log("restored last good snapshot")
    return True


def import_check() -> tuple[bool, str]:
    env = os.environ.copy()
    src = str(REPO / "src")
    env["PYTHONPATH"] = src + os.pathsep + env.get("PYTHONPATH", "")
    python = REPO / ".venv" / "bin" / "python"
    if not python.is_file():
        python = Path(sys.executable)
    code = (
        "import telegram_cursor_agent.queue.worker, "
        "telegram_cursor_agent.main, telegram_cursor_agent.telegram.bot"
    )
    result = subprocess.run(
        [str(python), "-c", code],
        cwd=str(REPO),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if result.returncode == 0:
        return True, ""
    detail = (result.stderr or result.stdout or "import failed").strip()
    return False, detail[-4000:]


def run_tests() -> tuple[bool, str]:
    python = REPO / ".venv" / "bin" / "python"
    if not python.is_file():
        return True, "venv missing, tests skipped"
    result = subprocess.run(
        [str(python), "-m", "pytest", "-q", "--tb=line", "-p", "no:cacheprovider"],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=240,
        check=False,
    )
    tail = (result.stdout + "\n" + result.stderr).strip()[-4000:]
    return result.returncode == 0, tail


def restart_services() -> None:
    subprocess.run(
        ["docker", "compose", "up", "-d", "--force-recreate", "--no-deps", "bot"],
        cwd=str(REPO),
        check=False,
        timeout=180,
    )
    subprocess.run(
        ["systemctl", "restart", "telegram-cursor-agent-worker"],
        check=False,
        timeout=60,
    )


def _heartbeat_payload(component: str) -> dict | None:
    raw = redis_command("GET", f"tca:health:{component}")
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def heartbeat_fresh(component: str) -> bool:
    payload = _heartbeat_payload(component)
    if payload is None:
        return False
    try:
        ts = float(payload.get("ts", 0))
    except (TypeError, ValueError):
        return False
    return time.time() - ts <= HEARTBEAT_MAX_AGE


def heartbeat_from_this_deploy(component: str) -> bool:
    """A pulse left by the previous process must not count as a successful restart."""
    if not heartbeat_fresh(component):
        return False
    deploy_started = float(read_state().get("started_at") or 0)
    if deploy_started <= 0:
        return True
    payload = _heartbeat_payload(component) or {}
    try:
        started = float(payload.get("started_at", 0))
    except (TypeError, ValueError):
        return False
    return started >= deploy_started - 1


def error_count(component: str) -> int:
    raw = redis_command("GET", f"tca:health:{component}:errors")
    try:
        return int(raw or 0)
    except ValueError:
        return 0


def worker_active() -> bool:
    result = subprocess.run(
        ["systemctl", "is-active", "telegram-cursor-agent-worker"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() == "active"


def bot_running() -> bool:
    result = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", "telegram-cursor-agent-bot-1"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() == "true"


def bot_logs_crashing() -> str:
    result = subprocess.run(
        ["docker", "logs", "--tail", "80", "telegram-cursor-agent-bot-1"],
        capture_output=True,
        text=True,
        check=False,
    )
    text = (result.stdout + result.stderr)[-4000:]
    markers = ("Traceback", "Error", "ModuleNotFoundError", "ImportError", "SyntaxError")
    if any(marker in text for marker in markers):
        return text
    return ""


def healthy() -> tuple[bool, str]:
    problems: list[str] = []
    if not worker_active():
        problems.append("worker systemd is not active")
    elif not heartbeat_from_this_deploy("worker"):
        problems.append("worker heartbeat is not from this deploy")
    if not bot_running():
        problems.append("bot container is not running")
    elif not heartbeat_from_this_deploy("bot"):
        problems.append("bot heartbeat is not from this deploy")
    crash = bot_logs_crashing()
    if crash and "bot heartbeat is stale" in problems:
        problems.append("bot logs:\n" + crash[-1500:])
    for component in ("worker", "bot"):
        count = error_count(component)
        if count >= ERROR_LIMIT:
            problems.append(f"{component} recorded {count} delivery/handler errors")
    if problems:
        return False, "\n".join(problems)
    return True, "heartbeats fresh"


def notify(text: str) -> None:
    env = load_env()
    token = env.get("BOT_TOKEN", "")
    chat = env.get("ADMIN_TELEGRAM_ID", "").split(",")[0].strip()
    if not token or not chat:
        log("notify skipped: no token or admin id")
        return
    body = json.dumps(
        {"chat_id": chat, "text": text[:4000], "disable_web_page_preview": True}
    ).encode()
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            response.read()
    except (OSError, urllib.error.URLError) as exc:
        log(f"notify failed: {exc}")


def queue_fix(reason: str) -> None:
    """Ask the restarted worker to diagnose this chat and deliver the report."""
    marker = REPO / ".deploy-pending.json"
    raw: dict = {}
    if marker.is_file():
        try:
            loaded = json.loads(marker.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                raw = loaded
        except (OSError, json.JSONDecodeError):
            raw = {}
    if raw.get("fix_queued"):
        log("fix already queued for this deploy")
        return
    request = {
        "reason": reason[-4000:],
        "telegram_id": raw.get("telegram_id"),
        "user_id": raw.get("user_id"),
        "session_id": raw.get("session_id"),
        "cursor_chat_id": raw.get("cursor_chat_id"),
        "workspace": raw.get("workspace") or str(REPO),
        "created_at": datetime.now(UTC).isoformat(),
    }
    (REPO / ".deploy-fix-request.json").write_text(
        json.dumps(request, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if raw:
        raw["fix_queued"] = True
        marker.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    if not request.get("cursor_chat_id"):
        notify(
            "⚠️ Обновление сломалось, откатил на прошлую версию. "
            "Сессию Cursor не нашёл — напиши, что чинить.\n\n"
            + reason[-500:]
        )
        return
    notify(
        "⚠️ Ответ не дошёл: откатил поломку. "
        "Смотрю последний диалог в этой сессии и пришлю отчёт."
    )
    log("queued cursor diagnosis for the worker")


def rollback(reason: str) -> int:
    state = read_state()
    if state.get("rolling_back"):
        log("rollback already in progress")
        return 1
    write_state({**state, "rolling_back": True, "reason": reason[-2000:]})
    restored = restore_good()
    if not restored:
        notify("⚠️ Обновление сломалось, а снимка для отката нет. Смотри /tmp/tca-deploy.log")
        write_state({"rolling_back": False, "last_failure": reason[-2000:]})
        return 1
    uv = os.environ.get("UV_BIN", "/root/.hermes/bin/uv")
    subprocess.run([uv, "pip", "install", "-e", "."], cwd=str(REPO), check=False, timeout=180)
    queue_fix(reason)
    restart_services()
    write_state({"rolling_back": False, "rolled_back_at": datetime.now(UTC).isoformat()})
    return 1


def preflight() -> int:
    ok, detail = import_check()
    if not ok:
        log("preflight import failed")
        return rollback("import check failed:\n" + detail)
    ok, detail = run_tests()
    if not ok:
        log("preflight tests failed")
        return rollback("tests failed:\n" + detail)
    redis_command("DEL", "tca:health:worker:errors", "tca:health:bot:errors")
    write_state(
        {
            "deploy_id": datetime.now(UTC).strftime("%Y%m%d%H%M%S"),
            "started_at": time.time(),
            "rolling_back": False,
        }
    )
    log("preflight passed")
    return 0


def _mark_healthy() -> int:
    log("deploy healthy")
    snapshot_good()
    write_state({**read_state(), "healthy_at": datetime.now(UTC).isoformat(), "rolling_back": False})
    return 0


def watch() -> int:
    state = read_state()
    started = float(state.get("started_at") or time.time())
    if started > time.time():
        started = time.time()
    deadline = max(time.time() + 30, started + WATCH_SECONDS)
    log(f"watching until {datetime.fromtimestamp(deadline, UTC).isoformat()}")
    while time.time() < deadline:
        ok, detail = healthy()
        if ok:
            return _mark_healthy()
        time.sleep(10)
    ok, detail = healthy()
    if ok:
        return _mark_healthy()
    log(f"unhealthy after watch: {detail}")
    return rollback(detail)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("preflight", "watch", "snapshot"))
    args = parser.parse_args()
    if args.action == "snapshot":
        snapshot_good()
        return 0
    if args.action == "preflight":
        return preflight()
    return watch()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        log(f"guard crashed: {exc}")
        notify(f"⚠️ Сторож обновления сам упал: {exc}")
        raise
