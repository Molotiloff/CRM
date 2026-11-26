from datetime import UTC, datetime, timedelta

from db_asyncpg.repositories.broadcast_audience import BroadcastAudienceRepo


async def test_inactivity_is_based_on_last_customer_message(pool) -> None:
    now = datetime.now(UTC)
    cutoff = now - timedelta(days=7)
    old = cutoff - timedelta(seconds=1)
    async with pool.acquire() as con:
        await con.execute("INSERT INTO managers(user_id) VALUES (502)")
        await con.execute(
            "INSERT INTO users(tg_user_id,display_name,role) VALUES (503,'Staff','cashier')"
        )
        authors = {}
        for user_id, is_bot in [
            (501, True),
            (502, False),
            (503, False),
            (504, False),
            (505, False),
        ]:
            authors[user_id] = await con.fetchval(
                "INSERT INTO message_archive_authors(telegram_user_id,display_name,is_bot) VALUES ($1,'Author',$2) RETURNING id",
                user_id,
                is_bot,
            )

        async def client(chat_id, messages, *, active=True, enabled=True, created_at=old):
            await con.execute(
                "INSERT INTO clients(chat_id,name,is_active,created_at) VALUES ($1,'Client',$2,$3)",
                chat_id,
                active,
                created_at,
            )
            if messages is None:
                return
            archive_id = await con.fetchval(
                "INSERT INTO message_archive_chats(telegram_chat_id,chat_type,current_title,is_enabled) VALUES ($1,'group','Client',$2) RETURNING id",
                chat_id,
                enabled,
            )
            for message_id, (user_id, sent_at, direction, kind) in enumerate(messages, 1):
                await con.execute(
                    """INSERT INTO message_archive_messages
                    (chat_id,telegram_message_id,author_id,sent_at,direction,message_type,source,content_hash)
                    VALUES ($1,$2,$3,$4,$5,$6,'bot_api','fixture')""",
                    archive_id,
                    message_id,
                    authors[user_id],
                    sent_at,
                    direction,
                    kind,
                )

        known = (505, old, "inbound", "text")
        await client(
            -501,
            [
                known,
                (501, now, "outbound", "text"),
                (502, now, "inbound", "text"),
                (503, now, "inbound", "text"),
                (504, now, "inbound", "text"),
                (505, now, "inbound", "new_chat_members"),
            ],
        )
        await client(-502, [known, (505, now, "inbound", "photo")])
        await client(-503, [(505, cutoff, "inbound", "text")])
        await client(-504, [(501, old, "outbound", "text")])
        await client(-505, None)
        await client(-506, [known], active=False)
        await client(-507, [known], enabled=False)
        await client(-508, [known], created_at=now)

    rows = await BroadcastAudienceRepo(pool).list_inactive_clients(
        cutoff=cutoff, staff_user_ids=[504]
    )
    assert [row["chat_id"] for row in rows] == [-501]
