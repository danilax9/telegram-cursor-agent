"""Telegram bot command menu (the / button list)."""

from aiogram import Bot
from aiogram.types import BotCommand


BOT_COMMANDS: list[BotCommand] = [
    BotCommand(command="start", description="Запуск бота"),
    BotCommand(command="help", description="Справка по командам"),
    BotCommand(command="model", description="Выбор модели Cursor"),
    BotCommand(command="new", description="Новая сессия"),
    BotCommand(command="resume", description="Продолжить сессию"),
    BotCommand(command="delete", description="Удалить сессию"),
    BotCommand(command="summarize", description="Сжать контекст чата"),
    BotCommand(command="context", description="Что занимает контекст"),
    BotCommand(command="limits", description="Лимиты плана Cursor"),
    BotCommand(command="account", description="Аккаунты Cursor"),
    BotCommand(command="deploy", description="Обновить и перезапустить бота"),
    BotCommand(command="mcp", description="MCP: список и установка"),
    BotCommand(command="access", description="Управление доступом к боту"),
]


async def setup_bot_commands(bot: Bot) -> None:
    await bot.set_my_commands(BOT_COMMANDS)
