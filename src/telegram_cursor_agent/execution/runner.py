"""Async subprocess runner with process-group cancellation."""

import asyncio
import contextlib
import os
import signal
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field

from telegram_cursor_agent.core.config import Settings
from telegram_cursor_agent.core.security import sanitize_for_telegram

_READ_CHUNK_SIZE = 65536


@dataclass
class ProcessHandle:
    pid: int
    process: asyncio.subprocess.Process
    cancelled: bool = False


@dataclass
class RunResult:
    returncode: int | None
    stdout: str
    stderr: str
    cancelled: bool = False
    truncated: bool = False
    timed_out: bool = False


class ProcessRunner:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._active: dict[int, ProcessHandle] = {}

    async def run(
        self,
        command: list[str],
        cwd: str | None = None,
        timeout_seconds: float | None = None,
        sanitize_output: bool = True,
        on_stdout_line: Callable[[str], Awaitable[None]] | None = None,
        on_process_start: Callable[[int], Awaitable[None]] | None = None,
        env: dict[str, str] | None = None,
    ) -> RunResult:
        timeout_seconds = timeout_seconds or float(self._settings.cursor_agent_timeout_seconds)
        process_env = None
        if env:
            process_env = os.environ.copy()
            process_env.update(env)
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
            env=process_env,
        )
        handle = ProcessHandle(pid=process.pid or 0, process=process)
        self._active[handle.pid] = handle
        if on_process_start is not None and handle.pid:
            await on_process_start(handle.pid)

        # Collected outside the readers so a timeout keeps whatever arrived so far.
        stdout_chunks: list[bytes] = []
        stderr_chunks: list[bytes] = []

        async def read_stdout() -> None:
            assert process.stdout is not None
            async for line in _iter_stdout_lines(process.stdout):
                stdout_chunks.append(line)
                if on_stdout_line is not None:
                    await on_stdout_line(line.decode("utf-8", errors="replace").rstrip())

        async def read_stderr() -> None:
            assert process.stderr is not None
            while True:
                chunk = await process.stderr.read(_READ_CHUNK_SIZE)
                if not chunk:
                    break
                stderr_chunks.append(chunk)

        timed_out = False
        user_cancelled = False
        try:
            await asyncio.wait_for(
                asyncio.gather(read_stdout(), read_stderr(), process.wait()),
                timeout=timeout_seconds,
            )
        except TimeoutError:
            timed_out = True
            user_cancelled = handle.cancelled
            await self.cancel(handle.pid)
            await self._reap(process)
        finally:
            self._active.pop(handle.pid, None)

        stdout_bytes = b"".join(stdout_chunks)
        stderr_bytes = b"".join(stderr_chunks)
        if timed_out:
            stderr_bytes += b"\nProcess timed out"

        max_bytes = self._settings.cursor_agent_max_output_bytes
        stdout_raw = stdout_bytes.decode("utf-8", errors="replace")
        stderr_raw = stderr_bytes.decode("utf-8", errors="replace")
        truncated = (
            len(stdout_raw.encode("utf-8")) > max_bytes
            or len(stderr_raw.encode("utf-8")) > max_bytes
        )

        return RunResult(
            returncode=process.returncode,
            stdout=(
                sanitize_for_telegram(stdout_raw, max_bytes) if sanitize_output else stdout_raw
            ),
            stderr=(
                sanitize_for_telegram(stderr_raw, max_bytes) if sanitize_output else stderr_raw
            ),
            # A timeout kills the process group, so only report a real cancel as cancelled.
            cancelled=user_cancelled if timed_out else handle.cancelled,
            truncated=truncated,
            timed_out=timed_out,
        )

    async def cancel(self, pid: int) -> bool:
        handle = self._active.get(pid)
        if handle is None:
            return False
        handle.cancelled = True
        try:
            os.killpg(os.getpgid(handle.pid), signal.SIGTERM)
            await asyncio.sleep(0.15)
            if handle.process.returncode is None:
                os.killpg(os.getpgid(handle.pid), signal.SIGKILL)
        except OSError:
            # Already gone, or the group is no longer ours to signal.
            pass
        return True

    @staticmethod
    async def _reap(process: asyncio.subprocess.Process) -> None:
        """Collect the exit status so a killed agent cannot linger as a zombie."""
        if process.returncode is not None:
            return
        with contextlib.suppress(TimeoutError, ProcessLookupError):
            await asyncio.wait_for(process.wait(), timeout=5)

    async def cancel_all(self) -> int:
        count = 0
        for pid in list(self._active.keys()):
            if await self.cancel(pid):
                count += 1
        return count

    async def cancel_pid(self, pid: int) -> bool:
        return await self.cancel(pid)


async def _iter_stdout_lines(stream: asyncio.StreamReader) -> AsyncIterator[bytes]:
    """Read stdout line-by-line without asyncio's readline buffer limit."""
    buffer = bytearray()
    while True:
        chunk = await stream.read(_READ_CHUNK_SIZE)
        if not chunk:
            if buffer:
                yield bytes(buffer)
            return
        buffer.extend(chunk)
        while True:
            newline = buffer.find(b"\n")
            if newline < 0:
                break
            yield bytes(buffer[: newline + 1])
            del buffer[: newline + 1]


@dataclass
class RunnerRegistry:
    runners: dict[str, ProcessRunner] = field(default_factory=dict)

    def get_or_create(self, key: str, settings: Settings) -> ProcessRunner:
        if key not in self.runners:
            self.runners[key] = ProcessRunner(settings)
        return self.runners[key]
