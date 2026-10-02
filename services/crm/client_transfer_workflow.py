from __future__ import annotations

import logging
from html import escape

from services.messaging import MessengerPort
from services.number_formatting import format_amount_core
from services.receipts import ReceiptImageBuilder, ReceiptRow
from services.receipts.image import format_receipt_datetime

from .client_transfer_models import ClientTransferCommand, ClientTransferResult
from .client_transfer_service import ClientTransferService

log = logging.getLogger(__name__)


class ClientTransferWorkflow:
    """The same posting, receipt and notification flow for Telegram and CRM."""

    def __init__(
        self,
        service: ClientTransferService,
        messenger: MessengerPort,
        *,
        receipt_builder: ReceiptImageBuilder | None = None,
    ) -> None:
        self._service = service
        self._messenger = messenger
        self._receipt_builder = receipt_builder or ReceiptImageBuilder()

    async def transfer(
        self,
        command: ClientTransferCommand,
        *,
        reply_to_message_id: int | None = None,
    ) -> ClientTransferResult:
        result = await self._service.transfer(command)
        await self._notify(result, reply_to_message_id=reply_to_message_id)
        return result

    async def _notify(
        self,
        result: ClientTransferResult,
        *,
        reply_to_message_id: int | None,
    ) -> None:
        if not result.repeated:
            try:
                receipt = self._receipt_builder.build(
                    amount=result.amount,
                    currency=result.currency,
                    precision=result.precision,
                    rows=(
                        ReceiptRow("Статус", "Проведено", accent=True),
                        ReceiptRow("Номер операции", f"#{result.deal_id}"),
                        ReceiptRow("Отправитель", result.from_client_name),
                        ReceiptRow("Получатель", result.to_client_name),
                        ReceiptRow("Дата и время", format_receipt_datetime(result.created_at)),
                    ),
                )
            except Exception:
                log.exception("Failed to build client transfer receipt deal_id=%s", result.deal_id)
            else:
                for chat_id in (result.from_chat_id, result.to_chat_id):
                    try:
                        await self._messenger.send_photo(
                            chat_id,
                            receipt,
                            filename=f"client_transfer_{result.deal_id}.png",
                            caption=f"Перевод #{result.deal_id}",
                        )
                    except Exception:
                        log.exception(
                            "Failed to send client transfer receipt deal_id=%s chat_id=%s",
                            result.deal_id,
                            chat_id,
                        )

        amount = format_amount_core(result.amount, result.precision)
        sender_text = (
            f"Перевод #{result.deal_id} проведён{' (повтор)' if result.repeated else ''}.\n"
            f"Получатель: {escape(result.to_client_name)}\n"
            f"Списано: {amount} {result.currency}\n"
            f"Баланс: {format_amount_core(result.from_balance, result.precision)} {result.currency}"
        )
        try:
            await self._messenger.send(
                result.from_chat_id,
                sender_text,
                reply_to_message_id=reply_to_message_id,
            )
        except Exception:
            log.exception(
                "Failed to notify transfer sender deal_id=%s chat_id=%s",
                result.deal_id,
                result.from_chat_id,
            )
        if result.repeated:
            return
        recipient_text = (
            f"Перевод #{result.deal_id} от {escape(result.from_client_name)}.\n"
            f"Зачислено: {amount} {result.currency}\n"
            f"Баланс: {format_amount_core(result.to_balance, result.precision)} {result.currency}"
        )
        try:
            await self._messenger.send(result.to_chat_id, recipient_text)
        except Exception:
            log.exception(
                "Failed to notify transfer recipient deal_id=%s chat_id=%s",
                result.deal_id,
                result.to_chat_id,
            )
