"""Cursor CLI adapter with verified command syntax."""

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from telegram_cursor_agent.agent.prompts import SELF_DEPLOY_RULE_CONTENT, TELEGRAM_RULE_CONTENT
from telegram_cursor_agent.agent.stream_progress import StreamProgressHandler
from telegram_cursor_agent.core.config import Settings
from telegram_cursor_agent.execution.runner import ProcessRunner
from telegram_cursor_agent.services.cursor_models import resolve_model_file

_OUTPUT_SKIP_EVENT_TYPES = frozenset(
    {"user", "system", "thinking", "tool_call", "assistant"}
)


@dataclass
class AgentStreamEvent:
    event_type: str
    content: str = ""
    raw: dict[str, object] = field(default_factory=dict)


@dataclass
class AgentResult:
    output: str
    cursor_chat_id: str | None
    returncode: int | None
    events: list[AgentStreamEvent] = field(default_factory=list)
    cancelled: bool = False
    stderr: str = ""
    timed_out: bool = False


class CursorAgentAdapter:
    """Invokes cursor-agent CLI with verified flags."""

    def __init__(self, settings: Settings, runner: ProcessRunner) -> None:
        self._settings = settings
        self._runner = runner

    def build_command(
        self,
        workspace: str,
        prompt: str,
        resume_chat_id: str | None = None,
    ) -> list[str]:
        self._ensure_telegram_rules(workspace)
        cmd = [
            self._settings.cursor_agent_bin,
            "--print",
            "--output-format",
            "stream-json",
            "--stream-partial-output",
            "--workspace",
            workspace,
            "--trust",
            "--force",
            "--sandbox",
            "disabled",
        ]
        model_file = resolve_model_file(self._settings, workspace)
        if model_file is not None:
            model = model_file.read_text(encoding="utf-8").strip()
            if model:
                cmd.extend(["--model", model])
        if resume_chat_id:
            cmd.extend(["--resume", resume_chat_id])
        if self._settings.cursor_approve_mcps:
            cmd.append("--approve-mcps")
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
    ) -> AgentResult:
        progress_handler: StreamProgressHandler | None = None
        if on_progress is not None:
            progress_handler = StreamProgressHandler(on_progress, self._event_text)

        async def handle_line(line: str) -> None:
            if progress_handler is None:
                return
            data = self._parse_stream_line(line)
            if data is None:
                return
            await progress_handler.handle(data)

        command = self.build_command(workspace, prompt, resume_chat_id)
        result = await self._runner.run(
            command,
            cwd=workspace,
            sanitize_output=False,
            on_stdout_line=handle_line,
            on_process_start=on_process_start,
            env=process_env,
        )
        events, output, chat_id = self._parse_stream_output(result.stdout)
        if progress_handler is not None and progress_handler.last_assistant:
            output = progress_handler.last_assistant
        return AgentResult(
            # On a timeout the stream is cut mid-flight; never surface raw JSON.
            output=output or ("" if result.timed_out else result.stdout),
            cursor_chat_id=chat_id or resume_chat_id,
            returncode=result.returncode,
            events=events,
            cancelled=result.cancelled,
            stderr=result.stderr,
            timed_out=result.timed_out,
        )

    def _ensure_telegram_rules(self, workspace: str) -> None:
        rules_root = self._settings.projects_root
        if self._settings.sandbox_open and Path(workspace) == Path("/"):
            rules_root = self._settings.projects_root

        rules_dir = rules_root / ".cursor" / "rules"
        rules_dir.mkdir(parents=True, exist_ok=True)
        (rules_dir / "telegram-bot.mdc").write_text(TELEGRAM_RULE_CONTENT)
        if self._settings.self_deploy_enabled:
            (rules_dir / "self-deploy.mdc").write_text(SELF_DEPLOY_RULE_CONTENT)

    @staticmethod
    def _parse_stream_line(line: str) -> dict[str, Any] | None:
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

    def _parse_stream_output(
        self, stdout: str
    ) -> tuple[list[AgentStreamEvent], str, str | None]:
        events: list[AgentStreamEvent] = []
        text_parts: list[str] = []
        chat_id: str | None = None

        for line in stdout.splitlines():
            data = self._parse_stream_line(line)
            if data is None:
                text_parts.append(line.strip())
                continue

            event_type = str(data.get("type", data.get("event", "unknown")))
            content = self._event_text(data, event_type)

            if "chat_id" in data:
                chat_id = str(data["chat_id"])
            if "session_id" in data and chat_id is None:
                chat_id = str(data["session_id"])

            events.append(AgentStreamEvent(event_type=event_type, content=content, raw=data))
            if event_type == "assistant" and content:
                text_parts = [content]
            elif event_type in {"text", "done"} and content:
                text_parts.append(content)
            elif event_type == "result" and content and not text_parts:
                text_parts = [content]

        return events, "\n".join(text_parts), chat_id

    @staticmethod
    def _event_text(data: dict[str, Any], event_type: str) -> str:
        if event_type == "result":
            return str(data.get("result", ""))
        if "text" in data:
            return str(data["text"])
        message = data.get("message")
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, list):
                return "".join(
                    str(item.get("text", ""))
                    for item in content
                    if isinstance(item, dict)
                )
        return ""
