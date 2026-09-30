"""Atomic, auditable closure of every account belonging to a client chat."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal

from db_asyncpg.repositories.base import ConnectionBoundRepo


@dataclass(frozen=True)
class AccountToClose:
    id: int
    currency: str
    balance: Decimal
    is_active: bool


@dataclass(frozen=True)
class ClosurePreview:
    client_id: int
    client_name: str
    chat_id: int
    accounts: tuple[AccountToClose, ...]
    fingerprint: str


class ClientClosureError(ValueError):
    pass


class ClientClosureRepo(ConnectionBoundRepo):
    @staticmethod
    def _fingerprint(client_id: int, rows: list) -> str:
        state = [
            [int(row["id"]), str(row["currency_code"]), str(row["balance"]), bool(row["is_active"])]
            for row in rows
        ]
        payload = json.dumps([client_id, state], ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()[:20]

    async def _read(self, con, chat_id: int, *, lock: bool) -> ClosurePreview:
        client = await con.fetchrow(
            "SELECT id, chat_id, name, is_active, closed_at FROM clients WHERE chat_id=$1"
            + (" FOR UPDATE" if lock else ""),
            chat_id,
        )
        if client is None:
            raise ClientClosureError("Клиент для этого чата не найден")
        if not client["is_active"] or client["closed_at"] is not None:
            raise ClientClosureError("Клиент уже неактивен или обнулён")
        rows = await con.fetch(
            "SELECT id, currency_code, balance, is_active FROM client_accounts "
            "WHERE client_id=$1 ORDER BY id" + (" FOR UPDATE" if lock else ""),
            client["id"],
        )
        return ClosurePreview(
            client_id=int(client["id"]),
            client_name=str(client["name"]),
            chat_id=int(client["chat_id"]),
            accounts=tuple(
                AccountToClose(int(row["id"]), str(row["currency_code"]),
                               Decimal(row["balance"]), bool(row["is_active"]))
                for row in rows
            ),
            fingerprint=self._fingerprint(int(client["id"]), rows),
        )

    async def preview(self, chat_id: int) -> ClosurePreview:
        async with self._connection() as con:
            async with con.transaction(isolation="repeatable_read", readonly=True):
                return await self._read(con, chat_id, lock=False)

    async def close(
        self, *, chat_id: int, expected_fingerprint: str, actor_tg_user_id: int,
        confirmation_message_id: int,
    ) -> ClosurePreview:
        async with self._connection() as con:
            async with con.transaction():
                preview = await self._read(con, chat_id, lock=True)
                if preview.fingerprint != expected_fingerprint:
                    raise ClientClosureError("Балансы изменились. Повторите /обнулить и проверьте новые суммы")
                for account in preview.accounts:
                    if account.balance != 0:
                        await con.execute(
                            """
                            INSERT INTO transactions
                                (client_id, account_id, amount, balance_after, comment, source, idempotency_key)
                            VALUES ($1, $2, $3, 0, $4, 'client_zero', $5)
                            """,
                            preview.client_id, account.id, -account.balance,
                            f"/обнулить; Telegram manager {actor_tg_user_id}; chat {chat_id}",
                            f"client_zero:{chat_id}:{confirmation_message_id}:{account.id}",
                        )
                await con.execute(
                    "UPDATE client_accounts SET balance=0, is_active=FALSE, deactivated_at=NOW() "
                    "WHERE client_id=$1",
                    preview.client_id,
                )
                await con.execute(
                    "UPDATE clients SET is_active=FALSE, deactivated_at=NOW(), closed_at=NOW() "
                    "WHERE id=$1",
                    preview.client_id,
                )
                return preview
