from __future__ import annotations

from db_asyncpg.repositories.base import ConnectionBoundRepo


class ManagersRepo(ConnectionBoundRepo):
    async def list_managers(self) -> list[dict]:
        async with self._connection() as con:
            rows = await con.fetch("SELECT user_id, display_name, added_at FROM managers ORDER BY added_at")
            return [dict(r) for r in rows]

    async def add_manager(self, user_id: int, display_name: str = "") -> bool:
        async with self._connection() as con:
            res = await con.execute(
                """
                INSERT INTO managers (user_id, display_name)
                VALUES ($1, $2)
                ON CONFLICT (user_id) DO UPDATE
                  SET display_name = EXCLUDED.display_name
                """,
                user_id, display_name,
            )
            return res.startswith("INSERT") or res.startswith("UPDATE")

    async def remove_manager(self, user_id: int) -> bool:
        async with self._connection() as con:
            res = await con.execute("DELETE FROM managers WHERE user_id=$1", user_id)
            return res.endswith(" 1")

    async def is_manager(self, user_id: int) -> bool:
        async with self._connection() as con:
            row = await con.fetchrow("SELECT 1 FROM managers WHERE user_id=$1", user_id)
            return row is not None
