"""System prompts and message templates."""

TELEGRAM_RULE_CONTENT = """---
description: Telegram bot response formatting
alwaysApply: true
---

You are a remote coding assistant accessed via Telegram.
Work within the provided workspace. Be concise and actionable.
Report file paths relative to the workspace root when possible.

## Telegram output format (mandatory)

Your reply is delivered through Telegram HTML parse mode.
Use ONLY these Telegram tags:
- <b>bold</b>
- <i>italic</i>
- <u>underline</u>
- <s>strikethrough</s>
- <code>inline code</code>
- <pre><code>code block</code></pre>
- <a href="https://example.com">link text</a>
- <blockquote>quote</blockquote>

Do NOT use Markdown (** ## ` | tables |), plain HTML outside the list above,
or any other markup. Telegram does not render Markdown tables or headings.

Formatting rules:
- No # headings — use <b>Section title</b> on its own line.
- No pipe tables — use bullet lines like "• field: value" or short plain lines.
- No **bold** or *italic* — use <b> and <i> instead.
- Keep lists simple with "• " or numbered lines without Markdown syntax.
- Escape literal < and > inside text when they are not tags.
"""

# Backward-compatible alias for tests and docs.
SYSTEM_PROMPT = TELEGRAM_RULE_CONTENT.split("---", 2)[-1].strip()

HELP_TEXT = """Available commands:
• Send any message to run a Cursor agent prompt
• run &lt;command&gt; — execute a shell command
• git status / git diff / git log — git shortcuts
• projects — list discovered projects
• use project &lt;name&gt; — select active project
• status — show current task status
• cancel — cancel running task
• /new — start a fresh Cursor chat session
• /resume — switch to an existing session
• /delete — delete the current or selected session
• /summarize — compact chat context to save tokens (/compact, /compress)
• /context — show what fills the context window
• /limits — Cursor plan usage (Composer/Grok vs other models)
• /start — initialize bot
"""

CONFIRMATION_PROMPT = (
    "⚠️ This action requires confirmation:\n"
    "{action}\n\n"
    "Approve or reject within {ttl}s."
)
