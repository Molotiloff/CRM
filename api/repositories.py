from __future__ import annotations

from collections.abc import Sequence

from db_asyncpg.repositories.base import ConnectionBoundRepo

from .models import ApiUser


class UserRepository(ConnectionBoundRepo):
    async def seed_from_managers(self, admin_ids: Sequence[int] = ()) -> None:
        async with self._connection() as con:
            async with con.transaction():
                await con.execute(
                    """
                    INSERT INTO users (tg_user_id, display_name, role)
                    SELECT m.user_id, COALESCE(NULLIF(m.display_name, ''), m.user_id::text), 'manager'
                    FROM managers m
                    ON CONFLICT (tg_user_id) DO NOTHING
                    """
                )
                await con.execute(
                    """
                    INSERT INTO users (tg_user_id, display_name, role)
                    SELECT admin_id, admin_id::text, 'admin'
                    FROM UNNEST($1::bigint[]) AS admin_id
                    ON CONFLICT (tg_user_id) DO UPDATE SET role = 'admin'
                    """,
                    list(admin_ids),
                )

    async def get_active_by_tg_user_id(self, tg_user_id: int) -> ApiUser | None:
        async with self._connection() as con:
            row = await con.fetchrow(
                """
                SELECT id, tg_user_id, display_name, role, cities, is_active
                FROM users
                WHERE tg_user_id = $1 AND is_active
                """,
                tg_user_id,
            )
        return _api_user(row)

    async def get_dev_user(self, tg_user_id: int | None = None) -> ApiUser | None:
        async with self._connection() as con:
            row = await con.fetchrow(
                """
                SELECT id, tg_user_id, display_name, role, cities, is_active
                FROM users
                WHERE is_active
                  AND ($1::bigint IS NULL OR tg_user_id = $1)
                ORDER BY
                    CASE role
                        WHEN 'admin' THEN 1
                        WHEN 'owner' THEN 2
                        WHEN 'accountant' THEN 3
                        WHEN 'manager' THEN 4
                        ELSE 5
                    END,
                    id
                LIMIT 1
                """,
                tg_user_id,
            )
        return _api_user(row)


def _api_user(row) -> ApiUser | None:
    if row is None:
        return None
    return ApiUser(
        id=row["id"],
        tg_user_id=row["tg_user_id"],
        display_name=row["display_name"],
        role=row["role"],
        cities=list(row["cities"] or []),
        is_active=row["is_active"],
    )
