from __future__ import annotations

from db_asyncpg.ports.exchange import ExchangeRequestRepositoryPort
from domain import TelegramMessageRef

from .request_context import ExchangeRequestContext


class ExchangeSourceLinkService:
    def __init__(self, repository: ExchangeRequestRepositoryPort) -> None:
        self._repository = repository

    async def get_context(self, request_id: str) -> ExchangeRequestContext | None:
        record = await self._repository.get_exchange_request_link(client_req_id=str(request_id))
        return ExchangeRequestContext.from_record(record) if record else None

    async def bind_client_card(
        self,
        *,
        request_id: str,
        table_request_id: str,
        message: TelegramMessageRef,
        request_text: str | None = None,
        is_request_card: bool = False,
    ) -> None:
        await self._repository.upsert_exchange_request_link(
            client_req_id=request_id,
            table_req_id=table_request_id,
            client_chat_id=message.chat_id,
            client_message_id=message.message_id,
            request_chat_id=message.chat_id if is_request_card else None,
            request_message_id=message.message_id if is_request_card else None,
            request_text=request_text if is_request_card else None,
        )

    async def bind_request_card(
        self,
        *,
        request_id: str,
        table_request_id: str,
        message: TelegramMessageRef,
        request_text: str,
    ) -> None:
        await self._repository.upsert_exchange_request_link(
            client_req_id=request_id,
            table_req_id=table_request_id,
            request_chat_id=message.chat_id,
            request_message_id=message.message_id,
            request_text=request_text,
        )

    async def mark_request_card_cancelled(
        self,
        *,
        request_id: str,
        table_request_id: str,
        message: TelegramMessageRef,
        request_text: str,
    ) -> None:
        await self._repository.upsert_exchange_request_link(
            client_req_id=request_id,
            table_req_id=table_request_id,
            request_chat_id=message.chat_id,
            request_message_id=message.message_id,
            request_text=request_text,
            status="cancelled",
        )
