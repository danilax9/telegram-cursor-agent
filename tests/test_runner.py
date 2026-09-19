"""Process runner tests."""

import asyncio
from unittest.mock import AsyncMock, patch

from telegram_cursor_agent.execution.runner import ProcessRunner


def _stream_reader(data: bytes) -> asyncio.StreamReader:
    reader = asyncio.StreamReader(limit=2**20)
    reader.feed_data(data)
    reader.feed_eof()
    return reader


async def test_run_captures_output(test_settings) -> None:
    runner = ProcessRunner(test_settings)
    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
        mock_process = AsyncMock()
        mock_process.pid = 1234
        mock_process.returncode = 0
        mock_process.stdout = _stream_reader(b"hello\n")
        mock_process.stderr = _stream_reader(b"")
        mock_exec.return_value = mock_process

        result = await runner.run(["echo", "hello"])
        assert "hello" in result.stdout
        assert result.returncode == 0


async def test_run_truncates_large_output(test_settings) -> None:
    test_settings = test_settings.model_copy(update={"cursor_agent_max_output_bytes": 50})
    runner = ProcessRunner(test_settings)
    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
        mock_process = AsyncMock()
        mock_process.pid = 1234
        mock_process.returncode = 0
        mock_process.stdout = _stream_reader(b"x" * 200)
        mock_process.stderr = _stream_reader(b"")
        mock_exec.return_value = mock_process

        result = await runner.run(["cat", "big.txt"])
        assert "truncated" in result.stdout
        assert result.truncated is True
