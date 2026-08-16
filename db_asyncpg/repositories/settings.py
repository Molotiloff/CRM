from __future__ import annotations

from db_asyncpg.repositories.base import ConnectionBoundRepo


class SettingsRepo(ConnectionBoundRepo):
    async def get_setting(self, key: str) -> str | None:
        async with self._connection() as con:
            row = await con.fetchrow("SELECT value FROM app_settings WHERE key=$1", key)
            return None if row is None else str(row["value"])

    async def set_setting(self, key: str, value: str) -> None:
        async with self._connection() as con:
            async with con.transaction():
                await con.execute(
                    """
                    INSERT INTO app_settings(key, value)
                    VALUES ($1, $2)
                    ON CONFLICT (key) DO UPDATE SET
                        value = EXCLUDED.value,
                        updated_at = now()
                    """,
                    key, value,
                )
