"""OpenCode CLI adapter. Headless `opencode run --format json`."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from telegram_cursor_agent.agent.adapter import AgentResult, AgentStreamEvent
from telegram_cursor_agent.agent.opencode_context import sync_opencode_context
from telegram_cursor_agent.agent.skills import cursor_skill_name_from_path
from telegram_cursor_agent.agent.tool_call_format import SKILL_TOOL_SUMMARY_PREFIX
from telegram_cursor_agent.core.config import Settings
from telegram_cursor_agent.core.logging import get_logger
from telegram_cursor_agent.execution.runner import ProcessRunner
from telegram_cursor_agent.services.cursor_models import resolve_model_file
from telegram_cursor_agent.services.opencode_models import is_opencode_model
from telegram_cursor_agent.telegram.live_tool_calls import ToolCallLiveComposer

logger = get_logger(__name__)


class OpenCodeAgentAdapter:
    """Invokes opencode with the verified free-model flags."""

    def __init__(self, settings: Settings, runner: ProcessRunner) -> None:
        self._settings = settings
        self._runner = runner

    def build_command(
        self,
        workspace: str,
        prompt: str,
        resume_chat_id: str | None = None,
        *,
        telegram_id: int | None = None,
    ) -> list[str]:
        sync_opencode_context(self._settings, workspace, telegram_id)
        cmd = [
            self._settings.opencode_cli_path,
            "run",
            "--dir",
            workspace,
            "--auto",
            "--format",
            "json",
        ]
        model_file = resolve_model_file(self._settings, workspace)
        if model_file is not None:
            model = model_file.read_text(encoding="utf-8").strip()
            if is_opencode_model(model):
                cmd.extend(["--model", model])
        if resume_chat_id and resume_chat_id.startswith("ses_"):
            cmd.extend(["--session", resume_chat_id])
        cmd.append(prompt)
        return cmd

    async def run_prompt(
        self,
        workspace: str,
        prompt: str,
        resume_chat_id: str | None = None,
        on_progress: Callable[[str], Awaitable[None]] | None = None,
        on_process_start: Callable[[int], Awaitable[None]] | None = None,
        process_env: dict[str, str] | None = None,
        *,
        show_tool_calls_live: bool = False,
        telegram_id: int | None = None,
    ) -> AgentResult:
        progress = OpenCodeProgress(
            show_tool_calls_live=show_tool_calls_live,
            use_rich_tool_details=self._settings.telegram_uses_rich_messages,
            tool_expandable=self._settings.telegram_live_tool_expandable,
        )

        async def handle_line(line: str) -> None:
            if on_progress is None:
                return
            data = _parse_line(line)
            if data is None:
                return
            try:
                preview = progress.on_event(data)
                if preview:
                    await on_progress(preview)
            except Exception:
                logger.exception("opencode_stream_progress_failed")

        command = self.build_command(
            workspace, prompt, resume_chat_id, telegram_id=telegram_id
        )
        stop_watch = asyncio.Event()
        watch: asyncio.Task[None] | None = None
        if on_progress is not None and show_tool_calls_live:
            watch = asyncio.create_task(
                self._watch_running_tools(progress, on_progress, stop_watch)
            )
        try:
            result = await self._runner.run(
                command,
                cwd=workspace,
                sanitize_output=False,
                on_stdout_line=handle_line,
                on_process_start=on_process_start,
                env=process_env,
            )
        finally:
            stop_watch.set()
            if watch is not None:
                watch.cancel()
                await asyncio.gather(watch, return_exceptions=True)
        events, output, chat_id = _parse_stream_output(result.stdout)
        if not output and not result.timed_out:
            output = result.stderr.strip()
        return AgentResult(
            output=output,
            cursor_chat_id=chat_id or (
                resume_chat_id if resume_chat_id and resume_chat_id.startswith("ses_") else None
            ),
            returncode=result.returncode,
            events=events,
            cancelled=result.cancelled,
            stderr=result.stderr,
            timed_out=result.timed_out,
        )

    async def _watch_running_tools(
        self,
        progress: OpenCodeProgress,
        on_progress: Callable[[str], Awaitable[None]],
        stop: asyncio.Event,
    ) -> None:
        seen: set[str] = set()
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=0.4)
                return
            except TimeoutError:
                pass
            session_id = progress.chat_id
            if not session_id:
                continue
            try:
                rows = await asyncio.to_thread(
                    running_tool_summaries,
                    self._settings.opencode_state_db,
                    session_id,
                )
            except Exception:
                logger.exception("opencode_running_tools_failed")
                continue
            for part_id, summary in rows:
                if part_id in seen:
                    continue
                seen.add(part_id)
                preview = progress.note_tool(summary)
                if preview:
                    await on_progress(preview)


def running_tool_summaries(db_path: Path, session_id: str) -> list[tuple[str, str]]:
    """Tools OpenCode has started. The JSON stream reports them only when they finish."""
    if not session_id.startswith("ses_") or not db_path.is_file():
        return []
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=0.2)
    except sqlite3.Error:
        return []
    try:
        rows = conn.execute(
            """
            SELECT id, data FROM part
            WHERE session_id = ?
              AND json_extract(data, '$.type') = 'tool'
              AND json_extract(data, '$.state.status') = 'running'
            """,
            (session_id,),
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()

    found: list[tuple[str, str]] = []
    for part_id, raw in rows:
        try:
            payload: Any = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        summary = _tool_summary({"part": payload})
        if summary:
            found.append((str(part_id), summary))
    return found


def _parse_line(line: str) -> dict[str, Any] | None:
    line = line.strip()
    if not line:
        return None
    try:
        parsed: Any = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


class OpenCodeProgress:
    """Live steps like Cursor: one planning line, tools under it, last text is the answer."""

    def __init__(
        self,
        *,
        show_tool_calls_live: bool = False,
        use_rich_tool_details: bool = False,
        tool_expandable: bool = False,
    ) -> None:
        self.answer = ""
        self.error = ""
        self.chat_id: str | None = None
        self._composer = (
            ToolCallLiveComposer(
                use_rich_details=use_rich_tool_details,
                tool_expandable=tool_expandable,
            )
            if show_tool_calls_live
            else None
        )

    def on_event(self, data: dict[str, Any]) -> str | None:
        session_id = data.get("sessionID")
        if isinstance(session_id, str) and session_id:
            self.chat_id = session_id
        event_type = str(data.get("type") or "")
        if event_type == "text":
            piece = _text_piece(data).strip()
            if not piece or _is_injected_prompt(piece):
                return None
            self.answer = piece
            if self._composer is not None:
                return self._composer.on_planning_step(piece)
            return piece
        if event_type == "tool_use" and self._composer is not None:
            summary = _tool_summary(data)
            if not summary:
                return None
            return self._composer.on_tool_call(summary)
        if event_type == "error":
            message = _error_message(data)
            if message:
                self.error = message
        return None

    def note_tool(self, summary: str) -> str | None:
        if self._composer is None or not summary.strip():
            return None
        return self._composer.on_tool_call(summary)


def _is_injected_prompt(text: str) -> bool:
    head = text.lstrip()
    return head.startswith("[Identity]") or head.startswith("[System:")


def _text_piece(data: dict[str, Any]) -> str:
    part = data.get("part")
    if isinstance(part, dict) and part.get("text"):
        return str(part["text"])
    if data.get("text"):
        return str(data["text"])
    return ""


_TOOL_LABELS = {
    "bash": "Shell",
    "read": "Read",
    "write": "Write",
    "edit": "Edit",
    "grep": "Grep",
    "glob": "Glob",
    "task": "Subagent",
    "todowrite": "Todo",
    "webfetch": "Fetch",
    "websearch": "Search",
}


def _tool_summary(data: dict[str, Any]) -> str:
    part = data.get("part")
    if not isinstance(part, dict):
        return ""
    name = str(part.get("tool") or part.get("name") or "tool")
    state = part.get("state")
    if not isinstance(state, dict):
        state = {}
    tool_input = state.get("input")
    if not isinstance(tool_input, dict):
        tool_input = {}
    path = str(tool_input.get("filePath") or tool_input.get("path") or "").strip()
    if name == "read" and path:
        skill_name = cursor_skill_name_from_path(path)
        if skill_name:
            return f"{SKILL_TOOL_SUMMARY_PREFIX}{skill_name}"
    label = _TOOL_LABELS.get(name, name)
    if name == "bash":
        command = str(tool_input.get("command") or state.get("title") or "").strip()
        command = command.splitlines()[0] if command else "(command)"
        return f"🔧 {label}: {_truncate(command)}"
    if path:
        return f"🔧 {label}: {_truncate(path)}"
    title = str(state.get("title") or "").strip()
    if title:
        return f"🔧 {label}: {_truncate(title.splitlines()[0])}"
    return f"🔧 {label}"


def _truncate(value: str, max_len: int = 80) -> str:
    compact = " ".join(value.split())
    if len(compact) <= max_len:
        return compact
    return compact[: max_len - 1].rstrip() + "…"


def _error_message(data: dict[str, Any]) -> str:
    error = data.get("error")
    if isinstance(error, dict):
        payload = error.get("data")
        if isinstance(payload, dict) and payload.get("message"):
            return str(payload["message"])
        if error.get("name"):
            return str(error["name"])
    if data.get("message"):
        return str(data["message"])
    return ""


def _parse_stream_output(
    stdout: str,
) -> tuple[list[AgentStreamEvent], str, str | None]:
    events: list[AgentStreamEvent] = []
    progress = OpenCodeProgress()

    for line in stdout.splitlines():
        data = _parse_line(line)
        if data is None:
            continue
        event_type = str(data.get("type") or "unknown")
        progress.on_event(data)
        content = ""
        if event_type == "text":
            content = _text_piece(data)
        elif event_type == "error":
            content = _error_message(data)
        elif event_type == "tool_use":
            content = _tool_summary(data)
        events.append(AgentStreamEvent(event_type=event_type, content=content, raw=data))

    output = progress.answer or progress.error
    return events, output, progress.chat_id
