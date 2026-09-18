from __future__ import annotations

from collections import OrderedDict, defaultdict

from aiogram.types import Message

from telegram_adapters.broadcast_models import BroadcastPayload


class AiogramBroadcastSessionStore:
    """Unconfirmed Telegram UI drafts; safe to discard when the process stops."""

    def __init__(self, *, max_pending_items: int = 500) -> None:
        if max_pending_items < 1:
            raise ValueError("max_pending_items must be positive")
        self._pending_prompts: dict[int, set[int]] = defaultdict(set)
        self._prompt_groups: OrderedDict[int, tuple[int, str | None]] = OrderedDict()
        self._media_groups: OrderedDict[tuple[int, str], list[Message]] = OrderedDict()
        self._payloads: OrderedDict[int, BroadcastPayload] = OrderedDict()
        self._max_pending_items = max_pending_items

    def add_prompt(
        self,
        *,
        chat_id: int,
        prompt_message_id: int,
        group: str | None,
    ) -> None:
        chat_id = int(chat_id)
        prompt_message_id = int(prompt_message_id)
        self._pending_prompts[chat_id].add(prompt_message_id)
        self._prompt_groups[prompt_message_id] = (chat_id, group)
        self._prompt_groups.move_to_end(prompt_message_id)
        self._trim_prompts()

    def is_pending_prompt(self, *, chat_id: int, prompt_message_id: int) -> bool:
        return int(prompt_message_id) in self._pending_prompts.get(int(chat_id), set())

    def prompt_group(self, *, prompt_message_id: int | None) -> str | None:
        if not prompt_message_id:
            return None
        meta = self._prompt_groups.get(int(prompt_message_id))
        return meta[1] if meta else None

    def remove_prompt(self, *, chat_id: int, prompt_message_id: int) -> None:
        chat_id = int(chat_id)
        prompt_message_id = int(prompt_message_id)
        prompts = self._pending_prompts.get(chat_id)
        if prompts is not None:
            prompts.discard(prompt_message_id)
            if not prompts:
                self._pending_prompts.pop(chat_id, None)
        self._prompt_groups.pop(int(prompt_message_id), None)

    def add_media_group_message(
        self,
        *,
        chat_id: int,
        media_group_id: str,
        message: Message,
    ) -> tuple[int, str]:
        key = (int(chat_id), str(media_group_id))
        messages = self._media_groups.setdefault(key, [])
        messages.append(message)
        self._media_groups.move_to_end(key)
        while len(self._media_groups) > self._max_pending_items:
            self._media_groups.popitem(last=False)
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
        control_message_id = int(control_message_id)
        self._payloads[control_message_id] = payload
        self._payloads.move_to_end(control_message_id)
        while len(self._payloads) > self._max_pending_items:
            self._payloads.popitem(last=False)

    def get_payload(self, *, control_message_id: int) -> BroadcastPayload | None:
        return self._payloads.get(int(control_message_id))

    def pop_payload(self, *, control_message_id: int) -> BroadcastPayload | None:
        return self._payloads.pop(int(control_message_id), None)

    def _trim_prompts(self) -> None:
        while len(self._prompt_groups) > self._max_pending_items:
            prompt_message_id, (chat_id, _group) = self._prompt_groups.popitem(last=False)
            prompts = self._pending_prompts.get(chat_id)
            if prompts is None:
                continue
            prompts.discard(prompt_message_id)
            if not prompts:
                self._pending_prompts.pop(chat_id, None)
