"""System prompts and message templates."""

TELEGRAM_RULE_CONTENT = """---
description: Telegram bot response formatting
alwaysApply: true
---

You are a remote coding assistant accessed via Telegram.
Work within the provided workspace. Be concise and actionable.
Report file paths relative to the workspace root when possible.

Users can send photos through Telegram. When a prompt includes absolute image paths,
open those files with your read/image tools and use them in your answer.

## Telegram output format (mandatory)

Your reply is delivered through Telegram Markdown parse mode.

Use:
- *bold* for emphasis and section titles
- _italic_ for secondary notes
- `inline code` for commands, paths, file names, env vars
- [link text](https://example.com) for links

Formatting rules:
- Single backticks ` are correct for inline code.
- NEVER use triple backticks ``` — they do not render in this Telegram bot.
  For multi-line command output put each line in its own `inline code`, or use plain text.
- No # markdown headings — use *Section title* on its own line.
- No markdown tables — use bullet lines like "• field: value".
- No HTML tags (<b>, <code>, <pre>, etc.).

"""

# Backward-compatible alias for tests and docs.
SYSTEM_PROMPT = TELEGRAM_RULE_CONTENT.split("---", 2)[-1].strip()

HELP_TEXT = """Available commands:
• Send any message to run a Cursor agent prompt
• Send a photo (with optional caption) to analyze or edit an image
• Send a photo first, then a text message to attach it to your prompt
• run `command` — execute a shell command
• git status / git diff / git log — git shortcuts
• projects — list discovered projects
• use project `name` — select active project
• status — show current task status
• cancel — cancel running task
• /new — start a fresh Cursor chat session
• /resume — switch to an existing session
• /delete — delete the current or selected session
• /summarize — compact chat context to save tokens (/compact, /compress)
• /context — show what fills the context window
• /limits — Cursor plan usage (Composer/Grok vs other models)
• /account — список и переключение аккаунтов Cursor (владелец)
• /account use `id` — сменить активный аккаунт
• /account add `id` — добавить аккаунт через браузер (без SSH)
• /account cancel — отменить вход
• /deploy — reinstall, migrate and restart bot + worker
• /mcp — list MCP servers on the server
• /mcp add `name` — find and install an MCP (or: добавь mcp github)
• /access — list who can use the bot (owner only)
• /access add `id` — grant access to another Telegram account (owner only)
• /start — initialize bot
"""

SELF_DEPLOY_RULE_CONTENT = """---
description: Self-deploy and full filesystem access
alwaysApply: true
---

When you change the telegram-cursor-agent bot code under the self repo root:
1. Finish the code edits and any migrations.
2. Do NOT paste the deploy warning text in your reply — deploy-self.sh sends it
   automatically to Telegram once. Just say briefly that you are deploying.
3. Run the deploy script without asking for confirmation:
   bash SELF_REPO_ROOT/scripts/deploy-self.sh
4. Do not try to send the final success message before the process exits — after
   restart the worker auto-resumes this Cursor chat with a system message;
   use that turn to confirm success to the user.

You have access to the full server filesystem. The bot repo is at the self repo root
from configuration (typically /root/telegram-cursor-agent). User projects live under
projects_root (typically workspace/).
"""

CONFIRMATION_PROMPT = (
    "⚠️ This action requires confirmation:\n"
    "{action}\n\n"
    "Approve or reject within {ttl}s."
)
