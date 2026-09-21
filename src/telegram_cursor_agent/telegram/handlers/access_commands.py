"""Telegram /access management commands."""

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from telegram_cursor_agent.core.config import Settings
from telegram_cursor_agent.services.access import AccessError, AccessService

router = Router()


@router.message(Command("access"))
async def cmd_access(
    message: Message,
    command: CommandObject,
    db: AsyncSession,
    settings: Settings,
    telegram_user_id: int,
) -> None:
    service = AccessService(db, settings)
    args = (command.args or "").strip()

    if not args or args.lower() in {"list", "ls"}:
        try:
            service.ensure_can_manage(telegram_user_id)
        except PermissionError:
            await message.answer("Только владелец бота может смотреть список доступа.")
            return
        await message.answer(await service.list_access())
        return

    parts = args.split(maxsplit=1)
    action = parts[0].lower()
    target = parts[1].strip() if len(parts) > 1 else ""

    try:
        if action == "add":
            if (
                not target
                and message.reply_to_message
                and message.reply_to_message.from_user
            ):
                replied = message.reply_to_message.from_user
                text = await service.grant_from_reply(
                    telegram_user_id,
                    replied.id,
                    replied.username,
                )
            else:
                text = await service.grant(telegram_user_id, target)
        elif action in {"remove", "revoke", "del", "delete"}:
            text = await service.revoke(telegram_user_id, target)
        else:
            await message.answer(
                "Команды:\n"
                "• `/access` — список\n"
                "• `/access add 123456789`\n"
                "• `/access add @username`\n"
                "• ответь `/access add` на сообщение\n"
                "• `/access remove 123456789`"
            )
            return
    except PermissionError as exc:
        await message.answer(str(exc))
        return
    except AccessError as exc:
        await message.answer(str(exc))
        return

    await message.answer(text)
