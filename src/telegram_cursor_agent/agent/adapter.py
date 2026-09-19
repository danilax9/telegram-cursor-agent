"""Cursor CLI adapter with verified command syntax."""

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from telegram_cursor_agent.agent.prompts import TELEGRAM_RULE_CONTENT
from telegram_cursor_agent.agent.stream_progress import StreamProgressHandler
from telegram_cursor_agent.core.config import Settings
from telegram_cursor_agent.execution.runner import ProcessRunner

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
        model_file = Path(workspace) / ".cursor_model"
        if model_file.is_file():
            model = model_file.read_text().strip()
            if model:
                cmd.extend(["--model", model])
        if resume_chat_id:
            cmd.extend(["--resume", resume_chat_id])
        cmd.append(prompt)
        return cmd

    async def create_chat(self) -> str:
        command = [self._settings.cursor_agent_bin, "create-chat"]
        result = await self._runner.run(command, sanitize_output=False)
        chat_id = result.stdout.strip()
        if result.returncode not in {0, None} or not chat_id:
            msg = result.stderr.strip() or "Failed to create Cursor chat."
            raise RuntimeError(msg)
        return chat_id

    async def run_prompt(
        self,
        workspace: str,
        prompt: str,
        resume_chat_id: str | None = None,
        on_progress: Callable[[str], Awaitable[None]] | None = None,
        on_process_start: Callable[[int], Awaitable[None]] | None = None,
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
        )
        events, output, chat_id = self._parse_stream_output(result.stdout)
        if progress_handler is not None and progress_handler.last_assistant:
            output = progress_handler.last_assistant
        return AgentResult(
            output=output or result.stdout,
            cursor_chat_id=chat_id or resume_chat_id,
            returncode=result.returncode,
            events=events,
            cancelled=result.cancelled,
        )

    def _ensure_telegram_rules(self, workspace: str) -> None:
        rules_dir = Path(workspace) / ".cursor" / "rules"
        rules_dir.mkdir(parents=True, exist_ok=True)
        rule_file = rules_dir / "telegram-bot.mdc"
        if not rule_file.exists() or rule_file.read_text() != TELEGRAM_RULE_CONTENT:
            rule_file.write_text(TELEGRAM_RULE_CONTENT)

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
