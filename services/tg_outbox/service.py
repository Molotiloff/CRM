from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, Protocol

from db_asyncpg.repositories.tg_outbox import TgOutboxItem
from observability import bind_log_context
from services.messaging import MessengerError, MessengerPort

from .message_builder import TelegramDealMessageBuilder

log = logging.getLogger(__name__)


class TgOutboxDeliveryRepositoryPort(Protocol):
    async def get_deal_delivery_context(self, deal_id: int) -> dict[str, Any] | None: ...


class DealTelegramSyncService:
    TERMINAL_STATUSES = frozenset({"done", "canceled"})

    def __init__(
        self,
        *,
        repository: TgOutboxDeliveryRepositoryPort,
        messenger: MessengerPort,
        message_builder: TelegramDealMessageBuilder | None = None,
    ) -> None:
        self._repository = repository
        self._messenger = messenger
        self._builder = message_builder or TelegramDealMessageBuilder()

    async def deliver(self, item: TgOutboxItem) -> None:
        if item.kind == "settlement_needs_review":
            await self._deliver_settlement_review(item.payload)
            return
        if item.kind not in {"deal_status_changed", "deal_source_updated"}:
            raise ValueError(f"Unsupported Telegram outbox kind: {item.kind}")
        deal_id = int(item.payload["dealId"])
        status = str(item.payload.get("newStatus") or item.payload.get("status") or "")
        event_payload = _mapping(item.payload.get("eventPayload"))
        context = await self._repository.get_deal_delivery_context(deal_id)
        if context is None:
            raise LookupError(f"Deal {deal_id} was not found for Telegram delivery")

        body = _mapping(context.get("body"))
        bind_log_context(
            deal_id=deal_id,
            request_id=(
                body.get("client_req_id")
                or body.get("req_id")
                or context.get("exchange_client_req_id")
                or context.get("deal_no")
            ),
        )
        preserve_markup = status not in self.TERMINAL_STATUSES
        for chat_id, message_id, base_text, client_facing in self._card_targets(
            context, body
        ):
            try:
                await self._messenger.edit_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=self._builder.card_text(
                        base_text,
                        status=status,
                        payload=event_payload,
                        client_facing=client_facing,
                    ),
                    preserve_reply_markup=preserve_markup,
                )
            except MessengerError as exc:
                if not exc.benign:
                    raise
                log.debug(
                    "Telegram card already unavailable or unchanged chat_id=%s message_id=%s: %s",
                    chat_id,
                    message_id,
                    exc,
                )

        if (
            item.kind == "deal_status_changed"
            and status == "balance_check"
            and event_payload.get("insufficientUsdt")
        ):
            request_chat_id = _int_or_none(event_payload.get("requestChatId"))
            if request_chat_id is not None:
                request_id = str(
                    body.get("client_req_id") or body.get("req_id") or context.get("deal_no")
                )
                await self._messenger.send(
                    chat_id=request_chat_id,
                    text=self._builder.shortage_notification(
                        request_id=request_id,
                        current_usdt=event_payload.get(
                            "usdtFact",
                            event_payload.get("actCurrentUsdt", "0"),
                        ),
                        shortage_usdt=event_payload.get("shortageUsdt", "0"),
                    ),
                )

        client_chat_id = _int_or_none(context.get("client_chat_id"))
        if client_chat_id is not None and item.kind == "deal_status_changed":
            request_id = str(
                body.get("client_req_id") or body.get("req_id") or context.get("deal_no")
            )
            await self._messenger.send(
                chat_id=client_chat_id,
                text=self._builder.notification(
                    request_id=request_id,
                    status=status,
                    payload=event_payload,
                ),
            )

    async def _deliver_settlement_review(self, payload: Mapping[str, Any]) -> None:
        text = (
            f"⚠️ Сделка #{payload['dealId']}: сумма платежа требует проверки.\n"
            f"Ожидалось: {payload['expected']} USDT; "
            f"фактически: {payload['actual']} USDT; "
            f"отклонение: {payload['delta']} USDT.\n"
            "Tronscan: https://tronscan.org/#/transaction/"
            f"{payload['txHash']}"
        )
        for raw_chat_id in payload.get("chatIds", []):
            await self._messenger.send(chat_id=int(raw_chat_id), text=text)

    def _card_targets(
        self,
        context: Mapping[str, Any],
        body: Mapping[str, Any],
    ) -> list[tuple[int, int, str, bool]]:
        targets: list[tuple[int, int, str, bool]] = []
        if context.get("source_kind") == "exchange":
            exchange_body = dict(body)
            exchange_body.setdefault("note", context.get("comment"))
            client_base = self._builder.exchange_client_base(exchange_body)
            _append_target(
                targets,
                context.get("exchange_client_chat_id"),
                context.get("exchange_client_message_id"),
                client_base,
                client_facing=True,
            )
            _append_target(
                targets,
                context.get("exchange_request_chat_id"),
                context.get("exchange_request_message_id"),
                context.get("exchange_request_text"),
                client_facing=False,
            )
        elif context.get("source_kind") == "cash":
            _append_target(
                targets,
                body.get("telegram_client_chat_id"),
                body.get("telegram_client_message_id"),
                body.get("telegram_client_text"),
                client_facing=True,
            )
            _append_target(
                targets,
                context.get("cash_request_chat_id"),
                context.get("cash_request_message_id"),
                body.get("telegram_request_text"),
                client_facing=False,
            )
        return list(dict.fromkeys(targets))


def _append_target(
    targets: list[tuple[int, int, str, bool]],
    chat_id: object,
    message_id: object,
    text: object,
    *,
    client_facing: bool,
) -> None:
    parsed_chat_id = _int_or_none(chat_id)
    parsed_message_id = _int_or_none(message_id)
    base_text = str(text or "").strip()
    if parsed_chat_id is not None and parsed_message_id is not None and base_text:
        targets.append(
            (parsed_chat_id, parsed_message_id, base_text, client_facing)
        )


def _int_or_none(value: object) -> int | None:
    return int(value) if value is not None else None


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}
