from __future__ import annotations

import logging

from services.messaging import MessengerError, MessengerPort
from services.payment_watch.models import PaymentWatchNotification
from telegram_adapters.payment_watch_presenter import AiogramPaymentWatchPresenter

log = logging.getLogger("payment_watch")


class AiogramPaymentWatchNotifier:
    def __init__(
        self,
        *,
        messenger: MessengerPort,
        presenter: AiogramPaymentWatchPresenter | None = None,
    ) -> None:
        self.messenger = messenger
        self.presenter = presenter or AiogramPaymentWatchPresenter()

    async def deliver(self, notification: PaymentWatchNotification) -> int:
        await self._delete_previous(notification)
        reply_markup = None
        if notification.with_timeout_actions and notification.watch_id is not None:
            reply_markup = self.presenter.timeout_keyboard(notification.watch_id)

        if notification.photo_bytes:
            sent = await self.messenger.send_photo(
                chat_id=notification.chat_id,
                photo_bytes=notification.photo_bytes,
                filename=notification.photo_filename or "payment_receipt.png",
                caption=notification.text,
                reply_markup=reply_markup,
                reply_to_message_id=notification.reply_message_id,
            )
        else:
            sent = await self.messenger.send(
                chat_id=notification.chat_id,
                text=notification.text,
                reply_markup=reply_markup,
                reply_to_message_id=notification.reply_message_id,
            )
        return sent.message_id

    async def _delete_previous(self, notification: PaymentWatchNotification) -> None:
        if not notification.delete_message_id:
            return
        try:
            await self.messenger.delete(
                chat_id=notification.chat_id,
                message_id=notification.delete_message_id,
            )
        except MessengerError as exc:
            if exc.benign:
                log.debug(
                    "Previous payment watch message already gone chat_id=%s msg_id=%s: %s",
                    notification.chat_id,
                    notification.delete_message_id,
                    exc,
                )
            else:
                log.exception(
                    "Failed to delete previous payment watch message chat_id=%s msg_id=%s",
                    notification.chat_id,
                    notification.delete_message_id,
                )
        except Exception:
            log.exception(
                "Failed to delete previous payment watch message chat_id=%s msg_id=%s",
                notification.chat_id,
                notification.delete_message_id,
            )
