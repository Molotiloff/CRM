# handlers/debug.py
import html

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

debug_router = Router()


@debug_router.message(Command("whoami"))
async def cmd_whoami(message: Message) -> None:
    u = message.from_user
    c = message.chat

    user_id = u.id if u else None
    username = f"@{u.username}" if (u and u.username) else "—"
    full_name = u.full_name if u else "—"

    chat_id = c.id if c else None
    chat_title = c.title or c.full_name or c.username or str(chat_id)

    text = (
        f"👤 Пользователь:\n"
        f"• user_id: <code>{user_id}</code>\n"
        f"• имя: {html.escape(full_name)}\n"
        f"• username: {html.escape(username)}\n\n"
        f"💬 Текущий чат:\n"
        f"• chat_id: <code>{chat_id}</code>\n"
        f"• название: {html.escape(chat_title)}"
    )
    await message.answer(text, parse_mode="HTML")
