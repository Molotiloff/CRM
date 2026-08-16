from __future__ import annotations

import html
import logging
from collections.abc import Callable
from decimal import Decimal, InvalidOperation

from db_asyncpg.ports.market import RateOrderRepositoryPort
from services.messaging import MessengerError, MessengerPort
from services.number_formatting import format_rate

log = logging.getLogger("rate_orders")


class RateOrderService:
    def __init__(
        self,
        *,
        repo: RateOrderRepositoryPort,
        orders_chat_id: int,
        get_current_best_ask: Callable[[], Decimal | None] | None = None,
    ) -> None:
        self.repo = repo
        self.orders_chat_id = int(orders_chat_id)
        self.get_current_best_ask = get_current_best_ask

    def _current_ask_line(self) -> str:
        if not self.get_current_best_ask:
            return "<b>Актуальный курс Биржи</b>: <code>недоступен</code>"

        current_ask = self.get_current_best_ask()
        if current_ask is None:
            return "<b>Актуальный курс Биржи</b>: <code>недоступен</code>"

        return f"<b>Актуальный курс Биржи</b>: <code>{format_rate(current_ask)}</code>"

    @staticmethod
    def _condition_text(commission: Decimal, *, triggered: bool = False) -> str:
        if commission < 0:
            return "курс биржи стал ≤ ожидаемого" if triggered else "ждём курс биржи ≤ ожидаемого"
        return "курс биржи стал ≥ ожидаемого" if triggered else "ждём курс биржи ≥ ожидаемого"

    def build_draft_text(
        self,
        *,
        order_id: int,
        client_name: str,
        client_chat_id: int,
        requested_rate: Decimal,
    ) -> str:
        return (
            "📌 <b>Новый ордер на курс</b>\n\n"
            f"<b>ID</b>: <code>{order_id}</code>\n"
            f"<b>Клиент</b>: <code>{html.escape(client_name)}</code>\n"
            f"<b>Курс клиента</b>: <code>{format_rate(requested_rate)}</code>\n"
            f"{self._current_ask_line()}\n"
            f"<b>Статус</b>: <code>ждёт комиссию</code>\n\n"
            "Ответьте на это сообщение командой вида:\n"
            "<code>/курс -0.5</code> или <code>/курс +0.5</code>"
        )

    def build_active_text(
        self,
        *,
        order_id: int,
        client_name: str,
        client_chat_id: int,
        requested_rate: Decimal,
        commission: Decimal,
        target_ask: Decimal,
    ) -> str:
        return (
            "📌 <b>Ордер активирован</b>\n\n"
            f"<b>ID</b>: <code>{order_id}</code>\n"
            f"<b>Клиент</b>: <code>{html.escape(client_name)}</code>\n"
            f"<b>Курс клиента</b>: <code>{format_rate(requested_rate)}</code>\n"
            f"<b>Комиссия</b>: <code>{format_rate(commission)}</code>\n"
            f"<b>Ожидаемый курс биржи по ордеру</b>: <code>{format_rate(target_ask)}</code>\n"
            f"<b>Условие</b>: <code>{html.escape(self._condition_text(commission))}</code>\n"
            f"{self._current_ask_line()}\n"
            f"<b>Статус</b>: <code>отслеживается</code>"
        )

    def build_triggered_text(
        self,
        *,
        order_id: int,
        client_name: str,
        client_chat_id: int,
        requested_rate: Decimal,
        commission: Decimal,
        target_ask: Decimal,
        best_ask: Decimal,
    ) -> str:
        return (
            "✅ <b>Ордер выполнен</b>\n\n"
            f"<b>ID</b>: <code>{order_id}</code>\n"
            f"<b>Клиент</b>: <code>{html.escape(client_name)}</code>\n"
            f"<b>Курс клиента</b>: <code>{format_rate(requested_rate)}</code>\n"
            f"<b>Комиссия</b>: <code>{format_rate(commission)}</code>\n"
            f"<b>Ожидаемый курс биржи по ордеру</b>: <code>{format_rate(target_ask)}</code>\n"
            f"<b>Условие</b>: <code>{html.escape(self._condition_text(commission, triggered=True))}</code>\n"
            f"<b>Актуальный курс на бирже при выполнении ордера</b>: <code>{format_rate(best_ask)}</code>\n"
            f"<b>Статус</b>: <code>выполнено</code>"
        )

    async def create_order(
        self,
        *,
        messenger: MessengerPort,
        client_chat_id: int,
        client_name: str,
        requested_rate: Decimal,
        created_by_user_id: int | None,
    ) -> int:
        temp_order_id = await self.repo.create_rate_order(
            client_chat_id=client_chat_id,
            client_name=client_name,
            requested_rate=requested_rate,
            created_by_user_id=created_by_user_id,
            order_chat_id=self.orders_chat_id,
            order_message_id=0,
        )

        text = self.build_draft_text(
            order_id=temp_order_id,
            client_name=client_name,
            client_chat_id=client_chat_id,
            requested_rate=requested_rate,
        )

        sent = await messenger.send(
            chat_id=self.orders_chat_id,
            text=text,
        )

        await self.repo.set_rate_order_message_binding(
            order_id=temp_order_id,
            order_chat_id=sent.chat_id,
            order_message_id=sent.message_id,
        )

        return temp_order_id

    async def activate_order_from_reply(
        self,
        *,
        messenger: MessengerPort,
        reply_chat_id: int,
        reply_message_id: int,
        commission_text: str,
        activated_by_user_id: int | None,
    ) -> dict | None:
        order = await self.repo.get_rate_order_by_message(
            order_chat_id=reply_chat_id,
            order_message_id=reply_message_id,
        )
        if not order:
            return None

        try:
            commission = Decimal(commission_text.replace(",", "."))
        except InvalidOperation:
            raise ValueError("Комиссия должна быть числом. Пример: /курс -0.5 или /курс +0.5") from None

        requested_rate = Decimal(str(order["requested_rate"]))
        target_ask = requested_rate + commission

        await self.repo.activate_rate_order(
            order_id=int(order["id"]),
            commission=commission,
            target_ask=target_ask,
            activated_by_user_id=activated_by_user_id,
        )

        text = self.build_active_text(
            order_id=int(order["id"]),
            client_name=str(order["client_name"]),
            client_chat_id=int(order["client_chat_id"]),
            requested_rate=requested_rate,
            commission=commission,
            target_ask=target_ask,
        )

        try:
            await messenger.edit_text(
                chat_id=reply_chat_id,
                message_id=reply_message_id,
                text=text,
            )
        except MessengerError as e:
            log.warning(
                "Failed to edit rate-order reply chat_id=%s message_id=%s: %r",
                reply_chat_id,
                reply_message_id,
                e,
            )

        return await self.repo.get_rate_order_by_id(int(order["id"]))

    async def process_best_ask(
        self,
        *,
        messenger: MessengerPort,
        best_ask: Decimal,
    ) -> None:
        orders = await self.repo.list_active_rate_orders()
        if not orders:
            return

        for order in orders:
            try:
                target_ask = Decimal(str(order["target_ask"]))
                commission = Decimal(str(order["commission"]))
            except (KeyError, TypeError, InvalidOperation):
                continue

            if commission < 0:
                # Ждём снижения курса
                if best_ask > target_ask:
                    continue
            else:
                # Ждём роста курса
                if best_ask < target_ask:
                    continue

            changed = await self.repo.mark_rate_order_triggered(
                order_id=int(order["id"]),
            )
            if not changed:
                continue

            client_chat_id = int(order["client_chat_id"])
            client_name = str(order["client_name"])
            order_chat_id = int(order["order_chat_id"])
            order_message_id = int(order["order_message_id"])
            requested_rate = Decimal(str(order["requested_rate"]))

            client_text = (
                "✅ Курс достиг нужного уровня.\n\n"
                "Свяжитесь с менеджером для актуального курса."
            )

            orders_text = (
                "✅ <b>Ордер сработал</b>\n\n"
                f"<b>ID</b>: <code>{order['id']}</code>\n"
                f"<b>Клиент</b>: <b>{html.escape(client_name)}</b>\n"
                f"<b>Актуальный курс на бирже по ордеру</b>: <code>{format_rate(best_ask)}</code>"
            )

            updated_order_text = self.build_triggered_text(
                order_id=int(order["id"]),
                client_name=client_name,
                client_chat_id=client_chat_id,
                requested_rate=requested_rate,
                commission=commission,
                target_ask=target_ask,
                best_ask=best_ask,
            )

            try:
                await messenger.edit_text(
                    chat_id=order_chat_id,
                    message_id=order_message_id,
                    text=updated_order_text,
                )
            except MessengerError as e:
                log.warning(
                    "Failed to edit triggered order message chat_id=%s message_id=%s: %r",
                    order_chat_id,
                    order_message_id,
                    e,
                )

            try:
                await messenger.send(
                    chat_id=client_chat_id,
                    text=client_text,
                )
            except MessengerError as e:
                log.warning("Failed to notify client chat_id=%s: %r", client_chat_id, e)

            try:
                await messenger.send(
                    chat_id=order_chat_id,
                    text=orders_text,
                    reply_to_message_id=order_message_id,
                )
            except MessengerError as e:
                log.warning("Failed to notify orders chat: %r", e)
