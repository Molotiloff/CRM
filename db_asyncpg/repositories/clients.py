from __future__ import annotations

from typing import Any

from db_asyncpg.repositories.base import ConnectionBoundRepo
from db_asyncpg.utils import to_upper


class ClientsRepo(ConnectionBoundRepo):
    async def update_client_chat_id(self, *, client_id: int, new_chat_id: int) -> None:
        async with self._connection() as con:
            async with con.transaction():
                exist = await con.fetchrow(
                    "SELECT id FROM clients WHERE chat_id=$1 AND id<>$2",
                    int(new_chat_id), int(client_id),
                )
                if exist:
                    raise ValueError(f"chat_id {new_chat_id} already belongs to client_id={exist['id']}")

                await con.execute(
                    "UPDATE clients SET chat_id=$1 WHERE id=$2",
                    int(new_chat_id), int(client_id),
                )

    async def find_client_by_name_exact(self, name: str) -> dict[str, Any] | None:
        async with self._connection() as con:
            row = await con.fetchrow(
                """
                SELECT id, chat_id, name, client_group
                FROM clients
                WHERE is_active = TRUE
                  AND name = $1
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                name.strip(),
            )
            return dict(row) if row else None

    async def ensure_client(self, chat_id: int, name: str, client_group: str | None = None) -> int:
        async with self._connection() as con:
            async with con.transaction():
                row = await con.fetchrow(
                    "SELECT id, name, client_group, is_active FROM clients WHERE chat_id=$1",
                    int(chat_id),
                )
                if row:
                    need_update_ng = (
                        (name and row["name"] != name)
                        or (client_group is not None and row["client_group"] != client_group)
                    )

                    if not row["is_active"]:
                        await con.execute(
                            """
                            UPDATE clients
                            SET is_active = TRUE,
                                deactivated_at = NULL,
                                name = COALESCE($2, name),
                                client_group = COALESCE($3, client_group)
                            WHERE id = $1
                            """,
                            int(row["id"]), name, client_group,
                        )
                    elif need_update_ng:
                        await con.execute(
                            """
                            UPDATE clients
                            SET name = COALESCE($2, name),
                                client_group = COALESCE($3, client_group)
                            WHERE id = $1
                            """,
                            int(row["id"]), name, client_group,
                        )
                    return int(row["id"])

                by_name = await con.fetchrow(
                    """
                    SELECT id, chat_id, name, client_group, is_active
                    FROM clients
                    WHERE is_active = TRUE
                      AND name = $1
                    ORDER BY created_at DESC, id DESC
                    LIMIT 1
                    """,
                    (name or "").strip(),
                )
                if by_name:
                    await con.execute(
                        """
                        UPDATE clients
                        SET chat_id = $1,
                            client_group = COALESCE($2, client_group)
                        WHERE id = $3
                        """,
                        int(chat_id), client_group, int(by_name["id"]),
                    )
                    return int(by_name["id"])

                rec = await con.fetchrow(
                    """
                    INSERT INTO clients(chat_id, name, client_group)
                    VALUES($1, $2, $3)
                    RETURNING id
                    """,
                    int(chat_id), name, client_group,
                )
                return int(rec["id"])

    async def remove_client(self, chat_id: int) -> bool:
        async with self._connection() as con:
            async with con.transaction():
                res = await con.execute(
                    """
                    UPDATE clients
                    SET is_active = FALSE,
                        deactivated_at = NOW()
                    WHERE chat_id = $1
                      AND is_active = TRUE
                    """,
                    chat_id,
                )
                return res.endswith(" 1")

    async def list_clients(self) -> list[dict]:
        async with self._connection() as con:
            rows = await con.fetch(
                """
                SELECT
                    c.id,
                    c.chat_id,
                    c.name,
                    c.client_group,
                    c.created_at,
                    COUNT(a.*)               AS accounts_total,
                    COUNT(a.*) FILTER (WHERE a.is_active) AS accounts_active
                FROM clients c
                LEFT JOIN client_accounts a ON a.client_id = c.id
                WHERE c.is_active = TRUE
                GROUP BY c.id
                ORDER BY c.created_at DESC, c.id DESC
                """
            )
            return [dict(r) for r in rows]

    async def list_clients_by_group(self, client_group: str) -> list[dict]:
        async with self._connection() as con:
            rows = await con.fetch(
                """
                SELECT
                    c.id,
                    c.chat_id,
                    c.name,
                    c.client_group,
                    c.created_at,
                    COUNT(a.*) AS accounts_total,
                    COUNT(a.*) FILTER (WHERE a.is_active) AS accounts_active
                FROM clients c
                LEFT JOIN client_accounts a ON a.client_id = c.id
                WHERE c.is_active = TRUE
                  AND LOWER(COALESCE(c.client_group, '')) = LOWER($1)
                GROUP BY c.id
                ORDER BY c.created_at DESC, c.id DESC
                """,
                client_group.strip(),
            )
            return [dict(r) for r in rows]

    async def add_currency(self, client_id: int, currency_code: str, precision: int) -> int:
        code = to_upper(currency_code)
        async with self._connection() as con:
            async with con.transaction():
                rec = await con.fetchrow(
                    """
                    INSERT INTO client_accounts(client_id, currency_code, precision)
                    VALUES($1, $2, $3)
                    ON CONFLICT (client_id, currency_code)
                    DO UPDATE SET is_active = TRUE, precision = EXCLUDED.precision, deactivated_at = NULL
                    RETURNING id
                    """,
                    client_id, code, precision,
                )
                return rec["id"]

    async def remove_currency(self, client_id: int, currency_code: str) -> bool:
        code = to_upper(currency_code)
        async with self._connection() as con:
            async with con.transaction():
                res = await con.execute(
                    """
                    UPDATE client_accounts
                    SET is_active = FALSE,
                        deactivated_at = NOW()
                    WHERE client_id = $1
                      AND currency_code = $2
                      AND is_active = TRUE
                    """,
                    client_id, code,
                )
                return res.endswith(" 1")

    async def snapshot_wallet(self, client_id: int) -> list[dict[str, Any]]:
        async with self._connection() as con:
            rows = await con.fetch(
                """
                SELECT id, currency_code, precision, balance
                FROM client_accounts
                WHERE client_id=$1 AND is_active=TRUE
                ORDER BY currency_code
                """,
                client_id,
            )
            return [dict(r) for r in rows]

    async def balances_by_client(self) -> list[dict[str, Any]]:
        async with self._connection() as con:
            rows = await con.fetch(
                """
                SELECT
                    a.client_id,
                    c.name AS client_name,
                    c.chat_id,
                    c.client_group,
                    a.currency_code,
                    a.balance,
                    a.precision
                FROM client_accounts a
                JOIN clients c ON c.id = a.client_id
                WHERE a.is_active = TRUE
                ORDER BY a.client_id, a.currency_code
                """
            )
            return [dict(r) for r in rows]

    async def set_client_group_by_chat_id(self, chat_id: int, client_group: str) -> dict | None:
        async with self._connection() as con:
            row = await con.fetchrow(
                """
                UPDATE clients
                SET client_group = $2
                WHERE chat_id = $1
                RETURNING id, chat_id, name, client_group, created_at
                """,
                chat_id, client_group.strip(),
            )
            return dict(row) if row else None
