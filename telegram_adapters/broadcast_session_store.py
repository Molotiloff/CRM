from __future__ import annotations

from collections import defaultdict

from aiogram.types import Message

from telegram_adapters.broadcast_models import BroadcastPayload


class AiogramBroadcastSessionStore:
    """Unconfirmed Telegram UI drafts; safe to discard when the process stops."""

    def __init__(self) -> None:
        self._pending_prompts: dict[int, set[int]] = defaultdict(set)
        self._prompt_groups: dict[int, str | None] = {}
        self._media_groups: dict[tuple[int, str], list[Message]] = defaultdict(list)
        self._payloads: dict[int, BroadcastPayload] = {}

    def add_prompt(
        self,
        *,
        chat_id: int,
        prompt_message_id: int,
        group: str | None,
    ) -> None:
        self._pending_prompts[int(chat_id)].add(int(prompt_message_id))
        self._prompt_groups[int(prompt_message_id)] = group

    def is_pending_prompt(self, *, chat_id: int, prompt_message_id: int) -> bool:
        return int(prompt_message_id) in self._pending_prompts.get(int(chat_id), set())

    def prompt_group(self, *, prompt_message_id: int | None) -> str | None:
        if not prompt_message_id:
            return None
        return self._prompt_groups.get(int(prompt_message_id))

    def remove_prompt(self, *, chat_id: int, prompt_message_id: int) -> None:
        self._pending_prompts[int(chat_id)].discard(int(prompt_message_id))
        self._prompt_groups.pop(int(prompt_message_id), None)

    def add_media_group_message(
        self,
        *,
        chat_id: int,
        media_group_id: str,
        message: Message,
    ) -> tuple[int, str]:
        key = (int(chat_id), str(media_group_id))
        self._media_groups[key].append(message)
        return key

    def has_media_group(self, key: tuple[int, str]) -> bool:
        return key in self._media_groups

    def pop_media_group(self, key: tuple[int, str]) -> list[Message]:
        return self._media_groups.pop(key, [])

    def add_payload(
        self,
        *,
        control_message_id: int,
        payload: BroadcastPayload,
    ) -> None:
        self._payloads[int(control_message_id)] = payload

    def get_payload(self, *, control_message_id: int) -> BroadcastPayload | None:
        return self._payloads.get(int(control_message_id))

    def pop_payload(self, *, control_message_id: int) -> BroadcastPayload | None:
        return self._payloads.pop(int(control_message_id), None)
