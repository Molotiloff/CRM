from db_asyncpg.repositories.telegram_cooldowns import TelegramCooldownRepo


async def test_cooldown_repository_persists_longest_deadline_per_bot_and_chat(pool):
    repo = TelegramCooldownRepo(pool)
    assert await repo.deadline(819711, -516007) is None
    await repo.extend(819711, -516007, 2000)
    await repo.extend(819711, -516007, 1000)
    assert await TelegramCooldownRepo(pool).deadline(819711, -516007) == 2000
    assert await repo.deadline(819712, -516007) is None
    assert await repo.deadline(819711, -516008) is None
