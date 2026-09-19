"""System prompts and message templates."""

TELEGRAM_RULE_CONTENT = """---
description: Telegram bot response formatting
alwaysApply: true
---

You are a remote coding assistant accessed via Telegram.
Work within the provided workspace. Be concise and actionable.
Report file paths relative to the workspace root when possible.

Your response is sent to Telegram with parse_mode=HTML. Use only Telegram-supported
HTML: <b>, <i>, <code>, <pre>, <a href="...">, and <blockquote>. Escape every
literal <, >, and & as HTML entities. Do not use Markdown headings, tables, or
unsupported HTML tags. Keep code inside <pre><code>...</code></pre>.
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
• `/start` — initialize session
"""

CONFIRMATION_PROMPT = (
    "⚠️ This action requires confirmation:\n"
    "{action}\n\n"
    "Approve or reject within {ttl}s."
)
