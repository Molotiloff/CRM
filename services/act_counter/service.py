from __future__ import annotations

from decimal import Decimal

from db_asyncpg.ports.workflows import ActCounterLedgerRepositoryPort
from services.act_counter.models import AppliedExchangeMovement


class ActCounterService:
    USDT_CODE = "USDT"
    USDT_PRECISION = 3

    def __init__(self, repo: ActCounterLedgerRepositoryPort) -> None:
        self.repo = repo

    async def get_current_amount(
        self,
        *,
        request_chat_id: int,
        chat_name: str | None = None,
    ) -> Decimal:
        client_id = await self._ensure_request_chat_client(
            request_chat_id=request_chat_id,
            chat_name=chat_name,
        )
        rows = await self.repo.snapshot_wallet(client_id)
        for row in rows:
            if str(row["currency_code"]).upper() == self.USDT_CODE:
                return Decimal(str(row["balance"]))
        return Decimal("0")

    async def set_current_amount(
        self,
        *,
        request_chat_id: int,
        chat_name: str | None,
        amount: Decimal,
        comment: str | None = None,
        idempotency_key: str | None = None,
    ) -> Decimal:
        client_id = await self._ensure_request_chat_client(
            request_chat_id=request_chat_id,
            chat_name=chat_name,
        )
        current_amount = await self.get_current_amount(
            request_chat_id=request_chat_id,
            chat_name=chat_name,
        )
        delta = amount - current_amount
        if delta == 0:
            return current_amount

        if delta > 0:
            await self.repo.deposit(
                client_id=client_id,
                currency_code=self.USDT_CODE,
                amount=delta,
                comment=comment or "act",
                source="act_set",
                idempotency_key=idempotency_key,
            )
        else:
            await self.repo.withdraw(
                client_id=client_id,
                currency_code=self.USDT_CODE,
                amount=abs(delta),
                comment=comment or "act",
                source="act_set",
                idempotency_key=idempotency_key,
            )
        return amount

    async def register_exchange_movements(
        self,
        *,
        req_id: str,
        request_chat_id: int,
        request_message_id: int,
        movements: list[AppliedExchangeMovement],
        table_req_id: str | None = None,
    ) -> None:
        for movement in movements:
            if movement.currency_code.upper() != self.USDT_CODE:
                continue
            await self.repo.link_act_request_transaction(
                req_id=req_id,
                table_req_id=table_req_id,
                request_chat_id=request_chat_id,
                request_message_id=request_message_id,
                transaction_id=movement.transaction_id,
                direction=movement.direction,
                status="ACTIVE",
            )

    async def apply_request_wallet_movements(
        self,
        *,
        req_id: str,
        request_chat_id: int,
        request_message_id: int,
        movements: list[AppliedExchangeMovement],
        table_req_id: str | None = None,
        chat_name: str | None = None,
    ) -> None:
        client_id = await self._ensure_request_chat_client(
            request_chat_id=request_chat_id,
            chat_name=chat_name,
        )
        for movement in movements:
            if movement.currency_code.upper() != self.USDT_CODE:
                continue
            idem = f"actwallet:{request_chat_id}:{req_id}:{movement.transaction_id}"
            comment = f"ACT req {table_req_id or req_id} {movement.direction}"
            if movement.direction == "IN":
                await self.repo.deposit(
                    client_id=client_id,
                    currency_code=self.USDT_CODE,
                    amount=movement.amount,
                    comment=comment,
                    source="act_exchange",
                    idempotency_key=idem,
                )
            else:
                await self.repo.withdraw(
                    client_id=client_id,
                    currency_code=self.USDT_CODE,
                    amount=movement.amount,
                    comment=comment,
                    source="act_exchange",
                    idempotency_key=idem,
                )

    async def revert_request_wallet_movements(
        self,
        *,
        req_id: str,
        request_chat_id: int,
        chat_name: str | None = None,
    ) -> None:
        rows = await self.repo.get_act_request_transaction(req_id=req_id)
        if not rows:
            return
        client_id = await self._ensure_request_chat_client(
            request_chat_id=request_chat_id,
            chat_name=chat_name,
        )
        for row in rows:
            if str(row.get("status") or "").upper() == "CANCELED":
                continue
            if str(row.get("currency_code") or "").upper() != self.USDT_CODE:
                continue
            amount = abs(Decimal(str(row["amount"])))
            direction = str(row["direction"]).upper()
            tx_id = int(row["transaction_id"])
            idem = f"actwallet:cancel:{request_chat_id}:{req_id}:{tx_id}"
            comment = f"ACT cancel req {row.get('table_req_id') or req_id} {direction}"
            if direction == "IN":
                await self.repo.withdraw(
                    client_id=client_id,
                    currency_code=self.USDT_CODE,
                    amount=amount,
                    comment=comment,
                    source="act_exchange_cancel",
                    idempotency_key=idem,
                )
            else:
                await self.repo.deposit(
                    client_id=client_id,
                    currency_code=self.USDT_CODE,
                    amount=amount,
                    comment=comment,
                    source="act_exchange_cancel",
                    idempotency_key=idem,
                )

    async def cancel_request(self, *, req_id: str) -> int:
        return await self.repo.cancel_act_request_transactions(req_id=req_id)

    async def _ensure_request_chat_client(
        self,
        *,
        request_chat_id: int,
        chat_name: str | None = None,
    ) -> int:
        client_id = await self.repo.ensure_client(
            chat_id=int(request_chat_id),
            name=(chat_name or f"ACT {request_chat_id}"),
        )
        rows = await self.repo.snapshot_wallet(client_id)
        if not any(str(row["currency_code"]).upper() == self.USDT_CODE for row in rows):
            await self.repo.add_currency(client_id, self.USDT_CODE, self.USDT_PRECISION)
        return client_id
