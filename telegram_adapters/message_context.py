from __future__ import annotations

from aiogram.types import Message


def actor_from_message(message: Message) -> str:
    user = message.from_user
    if user is None:
        return "unknown"
    if user.full_name:
        return user.full_name
    if user.username:
        return f"@{user.username}"
    return f"id:{user.id}"


def get_chat_name(message: Message) -> str:
    chat = message.chat
    if chat.title:
        return chat.title

    name_parts = [part for part in (chat.first_name, chat.last_name) if part]
    if name_parts:
        return " ".join(name_parts)
    if chat.username:
        return f"@{chat.username}"
    return "незнакомца"
