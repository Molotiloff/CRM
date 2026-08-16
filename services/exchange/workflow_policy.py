from __future__ import annotations


def is_request_chat(chat_id: int, request_chat_id: int | None) -> bool:
    return request_chat_id is not None and int(chat_id) == int(request_chat_id)


def tracked_exchange_currencies(chat_id: int, request_chat_id: int | None) -> frozenset[str] | None:
    return frozenset({"USDT"}) if is_request_chat(chat_id, request_chat_id) else None
