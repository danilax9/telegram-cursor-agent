"""Telegram message templates."""

START_MESSAGE = (
    "Welcome to the Cursor Remote Agent.\n"
    "Send a coding prompt or type `help` for commands."
)

UNAUTHORIZED_MESSAGE = "You are not authorized to use this bot."

TASK_QUEUED_MESSAGE = "Task queued. Processing..."

TASK_COMPLETED_PREFIX = "Task completed:\n"

TASK_FAILED_PREFIX = "Task failed:\n"

CONFIRMATION_MESSAGE = (
    "Confirmation required:\n{action}\n\nExpires in {ttl}s."
)
