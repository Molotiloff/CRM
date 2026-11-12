"""Widget bindings reuse live_messages; no additional schema migration."""

from services.rate_order.widget_binding import WidgetBinding

from .base import ConnectionBoundRepo

PREFIX = "rapira_widget_v1:"


class RapiraWidgetRepository(ConnectionBoundRepo):
    async def load(self, *, limit: int) -> list[WidgetBinding]:
        async with self._connection() as con:
            rows = await con.fetch(
                """SELECT chat_id, message_id, message_key FROM live_messages
                WHERE message_key IN ('rapira_widget_v1:5','rapira_widget_v1:15','rapira_widget_v1:60')
                ORDER BY updated_at DESC LIMIT $1""",
                limit,
            )
            result = []
            for row in rows:
                tails = await con.fetch(
                    """SELECT message_id FROM live_messages WHERE chat_id=$1
                    AND message_key LIKE 'rapira_widget_v1:tail:%'
                    ORDER BY substring(message_key FROM '[0-9]+$')::integer LIMIT 16""",
                    row["chat_id"],
                )
                result.append(
                    WidgetBinding(
                        row["chat_id"],
                        row["message_id"],
                        int(row["message_key"].rsplit(":", 1)[-1]),
                        [item["message_id"] for item in tails],
                    )
                )
        return result

    async def save(self, binding: WidgetBinding) -> None:
        async with self._connection() as con:
            async with con.transaction():
                await con.execute(
                    "DELETE FROM live_messages WHERE chat_id=$1 AND message_key LIKE 'rapira_widget_v1:%'",
                    binding.chat_id,
                )
                records = [(binding.chat_id, PREFIX + str(binding.frame), binding.message_id)]
                records.extend(
                    (binding.chat_id, PREFIX + f"tail:{index}", message_id)
                    for index, message_id in enumerate(binding.tails)
                )
                await con.executemany(
                    "INSERT INTO live_messages(chat_id,message_key,message_id,updated_at) VALUES($1,$2,$3,NOW())",
                    records,
                )

    async def remove(self, chat_id: int) -> None:
        async with self._connection() as con:
            await con.execute(
                "DELETE FROM live_messages WHERE chat_id=$1 AND message_key LIKE 'rapira_widget_v1:%'",
                chat_id,
            )
