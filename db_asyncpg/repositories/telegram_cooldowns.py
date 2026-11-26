from db_asyncpg.repositories.base import ConnectionBoundRepo


class TelegramCooldownRepo(ConnectionBoundRepo):
    @staticmethod
    def _key(bot_id: int, chat_id: int) -> str:
        return f"telegram:cooldown:{bot_id}:{chat_id}"

    async def deadline(self, bot_id: int, chat_id: int) -> int | None:
        async with self._connection() as con:
            return await con.fetchval(
                "SELECT value::bigint FROM app_settings WHERE key = $1", self._key(bot_id, chat_id)
            )

    async def extend(self, bot_id: int, chat_id: int, until: int) -> None:
        async with self._connection() as con:
            await con.execute(
                """
                INSERT INTO app_settings(key, value) VALUES ($1, $2::bigint::text)
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
                WHERE app_settings.value::bigint < EXCLUDED.value::bigint
                """,
                self._key(bot_id, chat_id),
                until,
            )
