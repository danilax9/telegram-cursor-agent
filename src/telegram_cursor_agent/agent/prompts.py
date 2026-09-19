"""System prompts and message templates."""

TELEGRAM_RULE_CONTENT = """---
description: Telegram bot response formatting
alwaysApply: true
---

You are a remote coding assistant accessed via Telegram.
Work within the provided workspace. Be concise and actionable.
Report file paths relative to the workspace root when possible.

Your response is sent to Telegram. Markdown is supported: **bold**, *italic*,
`inline code`, ``` fenced code blocks ```, [links](https://example.com), and
# headings. Prefer concise Markdown over raw HTML.
"""

# Backward-compatible alias for tests and docs.
SYSTEM_PROMPT = TELEGRAM_RULE_CONTENT.split("---", 2)[-1].strip()

HELP_TEXT = """Available commands:
• Send any message to run a Cursor agent prompt
• `run <command>` — execute a shell command
• `git status` / `git diff` / `git log` — git shortcuts
• `projects` — list discovered projects
• `use project <name>` — select active project
• `status` — show current task status
• `cancel` — cancel running task
• `/new` — start a fresh Cursor chat session
• `/resume` — switch to an existing session
• `/delete` — delete the current or selected session
• `/limits` — Cursor plan usage (Composer/Grok vs other models)
• `/start` — initialize bot
"""

CONFIRMATION_PROMPT = (
    "⚠️ This action requires confirmation:\n"
    "{action}\n\n"
    "Approve or reject within {ttl}s."
)
