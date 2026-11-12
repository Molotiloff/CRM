from db_asyncpg.repositories.rapira_widgets import RapiraWidgetRepository
from services.rate_order.widget_binding import WidgetBinding


async def test_widget_binding_restores_frame_and_overflow_messages_without_new_schema(pool):
    repo = RapiraWidgetRepository(pool)
    chat_id = 7442042
    async with pool.acquire() as con:
        await con.execute(
            "INSERT INTO live_messages(chat_id,message_key,message_id) VALUES($1,'widget_repo_unrelated',123) ON CONFLICT DO NOTHING",
            chat_id,
        )
    try:
        await repo.save(WidgetBinding(chat_id, 100, 15, [200, 201]))
        restored = next(item for item in await repo.load(limit=32) if item.chat_id == chat_id)
        assert (restored.message_id, restored.frame, restored.tails) == (100, 15, [200, 201])
        await repo.save(WidgetBinding(chat_id, 101, 5, [300]))
        restored = next(item for item in await repo.load(limit=32) if item.chat_id == chat_id)
        assert (restored.message_id, restored.frame, restored.tails) == (101, 5, [300])
        await repo.remove(chat_id)
        assert all(item.chat_id != chat_id for item in await repo.load(limit=32))
        async with pool.acquire() as con:
            assert (
                await con.fetchval(
                    "SELECT message_id FROM live_messages WHERE chat_id=$1 AND message_key='widget_repo_unrelated'",
                    chat_id,
                )
                == 123
            )
    finally:
        await repo.remove(chat_id)
        async with pool.acquire() as con:
            await con.execute(
                "DELETE FROM live_messages WHERE chat_id=$1 AND message_key='widget_repo_unrelated'",
                chat_id,
            )
