"""Human-readable summaries of Cursor stream-json tool_call events."""

from __future__ import annotations

from typing import Any


def format_tool_call_event(data: dict[str, Any]) -> str | None:
    if str(data.get("type")) != "tool_call":
        return None
    if str(data.get("subtype")) != "started":
        return None

    tool_call = data.get("tool_call")
    if not isinstance(tool_call, dict):
        return None

    for key, value in tool_call.items():
        if not key.endswith("ToolCall") or not isinstance(value, dict):
            continue
        tool_name = key[: -len("ToolCall")]
        args = value.get("args")
        if not isinstance(args, dict):
            args = {}
        return _format_tool(tool_name, args)
    return None


def _format_tool(tool_name: str, args: dict[str, Any]) -> str:
    label = _TOOL_LABELS.get(tool_name, tool_name.capitalize())

    if tool_name == "shell":
        command = str(args.get("command", "")).strip()
        return f"🔧 {label}: {_truncate(command or '(command)')}"

    if tool_name == "grep":
        pattern = str(args.get("pattern", "")).strip()
        path = str(args.get("path", "")).strip()
        if path:
            return f"🔧 {label}: {_truncate(pattern)} → {_truncate(path, 40)}"
        return f"🔧 {label}: {_truncate(pattern)}"

    if tool_name in {"read", "write", "strReplace", "delete"}:
        path = str(args.get("path", "")).strip()
        return f"🔧 {label}: {_truncate(path or '(file)')}"

    if tool_name == "glob":
        pattern = str(args.get("glob_pattern", args.get("pattern", ""))).strip()
        directory = str(args.get("target_directory", "")).strip()
        if directory:
            return f"🔧 {label}: {_truncate(pattern)} in {_truncate(directory, 40)}"
        return f"🔧 {label}: {_truncate(pattern or '(pattern)')}"

    if tool_name == "task":
        description = str(args.get("description", "")).strip()
        return f"🔧 {label}: {_truncate(description or 'subagent')}"

    if tool_name == "webFetch":
        url = str(args.get("url", "")).strip()
        return f"🔧 {label}: {_truncate(url or '(url)')}"

    if tool_name == "webSearch":
        query = str(args.get("search_term", args.get("query", ""))).strip()
        return f"🔧 {label}: {_truncate(query or '(query)')}"

    return f"🔧 {label}"


def _truncate(value: str, max_len: int = 80) -> str:
    compact = " ".join(value.split())
    if len(compact) <= max_len:
        return compact
    return f"{compact[: max_len - 1]}…"


_TOOL_LABELS = {
    "shell": "Shell",
    "grep": "Grep",
    "read": "Read",
    "write": "Write",
    "strReplace": "Edit",
    "delete": "Delete",
    "glob": "Glob",
    "task": "Subagent",
    "webFetch": "Fetch",
    "webSearch": "Search",
}
